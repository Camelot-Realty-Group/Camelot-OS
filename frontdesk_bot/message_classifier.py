"""
message_classifier.py — Inbound Message Classifier
Camelot Property Management Services Corp / Front Desk Bot

Rule-based classifier for resident and owner messages across all channels.
No external ML dependencies — pure keyword matching and regex rules.

Author: Camelot OS
"""

import logging
import re
from datetime import date
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Heat season: emergency-escalate "no heat" Oct 1 – Apr 30
HEAT_SEASON_MONTHS = {10, 11, 12, 1, 2, 3, 4}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    category: str          # maintenance / rent / lease / amenity / emergency / complaint / other
    urgency: str           # emergency / urgent / routine
    sentiment: str         # positive / neutral / negative
    matched_keywords: list[str]
    confidence: str        # high / medium / low
    raw_text: str
    notes: str = ""


# ---------------------------------------------------------------------------
# Keyword rule tables
# ---------------------------------------------------------------------------

# --- Emergency keywords (Tier 1 — immediate response) ---
EMERGENCY_PATTERNS = [
    r"\bfire\b",
    r"\bflood(?:ing|ed)?\b",
    r"\bgas\s*leak\b",
    r"\bgas\s*smell\b",
    r"\bsmell(?:s|ing)?\s*(?:like\s*)?gas\b",
    r"\bsmoke\b",
    r"\bcarbon\s*monoxide\b",
    r"\bco\s*detector\b",
    r"\bco\s*alarm\b",
    r"\bburst\s*pipe\b",
    r"\bpipe\s*burst(?:ing)?\b",
    r"\bwater\s*(?:is\s*)?(?:pouring|gushing|everywhere|shooting)\b",
    r"\bbuilding\s*(?:is\s*)?(?:collapsing|collapsed|caving)\b",
    r"\bstructural\s*collapse\b",
    r"\belevator\s*(?:stuck|trapped|trapped\s+inside|not\s*moving)\b.*(?:person|people|someone|resident)",
    r"\bperson\s*trapped\b",
    r"\belectrical\s*fire\b",
    r"\bsparks\b.*\boutlet\b",
    r"\bno\s*heat\b",             # conditionally emergency in heat season
    r"\bheat\s*(?:is\s*)?(?:out|off|broken|not\s*working)\b",
    r"\bno\s*electricity\b",      # full outage
    r"\bpower\s*(?:is\s*)?(?:out|off|completely\s*out)\b",
]

# --- Urgent keywords (Tier 2 — 2-hour response) ---
URGENT_PATTERNS = [
    r"\bno\s*hot\s*water\b",
    r"\bhot\s*water\s*(?:is\s*)?(?:out|off|broken|not\s*working|not\s*coming\s*out)\b",
    r"\blockout\b",
    r"\blocked\s*out\b",
    r"\bcan'?t\s*(?:get\s*)?in(?:to)?\s*(?:my\s*)?(?:apartment|unit|home|place)\b",
    r"\blost\s*(?:my\s*)?(?:keys?|access)\b",
    r"\bbr?oken\s*elevator\b",
    r"\belevator\s*(?:is\s*)?(?:out|broken|not\s*working|not\s*operating)\b",
    r"\bbr?oken\s*window\b",
    r"\bwindow\s*(?:is\s*)?(?:broken|shattered|cracked|open)\b",
    r"\bpest\s*infest(?:ation|ed)\b",
    r"\brat\b",
    r"\brats?\b",
    r"\bmice\b",
    r"\bbedbug\b",
    r"\bbed\s*bug\b",
    r"\bwater\s*leak\b",
    r"\bleaking\b",
    r"\bdripping\s*from\s*(?:the\s*)?ceiling\b",
    r"\bceiling\s*(?:is\s*)?(?:leaking|dripping|wet)\b",
    r"\bno\s*(?:power|electricity)\s*in\s*(?:my\s*)?(?:apartment|unit|room)\b",
    r"\bpartial\s*(?:power|electricity)\s*(?:outage|loss)\b",
    r"\bsewer\s*(?:back|overflow|smell)\b",
    r"\btoilet\s*overflow(?:ing)?\b",
    r"\bfront\s*door\s*(?:broken|not\s*closing|not\s*locking|won'?t\s*close)\b",
    r"\bintercom\s*(?:broken|not\s*working)\b",
]

# --- Maintenance keywords (routine, Tier 3) ---
MAINTENANCE_PATTERNS = [
    r"\brepair\b", r"\bfixe?d?\b", r"\bbroken\b", r"\bappliance\b",
    r"\bkitchen\b", r"\bbathroom\b", r"\bstove\b", r"\boven\b",
    r"\brefrigerator\b", r"\bfridge\b", r"\bdishwasher\b",
    r"\bwasher\b", r"\bdryer\b", r"\bfaucet\b", r"\bsink\b",
    r"\btoilet\b", r"\bshower\b", r"\bdoor\b", r"\block\b",
    r"\blight\b", r"\bbulb\b", r"\boutlet\b", r"\bheating\b",
    r"\bair\s*condition(?:er|ing)?\b", r"\bac\b", r"\bfurnace\b",
    r"\bboiler\b", r"\bmaintenance\b", r"\bwork\s*order\b",
    r"\bplumbing\b", r"\belectrical\b", r"\bpaint(?:ing)?\b",
    r"\bmold\b", r"\bmildew\b", r"\bleak(?:ing)?\b",
]

# --- Rent keywords ---
RENT_PATTERNS = [
    r"\bpayment\b", r"\bpay\s*rent\b", r"\brent\s*(?:is\s*)?due\b",
    r"\brent\s*portal\b", r"\bonline\s*payment\b", r"\bcheck\b",
    r"\blate\s*fee\b", r"\blate\s*payment\b", r"\bbalance\b",
    r"\baccount\b", r"\boverpaid?\b", r"\bunderpaid?\b",
    r"\brent\s*amount\b", r"\bhow\s*(?:much|do)\s*(?:is\s*)?(?:my\s*)?rent\b",
    r"\bmonth(?:ly)?\s*rent\b", r"\bcharge\b", r"\binvoice\b",
]

# --- Lease keywords ---
LEASE_PATTERNS = [
    r"\blease\b", r"\brenewal\b", r"\bnew\s*lease\b", r"\blease\s*expir\b",
    r"\bmoving\s*out\b", r"\bvacate\b", r"\bnotice\s*to\s*vacate\b",
    r"\bmonth[\s-]to[\s-]month\b", r"\bterminate\b", r"\bmove[\s-]out\b",
    r"\bmove\s*in\b", r"\bsecurity\s*deposit\b", r"\bdeposit\b",
    r"\bkeys?\b", r"\bfob\b", r"\bunit\s*transfer\b", r"\bnon[\s-]renewal\b",
]

# --- Amenity keywords ---
AMENITY_PATTERNS = [
    r"\bpackage\b", r"\bdelivery\b", r"\bparcel\b", r"\bups\b",
    r"\bfedex\b", r"\bmail\b", r"\blaundry\b", r"\bwasher\b",
    r"\bgym\b", r"\bfitness\b", r"\brooftop\b", r"\bparking\b",
    r"\bstorage\b", r"\bcommon\s*area\b", r"\blobby\b",
    r"\bamenities\b", r"\bpool\b", r"\bcoworking\b",
]

# --- Complaint keywords ---
COMPLAINT_PATTERNS = [
    r"\bcomplaint\b", r"\bcomplaining\b", r"\bunhappy\b", r"\bdissatisfied\b",
    r"\bunacceptable\b", r"\bignored\b", r"\bnobody\s*responded\b",
    r"\bno\s*one\s*called?\b", r"\bterrible\b", r"\bhorrible\b",
    r"\bnoise\b", r"\bnoisy\b", r"\bneighbor\b", r"\bloud\b",
    r"\bsmoking\b", r"\bsmoke\s*smell\b", r"\bparty\b",
    r"\bpet\s*(?:noise|smell)\b", r"\bbarking\b",
]

# --- Positive sentiment indicators ---
POSITIVE_PATTERNS = [
    r"\bthank\b", r"\bthanks\b", r"\bappreciate\b", r"\bgreat\b",
    r"\bwonderful\b", r"\bexcellent\b", r"\bhappy\b", r"\bsatisfied\b",
    r"\bawesome\b", r"\bperfect\b", r"\bwell\s*done\b", r"\bfantastic\b",
]

# --- Negative sentiment indicators ---
NEGATIVE_PATTERNS = [
    r"\bangry\b", r"\bfurious\b", r"\bdisgusted?\b", r"\bunacceptable\b",
    r"\bterrible\b", r"\bhorrible\b", r"\bdisappointed?\b", r"\bfrustrated?\b",
    r"\bignored\b", r"\bunhappy\b", r"\bworse\b", r"\bawful\b",
    r"\bnever\s*again\b", r"\blawyer\b", r"\bsue\b", r"\blegal\s*action\b",
    r"\btenants?\s*rights?\b", r"\bdhcr\b", r"\bhpd\b", r"\b311\b",
]


# ---------------------------------------------------------------------------
# Core classifier
# ---------------------------------------------------------------------------

def classify_message(text: str) -> ClassificationResult:
    """
    Classify an inbound resident/owner message by category, urgency, and sentiment.

    Args:
        text: Raw message text (email body, SMS, chat message)

    Returns:
        ClassificationResult with category, urgency, sentiment, matched keywords,
        confidence level, and any notes.
    """
    if not text or not text.strip():
        return ClassificationResult(
            category="other",
            urgency="routine",
            sentiment="neutral",
            matched_keywords=[],
            confidence="low",
            raw_text=text or "",
            notes="Empty message",
        )

    text_lower = text.lower()
    today = date.today()
    in_heat_season = today.month in HEAT_SEASON_MONTHS

    matched_keywords: list[str] = []
    category = "other"
    urgency = "routine"
    sentiment = "neutral"
    notes = ""

    # --- Step 1: Check for emergency signals ---
    emergency_hits = _match_patterns(EMERGENCY_PATTERNS, text_lower)

    # Heat season logic: "no heat" / "heat is out" → emergency; else urgent
    heat_hits = [h for h in emergency_hits if "heat" in h or "heat" in h.lower()]
    non_heat_emergency = [h for h in emergency_hits if h not in heat_hits]

    if non_heat_emergency:
        category = "emergency"
        urgency = "emergency"
        matched_keywords.extend(non_heat_emergency)

    if heat_hits:
        if in_heat_season:
            category = "emergency"
            urgency = "emergency"
            notes = "Heat season active (Oct 1–Apr 30): heat/hot water complaint escalated to emergency."
        else:
            # Outside heat season — treat as urgent maintenance
            if urgency != "emergency":
                urgency = "urgent"
                if category == "other":
                    category = "maintenance"
        matched_keywords.extend(heat_hits)

    # --- Step 2: Check for urgent signals (only if not already emergency) ---
    if urgency != "emergency":
        urgent_hits = _match_patterns(URGENT_PATTERNS, text_lower)
        if urgent_hits:
            urgency = "urgent"
            matched_keywords.extend(urgent_hits)

    # --- Step 3: Determine category (if not already emergency) ---
    if category not in ("emergency",):
        category_scores: dict[str, int] = {
            "maintenance": len(_match_patterns(MAINTENANCE_PATTERNS, text_lower)),
            "rent":        len(_match_patterns(RENT_PATTERNS, text_lower)),
            "lease":       len(_match_patterns(LEASE_PATTERNS, text_lower)),
            "amenity":     len(_match_patterns(AMENITY_PATTERNS, text_lower)),
            "complaint":   len(_match_patterns(COMPLAINT_PATTERNS, text_lower)),
        }

        best_category = max(category_scores, key=lambda k: category_scores[k])
        best_score = category_scores[best_category]

        if best_score > 0:
            category = best_category
            # Add top matched keywords for the winning category
            cat_patterns = {
                "maintenance": MAINTENANCE_PATTERNS,
                "rent":        RENT_PATTERNS,
                "lease":       LEASE_PATTERNS,
                "amenity":     AMENITY_PATTERNS,
                "complaint":   COMPLAINT_PATTERNS,
            }[best_category]
            matched_keywords.extend(_match_patterns(cat_patterns, text_lower)[:5])

        # Complaint + maintenance → maintenance takes priority
        if category_scores.get("complaint", 0) > 0 and category_scores.get("maintenance", 0) > 0:
            category = "maintenance"

    # --- Step 4: Sentiment ---
    pos_hits = _match_patterns(POSITIVE_PATTERNS, text_lower)
    neg_hits = _match_patterns(NEGATIVE_PATTERNS, text_lower)

    if pos_hits and not neg_hits:
        sentiment = "positive"
    elif neg_hits and len(neg_hits) >= len(pos_hits):
        sentiment = "negative"
    else:
        sentiment = "neutral"

    # --- Step 5: Confidence ---
    total_keywords = len(set(matched_keywords))
    if urgency == "emergency":
        confidence = "high"
    elif total_keywords >= 3:
        confidence = "high"
    elif total_keywords >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    # --- Special rule: legal threat escalation ---
    legal_hits = _match_patterns([r"\blawyer\b", r"\battorney\b", r"\bsue\b", r"\blegal\s*action\b"], text_lower)
    if legal_hits:
        if notes:
            notes += " "
        notes += "LEGAL THREAT DETECTED: Route to property manager immediately."
        if sentiment != "negative":
            sentiment = "negative"

    logger.info(
        f"Classified message: category={category}, urgency={urgency}, "
        f"sentiment={sentiment}, keywords={matched_keywords[:5]}, confidence={confidence}"
    )

    return ClassificationResult(
        category=category,
        urgency=urgency,
        sentiment=sentiment,
        matched_keywords=list(set(matched_keywords))[:10],
        confidence=confidence,
        raw_text=text,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _match_patterns(patterns: list[str], text: str) -> list[str]:
    """Return list of pattern strings that match the text."""
    hits = []
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            # Extract the actual matched text snippet for readability
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                hits.append(m.group(0))
    return hits


def classify_batch(messages: list[str]) -> list[ClassificationResult]:
    """Classify multiple messages."""
    return [classify_message(m) for m in messages]


def describe_classification(result: ClassificationResult) -> str:
    """Human-readable description of a classification result."""
    urgency_labels = {
        "emergency": "🚨 EMERGENCY — Respond immediately",
        "urgent": "⚠️  URGENT — Respond within 2 hours",
        "routine": "ℹ️  ROUTINE — Respond within 24 hours",
    }
    sentiment_labels = {
        "positive": "😊 Positive",
        "negative": "😤 Negative",
        "neutral": "😐 Neutral",
    }
    lines = [
        f"Category:  {result.category.upper()}",
        f"Urgency:   {urgency_labels.get(result.urgency, result.urgency)}",
        f"Sentiment: {sentiment_labels.get(result.sentiment, result.sentiment)}",
        f"Keywords:  {', '.join(result.matched_keywords) or 'none'}",
        f"Confidence: {result.confidence.upper()}",
    ]
    if result.notes:
        lines.append(f"Notes:     {result.notes}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    test_messages = [
        "I smell gas in my apartment! Coming from the kitchen.",
        "My heat isn't working and it's freezing in here.",
        "When is my rent due this month? How do I pay online?",
        "The elevator is out of service again. This is the third time this month.",
        "I'm locked out of my apartment, lost my keys.",
        "Can you fix the dripping faucet in my bathroom? Not urgent but annoying.",
        "Thank you so much for the quick response on the repairs!",
        "I'm furious. Nobody has responded to my work order in a week. I'm calling my lawyer.",
    ]

    for msg in test_messages:
        print(f"\nMessage: {msg[:80]}...")
        result = classify_message(msg)
        print(describe_classification(result))
        print("─" * 50)
