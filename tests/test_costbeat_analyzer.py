"""
Tests for costbeat_bot/analyzer.py and benchmarks.py.

These cover the guardrails the client-facing report depends on: savings are
never manufactured, a category with no comparable claims nothing, and protected
safety scope is never reduced.
"""
import pytest

import analyzer
from benchmarks import CategoryBenchmark, Comparable, normalize_category
from config_loader import load_config
from parser import BudgetLine, ParsedBudget

UNITS = 8


def _comp(name, per_unit, units=UNITS):
    return Comparable(
        building_name=name,
        category="x",
        annual_cost=per_unit * units,
        unit_count=units,
        as_of_date="2026-06-30",
    )


def _bench(category, *per_unit_values):
    return CategoryBenchmark(
        category=category,
        comps=[_comp(f"Comp {i + 1}", v) for i, v in enumerate(per_unit_values)],
    )


@pytest.fixture
def budget():
    return ParsedBudget(
        filename="subject.csv",
        source_format="flat",
        lines=[
            BudgetLine(raw_label="Insurance", amount=40_000.0),
            BudgetLine(raw_label="Electricity", amount=16_000.0),
            BudgetLine(raw_label="Sprinkler & Fire Alarm", amount=8_000.0),
            BudgetLine(raw_label="Carting & Refuse Removal", amount=6_000.0),
        ],
    )


@pytest.fixture
def benchmarks():
    return {
        "insurance": _bench("insurance", 2_000, 2_500, 3_000),
        "electricity": _bench("electricity", 1_900, 1_950, 2_000),
        "sprinkler_fire_alarm": _bench("sprinkler_fire_alarm", 500, 600, 700),
        # compactor_waste deliberately absent — nothing may be claimed on it.
    }


@pytest.fixture
def analysis(budget, benchmarks):
    return analyzer.analyze(
        budget, benchmarks,
        property_name="The Story House",
        address="36 East 22nd Street",
        unit_count=UNITS,
        building_type="condo",
        market="Manhattan",
        use_llm=False,
    )


def _line(analysis, category):
    return next(ln for ln in analysis.lines if ln.category == category)


def test_gl_labels_map_onto_the_taxonomy():
    assert normalize_category("Superintendent Wages") == "payroll_and_cleaning"
    assert normalize_category("Carting & Refuse Removal") == "compactor_waste"
    # The account label wins over its GL parent group.
    assert normalize_category("Elevator Service Contract", "Repairs") == "elevator_maintenance"
    assert normalize_category("Reserve Contribution") == "unmapped"


def test_target_sits_at_the_configured_percentile_of_the_comp_range(analysis):
    cfg = load_config()["analysis"]
    percentile = float(cfg["target_percentile_of_comp_range"])
    expected_per_unit = 2_000 + (3_000 - 2_000) * percentile

    insurance = _line(analysis, "insurance")
    assert insurance.target == pytest.approx(expected_per_unit * UNITS)
    assert insurance.savings == pytest.approx(40_000 - expected_per_unit * UNITS)
    assert insurance.addressed is True
    assert insurance.at_market is False


def test_thin_comp_coverage_pulls_the_target_back_toward_current_spend():
    """Fewer than min_comps must not drive a large claim."""
    cfg = load_config()["analysis"]
    percentile = float(cfg["target_percentile_of_comp_range"])

    thin = _bench("insurance", 2_000, 3_000)
    confident = _bench("insurance", 2_000, 3_000, 3_000)
    unblended = (2_000 + 1_000 * percentile) * UNITS

    assert thin.comp_count < int(cfg["min_comps_for_confident_target"])
    assert thin.target_annual(40_000, UNITS) == pytest.approx((unblended + 40_000) / 2)
    assert confident.target_annual(40_000, UNITS) == pytest.approx(unblended)


def test_target_never_exceeds_current_spend():
    expensive_comps = _bench("insurance", 9_000, 10_000)
    assert expensive_comps.target_annual(40_000, UNITS) == pytest.approx(40_000)


def test_line_within_threshold_reports_at_market_with_no_savings(analysis):
    electricity = _line(analysis, "electricity")
    assert electricity.at_market is True
    assert electricity.savings == 0.0
    assert electricity.target == electricity.current_budget
    assert electricity.recommendation == analyzer.AT_MARKET_TEXT


def test_category_with_no_comparable_claims_nothing(analysis):
    waste = _line(analysis, "compactor_waste")
    assert waste.addressed is False
    assert waste.comp_count == 0
    assert waste.savings == 0.0
    assert waste.target == waste.current_budget
    assert waste.recommendation == analyzer.NOT_ADDRESSED_TEXT
    assert analysis.not_addressed_total == pytest.approx(6_000)


def test_savings_total_excludes_unaddressed_lines(analysis):
    assert analysis.total_budget == pytest.approx(70_000)
    assert analysis.total_savings == pytest.approx(
        sum(ln.savings for ln in analysis.addressed_lines)
    )
    assert analysis.total_savings < analysis.total_budget
    assert analysis.savings_pct == pytest.approx(analysis.total_savings / 70_000)


def test_protected_scope_is_flagged_and_never_scope_reduced(analysis):
    sprinkler = _line(analysis, "sprinkler_fire_alarm")
    assert sprinkler.scope_protected is True
    # The only moves offered are a like-for-like rebid and a billing audit.
    assert "identical" in sprinkler.recommendation.lower()
    assert "no reduction in testing" in sprinkler.recommendation.lower()


def test_evidence_cites_the_comparable_by_name_and_number(analysis):
    insurance = _line(analysis, "insurance")
    assert "Comp" in insurance.evidence
    assert "/unit" in insurance.evidence
    assert "as of 2026-06-30" in insurance.evidence


def test_empty_benchmarks_addresses_nothing(budget):
    analysis = analyzer.analyze(
        budget, {},
        property_name="The Story House",
        address="",
        unit_count=UNITS,
        use_llm=False,
    )
    assert analysis.total_savings == 0.0
    assert analysis.addressed_lines == []
    assert len(analysis.not_addressed_lines) == 4
