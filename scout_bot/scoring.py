"""
scoring.py — Lead scoring (Python port of camelot-scout-v6's src/lib/scoring.ts)
==================================================================================
camelot-scout-v6's `calculateScore(factors)` was the intended scoring engine
for the daily lead hunt, but the automation that would have called it
(`supabase/functions/daily-hunt-run/index.ts`) was only ever a stub. This
module ports the same weighting scheme to Python so `lead_hunt.py` can score
NYC Open Data candidates the way the original app was designed to.

Total score: 0-100 across nine factors. Grade: A >= 75, B >= 50, else C.

Differences from the TypeScript original, both due to data actually
available from the NYC Open Data endpoints queried in this pass (HPD
registrations/violations + DOF property only — no DOB permits or LL97
energy queries yet, see lead_hunt.py docstring "caveats"):
  - `recent_dob_permits` / `energy_star_score` / `site_eui` /
    `ecb_violation_count` / `active_housing_litigation` /
    `rent_stabilized` factors default to 0/None/False when not supplied,
    which yields 0 points for those sub-scores rather than raising. This
    keeps the score honest (undercounts rather than guesses) until those
    data sources are wired in — flagged in the PR description as follow-up
    work, not a scoring change.

Author: Camelot OS
"""

from __future__ import annotations

from typing import Any, Optional

MAX_SCORE = 100

KNOWN_LARGE_FIRMS = [
    "firstservice residential", "related companies", "brookfield", "greystar",
    "equity residential", "avalon bay", "avalonbay", "cushman & wakefield",
    "cbre", "jll", "rudin management", "sl green", "vornado", "tishman speyer",
    "silverstein", "extell", "lefrak", "rose associates", "glenwood management",
]


def _score_hpd_violations(total: int, open_count: int) -> int:
    """Max 30 points."""
    if total > 50:
        base = 30
    elif total > 20:
        base = 22
    elif total > 10:
        base = 15
    elif total > 0:
        base = 8
    else:
        base = 0
    if open_count > 10:
        base += 5
    return min(base, 30)


def _score_building_size(units: int) -> int:
    """Max 20 points."""
    if units >= 100:
        return 20
    if units >= 50:
        return 16
    if units >= 30:
        return 12
    if units >= 10:
        return 8
    if units > 0:
        return 4
    return 0


def _score_management(self_managed_or_unknown: bool, known_large_firm: bool) -> int:
    """Max 20 points. Self-managed/unknown = 'prime opportunity' = full points."""
    if self_managed_or_unknown:
        return 20
    if known_large_firm:
        return 5
    return 14


def _score_building_age(age_years: Optional[int]) -> int:
    """Max 15 points."""
    if age_years is None:
        return 0
    if age_years > 80:
        return 15
    if age_years > 50:
        return 12
    if age_years > 30:
        return 8
    if age_years > 10:
        return 5
    return 2


def _score_dob_permits(recent_permits: int) -> int:
    """Max 8 points. Recent permit activity signals capital-improvement need
    or ownership transition — either way, a live conversation opener."""
    if recent_permits >= 5:
        return 8
    if recent_permits >= 2:
        return 5
    if recent_permits >= 1:
        return 2
    return 0


def _score_energy(energy_star_score: Optional[int], site_eui: Optional[float]) -> int:
    """Max 7 points."""
    if energy_star_score is not None:
        if energy_star_score < 50:
            return 7
        if energy_star_score < 75:
            return 4
        return 1
    if site_eui is not None and site_eui > 100:
        return 5
    return 0


def _score_ecb(violation_count: int, penalty_total: float) -> int:
    """Max 10 points."""
    if violation_count <= 0:
        return 0
    if penalty_total > 10_000:
        return 10
    return 5


def _score_litigation(active_housing_litigation: bool) -> int:
    """Max 15 points."""
    return 15 if active_housing_litigation else 0


def _score_rent_stabilization(rent_stabilized: bool) -> int:
    """Max 5 points."""
    return 5 if rent_stabilized else 0


def calculate_score(factors: dict[str, Any]) -> int:
    """Compute the 0-100 lead score from a factors dict. All keys optional;
    missing/None values score 0 for that sub-factor rather than raising.

    Expected keys:
        hpd_violations_total (int), hpd_violations_open (int), units (int),
        self_managed_or_unknown (bool), known_large_firm (bool),
        building_age_years (int|None), recent_dob_permits (int),
        energy_star_score (int|None), site_eui (float|None),
        ecb_violation_count (int), ecb_penalty_total (float),
        active_housing_litigation (bool), rent_stabilized (bool)
    """
    total = 0
    total += _score_hpd_violations(
        int(factors.get("hpd_violations_total") or 0),
        int(factors.get("hpd_violations_open") or 0),
    )
    total += _score_building_size(int(factors.get("units") or 0))
    # A missing signal here means management status was never positively
    # identified during the scan (as opposed to an explicit False, meaning a
    # known small/mid firm was found) — that counts as "unknown", which is
    # scored the same as self-managed (a prime cold-outreach opportunity).
    self_managed_or_unknown = factors.get("self_managed_or_unknown")
    total += _score_management(
        True if self_managed_or_unknown is None else bool(self_managed_or_unknown),
        bool(factors.get("known_large_firm")),
    )
    total += _score_building_age(factors.get("building_age_years"))
    total += _score_dob_permits(int(factors.get("recent_dob_permits") or 0))
    total += _score_energy(factors.get("energy_star_score"), factors.get("site_eui"))
    total += _score_ecb(
        int(factors.get("ecb_violation_count") or 0),
        float(factors.get("ecb_penalty_total") or 0),
    )
    total += _score_litigation(bool(factors.get("active_housing_litigation")))
    total += _score_rent_stabilization(bool(factors.get("rent_stabilized")))
    return min(total, MAX_SCORE)


def grade_for_score(score: int) -> str:
    if score >= 75:
        return "A"
    if score >= 50:
        return "B"
    return "C"


def is_known_large_firm(managing_agent: Optional[str]) -> bool:
    text = (managing_agent or "").lower()
    return any(firm in text for firm in KNOWN_LARGE_FIRMS)
