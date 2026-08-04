"""
benchmarks.py — CostBeat Bot category normalizer + portfolio comparables
========================================================================
Two jobs:

1. Map arbitrary GL labels onto Camelot's fixed expense taxonomy (see
   config.yaml `categories`). The mapping table below is keyword-driven and
   meant to be extended as new charts of accounts turn up.

2. Look up comparable costs from `portfolio_benchmarks` — Camelot's own managed
   buildings, seeded by ops from the MDS monthly reports. Comps are filtered to
   a similar unit-count band and, where available, the same building type and
   market. Everything is normalised per unit before comparison, because a
   34-unit comp's absolute water bill says nothing about an 8-unit building.

The target estimate is deliberately conservative: it sits near the expensive end
of the comp range, and with thin comp coverage it is pulled back toward the
subject building's own spend. That biases the report toward understating savings.

Author: Camelot OS
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from config_loader import load_config
from storage import SupabaseREST

logger = logging.getLogger("costbeat_bot.benchmarks")


# ---------------------------------------------------------------------------
# GL label → taxonomy mapping
# ---------------------------------------------------------------------------
#
# Ordered most-specific first: the first category whose keywords match wins.
# Keywords are matched as substrings against the lowercased label (GL group
# header + account label), so keep them distinctive.

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    # Payroll and cleaning are evaluated as one block — a super's wages, the
    # payroll taxes on them, and an outside porter contract are substitutes for
    # each other, so splitting them produces misleading per-line comparisons.
    "payroll_and_cleaning": [
        "payroll", "wages", "salary", "salaries", "superintendent", "super's",
        "porter", "handyman", "doorman", "concierge staff", "staff",
        "payroll tax", "fica", "workers comp", "workers' comp", "workmen",
        "disability insurance", "union", "benefits", "cleaning", "janitorial",
        "cleaning supplies", "sidewalk", "snow removal",
    ],
    "sprinkler_fire_alarm": [
        "sprinkler", "fire alarm", "fire safety", "standpipe", "fire extinguisher",
        "fire suppression", "smoke detector", "life safety", "fdny",
    ],
    "elevator_maintenance": [
        "elevator", "lift maintenance", "elevator inspection", "elevator service",
    ],
    "intercom_security": [
        "intercom", "security", "camera", "cctv", "access control", "alarm monitoring",
        "guard", "buzzer", "door entry",
    ],
    "hvac_mechanical": [
        "hvac", "boiler", "heating", "furnace", "burner", "chiller", "air conditioning",
        "cooling tower", "mechanical", "plumbing contract", "pump", "compressor",
    ],
    "electricity": ["electric", "con ed", "coned", "consolidated edison", "utility electric"],
    "water_sewer": ["water", "sewer", "dep ", "dep-", "water & sewer", "water/sewer"],
    "gas": ["gas", "national grid", "fuel", "oil delivery", "heating oil", "propane"],
    "phone_internet_cable": [
        "phone", "telephone", "internet", "cable", "wifi", "wi-fi", "broadband",
        "verizon", "spectrum", "altice", "telecom",
    ],
    "exterminator": ["exterminat", "pest", "rodent", "bed bug", "vermin"],
    "compactor_waste": [
        "compactor", "waste", "garbage", "trash", "refuse", "carting", "recycling",
        "sanitation", "dumpster",
    ],
    "insurance": [
        "insurance", "liability coverage", "umbrella policy", "property coverage",
        "d&o", "boiler & machinery", "flood policy",
    ],
    "legal_accounting_management": [
        "legal", "attorney", "counsel", "accounting", "accountant", "audit fee",
        "management fee", "managing agent", "cpa", "bookkeeping", "tax preparation",
    ],
    "admin_fees": [
        "administrative", "admin", "office", "postage", "printing", "supplies",
        "software", "dues", "subscription", "permit", "filing fee", "license",
    ],
    "taxes_bank_fees": [
        "real estate tax", "property tax", "re tax", "bank fee", "bank charge",
        "interest expense", "franchise tax", "commercial rent tax", "mortgage interest",
    ],
    "misc_repairs": [
        "repair", "maintenance", "general maintenance", "building supplies",
        "hardware", "painting", "misc", "miscellaneous", "contingency", "other expense",
    ],
}

CATEGORY_LABELS: dict[str, str] = {
    "payroll_and_cleaning": "Payroll & Cleaning",
    "insurance": "Insurance",
    "hvac_mechanical": "HVAC / Mechanical",
    "electricity": "Electricity",
    "water_sewer": "Water & Sewer",
    "gas": "Gas / Fuel",
    "phone_internet_cable": "Phone / Internet / Cable",
    "intercom_security": "Intercom & Security",
    "elevator_maintenance": "Elevator Maintenance",
    "sprinkler_fire_alarm": "Sprinkler & Fire Alarm",
    "exterminator": "Exterminator",
    "compactor_waste": "Compactor & Waste Removal",
    "misc_repairs": "Miscellaneous Repairs",
    "admin_fees": "Administrative Fees",
    "legal_accounting_management": "Legal, Accounting & Management",
    "taxes_bank_fees": "Taxes & Bank Fees",
}

UNMAPPED = "unmapped"


def category_label(category: str) -> str:
    """Human-readable name for a taxonomy key."""
    return CATEGORY_LABELS.get(category, category.replace("_", " ").title())


def normalize_category(label: str, category_header: Optional[str] = None) -> str:
    """
    Map a GL label onto the taxonomy, returning UNMAPPED when nothing matches.

    The account label is checked before its GL group header, so a
    "Repairs → Elevator Service Contract" row lands in elevator_maintenance
    rather than misc_repairs.
    """
    for candidate in (label, category_header):
        if not candidate:
            continue
        haystack = re.sub(r"\s+", " ", candidate.lower())
        for category, keywords in CATEGORY_KEYWORDS.items():
            if any(keyword in haystack for keyword in keywords):
                return category
    logger.debug("No taxonomy match for label=%r header=%r", label, category_header)
    return UNMAPPED


# ---------------------------------------------------------------------------
# Comparables
# ---------------------------------------------------------------------------

@dataclass
class Comparable:
    """One Camelot-managed building's actual cost for one category."""

    building_name: str
    category: str
    annual_cost: float
    unit_count: int
    address: str = ""
    building_type: str = ""
    market: str = ""
    source: str = ""
    as_of_date: str = ""

    @property
    def per_unit(self) -> float:
        return self.annual_cost / self.unit_count if self.unit_count else 0.0

    def evidence(self) -> str:
        """Reference-example citation style: name the building and its numbers."""
        parts = [f"{self.building_name}: ${self.annual_cost:,.0f}/yr"]
        if self.unit_count:
            parts.append(f"{self.unit_count} units, ${self.per_unit:,.0f}/unit")
        if self.as_of_date:
            parts.append(f"as of {self.as_of_date}")
        return f"{parts[0]} ({', '.join(parts[1:])})" if len(parts) > 1 else parts[0]


@dataclass
class CategoryBenchmark:
    """Aggregated comparable evidence for one taxonomy category."""

    category: str
    comps: list[Comparable] = field(default_factory=list)

    @property
    def comp_count(self) -> int:
        return len(self.comps)

    @property
    def per_unit_values(self) -> list[float]:
        return sorted(c.per_unit for c in self.comps if c.per_unit > 0)

    @property
    def low_per_unit(self) -> float:
        values = self.per_unit_values
        return values[0] if values else 0.0

    @property
    def high_per_unit(self) -> float:
        values = self.per_unit_values
        return values[-1] if values else 0.0

    @property
    def median_per_unit(self) -> float:
        values = self.per_unit_values
        return median(values) if values else 0.0

    def evidence_text(self, max_comps: int = 3) -> str:
        """Named comparables with their actual dollars, highest cost first."""
        if not self.comps:
            return "No comparable building on file for this category."
        ranked = sorted(self.comps, key=lambda c: c.per_unit, reverse=True)[:max_comps]
        text = "; ".join(c.evidence() for c in ranked)
        if self.comp_count > max_comps:
            text += f"; +{self.comp_count - max_comps} more Camelot comparable(s)"
        return text

    def target_annual(self, subject_amount: float, subject_units: int) -> float:
        """
        Conservative annual target for the subject building.

        Sits at `target_percentile_of_comp_range` between the cheapest and
        dearest comp on a per-unit basis. With fewer than
        `min_comps_for_confident_target` comps the target is averaged with the
        subject's own spend, so thin evidence cannot drive a large claim.
        The target is never allowed above what the building already pays.
        """
        if not self.comps or not subject_units:
            return subject_amount

        analysis = load_config()["analysis"]
        percentile = float(analysis["target_percentile_of_comp_range"])
        min_comps = int(analysis["min_comps_for_confident_target"])

        per_unit_target = self.low_per_unit + (self.high_per_unit - self.low_per_unit) * percentile
        target = per_unit_target * subject_units

        if self.comp_count < min_comps:
            target = (target + subject_amount) / 2

        return min(target, subject_amount)


# ---------------------------------------------------------------------------
# Supabase lookup
# ---------------------------------------------------------------------------

def _row_to_comparable(row: dict) -> Optional[Comparable]:
    """Build a Comparable from a portfolio_benchmarks row, or None if unusable."""
    annual = row.get("annual_cost")
    monthly = row.get("monthly_cost")
    if annual in (None, 0) and monthly not in (None, 0):
        annual = float(monthly) * 12
    if not annual:
        return None
    units = int(row.get("unit_count") or 0)
    if not units:
        return None
    return Comparable(
        building_name=row.get("building_name") or "Unnamed Camelot building",
        category=row.get("category") or UNMAPPED,
        annual_cost=float(annual),
        unit_count=units,
        address=row.get("address") or "",
        building_type=row.get("building_type") or "",
        market=row.get("market") or "",
        source=row.get("source") or "",
        as_of_date=(row.get("as_of_date") or "")[:10],
    )


def fetch_benchmarks(
    unit_count: int,
    building_type: Optional[str] = None,
    market: Optional[str] = None,
) -> dict[str, CategoryBenchmark]:
    """
    Load comparables for a subject building, keyed by taxonomy category.

    Filtering is progressive: same building type and market first, then market
    dropped, then type dropped, keeping whichever pass returns comps. Unit-count
    banding (±`unit_count_comp_band_pct`) is always applied — a 200-unit tower is
    not a comparable for an 8-unit walk-up at any level of specificity.

    Returns an empty dict when `portfolio_benchmarks` has no rows in band, which
    is the expected state until ops seeds the table.
    """
    cfg = load_config()
    table = cfg["supabase"]["benchmarks_table"]
    band = float(cfg["analysis"]["unit_count_comp_band_pct"])

    low = max(1, int(round(unit_count * (1 - band))))
    high = max(low, int(round(unit_count * (1 + band))))

    client = SupabaseREST()
    base_params = {
        "select": (
            "building_name,address,unit_count,building_type,market,category,"
            "annual_cost,monthly_cost,source,as_of_date"
        ),
        "and": f"(unit_count.gte.{low},unit_count.lte.{high})",
        "limit": "1000",
    }

    filter_passes: list[dict[str, str]] = []
    if building_type and market:
        filter_passes.append({"building_type": f"eq.{building_type}", "market": f"eq.{market}"})
    if market:
        filter_passes.append({"market": f"eq.{market}"})
    if building_type:
        filter_passes.append({"building_type": f"eq.{building_type}"})
    filter_passes.append({})

    rows: list[dict] = []
    for extra in filter_passes:
        rows = client.select(table, {**base_params, **extra})
        if rows:
            logger.info(
                "Found %d comparable rows (units %d–%d, filters=%s)",
                len(rows), low, high, extra or "unit band only",
            )
            break

    if not rows:
        logger.warning(
            "No rows in '%s' for units %d–%d. Seed the table from the MDS monthly "
            "reports before relying on this analysis.", table, low, high,
        )
        return {}

    benchmarks: dict[str, CategoryBenchmark] = {}
    for row in rows:
        comp = _row_to_comparable(row)
        if comp is None:
            continue
        benchmarks.setdefault(comp.category, CategoryBenchmark(category=comp.category)).comps.append(comp)

    return benchmarks
