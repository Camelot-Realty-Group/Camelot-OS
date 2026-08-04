"""Tests for costbeat_bot/fee_engine.py — the math on the two capture options."""
import pytest

import fee_engine
from analyzer import CostBeatAnalysis, CostBeatLine
from config_loader import load_config

FEES = load_config()["fees"]
ONE_TIME_PCT = float(FEES["one_time_fee_pct_of_year1_savings"])
UPLIFT_PCT = float(FEES["mgmt_fee_uplift_pct_of_annual_savings"])


def _line(category, savings, *, current=100_000.0, addressed=True, at_market=False):
    return CostBeatLine(
        category=category,
        label=category,
        current_budget=current,
        target=current - savings,
        savings=savings,
        savings_pct=savings / current if current else 0.0,
        evidence="comp evidence",
        recommendation="do the thing",
        comp_count=3,
        at_market=at_market,
        addressed=addressed,
    )


def _analysis(*lines):
    return CostBeatAnalysis(
        property_name="Test Building",
        address="1 Test Street",
        unit_count=20,
        building_type="condo",
        market="Manhattan",
        lines=list(lines),
    )


def test_one_time_fee_math():
    analysis = _analysis(_line("compactor_waste", 14_000.0))
    proposal = fee_engine.build_proposal(analysis)

    option = proposal.one_time
    assert option.camelot_year1 == pytest.approx(14_000 * ONE_TIME_PCT)
    assert option.camelot_annual_ongoing == 0.0
    assert option.client_year1 == pytest.approx(14_000 * (1 - ONE_TIME_PCT))
    # The client keeps the whole saving from Year 2 onward.
    assert option.client_annual_ongoing == pytest.approx(14_000)
    assert option.client_value_over(5) == pytest.approx(
        14_000 * (1 - ONE_TIME_PCT) + 14_000 * 4
    )


def test_uplift_fee_math():
    analysis = _analysis(_line("insurance", 14_000.0))
    proposal = fee_engine.build_proposal(analysis)

    option = proposal.uplift
    annual = 14_000 * UPLIFT_PCT
    assert option.camelot_year1 == pytest.approx(annual)
    assert option.camelot_annual_ongoing == pytest.approx(annual)
    assert option.monthly_amount == pytest.approx(annual / 12)
    assert option.client_annual_ongoing == pytest.approx(14_000 - annual)
    assert option.client_value_over(5) == pytest.approx((14_000 - annual) * 5)


def test_structural_savings_recommend_the_uplift():
    """Savings that need standing oversight to hold argue for the uplift."""
    analysis = _analysis(
        _line("insurance", 10_000.0),
        _line("compactor_waste", 4_000.0),
    )
    proposal = fee_engine.build_proposal(analysis)

    assert proposal.structural_savings == pytest.approx(10_000)
    assert proposal.one_off_savings == pytest.approx(4_000)
    assert proposal.recommended_model == fee_engine.MGMT_UPLIFT
    assert proposal.recommended is proposal.uplift
    assert proposal.structural_share == pytest.approx(10_000 / 14_000)
    assert "Insurance" in proposal.rationale


def test_one_off_savings_recommend_the_one_time_fee():
    """Savings from vendor switches run themselves once made."""
    analysis = _analysis(
        _line("compactor_waste", 9_000.0),
        _line("exterminator", 3_000.0),
        _line("insurance", 2_000.0),
    )
    proposal = fee_engine.build_proposal(analysis)

    assert proposal.one_off_savings == pytest.approx(12_000)
    assert proposal.recommended_model == fee_engine.ONE_TIME
    assert proposal.recommended is proposal.one_time


def test_at_market_and_not_addressed_lines_contribute_nothing():
    analysis = _analysis(
        _line("compactor_waste", 5_000.0),
        _line("electricity", 0.0, at_market=True),
        _line("gas", 0.0, addressed=False),
    )
    proposal = fee_engine.build_proposal(analysis)

    assert proposal.annual_savings == pytest.approx(5_000)
    assert proposal.structural_savings == pytest.approx(0)
    assert proposal.one_off_savings == pytest.approx(5_000)


def test_zero_savings_prices_nothing_and_says_so():
    analysis = _analysis(_line("electricity", 0.0, at_market=True))
    proposal = fee_engine.build_proposal(analysis)

    assert proposal.annual_savings == pytest.approx(0)
    assert proposal.one_time.camelot_year1 == pytest.approx(0)
    assert proposal.uplift.monthly_amount == pytest.approx(0)
    assert proposal.structural_share == 0.0
    assert "nothing to price" in proposal.rationale


def test_as_dict_carries_both_options():
    analysis = _analysis(_line("insurance", 8_000.0))
    payload = fee_engine.build_proposal(analysis).as_dict()

    assert set(payload["options"]) == {fee_engine.ONE_TIME, fee_engine.MGMT_UPLIFT}
    assert payload["annual_savings"] == pytest.approx(8_000)
    assert payload["recommended_model"] in payload["options"]
