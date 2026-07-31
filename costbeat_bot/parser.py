"""
parser.py — CostBeat Bot budget file parser
============================================
Extracts expense line items from an uploaded operating budget.

Three input shapes are supported:

1. GL-account hierarchy export (the format Camelot's accounting platform
   produces). Rows nest: section header ("EXPENSES") → category header
   ("Payroll") → account rows ("5010  Superintendent Wages   48,000") →
   subtotal rows ("Total Payroll" with the figure in a separate subtotal
   column). Only account rows carry addressable amounts; subtotal rows are
   recognised and skipped so nothing is double-counted.

2. Flat two-column (label, amount) sheets.

3. PDF — best-effort text extraction via pdfplumber. Always returns
   needs_review=True, because a PDF budget cannot be reconciled against a
   subtotal column. A number that could not be read is reported as a
   warning, never guessed.

Author: Camelot OS
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("costbeat_bot.parser")


class BudgetParseError(Exception):
    """Raised when a file cannot be read at all (wrong type, corrupt, empty)."""


# ---------------------------------------------------------------------------
# Row classification patterns
# ---------------------------------------------------------------------------

# "5010 Superintendent Wages", "5010 - Superintendent Wages", "5010.10  Wages"
ACCOUNT_ROW_RE = re.compile(r"^\s*(\d{3,6}(?:[.\-]\d{1,3})?)\s*[-–—:.]?\s+(.{2,})$")

# Subtotal / total rows — never a spendable line item.
TOTAL_ROW_RE = re.compile(
    r"^\s*(total|subtotal|sub-total|net\s|grand\s+total|sum\s+of)\b", re.IGNORECASE
)

# Section headers that switch us between income and expense blocks.
INCOME_SECTION_RE = re.compile(r"\b(income|revenue|receipts)\b", re.IGNORECASE)
EXPENSE_SECTION_RE = re.compile(r"\b(expense|expenditure|disbursement|operating\s+cost)", re.IGNORECASE)

# "Water & Sewer .......... 12,450.00" / "Water & Sewer   $12,450"
PDF_LINE_RE = re.compile(r"^(?P<label>[A-Za-z][^$\d]{2,60}?)[\s.…]*\$?\s*(?P<amount>\(?-?[\d,]+(?:\.\d{2})?\)?)\s*$")

_NUMERIC_CLEAN_RE = re.compile(r"[^\d.\-()]")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BudgetLine:
    """One expense line lifted out of the uploaded budget."""

    raw_label: str
    amount: float
    account_code: Optional[str] = None
    category_header: Optional[str] = None   # the GL parent group, e.g. "Payroll"
    section: str = "expense"                # "expense" | "income"

    @property
    def display_label(self) -> str:
        """Label for the report table, prefixed by its GL group when known."""
        if self.category_header and self.category_header.lower() not in self.raw_label.lower():
            return f"{self.category_header} — {self.raw_label}"
        return self.raw_label


@dataclass
class ParsedBudget:
    """Everything the parser could establish about an uploaded budget."""

    filename: str
    source_format: str                      # "gl_hierarchy" | "flat" | "pdf_text"
    lines: list[BudgetLine] = field(default_factory=list)
    needs_review: bool = False
    warnings: list[str] = field(default_factory=list)
    # Subtotals declared by the file itself, used to reconcile our own sum.
    declared_total_expense: Optional[float] = None

    @property
    def expense_lines(self) -> list[BudgetLine]:
        return [ln for ln in self.lines if ln.section == "expense"]

    @property
    def total_expense(self) -> float:
        return sum(ln.amount for ln in self.expense_lines)

    def reconcile(self, tolerance_pct: float = 0.01) -> None:
        """
        Compare our summed expense lines against the file's own declared total.
        A mismatch sets needs_review — the numbers go to a human, not to a guess.
        """
        if self.declared_total_expense is None or self.declared_total_expense == 0:
            return
        drift = abs(self.total_expense - self.declared_total_expense)
        if drift / abs(self.declared_total_expense) > tolerance_pct:
            self.needs_review = True
            self.warnings.append(
                f"Parsed expense total ${self.total_expense:,.0f} does not match the "
                f"total stated in the file (${self.declared_total_expense:,.0f}). "
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
# Spreadsheet loading
# ---------------------------------------------------------------------------

def _load_rows(content: bytes, filename: str) -> list[list[Any]]:
    """Read an xlsx/csv upload into a list of raw row value lists."""
    import pandas as pd

    lower = filename.lower()
    try:
        if lower.endswith(".csv"):
            frame = pd.read_csv(io.BytesIO(content), header=None, dtype=object)
        else:
            frame = pd.read_excel(io.BytesIO(content), header=None, dtype=object, engine="openpyxl")
    except Exception as exc:
        raise BudgetParseError(f"Could not read '{filename}': {exc}") from exc

    frame = frame.where(frame.notna(), None)
    return [list(row) for row in frame.itertuples(index=False, name=None)]


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
        label = str(values[0] or "").strip()
        if ACCOUNT_ROW_RE.match(label):
            hits += 1
            if hits >= 3:
                return True
    return False


# ---------------------------------------------------------------------------
# Format-specific parsers
# ---------------------------------------------------------------------------

def _parse_gl_hierarchy(rows: list[list[Any]], filename: str) -> ParsedBudget:
    """
    Walk the GL export top to bottom, tracking the current section and category
    header so every account row is attributed to its parent group.
    """
    budget = ParsedBudget(filename=filename, source_format="gl_hierarchy")
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
        amounts = [_to_amount(v) for v in values[1:]]
        numeric = [a for a in amounts if a is not None]

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
                budget.declared_total_expense = numeric[-1]
            category_header = None
            continue

        # ── Account rows ──────────────────────────────────────────────────
        match = ACCOUNT_ROW_RE.match(label)
        account_code: Optional[str] = None
        clean_label = label
        if match:
            account_code, clean_label = match.group(1), match.group(2).strip()

        if not numeric:
            # An account row with no readable figure is surfaced, not invented.
            if match:
                budget.needs_review = True
                budget.warnings.append(
                    f"No readable amount for GL account {account_code} "
                    f"('{clean_label}') — enter it manually before sending."
                )
            continue

        budget.lines.append(
            BudgetLine(
                raw_label=clean_label,
                amount=numeric[0],
                account_code=account_code,
                category_header=category_header,
                section=section,
            )
        )

    if not saw_expense_section:
        budget.warnings.append(
            "No explicit expense section header found — every account row was "
            "treated as an operating expense."
        )
    budget.reconcile()
    return budget


def _parse_flat(rows: list[list[Any]], filename: str) -> ParsedBudget:
    """Two-column (label, amount) fallback for hand-built budget sheets."""
    budget = ParsedBudget(filename=filename, source_format="flat")

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
            budget.declared_total_expense = amount
            continue

        budget.lines.append(BudgetLine(raw_label=label, amount=amount))

    if not budget.lines:
        raise BudgetParseError(
            f"No label/amount pairs found in '{filename}'. Expected either a GL "
            f"account export or a two-column (line item, annual amount) sheet."
        )
    budget.reconcile()
    return budget


def _parse_pdf(content: bytes, filename: str) -> ParsedBudget:
    """
    Best-effort PDF text extraction. Always flagged for review: a PDF gives us
    no subtotal column to reconcile against, so the figures are unverified.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise BudgetParseError(
            "pdfplumber is required to read PDF budgets. Install it "
            "(pip install -r requirements.txt) or upload the xlsx/csv export."
        ) from exc

    budget = ParsedBudget(filename=filename, source_format="pdf_text", needs_review=True)
    budget.warnings.append(
        "Parsed from PDF text. Amounts could not be reconciled against a "
        "subtotal column — verify every line against the source before sending."
    )

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        raise BudgetParseError(f"Could not read PDF '{filename}': {exc}") from exc

    if not text.strip():
        raise BudgetParseError(
            f"'{filename}' contains no extractable text — it is likely a scan. "
            f"Upload the xlsx/csv budget export instead."
        )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or TOTAL_ROW_RE.match(line):
            continue
        match = PDF_LINE_RE.match(line)
        if not match:
            continue
        amount = _to_amount(match.group("amount"))
        if amount is None or amount == 0:
            continue
        budget.lines.append(BudgetLine(raw_label=match.group("label").strip(" .-"), amount=amount))

    if not budget.lines:
        raise BudgetParseError(
            f"No 'line item + amount' rows recognised in '{filename}'. "
            f"Upload the xlsx/csv budget export instead."
        )
    return budget


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_budget(content: bytes, filename: str) -> ParsedBudget:
    """
    Parse an uploaded budget file into expense line items.

    Args:
        content:  Raw uploaded bytes.
        filename: Original filename — its extension selects the reader.

    Returns:
        ParsedBudget. Check `needs_review` and `warnings` before treating the
        figures as final.

    Raises:
        BudgetParseError: File type unsupported, unreadable, or with no
            recognisable line items. Never returns fabricated numbers.
    """
    if not content:
        raise BudgetParseError(f"'{filename}' is empty.")

    lower = filename.lower()
    if lower.endswith(".pdf"):
        budget = _parse_pdf(content, filename)
    elif lower.endswith((".xlsx", ".xlsm", ".xls", ".csv")):
        rows = _load_rows(content, filename)
        if not rows:
            raise BudgetParseError(f"'{filename}' contains no rows.")
        budget = (
            _parse_gl_hierarchy(rows, filename)
            if _looks_like_gl_hierarchy(rows)
            else _parse_flat(rows, filename)
        )
    else:
        raise BudgetParseError(
            f"Unsupported file type: '{filename}'. Upload .xlsx, .csv, or .pdf."
        )

    logger.info(
        "Parsed %s as %s — %d expense lines, $%s total, needs_review=%s",
        filename, budget.source_format, len(budget.expense_lines),
        f"{budget.total_expense:,.0f}", budget.needs_review,
    )
    return budget
