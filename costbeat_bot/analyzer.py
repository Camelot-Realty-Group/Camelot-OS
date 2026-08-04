"""
analyzer.py — CostBeat Bot analysis engine
===========================================
Turns parsed budget lines plus portfolio comparables into the cost-beat table:
one row per expense category with a target, a dollar and percentage saving, and
a specific mechanism for getting there.

Rules the engine holds to:

* Every dollar figure is computed from `portfolio_benchmarks` data. The LLM is
  called only to phrase the "How We Get There" mechanism, and only after the
  numbers are fixed. If it is unavailable the deterministic mechanism text
  stands on its own.
* A category within `at_market_threshold_pct` of its benchmark is reported at
  0% with "At market - no change recommended." Savings are never manufactured.
* A category with no comparable on file is reported as "Not addressed — needs
  records/vendor-bid review" and excluded from the savings total.
* Fire/life-safety, elevator safety, and statutory lines are never scope-reduced.
  The only moves available on them are rebidding identical scope, auditing the
  billing, or correcting the record.

Author: Camelot OS
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from benchmarks import (
    UNMAPPED,
    CategoryBenchmark,
    category_label,
    normalize_category,
)
from config_loader import load_config
from parser import ParsedBudget

logger = logging.getLogger("costbeat_bot.analyzer")

AT_MARKET_TEXT = "At market - no change recommended."
NOT_ADDRESSED_TEXT = "Not addressed — needs records/vendor-bid review."


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


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CostBeatLine:
    """One row of the cost-beat table."""

    category: str
    label: str
    current_budget: float
    target: float
    savings: float
    savings_pct: float
    evidence: str
    recommendation: str
    comp_count: int = 0
    at_market: bool = False
    addressed: bool = True
    scope_protected: bool = False
    source_labels: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "label": self.label,
            "current_budget": round(self.current_budget, 2),
            "target": round(self.target, 2),
            "savings": round(self.savings, 2),
            "savings_pct": round(self.savings_pct, 4),
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "comp_count": self.comp_count,
            "at_market": self.at_market,
            "addressed": self.addressed,
            "scope_protected": self.scope_protected,
            "source_labels": self.source_labels,
        }


@dataclass
class CostBeatAnalysis:
    """The complete analysis for one building."""

    property_name: str
    address: str
    unit_count: int
    building_type: str
    market: str
    lines: list[CostBeatLine] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
    needs_review: bool = False
    benchmark_coverage: int = 0     # categories with at least one comparable

    @property
    def addressed_lines(self) -> list[CostBeatLine]:
        return [ln for ln in self.lines if ln.addressed]

    @property
    def not_addressed_lines(self) -> list[CostBeatLine]:
        return [ln for ln in self.lines if not ln.addressed]

    @property
    def total_budget(self) -> float:
        return sum(ln.current_budget for ln in self.lines)

    @property
    def total_target(self) -> float:
        return sum(ln.target for ln in self.lines)

    @property
    def total_savings(self) -> float:
        """Only lines with real comparable support contribute."""
        return sum(ln.savings for ln in self.addressed_lines)

    @property
    def savings_pct(self) -> float:
        return self.total_savings / self.total_budget if self.total_budget else 0.0

    @property
    def not_addressed_total(self) -> float:
        return sum(ln.current_budget for ln in self.not_addressed_lines)

    def as_dict(self) -> dict:
        return {
            "property_name": self.property_name,
            "address": self.address,
            "unit_count": self.unit_count,
            "building_type": self.building_type,
            "market": self.market,
            "total_budget": round(self.total_budget, 2),
            "total_target": round(self.total_target, 2),
            "total_savings": round(self.total_savings, 2),
            "savings_pct": round(self.savings_pct, 4),
            "line_items": [ln.as_dict() for ln in self.lines],
            "not_addressed": [ln.as_dict() for ln in self.not_addressed_lines],
            "needs_review": self.needs_review,
            "parse_warnings": self.parse_warnings,
            "benchmark_coverage": self.benchmark_coverage,
        }


# ---------------------------------------------------------------------------
# Roll-up
# ---------------------------------------------------------------------------

@dataclass
class _CategoryRollup:
    amount: float = 0.0
    labels: list[str] = field(default_factory=list)


def roll_up_by_category(budget: ParsedBudget) -> dict[str, _CategoryRollup]:
    """
    Collapse the parsed expense lines into taxonomy categories, keeping the
    original GL labels so the report can show what fed each row.
    """
    rollup: dict[str, _CategoryRollup] = {}
    for line in budget.expense_lines:
        category = normalize_category(line.raw_label, line.category_header)
        entry = rollup.setdefault(category, _CategoryRollup())
        entry.amount += line.amount
        entry.labels.append(line.display_label)
    return rollup


# ---------------------------------------------------------------------------
# "How We Get There" prose
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


def _draft_recommendations(analysis: CostBeatAnalysis, benchmarks: dict[str, CategoryBenchmark]) -> None:
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

    targets = [ln for ln in analysis.addressed_lines if not ln.at_market and ln.savings > 0]
    if not targets:
        return

    try:
        from openai import OpenAI
    except ImportError:
        logger.info("openai package not installed — using deterministic mechanism text.")
        return

    system = (
        "You write the 'How We Get There' column of a Camelot Property Management "
        "cost-beat analysis for a building owner or board.\n"
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
            f"{line.category} | current ${line.current_budget:,.0f} | "
            f"target ${line.target:,.0f} | scope_protected={line.scope_protected} | "
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

def analyze(
    budget: ParsedBudget,
    benchmarks: dict[str, CategoryBenchmark],
    property_name: str,
    address: str,
    unit_count: int,
    building_type: str = "",
    market: str = "",
    use_llm: bool = True,
) -> CostBeatAnalysis:
    """
    Build the cost-beat analysis for one building.

    Args:
        budget:        Output of parser.parse_budget.
        benchmarks:    Output of benchmarks.fetch_benchmarks. An empty dict is
                       valid — every line then reports as not addressed.
        property_name: Subject building name as it should appear on the report.
        address:       Subject address.
        unit_count:    Subject unit count, used to scale per-unit comp costs.
        building_type: condo | co-op | rental | mixed-use.
        market:        Borough or submarket.
        use_llm:       Draft the recommendation prose with the LLM.

    Returns:
        CostBeatAnalysis with one line per expense category found.
    """
    cfg = load_config()["analysis"]
    at_market_threshold = float(cfg["at_market_threshold_pct"])
    protected = set(cfg["no_scope_reduction_categories"])

    analysis = CostBeatAnalysis(
        property_name=property_name,
        address=address,
        unit_count=unit_count,
        building_type=building_type,
        market=market,
        parse_warnings=list(budget.warnings),
        needs_review=budget.needs_review,
        benchmark_coverage=len(benchmarks),
    )

    rollup = roll_up_by_category(budget)

    for category, entry in sorted(rollup.items(), key=lambda kv: kv[1].amount, reverse=True):
        current = entry.amount
        scope_protected = category in protected
        bench = benchmarks.get(category)

        # ── No comparable on file — say so, claim nothing ──────────────────
        if bench is None or bench.comp_count == 0:
            reason = (
                "GL label did not map to a Camelot expense category."
                if category == UNMAPPED
                else "No comparable Camelot building on file for this category."
            )
            analysis.lines.append(CostBeatLine(
                category=category,
                label=category_label(category) if category != UNMAPPED else "Unmapped GL lines",
                current_budget=current,
                target=current,
                savings=0.0,
                savings_pct=0.0,
                evidence=reason,
                recommendation=NOT_ADDRESSED_TEXT,
                comp_count=0,
                addressed=False,
                scope_protected=scope_protected,
                source_labels=entry.labels,
            ))
            continue

        target = bench.target_annual(current, unit_count)
        savings = max(0.0, current - target)
        savings_pct = savings / current if current else 0.0

        # ── Already at market ─────────────────────────────────────────────
        if savings_pct <= at_market_threshold:
            analysis.lines.append(CostBeatLine(
                category=category,
                label=category_label(category),
                current_budget=current,
                target=current,
                savings=0.0,
                savings_pct=0.0,
                evidence=bench.evidence_text(),
                recommendation=AT_MARKET_TEXT,
                comp_count=bench.comp_count,
                at_market=True,
                scope_protected=scope_protected,
                source_labels=entry.labels,
            ))
            continue

        analysis.lines.append(CostBeatLine(
            category=category,
            label=category_label(category),
            current_budget=current,
            target=target,
            savings=savings,
            savings_pct=savings_pct,
            evidence=bench.evidence_text(),
            recommendation=_base_mechanism(category, scope_protected),
            comp_count=bench.comp_count,
            scope_protected=scope_protected,
            source_labels=entry.labels,
        ))

    if use_llm:
        _draft_recommendations(analysis, benchmarks)

    logger.info(
        "Analysis for '%s': $%s budget, $%s addressable (%.1f%%), %d of %d lines not addressed.",
        property_name, f"{analysis.total_budget:,.0f}", f"{analysis.total_savings:,.0f}",
        analysis.savings_pct * 100, len(analysis.not_addressed_lines), len(analysis.lines),
    )
    return analysis
