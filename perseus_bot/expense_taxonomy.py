"""
expense_taxonomy.py — Camelot expense category taxonomy
=======================================================
Maps arbitrary GL labels from an accounting export or an MDS management report
onto Camelot's fixed expense taxonomy (see config.yaml `categories`). The
mapping table is keyword-driven and meant to be extended as new charts of
accounts turn up.

CONSOLIDATION NOTE
------------------
This module is a standalone copy of the taxonomy CostBeat Bot carries in
`costbeat_bot/benchmarks.py`. Perseus is built on its own branch off `main`, so
it cannot import from a bot that has not merged yet. The names here —
CATEGORY_KEYWORDS, CATEGORY_LABELS, UNMAPPED, category_label(),
normalize_category() — deliberately match CostBeat's, so that once both bots are
merged this file moves to `utils/expense_taxonomy.py` unchanged and both bots
import it from there. Keep the two copies in step until that happens.

Author: Camelot OS
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("perseus_bot.expense_taxonomy")

# ---------------------------------------------------------------------------
# GL label → taxonomy mapping
# ---------------------------------------------------------------------------
#
# Ordered most-specific first: the first category whose keywords match wins.
# Keywords are matched as substrings against the lowercased label (GL group
# header + account label), so keep them distinctive.

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    # Payroll and cleaning are evaluated as one block — a super's wages, the
    # payroll taxes on them, and an outside porter contract are substitutes for
    # each other, so splitting them produces misleading per-line comparisons.
    "payroll_and_cleaning": [
        "payroll", "wages", "salary", "salaries", "superintendent", "super's",
        "porter", "handyman", "doorman", "concierge staff", "staff",
        "payroll tax", "fica", "workers comp", "workers' comp", "workmen",
        "disability insurance", "union", "benefits", "cleaning", "janitorial",
        "cleaning supplies", "sidewalk", "snow removal",
    ],
    "sprinkler_fire_alarm": [
        "sprinkler", "fire alarm", "fire safety", "standpipe", "fire extinguisher",
        "fire suppression", "smoke detector", "life safety", "fdny",
    ],
    "elevator_maintenance": [
        "elevator", "lift maintenance", "elevator inspection", "elevator service",
    ],
    "intercom_security": [
        "intercom", "security", "camera", "cctv", "access control", "alarm monitoring",
        "guard", "buzzer", "door entry",
    ],
    # Servicing the plant, not the fuel burned in it. A bare "heating" here would
    # capture "Gas / Heating Fuel" — a commodity bill, and one of the largest
    # controllable lines — and benchmark it against equipment contracts, so the
    # heating keywords are all equipment phrases.
    "hvac_mechanical": [
        "hvac", "boiler", "furnace", "burner", "chiller", "air conditioning",
        "heating system", "heating plant", "heating repair", "heating maintenance",
        "heating contract", "cooling tower", "mechanical", "plumbing contract",
        "pump", "compressor",
    ],
    "electricity": ["electric", "con ed", "coned", "consolidated edison", "utility electric"],
    "water_sewer": ["water", "sewer", "dep ", "dep-", "water & sewer", "water/sewer"],
    "gas": [
        "gas", "national grid", "fuel", "oil delivery", "heating oil", "heating fuel",
        "natural gas", "propane",
    ],
    "phone_internet_cable": [
        "phone", "telephone", "internet", "cable", "wifi", "wi-fi", "broadband",
        "verizon", "spectrum", "altice", "telecom",
    ],
    "exterminator": ["exterminat", "pest", "rodent", "bed bug", "vermin"],
    "compactor_waste": [
        "compactor", "waste", "garbage", "trash", "refuse", "carting", "recycling",
        "sanitation", "dumpster",
    ],
    "insurance": [
        "insurance", "liability coverage", "umbrella policy", "property coverage",
        "d&o", "boiler & machinery", "flood policy",
    ],
    "legal_accounting_management": [
        "legal", "attorney", "counsel", "accounting", "accountant", "audit fee",
        "management fee", "managing agent", "cpa", "bookkeeping", "tax preparation",
    ],
    "admin_fees": [
        "administrative", "admin", "office", "postage", "printing", "supplies",
        "software", "dues", "subscription", "permit", "filing fee", "license",
    ],
    "taxes_bank_fees": [
        "real estate tax", "property tax", "re tax", "bank fee", "bank charge",
        "interest expense", "franchise tax", "commercial rent tax", "mortgage interest",
    ],
    "misc_repairs": [
        "repair", "maintenance", "general maintenance", "building supplies",
        "hardware", "painting", "misc", "miscellaneous", "contingency", "other expense",
    ],
}

CATEGORY_LABELS: dict[str, str] = {
    "payroll_and_cleaning": "Payroll & Cleaning",
    "insurance": "Insurance",
    "hvac_mechanical": "HVAC / Mechanical",
    "electricity": "Electricity",
    "water_sewer": "Water & Sewer",
    "gas": "Gas / Fuel",
    "phone_internet_cable": "Phone / Internet / Cable",
    "intercom_security": "Intercom & Security",
    "elevator_maintenance": "Elevator Maintenance",
    "sprinkler_fire_alarm": "Sprinkler & Fire Alarm",
    "exterminator": "Exterminator",
    "compactor_waste": "Compactor & Waste Removal",
    "misc_repairs": "Miscellaneous Repairs",
    "admin_fees": "Administrative Fees",
    "legal_accounting_management": "Legal, Accounting & Management",
    "taxes_bank_fees": "Taxes & Bank Fees",
}

UNMAPPED = "unmapped"


def category_label(category: str) -> str:
    """Human-readable name for a taxonomy key."""
    return CATEGORY_LABELS.get(category, category.replace("_", " ").title())


def normalize_category(label: str, category_header: Optional[str] = None) -> str:
    """
    Map a GL label onto the taxonomy, returning UNMAPPED when nothing matches.

    The account label is checked before its GL group header, so a
    "Repairs → Elevator Service Contract" row lands in elevator_maintenance
    rather than misc_repairs.
    """
    for candidate in (label, category_header):
        if not candidate:
            continue
        haystack = re.sub(r"\s+", " ", candidate.lower())
        for category, keywords in CATEGORY_KEYWORDS.items():
            if any(keyword in haystack for keyword in keywords):
                return category
    logger.debug("No taxonomy match for label=%r header=%r", label, category_header)
    return UNMAPPED
