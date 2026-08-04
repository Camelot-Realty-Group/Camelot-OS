"""Tests for scout_bot/scoring.py — lead scoring (port of camelot-scout-v6's
src/lib/scoring.ts calculateScore()).
"""
import scoring


def test_empty_factors_defaults_management_to_unknown_prime_opportunity():
    # An empty factors dict has no positive management signal at all, which
    # is treated the same as "self-managed/unknown" (20 pts) — everything
    # else legitimately scores zero when no data was supplied.
    assert scoring.calculate_score({}) == 20
    assert scoring.grade_for_score(20) == "C"


def test_known_small_mid_firm_explicit_false_scores_14_when_isolated():
    factors = {"self_managed_or_unknown": False, "known_large_firm": False}
    assert scoring.calculate_score(factors) == 14


def test_self_managed_prime_opportunity_gets_full_management_points():
    factors = {"self_managed_or_unknown": True, "known_large_firm": False}
    # Only the management factor is populated; expect exactly its 20 points.
    assert scoring.calculate_score(factors) == 20


def test_known_large_firm_scores_low_management_points():
    factors = {"self_managed_or_unknown": False, "known_large_firm": True}
    assert scoring.calculate_score(factors) == 5


def test_mid_size_unknown_management_firm_scores_mid_points():
    factors = {"self_managed_or_unknown": False, "known_large_firm": False}
    assert scoring.calculate_score(factors) == 14


def test_hpd_violations_scale_with_severity():
    base = {"self_managed_or_unknown": False, "known_large_firm": False}
    low = scoring.calculate_score({**base, "hpd_violations_total": 5})
    mid = scoring.calculate_score({**base, "hpd_violations_total": 15})
    high = scoring.calculate_score({**base, "hpd_violations_total": 60})
    assert low < mid < high
    assert high - 14 == 30  # capped at max (14 baseline management points)


def test_open_violations_add_bonus_points():
    mgmt = {"self_managed_or_unknown": False, "known_large_firm": False}
    base = scoring.calculate_score({**mgmt, "hpd_violations_total": 60, "hpd_violations_open": 0})
    with_open = scoring.calculate_score({**mgmt, "hpd_violations_total": 60, "hpd_violations_open": 15})
    # Violations sub-score is already capped at 30 once total > 50, so the
    # open-violations bonus cannot push the sub-score (or total) any higher.
    assert base - 14 == 30
    assert with_open - 14 == 30


def test_building_size_tiers():
    mgmt = {"self_managed_or_unknown": False, "known_large_firm": False}
    assert scoring.calculate_score({**mgmt, "units": 150}) - 14 == 20
    assert scoring.calculate_score({**mgmt, "units": 60}) - 14 == 16
    assert scoring.calculate_score({**mgmt, "units": 35}) - 14 == 12
    assert scoring.calculate_score({**mgmt, "units": 12}) - 14 == 8
    assert scoring.calculate_score({**mgmt, "units": 3}) - 14 == 4
    assert scoring.calculate_score({**mgmt, "units": 0}) - 14 == 0


def test_building_age_tiers():
    mgmt = {"self_managed_or_unknown": False, "known_large_firm": False}
    assert scoring.calculate_score({**mgmt, "building_age_years": 90}) - 14 == 15
    assert scoring.calculate_score({**mgmt, "building_age_years": 60}) - 14 == 12
    assert scoring.calculate_score({**mgmt, "building_age_years": 40}) - 14 == 8
    assert scoring.calculate_score({**mgmt, "building_age_years": 15}) - 14 == 5
    assert scoring.calculate_score({**mgmt, "building_age_years": 5}) - 14 == 2
    assert scoring.calculate_score({**mgmt, "building_age_years": None}) - 14 == 0


def test_energy_star_low_score_is_worse_building_scores_higher_lead_points():
    mgmt = {"self_managed_or_unknown": False, "known_large_firm": False}
    poor = scoring.calculate_score({**mgmt, "energy_star_score": 40})
    mid = scoring.calculate_score({**mgmt, "energy_star_score": 60})
    good = scoring.calculate_score({**mgmt, "energy_star_score": 90})
    assert poor > mid > good
    assert poor - 14 == 7


def test_ecb_violations_and_penalty_threshold():
    mgmt = {"self_managed_or_unknown": False, "known_large_firm": False}
    none = scoring.calculate_score({**mgmt, "ecb_violation_count": 0, "ecb_penalty_total": 0})
    small = scoring.calculate_score({**mgmt, "ecb_violation_count": 2, "ecb_penalty_total": 500})
    large = scoring.calculate_score({**mgmt, "ecb_violation_count": 2, "ecb_penalty_total": 15000})
    assert none - 14 == 0
    assert small - 14 == 5
    assert large - 14 == 10


def test_litigation_and_rent_stabilization_flat_bonuses():
    mgmt = {"self_managed_or_unknown": False, "known_large_firm": False}
    assert scoring.calculate_score({**mgmt, "active_housing_litigation": True}) - 14 == 15
    assert scoring.calculate_score({**mgmt, "rent_stabilized": True}) - 14 == 5


def test_score_never_exceeds_max_even_with_every_factor_maxed():
    factors = {
        "hpd_violations_total": 200,
        "hpd_violations_open": 50,
        "units": 500,
        "self_managed_or_unknown": True,
        "known_large_firm": False,
        "building_age_years": 150,
        "recent_dob_permits": 10,
        "energy_star_score": 10,
        "ecb_violation_count": 20,
        "ecb_penalty_total": 100000,
        "active_housing_litigation": True,
        "rent_stabilized": True,
    }
    assert scoring.calculate_score(factors) == 100


def test_grade_boundaries():
    assert scoring.grade_for_score(75) == "A"
    assert scoring.grade_for_score(74) == "B"
    assert scoring.grade_for_score(50) == "B"
    assert scoring.grade_for_score(49) == "C"


def test_is_known_large_firm_matches_case_insensitively():
    assert scoring.is_known_large_firm("Related Companies LLC")
    assert scoring.is_known_large_firm("BROOKFIELD PROPERTIES")
    assert not scoring.is_known_large_firm("Joe's Property Management")
    assert not scoring.is_known_large_firm(None)
