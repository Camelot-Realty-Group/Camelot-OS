"""
Tests for perseus_bot/report_generator.py.

This is the file that produces the artifact an owner actually reads, so the
things worth pinning here are the ones that make a page untrustworthy: a totals
row whose figures do not add up, and a budget compared against an actual that
covers a different set of lines. A number that is right but presented beside the
wrong companion number reads as an error either way.
"""
import pytest

from perseus_bot import fee_engine, report_generator as rg
from perseus_bot import variance_engine as ve
from perseus_bot.benchmarks import CategoryBenchmark, Comparable
from perseus_bot.parser import ParsedReport, ReportLine

UNITS = 20


def _bench(per_unit_values) -> CategoryBenchmark:
    comps = [
        Comparable(
            building_name=f"Comp {i + 1}",
            category="electricity",
            annual_cost=value * UNITS,
            unit_count=UNITS,
        )
        for i, value in enumerate(per_unit_values)
    ]
    return CategoryBenchmark(category="electricity", comps=comps)


def _mixed_analysis() -> ve.VarianceAnalysis:
    """One line with a budget and comparables, one line with neither."""
    report = ParsedReport(
        filename="q2.csv",
        source_format="columnar",
        lines=[
            ReportLine(raw_label="Electricity", actual=4_000),
            ReportLine(raw_label="Insurance", actual=5_000),
        ],
    )
    baseline = ve.BudgetBaseline(
        source=ve.BUDGET_SOURCE_UPLOADED,
        cadence="quarterly",
        annual_by_category={"electricity": 20_000},
        origin_label="budget.xlsx",
    )
    return ve.analyze(
        report,
        baseline,
        {"electricity": _bench([400.0, 500.0, 500.0, 600.0])},
        property_name="552 West 150th Street",
        address="552 W 150th St, New York, NY",
        unit_count=UNITS,
        quarter="Q2",
        year=2026,
        cadence="quarterly",
        use_llm=False,
    )


def test_the_totals_row_reports_the_actual_on_the_same_basis_as_the_budget():
    """
    Budget, actual, variance and percentage sit in one row. Printing the whole
    report's actual next to a budget covering only some lines gives a row that
    does not subtract — $8,000 budget, $12,000 actual, $1,000 under.
    """
    analysis = _mixed_analysis()
    assert analysis.total_actual_period == pytest.approx(9_000)
    assert analysis.total_actual_budgeted_period == pytest.approx(4_000)
    assert analysis.total_budget_period == pytest.approx(5_000)
    assert analysis.budget_variance == pytest.approx(-1_000)


def test_the_totals_note_names_the_excluded_lines_and_the_full_spend():
    """An owner who totals the column themselves must be able to find the gap."""
    note = rg._totals_basis_note(_mixed_analysis())
    assert "1 line without one is listed" in note
    assert "$9,000" in note


def test_the_totals_note_says_so_when_every_line_is_budgeted():
    analysis = _mixed_analysis()
    analysis.lines = [ln for ln in analysis.lines if ln.budget_period is not None]
    assert rg._totals_basis_note(analysis) == "Totals cover every line on the report."


def test_the_totals_note_counts_a_single_budgeted_line_in_words():
    note = rg._totals_basis_note(_mixed_analysis())
    assert "the one line with a budget share" in note
    assert "the 1 lines" not in note


# ---------------------------------------------------------------------------
# Label copy
# ---------------------------------------------------------------------------

def test_two_labels_join_without_an_oxford_comma():
    """'Electricity, and Gas / Fuel' reads as a typo in an owner-facing sentence."""
    assert rg._join_labels(["Electricity", "Gas / Fuel"]) == "Electricity and Gas / Fuel"


def test_three_or_more_labels_take_the_oxford_comma():
    assert rg._join_labels(["A", "B", "C"]) == "A, B, and C"


def test_one_label_stands_alone():
    assert rg._join_labels(["Electricity"]) == "Electricity"


def test_repeated_labels_are_not_listed_twice():
    assert rg._join_labels(["Electricity", "Electricity"]) == "Electricity"


def test_a_long_list_is_capped_and_counts_the_remainder():
    joined = rg._join_labels(["A", "B", "C", "D", "E", "F"])
    assert joined == "A, B, C, and D, plus 2 more"


def test_no_labels_reads_as_none_rather_than_an_empty_string():
    assert rg._join_labels([]) == "none"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_a_report_renders_to_a_pdf_on_disk():
    import tempfile

    analysis = _mixed_analysis()
    proposal = fee_engine.build_proposal(analysis)
    with tempfile.TemporaryDirectory() as out:
        path = rg.generate_report(analysis, proposal, output_dir=out)
        assert path.endswith(".pdf")
        with open(path, "rb") as handle:
            head = handle.read(5)
        assert head == b"%PDF-"


def test_the_summary_separates_the_budget_result_from_the_billable_gap():
    """
    The building is under its own budget and still above the portfolio average.
    The summary has to say both without implying the first produced a saving.
    """
    analysis = _mixed_analysis()
    proposal = fee_engine.build_proposal(analysis)
    text = " ".join(rg.summary_paragraphs(analysis, proposal))
    assert "under its budget" in text
    assert "$6,000" in text            # the portfolio gap, annualized
    assert analysis.portfolio_savings_annual == pytest.approx(6_000)
