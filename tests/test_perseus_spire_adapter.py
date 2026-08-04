"""
Tests for perseus_bot/spire_adapter.py.

The rule these tests exist to protect: whatever comes back from Spire must
land in the exact same ParsedReport/ReportLine shape the file parsers already
produce, so roll_up_by_category, variance_engine.analyze, and everything
downstream runs completely unchanged. A Spire-sourced report that produced a
differently-shaped object would silently break the rest of the pipeline.
"""
from perseus_bot import spire_adapter
from perseus_bot.parser import ParsedReport, ReportLine


def test_actuals_from_spire_produces_a_parsed_report_with_actual_amounts():
    line_items = [
        {"account_code": "5100", "label": "Electricity", "amount": 12000.0},
        {"account_code": "5200", "label": "Water & Sewer", "amount": 9000.0},
    ]
    report = spire_adapter.actuals_from_spire(line_items, building_name="Test Tower")

    assert isinstance(report, ParsedReport)
    assert report.source_format == "spire_gl_actuals"
    assert "Test Tower" in report.filename
    assert len(report.lines) == 2
    assert all(isinstance(ln, ReportLine) for ln in report.lines)

    electricity = next(ln for ln in report.lines if ln.account_code == "5100")
    assert electricity.actual == 12000.0
    assert electricity.budget is None
    assert electricity.section == "expense"
    assert electricity.amount == 12000.0  # .amount property reads .actual


def test_actuals_from_spire_skips_zero_and_none_amounts():
    line_items = [
        {"account_code": "5100", "label": "Electricity", "amount": 12000.0},
        {"account_code": "5150", "label": "No activity", "amount": 0.0},
        {"account_code": "5160", "label": "Missing amount", "amount": None},
    ]
    report = spire_adapter.actuals_from_spire(line_items, building_name="Test Tower")
    assert len(report.lines) == 1
    assert report.lines[0].account_code == "5100"


def test_actuals_from_spire_warns_when_nothing_came_back():
    report = spire_adapter.actuals_from_spire([], building_name="Empty Building")
    assert report.lines == []
    assert report.warnings, "an empty Spire pull must be flagged, not silently reported as $0"


def test_actuals_from_spire_takes_absolute_value_of_negative_net_change():
    # Some GL postings roll up with a negative sign depending on account type;
    # Perseus's downstream pipeline expects a positive magnitude of spend.
    line_items = [{"account_code": "5100", "label": "Electricity", "amount": -12000.0}]
    report = spire_adapter.actuals_from_spire(line_items, building_name="Test Tower")
    assert report.lines[0].actual == 12000.0


def test_budget_from_spire_produces_a_parsed_report_with_budget_amounts():
    line_items = [
        {"account_code": "5100", "label": "Electricity", "amount": 12000.0},
    ]
    report = spire_adapter.budget_from_spire(line_items, building_name="Test Tower", year=2026)

    assert report.source_format == "spire_budget"
    assert "2026" in report.filename
    assert "Test Tower" in report.filename
    line = report.lines[0]
    assert line.budget == 12000.0
    assert line.actual is None
    # .amount reads .actual, which is None → 0.0 for a budget-only line, since
    # budget lines are meant for baseline building, not the actual/variance path.
    assert line.amount == 0.0


def test_budget_from_spire_warns_when_nothing_came_back():
    report = spire_adapter.budget_from_spire([], building_name="Empty Building", year=2026)
    assert report.lines == []
    assert report.warnings


def test_actuals_and_budget_reports_expose_expense_lines_property():
    """
    variance_engine.roll_up_by_category and the baseline builders read
    `.expense_lines`, not `.lines` directly — every Spire-sourced line must
    be tagged section='expense' so it shows up there.
    """
    line_items = [{"account_code": "5100", "label": "Electricity", "amount": 12000.0}]
    actuals = spire_adapter.actuals_from_spire(line_items, building_name="X")
    budget = spire_adapter.budget_from_spire(line_items, building_name="X", year=2026)
    assert len(actuals.expense_lines) == 1
    assert len(budget.expense_lines) == 1
