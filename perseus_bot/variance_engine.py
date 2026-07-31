"""
variance_engine.py — Perseus period variance + portfolio comparison
===================================================================
Two independent comparisons run over the same period actuals, and they answer
different questions:

**Against the building's own budget.** Each category's actual is set against its
prorated share of the annual budget. A category running more than
`budget_overrun_flag_pct` over that share is flagged "Investigate". A flag is a
question for the property manager — it is not a savings claim, because the
overrun may be a timing difference, a one-off repair, or a seasonal bill.

**Against the portfolio.** The same period's actuals are annualized and set
against what Camelot pays for that service across comparable managed buildings.
This is the check that catches structural overspend a budget cannot: a building
can be exactly on a budget that was set too high in the first place.

Only the portfolio gap produces a savings figure, and therefore a fee. Beating
your own budget is not a saving — the budget is a plan, not a market price.

Rules the engine holds to:

* Every dollar figure is computed from the parsed report, the budget baseline,
  and `portfolio_benchmarks`. The LLM is called only to phrase the recommendation
  after the numbers are fixed, and never to produce a figure.
* A category within `at_portfolio_average_threshold_pct` of the portfolio average
  is reported at market. Savings are never manufactured.
* A category with no comparable on file is reported as not addressed and
  contributes nothing to the savings total.
* Fire/life-safety, elevator safety, and statutory lines are never scope-reduced.
  The only moves available on them are rebidding identical scope, auditing the
  billing, or correcting the record.
* Utility and heating categories carry a seasonality caveat rather than a
  numeric seasonal adjustment. Camelot does not publish a seasonal curve it
  cannot evidence.

CONSOLIDATION NOTE
------------------
`MECHANISMS` and `REBID_ONLY_MECHANISM` are copied from CostBeat Bot's
`costbeat_bot/analyzer.py` under the same names, so they move to a shared module
under `utils/` once both bots merge. Keep the two copies in step until then.

Author: Camelot OS
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from perseus_bot.benchmarks import CategoryBenchmark
from perseus_bot.config_loader import load_config, periods_per_year
from perseus_bot.expense_taxonomy import UNMAPPED, category_label, normalize_category
from perseus_bot.parser import ParsedReport

logger = logging.getLogger("perseus_bot.variance_engine")

AT_PORTFOLIO_AVERAGE_TEXT = "At the portfolio average — no change recommended."
NOT_ADDRESSED_TEXT = "Not addressed — needs records/vendor-bid review."
NO_BASELINE_TEXT = "No budget line on file for this category."

FLAG_INVESTIGATE = "investigate"
FLAG_UNDERSPENT = "underspent"
FLAG_ON_TRACK = "on_track"
FLAG_NO_BASELINE = "no_baseline"

BUDGET_SOURCE_COSTBEAT = "costbeat_analysis"
BUDGET_SOURCE_UPLOADED = "uploaded"
BUDGET_SOURCE_REPORT_COLUMN = "report_column"


class MissingBaselineError(Exception):
    """Raised when no budget baseline could be established for a building."""


# ---------------------------------------------------------------------------
# Mechanism library — the specific lever available on each category
# ---------------------------------------------------------------------------
#
# Deterministic, category-specific, and stated as an action with an owner. These
# are the fallback and the ground truth; the LLM may only rephrase them.

MECHANISMS: dict[str, str] = {
    "payroll_and_cleaning": (
        "Rebuild the staffing model against the comparables: confirm actual hours "
        "worked versus hours billed, move fixed porter coverage to a scheduled "
        "route, and re-bid the outside cleaning contract alongside the payroll "
        "line so the two are priced as one scope."
    ),
    "insurance": (
        "Remarket the package to three carriers at identical limits and "
        "deductibles, using the loss runs for the last five years. Where the "
        "comparables carry the same coverage for less, the gap is pricing, "
        "not exposure."
    ),
    "hvac_mechanical": (
        "Re-bid the mechanical service contract at the same scope to three "
        "vendors already working in the Camelot portfolio, and separate "
        "preventive maintenance from time-and-materials repairs so the two "
        "stop being billed at one blended rate."
    ),
    "electricity": (
        "Audit the account for tariff class and meter assignment errors, then "
        "compare a fixed-rate ESCO supply quote against the current utility "
        "default supply. House meters carrying tenant-side load get reassigned."
    ),
    "water_sewer": (
        "Request a DEP water audit and a meter accuracy test, review the last "
        "three years of billing for estimated reads, and repair fixture leaks "
        "identified in the audit before the next billing cycle."
    ),
    "gas": (
        "Re-bid the supply contract and confirm the delivery-versus-supply split "
        "on the bill. Check the boiler's firing efficiency against its last "
        "combustion test before attributing the variance to rates."
    ),
    "phone_internet_cable": (
        "Cancel circuits with no active device on them, consolidate remaining "
        "lines onto one business account, and renegotiate at renewal against a "
        "competing quote."
    ),
    "intercom_security": (
        "Re-bid the monitoring and service contract at identical coverage, and "
        "confirm you are not paying for both a legacy intercom line and its "
        "IP replacement."
    ),
    "elevator_maintenance": (
        "Re-bid the maintenance contract at identical scope — full maintenance "
        "stays full maintenance — and confirm the contract has not auto-renewed "
        "with an escalator above the comparables. Inspection and safety-test "
        "scope is unchanged."
    ),
    "sprinkler_fire_alarm": (
        "Re-bid inspection and monitoring at identical code-required scope and "
        "frequency. Confirm you are not being billed for both a monitoring "
        "contract and a separate central-station line. No reduction in testing."
    ),
    "exterminator": (
        "Move from per-visit call-outs to a fixed monthly route contract at the "
        "frequency the comparables use, and re-bid against the vendor already "
        "servicing nearby Camelot buildings."
    ),
    "compactor_waste": (
        "Right-size pickup frequency and container count to actual volume, then "
        "re-bid the carting contract. Combine the pickup with adjacent Camelot "
        "buildings on the same route where the hauler allows it."
    ),
    "misc_repairs": (
        "Pull the last twelve months of invoices on this line and separate "
        "genuine repairs from deferred capital work. Recurring repairs on the "
        "same component get a scoped fix instead of repeat call-outs."
    ),
    "admin_fees": (
        "Reconcile the line against actual invoices, cancel unused software and "
        "subscription seats, and consolidate office and printing spend into the "
        "managing agent's existing vendor pricing."
    ),
    "legal_accounting_management": (
        "Re-scope the engagement letters: confirm the audit and tax preparation "
        "fees against the comparables, and move recurring routine matters from "
        "hourly billing to a fixed annual fee."
    ),
    "taxes_bank_fees": (
        "Verify the assessment and exemption status against the record and file "
        "a correction where the assessed value or exemptions are wrong. Bank "
        "charges get consolidated onto one operating account. No statutory "
        "obligation is reduced."
    ),
}

REBID_ONLY_MECHANISM = (
    "Re-bid at identical scope and audit the billing. Required testing, "
    "inspection frequency, and coverage stay exactly as they are."
)

# What to ask when a line is over its budget share but sits at or below the
# portfolio average — there is no savings claim to make, only a question.
OVERRUN_QUESTIONS: dict[str, str] = {
    "gas": (
        "Confirm how many heating months landed in this period before treating "
        "the overrun as a rate problem."
    ),
    "electricity": (
        "Check whether the period caught a summer cooling load the budget spread "
        "evenly across the year."
    ),
    "water_sewer": (
        "Check the bills in this period for an estimated read or a catch-up "
        "adjustment from a prior cycle."
    ),
    "hvac_mechanical": (
        "Separate the preventive-maintenance contract from repair call-outs in "
        "this period before treating the overrun as a contract problem."
    ),
    "misc_repairs": (
        "Identify which invoices drove the overrun and whether any of them "
        "belong in a capital reserve rather than an operating line."
    ),
    "insurance": (
        "Confirm whether the period carried an annual premium instalment the "
        "budget spread across twelve months."
    ),
    "taxes_bank_fees": (
        "Confirm which tax instalments fell in this period — instalment timing "
        "moves this line far more than assessment changes do."
    ),
}

GENERIC_OVERRUN_QUESTION = (
    "Pull the invoices behind this line for the period and confirm whether the "
    "overrun is timing, scope, or price."
)


# ---------------------------------------------------------------------------
# Budget baseline
# ---------------------------------------------------------------------------

@dataclass
class BudgetBaseline:
    """
    The annual budget Perseus prorates against, and where it came from.

    `annual_by_category` holds full-year figures. `period_share()` divides by the
    number of periods in the year — an even split, with a seasonality caveat
    printed on the categories where that split is known to be rough.
    """

    source: str                     # costbeat_analysis | uploaded | report_column
    cadence: str
    annual_by_category: dict[str, float] = field(default_factory=dict)
    costbeat_analysis_id: Optional[str] = None
    origin_label: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def periods(self) -> int:
        return periods_per_year(self.cadence)

    @property
    def total_annual(self) -> float:
        return sum(self.annual_by_category.values())

    def period_share(self, category: str) -> Optional[float]:
        annual = self.annual_by_category.get(category)
        return None if annual is None else annual / self.periods

    def describe(self) -> str:
        if self.source == BUDGET_SOURCE_COSTBEAT:
            return (
                f"Annual budget from the CostBeat analysis on file for this "
                f"building{f' ({self.origin_label})' if self.origin_label else ''}, "
                f"prorated to the period."
            )
        if self.source == BUDGET_SOURCE_REPORT_COLUMN:
            return (
                f"Period budget taken from the budget column of the uploaded "
                f"report{f' ({self.origin_label})' if self.origin_label else ''}."
            )
        return (
            f"Annual budget from the file uploaded with this report"
            f"{f' ({self.origin_label})' if self.origin_label else ''}, prorated "
            f"to the period."
        )


def _rollup_budget(lines: list[Any], amount_of) -> dict[str, float]:
    """Collapse budget lines into taxonomy categories."""
    totals: dict[str, float] = {}
    for line in lines:
        amount = amount_of(line)
        if not amount:
            continue
        category = normalize_category(line.raw_label, line.category_header)
        totals[category] = totals.get(category, 0.0) + amount
    return totals


def baseline_from_costbeat(analysis_row: dict[str, Any], cadence: str) -> BudgetBaseline:
    """
    Build a baseline from a `costbeat_analyses` row.

    CostBeat stores one entry per taxonomy category in `line_items`, each with
    the building's own annual budget for that category under `current_budget`.
    Those are the figures Perseus prorates — not CostBeat's targets, which are
    proposals rather than the client's budget.
    """
    items = analysis_row.get("line_items") or []
    annual: dict[str, float] = {}
    for item in items:
        category = item.get("category") or UNMAPPED
        amount = item.get("current_budget")
        if amount in (None, ""):
            continue
        annual[category] = annual.get(category, 0.0) + abs(float(amount))

    if not annual:
        raise MissingBaselineError(
            "The CostBeat analysis found for this building has no budget line "
            "items. Upload the annual budget file with this report instead."
        )

    return BudgetBaseline(
        source=BUDGET_SOURCE_COSTBEAT,
        cadence=cadence,
        annual_by_category=annual,
        costbeat_analysis_id=analysis_row.get("id"),
        origin_label=(analysis_row.get("created_at") or "")[:10],
    )


def baseline_from_annual_budget(budget: ParsedReport, cadence: str) -> BudgetBaseline:
    """Build a baseline from an annual budget file uploaded alongside the report."""
    annual = _rollup_budget(budget.expense_lines, lambda ln: ln.budget or ln.actual)
    if not annual:
        raise MissingBaselineError(
            f"No budget amounts could be read from '{budget.filename}'."
        )
    return BudgetBaseline(
        source=BUDGET_SOURCE_UPLOADED,
        cadence=cadence,
        annual_by_category=annual,
        origin_label=budget.filename,
        warnings=list(budget.warnings),
    )


def baseline_from_report_columns(report: ParsedReport, cadence: str) -> BudgetBaseline:
    """
    Build a baseline from the report's own budget column.

    That column is already scoped to the period, so it is scaled up to an annual
    figure to keep one representation throughout. No proration caveat applies
    here: the accounting group's own period budget already reflects whatever
    seasonal split they used.
    """
    period_totals = _rollup_budget(report.expense_lines, lambda ln: ln.budget)
    if not period_totals:
        raise MissingBaselineError(
            f"'{report.filename}' has no readable budget column."
        )
    periods = periods_per_year(cadence)
    return BudgetBaseline(
        source=BUDGET_SOURCE_REPORT_COLUMN,
        cadence=cadence,
        annual_by_category={k: v * periods for k, v in period_totals.items()},
        origin_label=report.filename,
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class VarianceLine:
    """One category's row: budget variance on the left, portfolio gap on the right."""

    category: str
    label: str

    # Against the building's own budget, for this period.
    actual_period: float = 0.0
    budget_period: Optional[float] = None
    budget_variance: float = 0.0            # actual - prorated budget share
    budget_variance_pct: float = 0.0
    budget_flag: str = FLAG_NO_BASELINE

    # Against the portfolio, annualized.
    annualized_actual: float = 0.0
    portfolio_average_annual: float = 0.0
    portfolio_target_annual: float = 0.0
    portfolio_savings_annual: float = 0.0
    portfolio_savings_period: float = 0.0
    portfolio_gap_pct: float = 0.0

    comp_count: int = 0
    evidence: str = ""
    recommendation: str = ""
    at_portfolio_average: bool = False
    addressed: bool = True
    scope_protected: bool = False
    seasonal: bool = False
    source_labels: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.budget_flag == FLAG_INVESTIGATE

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "label": self.label,
            "actual_period": round(self.actual_period, 2),
            "budget_period": None if self.budget_period is None else round(self.budget_period, 2),
            "budget_variance": round(self.budget_variance, 2),
            "budget_variance_pct": round(self.budget_variance_pct, 4),
            "budget_flag": self.budget_flag,
            "annualized_actual": round(self.annualized_actual, 2),
            "portfolio_average_annual": round(self.portfolio_average_annual, 2),
            "portfolio_target_annual": round(self.portfolio_target_annual, 2),
            "portfolio_savings_annual": round(self.portfolio_savings_annual, 2),
            "portfolio_savings_period": round(self.portfolio_savings_period, 2),
            "portfolio_gap_pct": round(self.portfolio_gap_pct, 4),
            "comp_count": self.comp_count,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "at_portfolio_average": self.at_portfolio_average,
            "addressed": self.addressed,
            "scope_protected": self.scope_protected,
            "seasonal": self.seasonal,
            "source_labels": self.source_labels,
        }


@dataclass
class VarianceAnalysis:
    """One period's analysis for one building."""

    property_name: str
    address: str
    period_label: str
    quarter: str
    year: int
    cadence: str
    unit_count: int
    building_type: str = ""
    market: str = ""
    baseline: Optional[BudgetBaseline] = None
    lines: list[VarianceLine] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
    needs_review: bool = False
    benchmark_coverage: int = 0
    source_format: str = ""
    uploaded_filename: str = ""

    # ── Budget side ───────────────────────────────────────────────────────
    @property
    def budgeted_lines(self) -> list[VarianceLine]:
        return [ln for ln in self.lines if ln.budget_period is not None]

    @property
    def total_budget_period(self) -> float:
        return sum(ln.budget_period or 0.0 for ln in self.lines)

    @property
    def total_actual_period(self) -> float:
        """Everything the building spent this period, budgeted or not."""
        return sum(ln.actual_period for ln in self.lines)

    @property
    def total_actual_budgeted_period(self) -> float:
        """
        Actual spend on the lines that have a budget share to compare against.

        Pairing the full actual with a budget that covers only some of the lines
        reads as an overrun when it is really an unbudgeted category, so any
        figure printed beside total_budget_period has to come from here.
        """
        return sum(ln.actual_period for ln in self.budgeted_lines)

    @property
    def budget_variance(self) -> float:
        """Positive means the building spent more than its prorated budget share."""
        return self.total_actual_budgeted_period - self.total_budget_period

    @property
    def budget_variance_pct(self) -> float:
        return self.budget_variance / self.total_budget_period if self.total_budget_period else 0.0

    @property
    def flagged_lines(self) -> list[VarianceLine]:
        return [ln for ln in self.lines if ln.budget_flag == FLAG_INVESTIGATE]

    @property
    def underspent_lines(self) -> list[VarianceLine]:
        return [ln for ln in self.lines if ln.budget_flag == FLAG_UNDERSPENT]

    # ── Portfolio side ────────────────────────────────────────────────────
    @property
    def addressed_lines(self) -> list[VarianceLine]:
        return [ln for ln in self.lines if ln.addressed]

    @property
    def not_addressed_lines(self) -> list[VarianceLine]:
        return [ln for ln in self.lines if not ln.addressed]

    @property
    def savings_lines(self) -> list[VarianceLine]:
        return [ln for ln in self.addressed_lines if ln.portfolio_savings_annual > 0]

    @property
    def portfolio_savings_annual(self) -> float:
        return sum(ln.portfolio_savings_annual for ln in self.addressed_lines)

    @property
    def portfolio_savings_period(self) -> float:
        """This period's share of the annual gap — what the one-time fee prices."""
        return sum(ln.portfolio_savings_period for ln in self.addressed_lines)

    @property
    def annualized_actual(self) -> float:
        return sum(ln.annualized_actual for ln in self.lines)

    @property
    def savings_pct_of_annualized(self) -> float:
        total = self.annualized_actual
        return self.portfolio_savings_annual / total if total else 0.0

    @property
    def seasonal_categories(self) -> list[str]:
        return [ln.label for ln in self.lines if ln.seasonal]

    def as_dict(self) -> dict:
        return {
            "property_name": self.property_name,
            "address": self.address,
            "period_label": self.period_label,
            "quarter": self.quarter,
            "year": self.year,
            "cadence": self.cadence,
            "unit_count": self.unit_count,
            "building_type": self.building_type,
            "market": self.market,
            "budget_source": self.baseline.source if self.baseline else None,
            "budget_source_description": self.baseline.describe() if self.baseline else "",
            "linked_costbeat_analysis_id": self.baseline.costbeat_analysis_id if self.baseline else None,
            "total_budget_period": round(self.total_budget_period, 2),
            "total_actual_period": round(self.total_actual_period, 2),
            "budget_variance": round(self.budget_variance, 2),
            "budget_variance_pct": round(self.budget_variance_pct, 4),
            "annualized_actual": round(self.annualized_actual, 2),
            "portfolio_savings_annual": round(self.portfolio_savings_annual, 2),
            "portfolio_savings_period": round(self.portfolio_savings_period, 2),
            "savings_pct_of_annualized": round(self.savings_pct_of_annualized, 4),
            "line_items": [ln.as_dict() for ln in self.lines],
            "flagged_categories": [ln.as_dict() for ln in self.flagged_lines],
            "not_addressed": [ln.as_dict() for ln in self.not_addressed_lines],
            "needs_review": self.needs_review,
            "parse_warnings": self.parse_warnings,
            "benchmark_coverage": self.benchmark_coverage,
            "source_format": self.source_format,
            "uploaded_filename": self.uploaded_filename,
        }


# ---------------------------------------------------------------------------
# Roll-up
# ---------------------------------------------------------------------------

@dataclass
class _CategoryRollup:
    amount: float = 0.0
    labels: list[str] = field(default_factory=list)


def roll_up_by_category(report: ParsedReport) -> dict[str, _CategoryRollup]:
    """
    Collapse the parsed expense lines into taxonomy categories, keeping the
    original GL labels so the report can show what fed each row.
    """
    rollup: dict[str, _CategoryRollup] = {}
    for line in report.expense_lines:
        category = normalize_category(line.raw_label, line.category_header)
        entry = rollup.setdefault(category, _CategoryRollup())
        entry.amount += line.amount
        entry.labels.append(line.display_label)
    return rollup


# ---------------------------------------------------------------------------
# Recommendation prose
# ---------------------------------------------------------------------------

def _base_mechanism(category: str, scope_protected: bool) -> str:
    if scope_protected:
        return MECHANISMS.get(category, REBID_ONLY_MECHANISM)
    return MECHANISMS.get(
        category,
        "Pull the underlying invoices, re-bid the scope against vendors already "
        "serving comparable Camelot buildings, and correct any billing errors "
        "found in the review.",
    )


def _overrun_question(category: str) -> str:
    return OVERRUN_QUESTIONS.get(category, GENERIC_OVERRUN_QUESTION)


def _draft_recommendations(
    analysis: VarianceAnalysis,
    benchmarks: dict[str, CategoryBenchmark],
) -> None:
    """
    Ask the LLM to tighten each mechanism into one or two sentences that cite the
    named comparable. Dollar figures are supplied as already-computed context and
    the model is told not to introduce new ones. Any failure leaves the
    deterministic text in place.
    """
    cfg = load_config()["llm"]
    if not cfg.get("enabled", True) or not os.getenv("OPENAI_API_KEY"):
        logger.info("LLM drafting skipped — using deterministic mechanism text.")
        return

    targets = analysis.savings_lines
    if not targets:
        return

    try:
        from openai import OpenAI
    except ImportError:
        logger.info("openai package not installed — using deterministic mechanism text.")
        return

    system = (
        "You write the 'How We Get There' column of a Camelot Property Management "
        "quarterly variance report for a building owner or board.\n"
        "Rules:\n"
        "- One or two sentences per line item. Name the specific mechanism and, "
        "where given, the comparable building.\n"
        "- Do not introduce, restate, or alter any dollar figure or percentage. "
        "The numbers are fixed elsewhere.\n"
        "- No sales adjectives. Do not write 'significant', 'substantial', "
        "'amazing', 'great news', 'huge', 'exciting', or 'opportunity'.\n"
        "- Never propose reducing fire/life-safety scope, elevator safety scope, "
        "inspection frequency, or a statutory obligation. On those lines the only "
        "moves are re-bidding identical scope, auditing the billing, or correcting "
        "the record.\n"
        "- Return one line per item, in the form 'category_key: text'."
    )

    items = []
    for line in targets:
        bench = benchmarks.get(line.category)
        items.append(
            f"{line.category} | annualized ${line.annualized_actual:,.0f} | "
            f"portfolio target ${line.portfolio_target_annual:,.0f} | "
            f"scope_protected={line.scope_protected} | "
            f"comparables: {bench.evidence_text() if bench else 'none'} | "
            f"baseline mechanism: {_base_mechanism(line.category, line.scope_protected)}"
        )

    try:
        client = OpenAI(timeout=float(cfg.get("timeout_seconds", 30)))
        response = client.chat.completions.create(
            model=cfg.get("model", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(items)},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("LLM drafting failed (%s) — keeping deterministic text.", exc)
        return

    drafted: dict[str, str] = {}
    for row in content.splitlines():
        if ":" not in row:
            continue
        key, _, text = row.partition(":")
        key = key.strip().strip("-*• ").lower()
        text = text.strip()
        if key in MECHANISMS and len(text) > 30:
            drafted[key] = text

    for line in targets:
        if line.category in drafted:
            line.recommendation = drafted[line.category]

    logger.info("LLM redrafted %d of %d recommendations.", len(drafted), len(targets))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def period_label(quarter: str, year: int, cadence: str) -> str:
    """Human label for the period, e.g. "Q3 2025"."""
    if cadence == "annual":
        return str(year)
    return f"{quarter} {year}" if quarter else str(year)


def analyze(
    report: ParsedReport,
    baseline: BudgetBaseline,
    benchmarks: dict[str, CategoryBenchmark],
    property_name: str,
    address: str,
    unit_count: int,
    quarter: str,
    year: int,
    cadence: str = "quarterly",
    building_type: str = "",
    market: str = "",
    use_llm: bool = True,
) -> VarianceAnalysis:
    """
    Run both comparisons over one period's actuals.

    Args:
        report:        Output of parser.parse_report.
        baseline:      Budget baseline built by one of the `baseline_from_*`
                       functions. Required — Perseus does not estimate a budget.
        benchmarks:    Output of benchmarks.fetch_benchmarks. An empty dict is
                       valid: the budget-variance half of the report still runs
                       and every line reports as not addressed.
        property_name: Subject building name as it should appear on the report.
        address:       Subject address.
        unit_count:    Subject unit count, used to scale per-unit comp costs.
        quarter:       Q1–Q4, or "" for annual cadence.
        year:          Calendar year of the period.
        cadence:       quarterly | monthly | semiannual | annual.
        building_type: condo | co-op | rental | mixed-use.
        market:        Borough or submarket.
        use_llm:       Draft the recommendation prose with the LLM.

    Returns:
        VarianceAnalysis with one line per expense category found in the report.
    """
    cfg = load_config()
    overrun_threshold = float(cfg["variance"]["budget_overrun_flag_pct"])
    underrun_threshold = float(cfg["variance"]["budget_underrun_flag_pct"])
    seasonal_categories = set(cfg["variance"]["seasonality_caveat_categories"])
    protected = set(cfg["portfolio"]["no_scope_reduction_categories"])
    periods = periods_per_year(cadence)

    analysis = VarianceAnalysis(
        property_name=property_name,
        address=address,
        period_label=period_label(quarter, year, cadence),
        quarter=quarter,
        year=year,
        cadence=cadence,
        unit_count=unit_count,
        building_type=building_type,
        market=market,
        baseline=baseline,
        parse_warnings=list(report.warnings) + list(baseline.warnings),
        needs_review=report.needs_review,
        benchmark_coverage=len(benchmarks),
        source_format=report.source_format,
        uploaded_filename=report.filename,
    )

    rollup = roll_up_by_category(report)

    for category, entry in sorted(rollup.items(), key=lambda kv: kv[1].amount, reverse=True):
        actual = entry.amount
        annualized = actual * periods
        scope_protected = category in protected
        bench = benchmarks.get(category)

        line = VarianceLine(
            category=category,
            label="Unmapped GL lines" if category == UNMAPPED else category_label(category),
            actual_period=actual,
            annualized_actual=annualized,
            scope_protected=scope_protected,
            seasonal=category in seasonal_categories,
            source_labels=entry.labels,
        )

        # ── Against the building's own budget ─────────────────────────────
        budget_share = baseline.period_share(category)
        if budget_share is None:
            line.budget_flag = FLAG_NO_BASELINE
        else:
            line.budget_period = budget_share
            line.budget_variance = actual - budget_share
            line.budget_variance_pct = (
                line.budget_variance / budget_share if budget_share else 0.0
            )
            if line.budget_variance_pct > overrun_threshold:
                line.budget_flag = FLAG_INVESTIGATE
            elif line.budget_variance_pct < -underrun_threshold:
                line.budget_flag = FLAG_UNDERSPENT
            else:
                line.budget_flag = FLAG_ON_TRACK

        # ── Against the portfolio ─────────────────────────────────────────
        if bench is None or bench.comp_count == 0:
            line.addressed = False
            line.portfolio_target_annual = annualized
            line.evidence = (
                "GL label did not map to a Camelot expense category."
                if category == UNMAPPED
                else "No comparable Camelot building on file for this category."
            )
            line.recommendation = NOT_ADDRESSED_TEXT
            analysis.lines.append(line)
            continue

        line.comp_count = bench.comp_count
        line.evidence = bench.evidence_text()
        line.portfolio_average_annual = bench.portfolio_average_annual(unit_count)
        line.portfolio_target_annual = bench.portfolio_target_annual(annualized, unit_count)
        if line.portfolio_average_annual:
            line.portfolio_gap_pct = (
                annualized - line.portfolio_average_annual
            ) / line.portfolio_average_annual

        if bench.is_at_portfolio_average(annualized, unit_count):
            line.at_portfolio_average = True
            line.portfolio_target_annual = annualized
            line.recommendation = (
                AT_PORTFOLIO_AVERAGE_TEXT
                if line.budget_flag != FLAG_INVESTIGATE
                else f"{AT_PORTFOLIO_AVERAGE_TEXT} {_overrun_question(category)}"
            )
            analysis.lines.append(line)
            continue

        line.portfolio_savings_annual = max(0.0, annualized - line.portfolio_target_annual)
        line.portfolio_savings_period = line.portfolio_savings_annual / periods
        line.recommendation = _base_mechanism(category, scope_protected)
        analysis.lines.append(line)

    if use_llm:
        _draft_recommendations(analysis, benchmarks)

    logger.info(
        "Perseus %s '%s': actual $%s vs budget share $%s (%.1f%%), "
        "$%s addressable this period, %d flagged, %d of %d lines not addressed.",
        analysis.period_label, property_name,
        f"{analysis.total_actual_budgeted_period:,.0f}", f"{analysis.total_budget_period:,.0f}",
        analysis.budget_variance_pct * 100,
        f"{analysis.portfolio_savings_period:,.0f}",
        len(analysis.flagged_lines), len(analysis.not_addressed_lines), len(analysis.lines),
    )
    return analysis
