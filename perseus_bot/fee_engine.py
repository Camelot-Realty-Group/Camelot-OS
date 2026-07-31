"""
fee_engine.py — Perseus per-period fee proposal
===============================================
Camelot only bills for value it has already created. This module turns one
period's addressable savings into two priced options:

1. One-time fee — a share of the savings identified for THIS period, billed once
   when the client approves and the vendor transitions complete.
2. Management-fee uplift — a share of those savings annualized, converted into a
   permanent addition to the monthly management fee.

**Each period stands alone.** The proposal prices only the savings newly
identified in the period being reported. Nothing is carried forward from a prior
quarter, nothing is netted against a CostBeat annual-budget proposal for the same
building, and no running total accumulates across reports. A finding already
priced in an earlier period is not priced again here.

Which option to recommend depends on where the savings come from. Savings from
one-off vendor switches and rebids are captured once and then run themselves —
that is a one-time fee. Savings that require standing oversight to hold —
insurance remarketing, water and electric audits, staffing discipline, assessment
work — argue for the uplift, because Camelot has to keep working to keep them.

Only the portfolio-average gap feeds these numbers. A category over its own
budget is a flag for the manager, not a saving: the building's budget is a plan,
and beating a plan is not the same as paying a market price. Lines reported at
the portfolio average or not addressed contribute nothing.

CONSOLIDATION NOTE
------------------
`FeeOption`, `FeeProposal`, `STRUCTURAL_CATEGORIES`, `split_savings_by_durability`
and `build_proposal` mirror CostBeat Bot's `costbeat_bot/fee_engine.py` names, so
the two collapse into a shared `utils/fee_engine.py` once both bots merge. The
difference to preserve at that point: CostBeat prices a year, Perseus prices one
period and annualizes separately.

Author: Camelot OS
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from perseus_bot.config_loader import load_config, periods_per_year
from perseus_bot.expense_taxonomy import category_label
from perseus_bot.variance_engine import VarianceAnalysis

logger = logging.getLogger("perseus_bot.fee_engine")

ONE_TIME = "one_time_fee"
MGMT_UPLIFT = "mgmt_fee_uplift"

# Categories whose savings need standing oversight to hold year after year.
# Everything else is captured once by switching or rebidding a vendor.
STRUCTURAL_CATEGORIES = {
    "insurance",                     # remarketed at every renewal
    "water_sewer",                   # audit findings and meter reads need re-checking
    "electricity",                   # tariff class and supply contract drift back
    "payroll_and_cleaning",          # hours and coverage need continuous management
    "misc_repairs",                  # invoice discipline is an ongoing habit
    "legal_accounting_management",   # engagement scope creeps back
    "taxes_bank_fees",               # assessment and exemption work is recurring
}


@dataclass
class FeeOption:
    """One priced way for Camelot to capture a share of this period's savings."""

    model: str
    headline: str
    camelot_share_pct: float
    camelot_amount: float               # billed once, or per year for the uplift
    client_period_retained: float
    client_annual_ongoing: float
    monthly_amount: float = 0.0         # uplift only
    talking_points: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "headline": self.headline,
            "camelot_share_pct": round(self.camelot_share_pct, 4),
            "camelot_amount": round(self.camelot_amount, 2),
            "client_period_retained": round(self.client_period_retained, 2),
            "client_annual_ongoing": round(self.client_annual_ongoing, 2),
            "monthly_amount": round(self.monthly_amount, 2),
            "talking_points": self.talking_points,
        }


@dataclass
class FeeProposal:
    """Both options for one period, the recommendation, and why."""

    period_label: str
    period_savings: float
    annual_savings: float
    structural_savings: float
    one_off_savings: float
    one_time: FeeOption
    uplift: FeeOption
    recommended_model: str
    rationale: str

    @property
    def structural_share(self) -> float:
        return self.structural_savings / self.annual_savings if self.annual_savings else 0.0

    @property
    def recommended(self) -> FeeOption:
        return self.one_time if self.recommended_model == ONE_TIME else self.uplift

    def as_dict(self) -> dict:
        return {
            "period_label": self.period_label,
            "period_savings": round(self.period_savings, 2),
            "annual_savings": round(self.annual_savings, 2),
            "structural_savings": round(self.structural_savings, 2),
            "one_off_savings": round(self.one_off_savings, 2),
            "structural_share": round(self.structural_share, 4),
            "recommended_model": self.recommended_model,
            "rationale": self.rationale,
            "independent_per_period": True,
            "options": {ONE_TIME: self.one_time.as_dict(), MGMT_UPLIFT: self.uplift.as_dict()},
        }


def split_savings_by_durability(
    analysis: VarianceAnalysis,
) -> tuple[float, float, list[str], list[str]]:
    """
    Split this period's annualized savings into the part that needs ongoing
    oversight and the part captured once. Returns (structural, one_off,
    structural_labels, one_off_labels).
    """
    structural = one_off = 0.0
    structural_labels: list[str] = []
    one_off_labels: list[str] = []

    for line in analysis.savings_lines:
        if line.category in STRUCTURAL_CATEGORIES:
            structural += line.portfolio_savings_annual
            structural_labels.append(category_label(line.category))
        else:
            one_off += line.portfolio_savings_annual
            one_off_labels.append(category_label(line.category))

    return structural, one_off, structural_labels, one_off_labels


def build_proposal(analysis: VarianceAnalysis) -> FeeProposal:
    """
    Price both capture options against one period's findings and pick a default.

    A period with no addressable savings still returns a proposal, with both
    options at $0 and a rationale saying there is nothing to bill for. That is
    the honest output when the building is already at the portfolio average.
    """
    fees = load_config()["fees"]
    one_time_pct = float(fees["one_time_fee_pct_of_savings"])
    uplift_pct = float(fees["mgmt_fee_uplift_pct_of_annual_savings"])

    period_savings = analysis.portfolio_savings_period
    annual = analysis.portfolio_savings_annual
    periods = periods_per_year(analysis.cadence)
    structural, one_off, structural_labels, one_off_labels = split_savings_by_durability(analysis)
    label = analysis.period_label

    # ── Option 1: one-time fee on this period's savings ────────────────────
    one_time_fee = period_savings * one_time_pct
    option_one_time = FeeOption(
        model=ONE_TIME,
        headline=(
            f"One-time fee of ${one_time_fee:,.0f} on the ${period_savings:,.0f} "
            f"identified in {label}, billed once on approval and completion of the "
            f"vendor transitions."
        ),
        camelot_share_pct=one_time_pct,
        camelot_amount=one_time_fee,
        client_period_retained=period_savings - one_time_fee,
        client_annual_ongoing=annual,
        talking_points=[
            f"You keep ${period_savings - one_time_fee:,.0f} of what {label} "
            f"identified ({(1 - one_time_pct) * 100:.0f}%), and the full "
            f"${annual:,.0f} a year once the changes are in place.",
            "The fee is billed only after the switches are made and the lower "
            "invoices are in hand. Nothing is owed on savings that do not land.",
            "Your management fee does not change.",
            f"This prices {label} only. Next quarter's report is priced on its own "
            f"findings, and nothing here is carried into it.",
        ],
    )

    # ── Option 2: permanent management-fee uplift ──────────────────────────
    uplift_annual = annual * uplift_pct
    option_uplift = FeeOption(
        model=MGMT_UPLIFT,
        headline=(
            f"Permanent management-fee uplift of ${uplift_annual / 12:,.0f} per month "
            f"(${uplift_annual:,.0f} per year) against the ${annual:,.0f} annualized "
            f"from {label}."
        ),
        camelot_share_pct=uplift_pct,
        camelot_amount=uplift_annual,
        client_period_retained=period_savings - uplift_annual / periods,
        client_annual_ongoing=annual - uplift_annual,
        monthly_amount=uplift_annual / 12,
        talking_points=[
            f"You keep ${annual - uplift_annual:,.0f} of the ${annual:,.0f} "
            f"({(1 - uplift_pct) * 100:.0f}%), every year, for as long as the "
            f"savings hold.",
            "Nothing is due up front. The uplift starts the month the reduced "
            "invoices begin.",
            "It ties our fee to your expense performance: if these lines drift "
            "back up, the case for the uplift goes with them.",
            f"The uplift covers what {label} found. A finding in a later quarter "
            f"is priced separately, and this one is not re-billed.",
        ],
    )

    # ── Recommendation ─────────────────────────────────────────────────────
    if annual <= 0:
        recommended = ONE_TIME
        rationale = (
            f"{label} identified no addressable savings against the Camelot "
            f"comparables, so there is nothing to price. Both options are shown "
            f"at zero."
        )
    elif structural > one_off:
        recommended = MGMT_UPLIFT
        rationale = (
            f"${structural:,.0f} of the ${annual:,.0f} annualized sits in lines "
            f"that need standing oversight to hold — {_join(structural_labels)}. "
            f"Insurance has to be remarketed at every renewal, audit findings have "
            f"to be re-checked, and staffing coverage has to be managed "
            f"continuously. The uplift pays Camelot to keep doing that work, and "
            f"stops paying if the savings stop."
        )
    else:
        recommended = ONE_TIME
        rationale = (
            f"${one_off:,.0f} of the ${annual:,.0f} annualized comes from one-off "
            f"vendor switches and rebids — {_join(one_off_labels)}. Once those "
            f"contracts are replaced the lower rate runs itself, so a single fee on "
            f"this period's findings matches the work involved better than a "
            f"permanent fee increase."
        )

    proposal = FeeProposal(
        period_label=label,
        period_savings=period_savings,
        annual_savings=annual,
        structural_savings=structural,
        one_off_savings=one_off,
        one_time=option_one_time,
        uplift=option_uplift,
        recommended_model=recommended,
        rationale=rationale,
    )
    logger.info(
        "Fee proposal for %s: $%s this period ($%s annualized), recommending %s "
        "(structural share %.0f%%).",
        label, f"{period_savings:,.0f}", f"{annual:,.0f}", recommended,
        proposal.structural_share * 100,
    )
    return proposal


def _join(labels: list[str]) -> str:
    """Oxford-comma join of category labels, capped so the sentence stays readable."""
    unique = list(dict.fromkeys(labels))[:4]
    if not unique:
        return "no single category"
    if len(unique) == 1:
        return unique[0]
    return ", ".join(unique[:-1]) + f", and {unique[-1]}"
