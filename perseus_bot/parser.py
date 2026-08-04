"""
parser.py — Perseus periodic actuals parser
===========================================
Extracts per-category actual spend for one period from whatever the accounting
group or the MDS run produced. Four input shapes are supported:

1. **Columnar budget/actual/variance sheet** — the pre-built layout accounting
   sometimes sends. A header row names the columns; Perseus reads the actual
   column and, when the sheet carries one, the budget column too, which then
   serves as the variance baseline without a second upload.

2. **GL-account hierarchy export** — rows nest: section header ("EXPENSES") →
   category header ("Payroll") → account rows ("5010  Superintendent Wages
   12,000") → subtotal rows ("Total Operating Expenses"). Only account rows
   carry addressable amounts; subtotals are recognised and skipped so nothing is
   double-counted, then used to reconcile the parsed sum.

3. **Flat two-column (label, amount) sheet.**

4. **PDF "MDS" report** — Camelot's internal Management Detail Statement, whose
   expense table runs Label | Current Period | YTD | Budget | Variance. The
   column headers are read to decide which column is which. If they cannot be
   identified, the parse fails with a message asking for the Excel export.

Nothing here guesses a number. An account row whose figure cannot be read is
raised as a warning against that line, and an unrecognisable layout is an error,
because a fabricated actual would flow straight into a fee proposal.

Author: Camelot OS
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("perseus_bot.parser")


class ReportParseError(Exception):
    """Raised when a file cannot be read at all (wrong type, corrupt, empty)."""


class MDSLayoutError(ReportParseError):
    """Raised when a PDF is readable but its column layout is unrecognised."""


# ---------------------------------------------------------------------------
# Row classification patterns
# ---------------------------------------------------------------------------

# "5010 Superintendent Wages", "5010 - Superintendent Wages", "5010.10  Wages"
# MDS separates code from description with a middle dot; other exports use a
# dash or a colon. Anything left in the separator class ends up printed in the
# owner-facing table, so all of them are consumed here.
ACCOUNT_ROW_RE = re.compile(
    r"^\s*(\d{3,6}(?:[.\-]\d{1,3})?)(?:\s*[-–—:.·•]\s*|\s+)(.{2,})$"
)
# A cell holding nothing but a GL code — the description lives in another column.
_ACCOUNT_CODE_ONLY_RE = re.compile(r"^\s*\d{3,6}(?:[.\-]\d{1,3})?\s*$")

# Subtotal / total rows — never a spendable line item.
TOTAL_ROW_RE = re.compile(
    r"^\s*(total|subtotal|sub-total|net\s|grand\s+total|sum\s+of)\b", re.IGNORECASE
)

# Section headers that switch us between income and expense blocks.
INCOME_SECTION_RE = re.compile(r"\b(income|revenue|receipts)\b", re.IGNORECASE)
EXPENSE_SECTION_RE = re.compile(
    r"\b(expense|expenditure|disbursement|operating\s+cost)", re.IGNORECASE
)

# Column header classification for columnar sheets and MDS tables.
ACTUAL_HEADER_RE = re.compile(
    r"\b(actual|current\s+(month|period|quarter)|this\s+(month|period|quarter)|"
    r"month\s+to\s+date|mtd|period|q[1-4]|qtr|quarter)\b",
    re.IGNORECASE,
)
BUDGET_HEADER_RE = re.compile(r"\b(budget|budgeted|plan)\b", re.IGNORECASE)
YTD_HEADER_RE = re.compile(r"\b(ytd|year\s*to\s*date|year-to-date)\b", re.IGNORECASE)
VARIANCE_HEADER_RE = re.compile(r"\b(variance|var|over/?under|diff(erence)?)\b", re.IGNORECASE)

# "Water & Sewer .......... 12,450.00   48,900.00   13,000.00   (550.00)"
PDF_ROW_RE = re.compile(
    r"^(?P<label>[A-Za-z][^$\d]{2,60}?)[\s.…]*"
    r"(?P<numbers>(?:\$?\s*\(?-?[\d,]+(?:\.\d{2})?\)?[\s]*){1,5})$"
)
NUMBER_RE = re.compile(r"\(?-?\$?[\d,]+(?:\.\d{2})?\)?")

_NUMERIC_CLEAN_RE = re.compile(r"[^\d.\-()]")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ReportLine:
    """One expense line lifted out of an uploaded report."""

    raw_label: str
    actual: Optional[float] = None
    budget: Optional[float] = None      # period budget, when the file carries one
    ytd: Optional[float] = None
    account_code: Optional[str] = None
    category_header: Optional[str] = None   # the GL parent group, e.g. "Payroll"
    section: str = "expense"                # "expense" | "income"

    @property
    def amount(self) -> float:
        """The figure Perseus compares — the period actual."""
        return self.actual or 0.0

    @property
    def display_label(self) -> str:
        """Label for the report table, prefixed by its GL group when known."""
        if self.category_header and self.category_header.lower() not in self.raw_label.lower():
            return f"{self.category_header} — {self.raw_label}"
        return self.raw_label


@dataclass
class ParsedReport:
    """Everything the parser could establish about an uploaded report."""

    filename: str
    source_format: str          # "columnar" | "gl_hierarchy" | "flat" | "mds_pdf"
    lines: list[ReportLine] = field(default_factory=list)
    needs_review: bool = False
    warnings: list[str] = field(default_factory=list)
    # Subtotals declared by the file itself, used to reconcile our own sum.
    declared_total_actual: Optional[float] = None
    declared_total_budget: Optional[float] = None

    @property
    def expense_lines(self) -> list[ReportLine]:
        return [ln for ln in self.lines if ln.section == "expense"]

    @property
    def total_actual(self) -> float:
        return sum(ln.actual or 0.0 for ln in self.expense_lines)

    @property
    def total_budget(self) -> float:
        return sum(ln.budget or 0.0 for ln in self.expense_lines)

    @property
    def carries_budget(self) -> bool:
        """True when the file itself supplies a budget figure per line."""
        return any(ln.budget for ln in self.expense_lines)

    def reconcile(self, tolerance_pct: float = 0.01) -> None:
        """
        Compare our summed expense lines against the file's own declared total.
        A mismatch sets needs_review — the numbers go to a human, not to a guess.
        """
        if not self.declared_total_actual:
            return
        drift = abs(self.total_actual - self.declared_total_actual)
        if drift / abs(self.declared_total_actual) > tolerance_pct:
            self.needs_review = True
            self.warnings.append(
                f"Parsed actual total ${self.total_actual:,.0f} does not match the "
                f"total stated in the file (${self.declared_total_actual:,.0f}). "
                f"Review the line items before sending this analysis to a client."
            )


# ---------------------------------------------------------------------------
# Numeric coercion
# ---------------------------------------------------------------------------

def _to_amount(value: Any) -> Optional[float]:
    """
    Coerce a cell to a positive expense amount, or None if it isn't a number.

    Accounting negatives — "(1,234)" and "-1,234" — are read as magnitudes,
    because an expense credit still represents spend on that line.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return abs(float(value)) if value == value else None  # NaN check

    text = str(value).strip()
    if not text or text in {"-", "—", "–"}:
        return None
    cleaned = _NUMERIC_CLEAN_RE.sub("", text)
    if not cleaned or cleaned in {"-", ".", "()"}:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return abs(-amount if negative else amount)


def _cells(row: Any) -> list[Any]:
    """Row values as a plain list, dropping trailing empties."""
    values = list(row)
    while values and (values[-1] is None or str(values[-1]).strip() == ""):
        values.pop()
    return values


# ---------------------------------------------------------------------------
# Column header classification
# ---------------------------------------------------------------------------

def _names_a_figure_column(text: Any) -> bool:
    """True when a header cell names a budget / actual / ytd / variance column."""
    value = str(text or "")
    return any(
        pattern.search(value)
        for pattern in (BUDGET_HEADER_RE, YTD_HEADER_RE, ACTUAL_HEADER_RE, VARIANCE_HEADER_RE)
    )


def _classify_columns(header_cells: list[Any]) -> dict[str, int]:
    """
    Map "actual" / "budget" / "ytd" to column indices from a header row.

    Variance columns are identified only so they are not mistaken for actuals —
    Perseus recomputes variance itself rather than trusting the file's arithmetic.
    Index 0 is assumed to be the label column and is never classified.
    """
    roles: dict[str, int] = {}
    for index, cell in enumerate(header_cells):
        if index == 0:
            continue
        text = str(cell or "").strip()
        if not text:
            continue
        if VARIANCE_HEADER_RE.search(text):
            continue
        if BUDGET_HEADER_RE.search(text) and "budget" not in roles:
            roles["budget"] = index
        elif YTD_HEADER_RE.search(text) and "ytd" not in roles:
            roles["ytd"] = index
        elif ACTUAL_HEADER_RE.search(text) and "actual" not in roles:
            roles["actual"] = index
    return roles


def _find_header_row(rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
    """
    Locate the column header row in a spreadsheet, returning its index and the
    role→column map. Returns (-1, {}) when no row names an actual column.
    """
    for index, row in enumerate(rows[:40]):
        values = _cells(row)
        if len(values) < 2:
            continue
        roles = _classify_columns(values)
        if "actual" in roles or ("budget" in roles and "ytd" in roles):
            return index, roles
    return -1, {}


# ---------------------------------------------------------------------------
# Spreadsheet loading
# ---------------------------------------------------------------------------

def _load_rows(content: bytes, filename: str) -> list[list[Any]]:
    """Read an xlsx/csv upload into a list of raw row value lists."""
    if filename.lower().endswith(".csv"):
        return _load_csv_rows(content, filename)

    import pandas as pd

    try:
        frame = pd.read_excel(io.BytesIO(content), header=None, dtype=object, engine="openpyxl")
    except Exception as exc:
        raise ReportParseError(f"Could not read '{filename}': {exc}") from exc

    frame = frame.where(frame.notna(), None)
    return [list(row) for row in frame.itertuples(index=False, name=None)]


def _load_csv_rows(content: bytes, filename: str) -> list[list[Any]]:
    """
    Read a CSV with the stdlib reader rather than pandas.

    Management reports are ragged by nature: a one-cell title row, a blank
    spacer, then a five-column header. pandas fixes the column count from the
    first row and rejects every row wider than it, which throws out most real
    exports before the parser sees them.
    """
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ReportParseError(f"Could not decode '{filename}' as text.")

    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise ReportParseError(f"Could not read '{filename}': {exc}") from exc

    return [[cell if cell != "" else None for cell in row] for row in rows]


def _looks_like_gl_hierarchy(rows: list[list[Any]]) -> bool:
    """
    True when at least three rows start with a GL account code, which is what
    separates the accounting export from a hand-built two-column sheet.
    """
    hits = 0
    for row in rows:
        values = _cells(row)
        if not values:
            continue
        if ACCOUNT_ROW_RE.match(str(values[0] or "").strip()):
            hits += 1
            if hits >= 3:
                return True
    return False


# ---------------------------------------------------------------------------
# Format-specific parsers
# ---------------------------------------------------------------------------

def _resolve_label(
    values: list[Any], figure_cols: set[int]
) -> tuple[str, Optional[str]]:
    """
    Work out what a row is called, and its GL code if it has one.

    Three shapes have to land on the same answer, because the category taxonomy
    matches on words and a bare account code carries none:

        "Electricity"                     → ("Electricity", None)
        "5100 · Electricity"              → ("Electricity", "5100")
        ["5100", "Electricity", 12400]    → ("Electricity", "5100")

    The third is the common MDS export: code and description in separate
    columns. Reading only column 0 there labels every line with a number, the
    taxonomy maps none of them, and the report finds no savings at all while
    looking like it worked.
    """
    first = str(values[0] or "").strip()

    match = ACCOUNT_ROW_RE.match(first)
    if match:
        return match.group(2).strip(), match.group(1)

    if first and not _ACCOUNT_CODE_ONLY_RE.match(first):
        return first, None

    # Column 0 is empty or holds a bare code — the description is in the next
    # non-figure column that carries text rather than a number.
    code = first or None
    for index in range(1, len(values)):
        if index in figure_cols:
            continue
        candidate = str(values[index] or "").strip()
        if candidate and _to_amount(candidate) is None:
            return candidate, code
    return "", code


def _parse_columnar(
    rows: list[list[Any]],
    filename: str,
    header_index: int,
    roles: dict[str, int],
) -> ParsedReport:
    """
    Read a sheet whose header row names its budget / actual / variance columns.

    Only the named columns are read. A row is kept when its actual column holds
    a number, so blank spacer rows and text notes fall away on their own.
    """
    report = ParsedReport(filename=filename, source_format="columnar")
    actual_col = roles.get("actual")
    budget_col = roles.get("budget")
    ytd_col = roles.get("ytd")
    figure_cols = {c for c in (actual_col, budget_col, ytd_col) if c is not None}
    section = "expense"
    category_header: Optional[str] = None

    for row in rows[header_index + 1:]:
        values = _cells(row)
        if not values:
            continue
        label, account_code = _resolve_label(values, figure_cols)
        if not label:
            continue

        def column(index: Optional[int]) -> Optional[float]:
            if index is None or index >= len(values):
                return None
            return _to_amount(values[index])

        actual = column(actual_col)
        budget = column(budget_col)
        ytd = column(ytd_col)

        if actual is None and budget is None:
            if INCOME_SECTION_RE.search(label) and not EXPENSE_SECTION_RE.search(label):
                section, category_header = "income", None
            elif EXPENSE_SECTION_RE.search(label):
                section, category_header = "expense", None
            elif not TOTAL_ROW_RE.match(label):
                category_header = label
            continue

        if TOTAL_ROW_RE.match(label):
            if section == "expense" and EXPENSE_SECTION_RE.search(label):
                report.declared_total_actual = actual
                report.declared_total_budget = budget
            category_header = None
            continue

        report.lines.append(
            ReportLine(
                raw_label=label,
                actual=actual,
                budget=budget,
                ytd=ytd,
                account_code=account_code,
                category_header=category_header,
                section=section,
            )
        )

    if not report.lines:
        raise ReportParseError(
            f"'{filename}' has a budget/actual header row but no readable expense "
            f"rows beneath it."
        )
    report.reconcile()
    return report


def _parse_gl_hierarchy(rows: list[list[Any]], filename: str) -> ParsedReport:
    """
    Walk the GL export top to bottom, tracking the current section and category
    header so every account row is attributed to its parent group.
    """
    report = ParsedReport(filename=filename, source_format="gl_hierarchy")
    section = "expense"
    category_header: Optional[str] = None
    saw_expense_section = False

    for row in rows:
        values = _cells(row)
        if not values:
            continue

        label = str(values[0] or "").strip()
        if not label:
            continue

        # Amounts live in every column after the label. In these exports the
        # detail figure sits in one column and subtotals in a later one, so we
        # keep them positionally rather than collapsing to a single number.
        numeric = [a for a in (_to_amount(v) for v in values[1:]) if a is not None]

        # ── Section headers ───────────────────────────────────────────────
        if not numeric and not ACCOUNT_ROW_RE.match(label):
            if INCOME_SECTION_RE.search(label) and not EXPENSE_SECTION_RE.search(label):
                section = "income"
                category_header = None
                continue
            if EXPENSE_SECTION_RE.search(label):
                section = "expense"
                saw_expense_section = True
                category_header = None
                continue
            if not TOTAL_ROW_RE.match(label):
                category_header = label
            continue

        # ── Subtotal rows ─────────────────────────────────────────────────
        if TOTAL_ROW_RE.match(label):
            if section == "expense" and numeric and EXPENSE_SECTION_RE.search(label):
                report.declared_total_actual = numeric[-1]
            category_header = None
            continue

        # ── Account rows ──────────────────────────────────────────────────
        match = ACCOUNT_ROW_RE.match(label)
        account_code, clean_label = (match.group(1), match.group(2).strip()) if match else (None, label)

        if not numeric:
            # An account row with no readable figure is surfaced, not invented.
            if match:
                report.needs_review = True
                report.warnings.append(
                    f"No readable amount for GL account {account_code} "
                    f"('{clean_label}') — enter it manually before sending."
                )
            continue

        report.lines.append(
            ReportLine(
                raw_label=clean_label,
                actual=numeric[0],
                account_code=account_code,
                category_header=category_header,
                section=section,
            )
        )

    if not saw_expense_section:
        report.warnings.append(
            "No explicit expense section header found — every account row was "
            "treated as an operating expense."
        )
    report.reconcile()
    return report


def _parse_flat(rows: list[list[Any]], filename: str) -> ParsedReport:
    """Two-column (label, amount) fallback for hand-built sheets."""
    report = ParsedReport(filename=filename, source_format="flat")

    for row in rows:
        values = _cells(row)
        if len(values) < 2:
            continue
        label = str(values[0] or "").strip()
        if not label:
            continue

        amount = next((a for a in (_to_amount(v) for v in values[1:]) if a is not None), None)
        if amount is None:
            continue

        if TOTAL_ROW_RE.match(label):
            report.declared_total_actual = amount
            continue

        report.lines.append(ReportLine(raw_label=label, actual=amount))

    if not report.lines:
        raise ReportParseError(
            f"No label/amount pairs found in '{filename}'. Expected a GL account "
            f"export, a budget/actual column sheet, or a two-column "
            f"(line item, amount) sheet."
        )
    report.reconcile()
    return report


# ---------------------------------------------------------------------------
# MDS PDF
# ---------------------------------------------------------------------------

def _pdf_tables_and_text(content: bytes, filename: str) -> tuple[list[list[list[Any]]], str]:
    """Extract structured tables and raw text from a PDF in one pass."""
    try:
        import pdfplumber
    except ImportError as exc:
        raise ReportParseError(
            "pdfplumber is required to read MDS PDF reports. Install it "
            "(pip install -r requirements.txt) or upload the xlsx/csv export."
        ) from exc

    tables: list[list[list[Any]]] = []
    text_parts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
                tables.extend(page.extract_tables() or [])
    except Exception as exc:
        raise ReportParseError(f"Could not read PDF '{filename}': {exc}") from exc

    return tables, "\n".join(text_parts)


def _parse_mds_table(table: list[list[Any]], filename: str) -> Optional[ParsedReport]:
    """
    Read one extracted PDF table if it has a recognisable header row.
    Returns None when the table is not an expense table.
    """
    for header_index, row in enumerate(table[:6]):
        values = _cells(row)
        if len(values) < 2:
            continue
        roles = _classify_columns(values)
        if "actual" not in roles and "budget" not in roles:
            continue
        report = _parse_columnar(table, filename, header_index, roles)
        report.source_format = "mds_pdf"
        if roles.get("ytd") is not None and roles.get("actual") is not None:
            report.warnings.append(
                "This statement reports both a current-period column and a "
                "year-to-date column. The current-period column was used for the "
                "variance; confirm it covers the period you selected."
            )
        return report
    return None


def _parse_mds_text(text: str, filename: str) -> ParsedReport:
    """
    Fall back to the statement's text layer when table extraction finds nothing.

    The header line is located first so the numbers on each row can be assigned
    to columns by position. Without a header, the columns are unknowable and the
    parse fails rather than picking one.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    roles: dict[str, int] = {}
    column_count = 0
    for line in lines:
        if not BUDGET_HEADER_RE.search(line) and not ACTUAL_HEADER_RE.search(line):
            continue
        # Header cells are separated by runs of whitespace in the text layer.
        cells = [c for c in re.split(r"\s{2,}|\t|\|", line) if c.strip()]
        if len(cells) < 2:
            continue
        # The label column usually has no header of its own, so the first cell is
        # already a figure column. Insert a placeholder to keep index 0 = label.
        if _names_a_figure_column(cells[0]):
            cells = [""] + cells
        candidate = _classify_columns(cells)
        if "actual" in candidate or "budget" in candidate:
            roles = candidate
            column_count = len(cells) - 1
            break

    if not roles:
        raise MDSLayoutError(
            f"Could not identify the budget/actual columns in '{filename}'. "
            f"Please export the period report to Excel and upload that instead — "
            f"Perseus will not guess which column is the actual spend."
        )

    report = ParsedReport(filename=filename, source_format="mds_pdf", needs_review=True)
    report.warnings.append(
        "Read from the PDF text layer rather than a structured table. Verify each "
        "figure against the statement before sending this report to a client."
    )

    # In the text layer the label is column 0, so a role index of 1 is the first
    # number on the row.
    def number_at(numbers: list[float], role: str) -> Optional[float]:
        index = roles.get(role)
        if index is None:
            return None
        position = index - 1
        return numbers[position] if 0 <= position < len(numbers) else None

    for line in lines:
        match = PDF_ROW_RE.match(line)
        if not match:
            continue
        label = match.group("label").strip(" .-")
        raw_numbers = NUMBER_RE.findall(match.group("numbers"))
        numbers = [n for n in (_to_amount(v) for v in raw_numbers) if n is not None]
        if not numbers:
            continue

        if TOTAL_ROW_RE.match(label):
            if EXPENSE_SECTION_RE.search(label):
                report.declared_total_actual = number_at(numbers, "actual")
                report.declared_total_budget = number_at(numbers, "budget")
            continue

        if column_count and len(numbers) != column_count:
            # A row with a different number of figures than the header declared
            # cannot be mapped to columns safely.
            report.warnings.append(
                f"Skipped '{label}' — {len(numbers)} figures on the row where the "
                f"column header declares {column_count}. Check it manually."
            )
            continue

        actual = number_at(numbers, "actual")
        if actual is None:
            continue
        report.lines.append(
            ReportLine(
                raw_label=label,
                actual=actual,
                budget=number_at(numbers, "budget"),
                ytd=number_at(numbers, "ytd"),
            )
        )

    if not report.lines:
        raise MDSLayoutError(
            f"No expense rows could be read from '{filename}'. Please export the "
            f"period report to Excel and upload that instead."
        )
    report.reconcile()
    return report


def _parse_mds_pdf(content: bytes, filename: str) -> ParsedReport:
    """Parse an MDS statement, preferring structured tables over the text layer."""
    tables, text = _pdf_tables_and_text(content, filename)

    for table in tables:
        report = _parse_mds_table(table, filename)
        if report is not None:
            return report

    if not text.strip():
        raise ReportParseError(
            f"'{filename}' contains no extractable text — it is likely a scan. "
            f"Upload the xlsx/csv export instead."
        )
    return _parse_mds_text(text, filename)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_report(content: bytes, filename: str) -> ParsedReport:
    """
    Parse an uploaded period actuals report into expense line items.

    Args:
        content:  Raw uploaded bytes.
        filename: Original filename — its extension selects the reader.

    Returns:
        ParsedReport. Check `needs_review` and `warnings` before treating the
        figures as final.

    Raises:
        ReportParseError: File type unsupported, unreadable, or with no
            recognisable line items.
        MDSLayoutError: PDF read but its columns could not be identified.
    """
    if not content:
        raise ReportParseError(f"'{filename}' is empty.")

    lower = filename.lower()
    if lower.endswith(".pdf"):
        report = _parse_mds_pdf(content, filename)
    elif lower.endswith((".xlsx", ".xlsm", ".xls", ".csv")):
        rows = _load_rows(content, filename)
        if not rows:
            raise ReportParseError(f"'{filename}' contains no rows.")
        header_index, roles = _find_header_row(rows)
        if header_index >= 0:
            report = _parse_columnar(rows, filename, header_index, roles)
        elif _looks_like_gl_hierarchy(rows):
            report = _parse_gl_hierarchy(rows, filename)
        else:
            report = _parse_flat(rows, filename)
    else:
        raise ReportParseError(
            f"Unsupported file type: '{filename}'. Upload .xlsx, .csv, or .pdf."
        )

    logger.info(
        "Parsed %s as %s — %d expense lines, $%s actual, carries_budget=%s, needs_review=%s",
        filename, report.source_format, len(report.expense_lines),
        f"{report.total_actual:,.0f}", report.carries_budget, report.needs_review,
    )
    return report


def parse_annual_budget(content: bytes, filename: str) -> ParsedReport:
    """
    Parse an uploaded ANNUAL budget file for use as the variance baseline.

    The same readers apply; the amounts land in `budget` rather than `actual`,
    because for a budget file the single figure per line *is* the budget.
    Sheets that already separate budget from actual keep their own mapping.
    """
    report = parse_report(content, filename)
    if not report.carries_budget:
        for line in report.lines:
            line.budget, line.actual = line.actual, None
        report.declared_total_budget = report.declared_total_actual
        report.declared_total_actual = None
    return report
