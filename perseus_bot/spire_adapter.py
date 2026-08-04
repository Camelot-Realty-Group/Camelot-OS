"""
spire_adapter.py — Spire JSON → Perseus's existing line-item pipeline
======================================================================
Converts `utils.spire_client.SpireLineItem` results into the exact same
`parser.ParsedReport` / `parser.ReportLine` shapes Perseus's file parsers
already produce, so everything downstream — `roll_up_by_category`,
`variance_engine.analyze`, `fee_engine`, `report_generator` — runs completely
unchanged whether the figures came from an uploaded file or were pulled live
from Spire.

Two entry points, mirroring the two things Perseus can source from Spire:

* `actuals_from_spire()` — a period's GL actuals from `SpireClient.get_gl_actuals`,
  turned into the "actual" side of a ParsedReport, source_format="spire_gl_actuals".
* `budget_from_spire()` — an annual budget from `SpireClient.get_budget`, turned
  into the "budget" side of a ParsedReport, ready to hand to
  `variance_engine.baseline_from_annual_budget()` exactly like an uploaded
  annual budget file would be.

Author: Camelot OS
"""

from __future__ import annotations

from perseus_bot.parser import ParsedReport, ReportLine

SOURCE_FORMAT_ACTUALS = "spire_gl_actuals"
SOURCE_FORMAT_BUDGET = "spire_budget"


def actuals_from_spire(
    line_items: list[dict],
    building_name: str = "",
) -> ParsedReport:
    """
    Build a ParsedReport of period actuals from Spire GL activity.

    `line_items` is the plain-dict shape produced by
    `utils.spire_client.line_items_to_dicts()`: a list of
    {account_code, label, amount}. Every item is treated as an expense line —
    Spire's GLSummary rollup used by `get_gl_actuals` already nets debits and
    credits per account, so the sign convention matches what the existing
    parsers hand off (a positive magnitude of period spend).
    """
    report = ParsedReport(
        filename=f"Spire GL actuals — {building_name}".strip(" —"),
        source_format=SOURCE_FORMAT_ACTUALS,
    )
    for item in line_items:
        amount = item.get("amount")
        if amount in (None, 0, 0.0):
            continue
        report.lines.append(
            ReportLine(
                raw_label=item.get("label") or item.get("account_code") or "Unlabeled",
                actual=abs(float(amount)),
                account_code=item.get("account_code"),
                section="expense",
            )
        )

    if not report.lines:
        report.warnings.append(
            "Spire returned no GL activity with a nonzero amount for this "
            "building and period."
        )
    return report


def budget_from_spire(
    line_items: list[dict],
    building_name: str = "",
    year: int = 0,
) -> ParsedReport:
    """
    Build a ParsedReport carrying a budget (not actual) figure per line, from
    Spire's annual budget endpoint — the same shape `parser.parse_annual_budget`
    produces for an uploaded budget file, so it can be handed directly to
    `variance_engine.baseline_from_annual_budget()` unchanged.
    """
    label = f"Spire annual budget {year} — {building_name}".strip(" —")
    report = ParsedReport(filename=label, source_format=SOURCE_FORMAT_BUDGET)
    for item in line_items:
        amount = item.get("amount")
        if amount in (None, 0, 0.0):
            continue
        report.lines.append(
            ReportLine(
                raw_label=item.get("label") or item.get("account_code") or "Unlabeled",
                budget=abs(float(amount)),
                account_code=item.get("account_code"),
                section="expense",
            )
        )

    if not report.lines:
        report.warnings.append(
            f"Spire returned no {year} budget line items for this building."
        )
    return report
