"""
benchmarks.py — Perseus portfolio comparables
=============================================
Reads `portfolio_benchmarks` — Camelot's own managed buildings' actual costs,
seeded by ops from the MDS monthly reports — and turns it into a per-category
portfolio *average* for a subject building.

Perseus differs from CostBeat here on purpose. CostBeat prices an annual budget
against the comp *range*, sitting near its expensive end. Perseus answers the
question the client actually asked of the quarterly cycle: what does Camelot pay
for this service, on average, across everything it manages? So the yardstick is
the mean per-unit cost of the comps, not a percentile of their spread.

Everything is normalised per unit before comparison, because a 34-unit comp's
absolute water bill says nothing about an 8-unit building.

CONSOLIDATION NOTE
------------------
`Comparable` and `CategoryBenchmark` carry the same field names and the same
`fetch_benchmarks(unit_count, building_type, market)` signature as CostBeat Bot's
`costbeat_bot/benchmarks.py`, so the comp-loading half of this module merges into
a shared `utils/` module once both bots land. The averaging/target methods are
Perseus-specific and stay here.

Author: Camelot OS
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from statistics import mean, median
from typing import Optional

from perseus_bot.config_loader import load_config
from perseus_bot.expense_taxonomy import UNMAPPED
from perseus_bot.storage import SupabaseREST

logger = logging.getLogger("perseus_bot.benchmarks")


@dataclass
class Comparable:
    """One Camelot-managed building's actual annual cost for one category."""

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

    @property
    def average_per_unit(self) -> float:
        """The portfolio average — Perseus's yardstick."""
        values = self.per_unit_values
        return mean(values) if values else 0.0

    def portfolio_average_annual(self, subject_units: int) -> float:
        """What a building this size pays for this category on portfolio average."""
        return self.average_per_unit * subject_units

    def evidence_text(self, max_comps: int = 3) -> str:
        """Named comparables with their actual dollars, highest cost first."""
        if not self.comps:
            return "No comparable building on file for this category."
        ranked = sorted(self.comps, key=lambda c: c.per_unit, reverse=True)[:max_comps]
        text = "; ".join(c.evidence() for c in ranked)
        if self.comp_count > max_comps:
            text += f"; +{self.comp_count - max_comps} more Camelot comparable(s)"
        return text

    def portfolio_target_annual(self, subject_annualized: float, subject_units: int) -> float:
        """
        Conservative annual target for the subject building.

        The target is the portfolio average for a building of this unit count.
        With fewer than `min_comps_for_confident_target` comps it is averaged
        with the building's own annualized spend, so thin evidence cannot drive a
        large claim. The target is never allowed above what the building already
        spends: a building at or below the portfolio average has no savings to
        show, and Perseus reports that rather than manufacturing a gap.
        """
        if not self.comps or not subject_units:
            return subject_annualized

        portfolio = load_config()["portfolio"]
        min_comps = int(portfolio["min_comps_for_confident_target"])

        target = self.portfolio_average_annual(subject_units)

        if self.comp_count < min_comps:
            target = (target + subject_annualized) / 2

        return min(target, subject_annualized)

    def is_at_portfolio_average(self, subject_annualized: float, subject_units: int) -> bool:
        """
        True when the building's run-rate sits within the configured band of the
        portfolio average. Inside that band the difference is noise, not a
        finding, and no savings are claimed.
        """
        if not self.comps or not subject_units:
            return True
        average = self.portfolio_average_annual(subject_units)
        if average <= 0:
            return True
        threshold = float(load_config()["portfolio"]["at_portfolio_average_threshold_pct"])
        return (subject_annualized - average) / average <= threshold


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
    is the expected state until ops seeds the table. Perseus still produces its
    budget-variance half of the report in that case.
    """
    cfg = load_config()
    table = cfg["supabase"]["benchmarks_table"]
    band = float(cfg["portfolio"]["unit_count_comp_band_pct"])

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
            "reports before relying on the portfolio half of this report.",
            table, low, high,
        )
        return {}

    benchmarks: dict[str, CategoryBenchmark] = {}
    for row in rows:
        comp = _row_to_comparable(row)
        if comp is None:
            continue
        benchmarks.setdefault(
            comp.category, CategoryBenchmark(category=comp.category)
        ).comps.append(comp)

    return benchmarks
