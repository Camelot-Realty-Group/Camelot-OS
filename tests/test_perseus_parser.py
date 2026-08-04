"""
Tests for perseus_bot/parser.py.

Perseus reads a building's period actuals out of four layouts without being
configured for any of them. The rule these tests exist to protect: when the
parser cannot tell which column holds the actual spend, it must fail loudly
rather than pick one. A wrong column produces a plausible report that is
entirely wrong, which is worse than no report.
"""
import csv
import io

import pytest

from perseus_bot import parser


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _csv(rows) -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode("utf-8")


COLUMNAR_ROWS = [
    ["552 West 150th Street — Q2 2026 Management Report"],
    [],
    ["Account", "Actual", "Budget", "YTD Actual", "Variance"],
    ["Electricity", "12,400", "10,000", "24,100", "(2,400)"],
    ["Water & Sewer", "8,200", "9,000", "16,000", "800"],
    ["Insurance", "15,000", "15,000", "30,000", "0"],
    ["Total Operating Expenses", "35,600", "34,000", "70,100", "(1,600)"],
]

GL_ROWS = [
    ["Riverside Court — Period Ending 06/30/2026"],
    ["Account", "Description", "Current Period", "Budget"],
    ["OPERATING EXPENSES"],
    ["5100", "Electricity", "12,400", "10,000"],
    ["5110", "Gas / Heating Fuel", "6,300", "7,000"],
    ["5200", "Water & Sewer", "8,200", "9,000"],
    ["5300", "Repairs & Maintenance", "4,100", "3,500"],
    ["", "Total Operating Expenses", "31,000", "29,500"],
]

FLAT_ROWS = [
    ["Electricity", "12,400"],
    ["Water & Sewer", "8,200"],
    ["Insurance", "15,000"],
]


# ---------------------------------------------------------------------------
# Columnar layout
# ---------------------------------------------------------------------------

def test_columnar_finds_the_actual_column_not_the_variance_column():
    report = parser.parse_report(_csv(COLUMNAR_ROWS), "q2.csv")
    assert report.source_format == "columnar"
    by_label = {ln.raw_label: ln for ln in report.expense_lines}
    assert by_label["Electricity"].actual == pytest.approx(12_400)
    assert by_label["Electricity"].budget == pytest.approx(10_000)
    # The variance column (2,400) must never be mistaken for a figure column.
    assert by_label["Electricity"].actual != pytest.approx(2_400)


def test_columnar_skips_the_total_row_but_keeps_it_as_a_declared_total():
    report = parser.parse_report(_csv(COLUMNAR_ROWS), "q2.csv")
    labels = [ln.raw_label for ln in report.expense_lines]
    assert not any("total" in lbl.lower() for lbl in labels)
    assert report.total_actual == pytest.approx(35_600)
    assert report.declared_total_actual == pytest.approx(35_600)


def test_columnar_reconciles_against_the_files_own_total():
    """A file whose stated total disagrees with its lines goes to a human."""
    rows = [r[:] for r in COLUMNAR_ROWS]
    rows[-1] = ["Total Operating Expenses", "99,999", "34,000", "70,100", "0"]
    report = parser.parse_report(_csv(rows), "q2.csv")
    report.reconcile()
    assert report.needs_review
    assert any("does not match" in w for w in report.warnings)


def test_columnar_carries_budget_when_the_file_has_a_budget_column():
    report = parser.parse_report(_csv(COLUMNAR_ROWS), "q2.csv")
    assert report.carries_budget
    assert report.total_budget == pytest.approx(34_000)


def test_parenthesized_and_negative_figures_read_as_magnitudes():
    assert parser._to_amount("(1,234)") == pytest.approx(1_234)
    assert parser._to_amount("-1,234") == pytest.approx(1_234)
    assert parser._to_amount("$1,234.56") == pytest.approx(1_234.56)
    assert parser._to_amount("n/a") is None
    assert parser._to_amount("") is None
    assert parser._to_amount(None) is None


# ---------------------------------------------------------------------------
# GL account hierarchy
# ---------------------------------------------------------------------------

def test_split_column_gl_export_labels_lines_by_description_not_account_code():
    """
    The common MDS export puts the code in one column and the description in
    the next. Labelling lines '5100' instead of 'Electricity' maps nothing in
    the taxonomy, and the report finds no savings while looking like it worked.
    """
    report = parser.parse_report(_csv(GL_ROWS), "riverside.csv")
    labels = {ln.raw_label for ln in report.expense_lines}
    assert "Electricity" in labels
    assert "Water & Sewer" in labels
    assert not any(lbl.isdigit() for lbl in labels)
    codes = {ln.account_code for ln in report.expense_lines}
    assert {"5100", "5110", "5200", "5300"} <= codes


def test_split_column_gl_export_does_not_double_count_the_total_row():
    report = parser.parse_report(_csv(GL_ROWS), "riverside.csv")
    # 12,400 + 6,300 + 8,200 + 4,100 = 31,000. Counting the total row too
    # would give 62,000 and double every saving in the report.
    assert report.total_actual == pytest.approx(31_000)
    assert report.declared_total_actual == pytest.approx(31_000)


def test_inline_gl_code_layout_is_detected_as_a_hierarchy():
    rows = [
        ["Riverside Court"],
        ["OPERATING EXPENSES"],
        ["5100 · Electricity", "12,400"],
        ["5110 · Gas / Heating Fuel", "6,300"],
        ["5200 · Water & Sewer", "8,200"],
        ["Total Operating Expenses", "26,900"],
    ]
    report = parser.parse_report(_csv(rows), "inline.csv")
    assert report.source_format == "gl_hierarchy"
    assert report.total_actual == pytest.approx(26_900)


def test_account_code_separators_are_stripped_from_the_label():
    """These labels are printed in the owner-facing table, separator and all."""
    for raw, expected in [
        ("5100 · Electricity", "Electricity"),
        ("5100 - Electricity", "Electricity"),
        ("5100: Electricity", "Electricity"),
        ("5100 Electricity", "Electricity"),
        ("5100.10 · Electricity", "Electricity"),
    ]:
        label, code = parser._resolve_label([raw, "12,400"], {1})
        assert label == expected, f"{raw!r} produced {label!r}"
        assert code.startswith("5100")


def test_a_street_number_in_the_label_is_not_read_as_an_account_code():
    label, code = parser._resolve_label(["150th Street Reserve", "1,000"], {1})
    assert label == "150th Street Reserve"
    assert code is None


def test_resolve_label_falls_back_to_the_next_text_column():
    label, code = parser._resolve_label(["5100", "Electricity", "12,400"], {2})
    assert (label, code) == ("Electricity", "5100")


def test_resolve_label_skips_figure_columns_when_hunting_for_a_description():
    # Column 1 is the actual; the description sits after it.
    label, code = parser._resolve_label(["5100", "12,400", "Electricity"], {1})
    assert (label, code) == ("Electricity", "5100")


# ---------------------------------------------------------------------------
# Flat layout
# ---------------------------------------------------------------------------

def test_flat_two_column_sheet_reads_the_amount_as_the_period_actual():
    report = parser.parse_report(_csv(FLAT_ROWS), "flat.csv")
    assert report.source_format == "flat"
    assert report.total_actual == pytest.approx(35_600)
    assert not report.carries_budget


# ---------------------------------------------------------------------------
# MDS PDF — the layout Perseus refuses to guess at
# ---------------------------------------------------------------------------

MDS_TABLE = [
    ["Account", "Current Period", "YTD", "Budget", "Variance"],
    ["Electricity", "12,400", "24,100", "10,000", "(2,400)"],
    ["Water & Sewer", "8,200", "16,000", "9,000", "800"],
    ["Insurance", "15,000", "30,000", "15,000", "0"],
    ["Total Operating Expenses", "35,600", "70,100", "34,000", "(1,600)"],
]


def test_mds_table_maps_current_period_to_actual_and_ytd_separately():
    report = parser._parse_mds_table(MDS_TABLE, "mds.pdf")
    assert report is not None
    assert report.source_format == "mds_pdf"
    by_label = {ln.raw_label: ln for ln in report.expense_lines}
    elec = by_label["Electricity"]
    assert elec.actual == pytest.approx(12_400)
    assert elec.ytd == pytest.approx(24_100)
    assert elec.budget == pytest.approx(10_000)


def test_mds_table_warns_when_both_period_and_ytd_columns_are_present():
    """Reading YTD as the period figure would inflate every annualized line."""
    report = parser._parse_mds_table(MDS_TABLE, "mds.pdf")
    assert any("year-to-date" in w.lower() for w in report.warnings)


def test_mds_table_returns_none_when_no_figure_column_can_be_classified():
    unlabeled = [
        ["Account", "Col A", "Col B", "Col C"],
        ["Electricity", "12,400", "24,100", "10,000"],
    ]
    assert parser._parse_mds_table(unlabeled, "mds.pdf") is None


def test_mds_text_layer_parses_a_headerless_figure_row():
    """
    MDS text layers drop the label-column header, so the row reads
    'Current Month  YTD  Budget  Variance'. The first figure column still has
    to be classified as the period actual.
    """
    text = (
        "552 West 150th Street\n"
        "Period Ending 06/30/2026\n"
        "Current Month        YTD        Budget      Variance\n"
        "Electricity          12,400     24,100      10,000      (2,400)\n"
        "Water & Sewer        8,200      16,000      9,000       800\n"
    )
    report = parser._parse_mds_text(text, "mds.pdf")
    by_label = {ln.raw_label: ln for ln in report.expense_lines}
    assert by_label["Electricity"].actual == pytest.approx(12_400)
    assert by_label["Electricity"].ytd == pytest.approx(24_100)
    # A text-layer parse is never trusted silently.
    assert report.needs_review


def test_mds_text_layer_raises_rather_than_guessing_the_actual_column():
    text = (
        "552 West 150th Street\n"
        "Electricity          12,400     24,100      10,000\n"
        "Water & Sewer        8,200      16,000      9,000\n"
    )
    with pytest.raises(parser.MDSLayoutError) as excinfo:
        parser._parse_mds_text(text, "mds.pdf")
    # The error has to tell the user what to do instead.
    assert "excel" in str(excinfo.value).lower()


def test_names_a_figure_column_recognizes_the_four_roles():
    assert parser._names_a_figure_column("Current Period")
    assert parser._names_a_figure_column("Budget")
    assert parser._names_a_figure_column("YTD")
    assert parser._names_a_figure_column("Variance")
    assert not parser._names_a_figure_column("Account")
    assert not parser._names_a_figure_column("")


def test_classify_columns_never_claims_the_label_column():
    roles = parser._classify_columns(["Account", "Actual", "Budget"])
    assert 0 not in roles.values()
    assert roles["actual"] == 1
    assert roles["budget"] == 2


def test_classify_columns_excludes_variance_columns():
    roles = parser._classify_columns(["Account", "Actual", "Variance"])
    assert "variance" not in roles


# ---------------------------------------------------------------------------
# Error surface
# ---------------------------------------------------------------------------

def test_unreadable_file_raises_report_parse_error():
    with pytest.raises(parser.ReportParseError):
        parser.parse_report(b"not a spreadsheet", "mystery.xlsx")


def test_unsupported_extension_raises_report_parse_error():
    with pytest.raises(parser.ReportParseError):
        parser.parse_report(b"anything", "report.docx")


def test_a_sheet_with_no_recognizable_lines_raises():
    with pytest.raises(parser.ReportParseError):
        parser.parse_report(_csv([["Notes"], ["Nothing numeric here"]]), "empty.csv")


# ---------------------------------------------------------------------------
# Annual budget upload
# ---------------------------------------------------------------------------

def test_annual_budget_upload_without_a_budget_column_reads_amounts_as_budget():
    budget = parser.parse_annual_budget(_csv(FLAT_ROWS), "budget.csv")
    assert budget.carries_budget
    assert budget.total_budget == pytest.approx(35_600)


def test_annual_budget_upload_keeps_an_explicit_budget_column():
    budget = parser.parse_annual_budget(_csv(COLUMNAR_ROWS), "budget.csv")
    assert budget.total_budget == pytest.approx(34_000)
