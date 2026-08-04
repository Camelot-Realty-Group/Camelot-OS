"""
fee_engine.py — CostBeat Bot savings-capture fee proposal
=========================================================
Camelot only bills for value it has already created. This module turns the
analyzer's addressable savings into two priced options:

1. One-time cost-recovery fee — a share of Year-1 implemented savings, billed
   once when the client approves and the vendor transitions complete.
2. Management-fee uplift — a share of the ongoing annual savings, converted into
   a permanent addition to the monthly management fee.

Which one to recommend depends on where the savings come from. Savings from
one-off vendor switches and rebids are captured once and then run themselves —
that is a one-time fee. Savings that require standing oversight to hold —
insurance remarketing, water and electric audits, staffing discipline, assessment
work — argue for the uplift, because Camelot has to keep working to keep them.

Only savings the analyzer marked addressed feed these numbers. Lines reported as
"at market" or "not addressed" contribute nothing.

Author: Camelot OS
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from analyzer import CostBeatAnalysis
from benchmarks import category_label
from config_loader import load_config

logger = logging.getLogger("costbeat_bot.fee_engine")

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
    """One priced way for Camelot to capture a share of the savings."""

    model: str
    headline: str
    camelot_share_pct: float
    camelot_year1: float
    camelot_annual_ongoing: float
    client_year1: float
    client_annual_ongoing: float
    monthly_amount: float = 0.0        # uplift only
    talking_points: list[str] = field(default_factory=list)

    def client_value_over(self, years: int) -> float:
        """Client's cumulative retained savings across `years`."""
        return self.client_year1 + self.client_annual_ongoing * max(0, years - 1)

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "headline": self.headline,
            "camelot_share_pct": round(self.camelot_share_pct, 4),
            "camelot_year1": round(self.camelot_year1, 2),
            "camelot_annual_ongoing": round(self.camelot_annual_ongoing, 2),
            "client_year1": round(self.client_year1, 2),
            "client_annual_ongoing": round(self.client_annual_ongoing, 2),
            "monthly_amount": round(self.monthly_amount, 2),
            "client_value_5yr": round(self.client_value_over(5), 2),
            "talking_points": self.talking_points,
        }


@dataclass
class FeeProposal:
    """Both options, the recommendation, and why."""

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
            "annual_savings": round(self.annual_savings, 2),
            "structural_savings": round(self.structural_savings, 2),
            "one_off_savings": round(self.one_off_savings, 2),
            "structural_share": round(self.structural_share, 4),
            "recommended_model": self.recommended_model,
            "rationale": self.rationale,
            "options": {ONE_TIME: self.one_time.as_dict(), MGMT_UPLIFT: self.uplift.as_dict()},
        }


def split_savings_by_durability(analysis: CostBeatAnalysis) -> tuple[float, float, list[str], list[str]]:
    """
    Split addressable savings into the part that needs ongoing oversight and the
    part captured once. Returns (structural, one_off, structural_labels,
    one_off_labels).
    """
    structural = one_off = 0.0
    structural_labels: list[str] = []
    one_off_labels: list[str] = []

    for line in analysis.addressed_lines:
        if line.savings <= 0:
            continue
        if line.category in STRUCTURAL_CATEGORIES:
            structural += line.savings
            structural_labels.append(category_label(line.category))
        else:
            one_off += line.savings
            one_off_labels.append(category_label(line.category))

    return structural, one_off, structural_labels, one_off_labels


def build_proposal(analysis: CostBeatAnalysis) -> FeeProposal:
    """
    Price both capture options against the analysis and pick a default.

    A zero-savings analysis still returns a proposal, with both options at $0 and
    a rationale saying there is nothing to bill for. That is the honest output
    when the building is already at market.
    """
    fees = load_config()["fees"]
    one_time_pct = float(fees["one_time_fee_pct_of_year1_savings"])
    uplift_pct = float(fees["mgmt_fee_uplift_pct_of_annual_savings"])

    annual = analysis.total_savings
    structural, one_off, structural_labels, one_off_labels = split_savings_by_durability(analysis)

    # ── Option 1: one-time cost-recovery fee ──────────────────────────────
    one_time_fee = annual * one_time_pct
    one_time_client_5yr = (annual - one_time_fee) + annual * 4
    option_one_time = FeeOption(
        model=ONE_TIME,
        headline=(
            f"One-time cost-recovery fee of ${one_time_fee:,.0f}, billed once on "
            f"approval and completion of the vendor transitions."
        ),
        camelot_share_pct=one_time_pct,
        camelot_year1=one_time_fee,
        camelot_annual_ongoing=0.0,
        client_year1=annual - one_time_fee,
        client_annual_ongoing=annual,
        talking_points=[
            f"You keep ${annual - one_time_fee:,.0f} of the ${annual:,.0f} in Year 1 "
            f"({(1 - one_time_pct) * 100:.0f}%), and the full ${annual:,.0f} every year after.",
            "The fee is billed only after the switches are made and the lower "
            "invoices are in hand. Nothing is owed on savings that do not land.",
            "Your management fee does not change.",
            f"Cumulative retained savings over five years: ${one_time_client_5yr:,.0f}.",
        ],
    )

    # ── Option 2: permanent management-fee uplift ─────────────────────────
    uplift_annual = annual * uplift_pct
    option_uplift = FeeOption(
        model=MGMT_UPLIFT,
        headline=(
            f"Permanent management-fee uplift of ${uplift_annual / 12:,.0f} per month "
            f"(${uplift_annual:,.0f} per year)."
        ),
        camelot_share_pct=uplift_pct,
        camelot_year1=uplift_annual,
        camelot_annual_ongoing=uplift_annual,
        client_year1=annual - uplift_annual,
        client_annual_ongoing=annual - uplift_annual,
        monthly_amount=uplift_annual / 12,
        talking_points=[
            f"You keep ${annual - uplift_annual:,.0f} of the ${annual:,.0f} "
            f"({(1 - uplift_pct) * 100:.0f}%), every year, for as long as the savings hold.",
            "Nothing is due up front. The uplift starts the month the reduced "
            "invoices begin.",
            "It ties our fee to your expense performance: if these lines drift "
            "back up, the case for the uplift goes with them.",
            f"Cumulative retained savings over five years: "
            f"${(annual - uplift_annual) * 5:,.0f}.",
        ],
    )

    # ── Recommendation ────────────────────────────────────────────────────
    if annual <= 0:
        recommended = ONE_TIME
        rationale = (
            "No addressable savings were identified against the Camelot "
            "comparables, so there is nothing to price. Both options are shown "
            "at zero."
        )
    elif structural > one_off:
        recommended = MGMT_UPLIFT
        rationale = (
            f"${structural:,.0f} of the ${annual:,.0f} sits in lines that need "
            f"standing oversight to hold — {_join(structural_labels)}. Insurance "
            f"has to be remarketed at every renewal, audit findings have to be "
            f"re-checked, and staffing coverage has to be managed continuously. "
            f"The uplift pays Camelot to keep doing that work, and stops paying "
            f"if the savings stop."
        )
    else:
        recommended = ONE_TIME
        rationale = (
            f"${one_off:,.0f} of the ${annual:,.0f} comes from one-off vendor "
            f"switches and rebids — {_join(one_off_labels)}. Once those contracts "
            f"are replaced the lower rate runs itself, so a single cost-recovery "
            f"fee matches the work involved better than a permanent fee increase."
        )

    proposal = FeeProposal(
        annual_savings=annual,
        structural_savings=structural,
        one_off_savings=one_off,
        one_time=option_one_time,
        uplift=option_uplift,
        recommended_model=recommended,
        rationale=rationale,
    )
    logger.info(
        "Fee proposal: $%s annual savings, recommending %s (structural share %.0f%%).",
        f"{annual:,.0f}", recommended, proposal.structural_share * 100,
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
