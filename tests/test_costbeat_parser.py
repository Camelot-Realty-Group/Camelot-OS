"""Tests for costbeat_bot/parser.py."""
import pytest

import parser as budget_parser

# A GL-account-hierarchy export in the shape Camelot's accounting platform
# produces: an income block, then an expense block whose account rows nest under
# category headers, each closed by a subtotal row that must NOT be counted.
GL_HIERARCHY_CSV = """The Story House - 36 East 22nd Street,,
Operating Budget FY2026,,
,,
INCOME,,
4010  Common Charges,384000,
4020  Commercial Rent,96000,
Total Income,,480000
,,
EXPENSES,,
Payroll,,
5010  Superintendent Wages,48000,
5015  Payroll Taxes,4600,
5020  Workers Compensation,3100,
Total Payroll,,55700
Utilities,,
5110  Electricity,14200,
5120  Water & Sewer,11800,
5130  Gas,9400,
Total Utilities,,35400
Repairs & Maintenance,,
5210  Elevator Service Contract,9600,
5220  Sprinkler & Fire Alarm Inspection,3400,
5230  Exterminator,1800,
5240  Miscellaneous Repairs,7500,
Total Repairs & Maintenance,,22300
Administrative,,
5310  Insurance,26500,
5320  Legal & Accounting,8200,
5330  Management Fee,19200,
Total Expenses,,167300
"""


@pytest.fixture
def gl_budget():
    return budget_parser.parse_budget(GL_HIERARCHY_CSV.encode("utf-8"), "story_house_budget.csv")


def test_gl_hierarchy_format_detected(gl_budget):
    assert gl_budget.source_format == "gl_hierarchy"


def test_subtotal_rows_are_not_counted_as_line_items(gl_budget):
    """13 account rows, and the total must equal their sum — not double the sum."""
    assert len(gl_budget.expense_lines) == 13
    assert gl_budget.total_expense == pytest.approx(167300)
    assert not any(
        line.raw_label.lower().startswith("total") for line in gl_budget.lines
    )


def test_income_lines_excluded_from_expenses(gl_budget):
    labels = [line.raw_label for line in gl_budget.expense_lines]
    assert "Common Charges" not in labels
    assert "Commercial Rent" not in labels
    assert any(line.section == "income" for line in gl_budget.lines)


def test_account_codes_and_parent_group_captured(gl_budget):
    electricity = next(ln for ln in gl_budget.expense_lines if ln.raw_label == "Electricity")
    assert electricity.account_code == "5110"
    assert electricity.category_header == "Utilities"
    assert electricity.display_label == "Utilities — Electricity"
    assert electricity.amount == pytest.approx(14200)


def test_reconciles_against_declared_total(gl_budget):
    assert gl_budget.declared_total_expense == pytest.approx(167300)
    assert gl_budget.needs_review is False


def test_declared_total_mismatch_flags_review():
    """A budget whose lines don't add up goes to a human, not to a guess."""
    tampered = GL_HIERARCHY_CSV.replace("Total Expenses,,167300", "Total Expenses,,199000")
    budget = budget_parser.parse_budget(tampered.encode("utf-8"), "tampered.csv")
    assert budget.needs_review is True
    assert any("does not match" in w for w in budget.warnings)


def test_flat_two_column_sheet():
    flat = "Insurance,26500\nElectricity,14200\nWater & Sewer,11800\nTotal,52500\n"
    budget = budget_parser.parse_budget(flat.encode("utf-8"), "flat.csv")
    assert budget.source_format == "flat"
    assert len(budget.expense_lines) == 3
    assert budget.total_expense == pytest.approx(52500)


def test_accounting_negatives_read_as_magnitudes():
    flat = "Insurance,\"(26,500)\"\nElectricity,-14200\n"
    budget = budget_parser.parse_budget(flat.encode("utf-8"), "credits.csv")
    assert budget.total_expense == pytest.approx(40700)


def test_empty_upload_rejected():
    with pytest.raises(budget_parser.BudgetParseError):
        budget_parser.parse_budget(b"", "empty.csv")


def test_unsupported_file_type_rejected():
    with pytest.raises(budget_parser.BudgetParseError):
        budget_parser.parse_budget(b"some bytes", "budget.docx")


def test_sheet_with_no_recognisable_rows_rejected():
    with pytest.raises(budget_parser.BudgetParseError):
        budget_parser.parse_budget(b"just,some\nprose,here\n", "notabudget.csv")
