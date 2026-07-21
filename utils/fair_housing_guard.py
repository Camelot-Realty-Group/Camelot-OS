"""
fair_housing_guard.py — Fair Housing Language Guardrail
Camelot Property Management Services Corp.

Screens outbound, tenant/prospect-facing text (listing descriptions,
outreach emails, resident messages, template content) for language that
creates Fair Housing Act / NYC Human Rights Law exposure.

Why this exists: a single Fair Housing violation starts around $22,000
for a first offense, and HUD's 2024 AI guidance makes operators
responsible for the output of any AI they deploy. Competing platforms
(EliseAI, AppFolio Realm-X) ship compliance screening on AI-generated
text; Camelot OS should not send AI-drafted copy to the public without
at least a deterministic first-pass screen.

This is a *guardrail*, not a lawyer: it catches well-known problem
phrasing deterministically (no LLM, no network). A "clean" result does
not certify compliance — final responsibility stays with the human
reviewer and counsel. Findings are advisory and should block automated
sending until a human clears them.

Usage:
    from utils.fair_housing_guard import check_text

    result = check_text("Perfect for young professionals, no kids please")
    result.is_clean        # False
    result.findings        # [Finding(category='familial_status', ...), ...]

Protected classes covered (federal FHA + NYC HRL additions):
race/color/national origin, religion, sex, familial status, disability,
age, lawful source of income (NYC), and common "steering" phrasing.
"""

import re
from dataclasses import dataclass, field
from typing import List

__all__ = ["Finding", "GuardResult", "check_text"]


@dataclass
class Finding:
    category: str
    matched: str
    severity: str        # "block" (do not send) | "review" (human should look)
    explanation: str
    suggestion: str


@dataclass
class GuardResult:
    is_clean: bool
    findings: List[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "block"]


# ---------------------------------------------------------------------------
# Rule table: (category, regex, severity, explanation, suggestion)
# Patterns are matched case-insensitively against whole text.
# ---------------------------------------------------------------------------

_RULES = [
    # --- Familial status ---
    ("familial_status", r"\bno (kids|children)\b", "block",
     "Refusing families with children violates the FHA (familial status).",
     "Describe the property, not the household: remove the restriction."),
    ("familial_status", r"\b(adults?[- ]only|couples?[- ]only|singles?[- ]only)\b", "block",
     "Occupancy limited by household type violates familial-status protections.",
     "Remove household-type restrictions; use lawful occupancy limits only."),
    ("familial_status", r"\b(perfect|ideal|great) for (young )?(singles?|couples?|professionals?|students?)\b", "review",
     "Describing the ideal *tenant* rather than the *property* can be read as steering.",
     "Describe property features instead: 'walkable to transit', 'quiet block'."),
    ("familial_status", r"\bempty[- ]nesters?\b", "review",
     "Age/familial-status coded phrase.",
     "Describe the unit or building, not the expected resident."),

    # --- Disability ---
    ("disability", r"\bno wheelchairs?\b", "block",
     "Excluding wheelchair users violates disability protections.",
     "Remove. If the building lacks an elevator, state the fact: 'walk-up building'."),
    ("disability", r"\b(not|isn'?t) (suitable|appropriate) for (the )?(disabled|handicapped)\b", "block",
     "Excluding disabled applicants violates the FHA.",
     "State objective features (e.g., '4th-floor walk-up'); let applicants decide."),
    ("disability", r"\bable[- ]bodied\b", "block",
     "Requiring able-bodied tenants violates disability protections.",
     "Remove the requirement entirely."),

    # --- Religion / national origin / race ---
    ("religion", r"\b(christian|jewish|muslim|hindu|catholic) (only|preferred|community welcome)\b", "block",
     "Religious preference in housing ads violates the FHA.",
     "Remove religious qualifiers."),
    ("national_origin", r"\b(english[- ]speak(ing|ers?) only|no (immigrants?|foreigners?))\b", "block",
     "National-origin discrimination.",
     "Remove. Language requirements in ads create FHA exposure."),
    ("national_origin", r"\bamericans? only\b", "block",
     "National-origin discrimination.",
     "Remove the restriction."),

    # --- Sex ---
    ("sex", r"\b(male|female|men|women) (tenants? )?(only|preferred)\b", "review",
     "Sex-based preference; lawful ONLY in narrow shared-living exceptions.",
     "Remove unless this is a legally-exempt shared-living situation confirmed by counsel."),

    # --- Age (NYC HRL) ---
    ("age", r"\b(no (seniors?|elderly)|under \d\d s? only|young (tenants?|people) (only|preferred))\b", "block",
     "Age discrimination under NYC Human Rights Law.",
     "Remove age qualifiers (55+/62+ senior housing exemptions need counsel sign-off)."),

    # --- Source of income (NYC) ---
    ("source_of_income", r"\bno (section[- ]?8|vouchers?|programs?|cityfheps|hasa)\b", "block",
     "Refusing lawful source of income (vouchers) is illegal in NYC.",
     "Remove. Income *amount* standards must be applied uniformly and lawfully."),
    ("source_of_income", r"\b(section[- ]?8|vouchers?) (not (accepted|welcome)|need not apply)\b", "block",
     "Refusing lawful source of income (vouchers) is illegal in NYC.",
     "Remove the restriction."),

    # --- Steering / neighborhood coding ---
    ("steering", r"\b(exclusive|traditional|desirable) (neighborhood|community|building)\b", "review",
     "Coded neighborhood language can be read as steering.",
     "Name concrete amenities (parks, transit, schools by name) instead."),
    ("steering", r"\bsafe neighborhood\b", "review",
     "'Safe' as a neighborhood descriptor is flagged in fair-housing ad guidance.",
     "Cite specifics (doorman, secure entry) rather than characterizing the area."),
]

_COMPILED = [
    (cat, re.compile(pattern, re.IGNORECASE), sev, expl, sugg)
    for cat, pattern, sev, expl, sugg in _RULES
]


def check_text(text: str) -> GuardResult:
    """
    Screen one piece of outbound text. Returns a GuardResult whose
    `is_clean` is True only when zero findings matched.

    Deterministic, offline, and fast (~microseconds) — safe to call on
    every outbound message.
    """
    findings: List[Finding] = []
    if text:
        for category, regex, severity, explanation, suggestion in _COMPILED:
            m = regex.search(text)
            if m:
                findings.append(Finding(
                    category=category,
                    matched=m.group(0),
                    severity=severity,
                    explanation=explanation,
                    suggestion=suggestion,
                ))
    return GuardResult(is_clean=not findings, findings=findings)
