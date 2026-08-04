"""
Tests for perseus_bot/variance_engine.py.

The rule the whole bot rests on: budget variance and the portfolio gap are two
separate findings, and only the second one is ever billed for. A building
beating its own budget has not saved anybody money — a budget is a plan
somebody wrote, and beating a plan is not the same as paying a market price.
These tests exist to keep those two numbers from leaking into each other.
"""
import pytest

from perseus_bot import variance_engine as ve
from perseus_bot.benchmarks import CategoryBenchmark, Comparable
from perseus_bot.parser import ParsedReport, ReportLine


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _report(pairs, **kwargs) -> ParsedReport:
    """A parsed report from (label, period_actual) pairs."""
    return ParsedReport(
        filename=kwargs.pop("filename", "q2.csv"),
        source_format=kwargs.pop("source_format", "columnar"),
        lines=[ReportLine(raw_label=label, actual=amount) for label, amount in pairs],
        **kwargs,
    )


def _baseline(annual_by_category, cadence="quarterly") -> ve.BudgetBaseline:
    return ve.BudgetBaseline(
        source=ve.BUDGET_SOURCE_UPLOADED,
        cadence=cadence,
        annual_by_category=dict(annual_by_category),
        origin_label="budget.xlsx",
    )


def _bench(per_unit_values, subject_units=20) -> CategoryBenchmark:
    """A benchmark built from explicit per-unit costs."""
    comps = [
        Comparable(
            building_name=f"Comp {i + 1}",
            category="electricity",
            annual_cost=value * subject_units,
            unit_count=subject_units,
            address=f"{100 + i} Test St",
            building_type="rental",
            market="Manhattan",
            source="portfolio_benchmarks",
            as_of_date="2026-01-01",
        )
        for i, value in enumerate(per_unit_values)
    ]
    return CategoryBenchmark(category="electricity", comps=comps)


def _analyze(report, baseline, benchmarks, **kwargs):
    params = dict(
        property_name="552 West 150th Street",
        address="552 W 150th St, New York, NY",
        unit_count=20,
        quarter="Q2",
        year=2026,
        cadence="quarterly",
        use_llm=False,
    )
    params.update(kwargs)
    return ve.analyze(report, baseline, benchmarks, **params)


# ---------------------------------------------------------------------------
# Proration and annualizing
# ---------------------------------------------------------------------------

def test_quarterly_budget_share_is_one_quarter_of_the_annual_figure():
    baseline = _baseline({"electricity": 40_000})
    assert baseline.periods == 4
    assert baseline.period_share("electricity") == pytest.approx(10_000)


def test_monthly_cadence_prorates_by_twelve():
    baseline = _baseline({"electricity": 48_000}, cadence="monthly")
    assert baseline.periods == 12
    assert baseline.period_share("electricity") == pytest.approx(4_000)


def test_annual_cadence_does_not_prorate():
    baseline = _baseline({"electricity": 40_000}, cadence="annual")
    assert baseline.period_share("electricity") == pytest.approx(40_000)


def test_period_share_is_none_for_a_category_the_budget_does_not_mention():
    assert _baseline({"electricity": 40_000}).period_share("insurance") is None


def test_a_quarter_is_annualized_by_multiplying_by_four():
    report = _report([("Electricity", 12_000)])
    analysis = _analyze(report, _baseline({"electricity": 40_000}), {})
    line = analysis.lines[0]
    assert line.actual_period == pytest.approx(12_000)
    assert line.annualized_actual == pytest.approx(48_000)


# ---------------------------------------------------------------------------
# Budget variance
# ---------------------------------------------------------------------------

def test_budget_variance_pct_is_measured_against_the_prorated_share():
    # $40,000/yr → $10,000/quarter. Spending $12,000 is 20% over.
    report = _report([("Electricity", 12_000)])
    analysis = _analyze(report, _baseline({"electricity": 40_000}), {})
    line = analysis.lines[0]
    assert line.budget_period == pytest.approx(10_000)
    assert line.budget_variance == pytest.approx(2_000)
    assert line.budget_variance_pct == pytest.approx(0.20)


def test_more_than_ten_percent_over_budget_is_flagged_for_investigation():
    report = _report([("Electricity", 12_000)])
    analysis = _analyze(report, _baseline({"electricity": 40_000}), {})
    assert analysis.lines[0].budget_flag == ve.FLAG_INVESTIGATE
    assert analysis.lines[0].flagged


def test_more_than_ten_percent_under_budget_is_flagged_underspent():
    report = _report([("Electricity", 8_000)])
    analysis = _analyze(report, _baseline({"electricity": 40_000}), {})
    line = analysis.lines[0]
    assert line.budget_variance_pct == pytest.approx(-0.20)
    assert line.budget_flag == ve.FLAG_UNDERSPENT


def test_within_ten_percent_either_way_is_on_track():
    for actual in (9_500, 10_000, 10_500):
        analysis = _analyze(
            _report([("Electricity", actual)]), _baseline({"electricity": 40_000}), {}
        )
        assert analysis.lines[0].budget_flag == ve.FLAG_ON_TRACK, actual


def test_a_category_absent_from_the_budget_reports_no_baseline_not_zero():
    """Comparing an actual against a budget of zero would read as infinitely over."""
    report = _report([("Insurance", 5_000)])
    analysis = _analyze(report, _baseline({"electricity": 40_000}), {})
    line = analysis.lines[0]
    assert line.budget_flag == ve.FLAG_NO_BASELINE
    assert line.budget_period is None
    assert line.budget_variance == pytest.approx(0.0)


def test_the_budget_comparison_excludes_lines_that_have_no_budget():
    """
    Insurance has no baseline, so it must not drag the budget comparison. The two
    actuals are deliberately different numbers: total spend is everything, but
    anything printed beside the budget covers only the comparable lines.
    """
    report = _report([("Electricity", 12_000), ("Insurance", 5_000)])
    analysis = _analyze(report, _baseline({"electricity": 40_000}), {})
    assert analysis.total_budget_period == pytest.approx(10_000)
    assert analysis.total_actual_period == pytest.approx(17_000)
    assert analysis.total_actual_budgeted_period == pytest.approx(12_000)
    assert analysis.budget_variance == pytest.approx(2_000)
    assert analysis.budget_variance_pct == pytest.approx(0.20)


def test_the_totals_row_of_the_variance_table_adds_up():
    """
    Budget, actual and variance sit in one row of an owner-facing table. If the
    actual cell counted unbudgeted lines the row would read 10,000 → 17,000 →
    +2,000 and the owner would rightly distrust the whole page.
    """
    report = _report([("Electricity", 12_000), ("Insurance", 5_000)])
    analysis = _analyze(report, _baseline({"electricity": 40_000}), {})
    assert (
        analysis.total_actual_budgeted_period - analysis.total_budget_period
        == pytest.approx(analysis.budget_variance)
    )


# ---------------------------------------------------------------------------
# Portfolio average — where savings actually come from
# ---------------------------------------------------------------------------

def test_savings_are_the_gap_to_the_portfolio_average_per_unit():
    # Four comps at $400-$600/unit average $500. 20 units → $10,000 target.
    bench = _bench([400.0, 500.0, 500.0, 600.0])
    # $4,000 a quarter → $16,000 a year against a $10,000 target.
    report = _report([("Electricity", 4_000)])
    analysis = _analyze(report, _baseline({"electricity": 16_000}), {"electricity": bench})
    line = analysis.lines[0]
    assert line.portfolio_average_annual == pytest.approx(10_000)
    assert line.portfolio_target_annual == pytest.approx(10_000)
    assert line.portfolio_savings_annual == pytest.approx(6_000)
    assert line.portfolio_savings_period == pytest.approx(1_500)


def test_beating_your_own_budget_is_not_a_saving():
    """
    The building is 20% under its own budget and still 60% above the portfolio
    average. The saving must come from the portfolio gap alone.
    """
    bench = _bench([400.0, 500.0, 500.0, 600.0])   # $10,000 target at 20 units
    report = _report([("Electricity", 4_000)])      # $16,000 annualized
    analysis = _analyze(report, _baseline({"electricity": 20_000}), {"electricity": bench})
    line = analysis.lines[0]
    assert line.budget_flag == ve.FLAG_UNDERSPENT
    assert line.portfolio_savings_annual == pytest.approx(6_000)


def test_being_over_your_own_budget_alone_produces_no_saving():
    """Over budget, but already cheaper than every comparable. Nothing to bill."""
    bench = _bench([500.0, 600.0, 700.0, 800.0])   # average $650/unit → $13,000
    report = _report([("Electricity", 2_500)])      # $10,000 annualized
    analysis = _analyze(report, _baseline({"electricity": 8_000}), {"electricity": bench})
    line = analysis.lines[0]
    assert line.budget_flag == ve.FLAG_INVESTIGATE
    assert line.portfolio_savings_annual == pytest.approx(0.0)
    assert analysis.portfolio_savings_annual == pytest.approx(0.0)


def test_a_building_below_the_average_shows_no_saving_not_a_negative_one():
    bench = _bench([500.0, 600.0, 700.0, 800.0])   # $13,000 target
    report = _report([("Electricity", 1_000)])      # $4,000 annualized
    analysis = _analyze(report, _baseline({"electricity": 4_000}), {"electricity": bench})
    line = analysis.lines[0]
    assert line.portfolio_target_annual == pytest.approx(4_000)
    assert line.portfolio_savings_annual == pytest.approx(0.0)


def test_a_line_within_the_threshold_of_the_average_is_reported_as_at_average():
    bench = _bench([500.0, 500.0, 500.0, 500.0])   # $10,000 target
    report = _report([("Electricity", 2_600)])      # $10,400 — 4% over
    analysis = _analyze(report, _baseline({"electricity": 10_400}), {"electricity": bench})
    line = analysis.lines[0]
    assert line.at_portfolio_average
    assert line.portfolio_savings_annual == pytest.approx(0.0)


def test_a_thin_comp_set_blends_the_average_toward_the_subject():
    """
    Two comps is not enough to claim a market price outright, so the target is
    pulled halfway toward what the building already spends and the claimed
    saving shrinks accordingly.
    """
    thin = _bench([400.0, 600.0])                   # average $500/unit → $10,000
    assert thin.comp_count == 2
    report = _report([("Electricity", 5_000)])      # $20,000 annualized
    analysis = _analyze(report, _baseline({"electricity": 20_000}), {"electricity": thin})
    line = analysis.lines[0]
    # Blended: (10,000 + 20,000) / 2 = 15,000 → a $5,000 saving, not $10,000.
    assert line.portfolio_target_annual == pytest.approx(15_000)
    assert line.portfolio_savings_annual == pytest.approx(5_000)
    assert line.comp_count == 2


def test_a_confident_comp_set_uses_the_average_directly():
    fat = _bench([400.0, 500.0, 600.0])             # three comps, average $500
    report = _report([("Electricity", 5_000)])       # $20,000 annualized
    analysis = _analyze(report, _baseline({"electricity": 20_000}), {"electricity": fat})
    assert analysis.lines[0].portfolio_target_annual == pytest.approx(10_000)


def test_a_category_with_no_comparables_is_marked_not_addressed():
    report = _report([("Electricity", 12_000)])
    analysis = _analyze(report, _baseline({"electricity": 40_000}), {})
    line = analysis.lines[0]
    assert not line.addressed
    assert line.portfolio_savings_annual == pytest.approx(0.0)
    assert line.portfolio_target_annual == pytest.approx(line.annualized_actual)
    assert line in analysis.not_addressed_lines


def test_portfolio_gap_pct_is_relative_to_the_average():
    bench = _bench([500.0, 500.0, 500.0, 500.0])   # $10,000
    report = _report([("Electricity", 3_750)])      # $15,000 annualized → +50%
    analysis = _analyze(report, _baseline({"electricity": 15_000}), {"electricity": bench})
    assert analysis.lines[0].portfolio_gap_pct == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# Roll-up and aggregates
# ---------------------------------------------------------------------------

def test_multiple_gl_lines_roll_into_one_category():
    report = _report([
        ("Electricity - Common Area", 3_000),
        ("Electric Utility", 1_000),
    ])
    rollup = ve.roll_up_by_category(report)
    assert rollup["electricity"].amount == pytest.approx(4_000)
    assert len(rollup["electricity"].labels) == 2


def test_period_savings_are_the_annual_savings_divided_by_the_periods():
    bench = _bench([400.0, 500.0, 500.0, 600.0])   # $10,000 target
    report = _report([("Electricity", 4_000)])      # $16,000 annualized
    analysis = _analyze(report, _baseline({"electricity": 16_000}), {"electricity": bench})
    assert analysis.portfolio_savings_annual == pytest.approx(6_000)
    assert analysis.portfolio_savings_period == pytest.approx(1_500)


def test_savings_pct_is_measured_against_the_annualized_run_rate():
    bench = _bench([400.0, 500.0, 500.0, 600.0])
    report = _report([("Electricity", 4_000)])
    analysis = _analyze(report, _baseline({"electricity": 16_000}), {"electricity": bench})
    assert analysis.annualized_actual == pytest.approx(16_000)
    assert analysis.savings_pct_of_annualized == pytest.approx(6_000 / 16_000)


def test_seasonal_categories_are_named_so_the_caveat_can_be_printed():
    """The caveat sentence prints these, so they are display labels, not keys."""
    report = _report([("Gas / Heating Fuel", 8_000), ("Insurance", 5_000)])
    analysis = _analyze(report, _baseline({"gas": 32_000, "insurance": 20_000}), {})
    assert analysis.seasonal_categories == ["Gas / Fuel"]


def test_a_heating_fuel_line_is_gas_not_hvac_equipment():
    """
    'Gas / Heating Fuel' is a commodity bill; HVAC / Mechanical is the contract to
    service the plant. Confusing them benchmarks a building's fuel spend against
    equipment contracts, and drops gas out of the seasonality caveat.
    """
    report = _report([("Gas / Heating Fuel", 8_000)])
    analysis = _analyze(report, _baseline({"gas": 32_000}), {})
    line = analysis.lines[0]
    assert line.category == "gas"
    assert line.seasonal
    # It found its baseline, which a line filed under hvac_mechanical would not.
    assert line.budget_period == pytest.approx(8_000)
    assert line.budget_flag == ve.FLAG_ON_TRACK


def test_servicing_the_heating_plant_is_still_hvac():
    report = _report([("Heating System Repair", 3_000)])
    analysis = _analyze(report, _baseline({"hvac_mechanical": 12_000}), {})
    assert analysis.lines[0].category == "hvac_mechanical"


def test_scope_protected_categories_are_marked():
    report = _report([("Elevator Maintenance Contract", 6_000)])
    analysis = _analyze(report, _baseline({"elevator_maintenance": 24_000}), {})
    assert analysis.lines[0].scope_protected


def test_period_label_formats_the_quarter_and_falls_back_for_annual():
    assert ve.period_label("Q3", 2026, "quarterly") == "Q3 2026"
    assert ve.period_label("", 2026, "annual") == "2026"
    assert ve.period_label("", 2026, "quarterly") == "2026"


def test_parse_warnings_and_review_flag_carry_through_to_the_analysis():
    report = _report([("Electricity", 12_000)])
    report.warnings.append("Skipped one row with an unreadable figure.")
    report.needs_review = True
    analysis = _analyze(report, _baseline({"electricity": 40_000}), {})
    assert analysis.needs_review
    assert any("unreadable" in w for w in analysis.parse_warnings)


# ---------------------------------------------------------------------------
# Baseline construction
# ---------------------------------------------------------------------------

def test_costbeat_baseline_uses_the_clients_budget_not_costbeats_target():
    row = {
        "id": "abc-123",
        "created_at": "2026-01-15T00:00:00Z",
        "line_items": [
            {"category": "electricity", "current_budget": 40_000, "target_cost": 25_000},
            {"category": "insurance", "current_budget": 20_000, "target_cost": 14_000},
        ],
    }
    baseline = ve.baseline_from_costbeat(row, "quarterly")
    assert baseline.source == ve.BUDGET_SOURCE_COSTBEAT
    assert baseline.costbeat_analysis_id == "abc-123"
    assert baseline.annual_by_category["electricity"] == pytest.approx(40_000)
    assert baseline.total_annual == pytest.approx(60_000)


def test_costbeat_baseline_without_line_items_raises_rather_than_guessing():
    with pytest.raises(ve.MissingBaselineError):
        ve.baseline_from_costbeat({"id": "x", "line_items": []}, "quarterly")


def test_report_column_baseline_scales_the_period_budget_up_to_a_year():
    report = ParsedReport(
        filename="q2.csv",
        source_format="columnar",
        lines=[ReportLine(raw_label="Electricity", actual=12_000, budget=10_000)],
    )
    baseline = ve.baseline_from_report_columns(report, "quarterly")
    assert baseline.source == ve.BUDGET_SOURCE_REPORT_COLUMN
    assert baseline.annual_by_category["electricity"] == pytest.approx(40_000)
    # Round-trips back to the period figure the file stated.
    assert baseline.period_share("electricity") == pytest.approx(10_000)


def test_report_column_baseline_raises_when_there_is_no_budget_column():
    report = _report([("Electricity", 12_000)])
    with pytest.raises(ve.MissingBaselineError):
        ve.baseline_from_report_columns(report, "quarterly")


def test_uploaded_annual_budget_baseline_rolls_up_by_category():
    budget = ParsedReport(
        filename="budget.xlsx",
        source_format="flat",
        lines=[
            ReportLine(raw_label="Electricity", budget=40_000),
            ReportLine(raw_label="Electric - House Meter", budget=8_000),
        ],
    )
    baseline = ve.baseline_from_annual_budget(budget, "quarterly")
    assert baseline.source == ve.BUDGET_SOURCE_UPLOADED
    assert baseline.annual_by_category["electricity"] == pytest.approx(48_000)


def test_baseline_describe_names_its_origin():
    assert "CostBeat" in ve.BudgetBaseline(
        source=ve.BUDGET_SOURCE_COSTBEAT, cadence="quarterly"
    ).describe()
    assert "budget column" in ve.BudgetBaseline(
        source=ve.BUDGET_SOURCE_REPORT_COLUMN, cadence="quarterly"
    ).describe()


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_as_dict_separates_budget_variance_from_portfolio_savings():
    bench = _bench([400.0, 500.0, 500.0, 600.0])
    report = _report([("Electricity", 4_000)])
    analysis = _analyze(report, _baseline({"electricity": 20_000}), {"electricity": bench})
    payload = analysis.as_dict()
    assert payload["total_actual_period"] == pytest.approx(4_000)
    assert payload["portfolio_savings_annual"] == pytest.approx(6_000)
    assert payload["portfolio_savings_period"] == pytest.approx(1_500)
    # Under its own budget, yet still billable against the portfolio.
    assert payload["budget_variance"] == pytest.approx(4_000 - 5_000)
    assert len(payload["line_items"]) == 1
