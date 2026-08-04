"""
router.py — Camelot OS Intent Classifier & Bot Router

Classifies natural-language user input into structured routing decisions:
    bot_name, action, params

Uses rule-based keyword/pattern matching — no external ML dependencies.
Designed for speed and reliability over coverage breadth. Add new patterns
in the INTENT_PATTERNS table below.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routing Decision Data Model
# ---------------------------------------------------------------------------

@dataclass
class RoutingDecision:
    """
    Represents the output of the intent classifier.

    Attributes:
        bot_name:    The target specialist bot (e.g., "scout", "compliance")
        action:      The specific capability/action to invoke (e.g., "search_leads")
        params:      Extracted parameters from the user's input
        confidence:  0.0–1.0 confidence score for this routing decision
        rationale:   Human-readable explanation of why this routing was chosen
        pipeline:    Optional named pipeline to execute instead of a single bot action
        raw_input:   The original user input, preserved for logging
    """
    bot_name: str
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    rationale: str = ""
    pipeline: Optional[str] = None
    raw_input: str = ""


@dataclass
class RouterError:
    """Returned when intent cannot be classified."""
    message: str
    suggestions: List[str] = field(default_factory=list)
    raw_input: str = ""


# ---------------------------------------------------------------------------
# Parameter Extraction Helpers
# ---------------------------------------------------------------------------

# US state names and abbreviations for region extraction
_US_STATES = {
    "connecticut": "CT", "ct": "CT",
    "new jersey": "NJ", "nj": "NJ",
    "new york": "NY", "ny": "NY",
    "westchester": "Westchester, NY",
    "queens": "Queens, NY",
    "brooklyn": "Brooklyn, NY",
    "bronx": "Bronx, NY",
    "manhattan": "Manhattan, NY",
    "staten island": "Staten Island, NY",
    "long island": "Long Island, NY",
    "nassau": "Nassau County, NY",
    "suffolk": "Suffolk County, NY",
    "pennsylvania": "PA", "pa": "PA",
    "massachusetts": "MA", "ma": "MA",
    "rhode island": "RI", "ri": "RI",
    "florida": "FL", "fl": "FL",
    "california": "CA", "ca": "CA",
    "texas": "TX", "tx": "TX",
}


def _extract_address(text: str) -> Optional[str]:
    """
    Extract a street address from user input.

    Matches patterns like:
        "123 Main Street", "456 Park Ave", "789 Eastern Pkwy, Brooklyn"
    """
    patterns = [
        # Number + full street name with type
        r'\b(\d+\s+[A-Za-z0-9\s]+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|'
        r'Drive|Dr|Lane|Ln|Place|Pl|Court|Ct|Parkway|Pkwy|Way|Terrace|Ter|'
        r'Highway|Hwy|Broadway|Park|Plaza)[,\s]?(?:[A-Za-z\s,]+)?)\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            addr = match.group(1).strip().rstrip(',')
            logger.debug("Extracted address: '%s'", addr)
            return addr
    return None


def _extract_region(text: str) -> Optional[str]:
    """Extract US state or NYC borough/county from user input."""
    text_lower = text.lower()
    for key, value in _US_STATES.items():
        if re.search(r'\b' + re.escape(key) + r'\b', text_lower):
            return value
    return None


def _extract_company_name(text: str) -> Optional[str]:
    """
    Heuristically extract a company/entity name from user input.

    Looks for patterns like "for [Company Name]", "at [Company Name]",
    "to [Company Name]", or capitalized multi-word proper nouns.
    """
    # Explicit prepositional phrases
    prep_patterns = [
        r'(?:for|to|at|about|research(?:ing)?|outreach(?:\s+to)?)\s+([A-Z][A-Za-z\s&\-\']+(?:LLC|Inc|Corp|Management|Properties|Realty|Group|Partners|Associates|Co\.?)?)',
        r'([A-Z][A-Za-z\s&\-\']+(?:LLC|Inc|Corp|Management|Properties|Realty|Group|Partners|Associates))',
    ]
    for pattern in prep_patterns:
        match = re.search(pattern, text)
        if match:
            company = match.group(1).strip()
            # Filter out bot/system keywords
            if company.lower() not in ('camelot', 'the', 'a', 'an'):
                logger.debug("Extracted company: '%s'", company)
                return company
    return None


def _extract_price(text: str) -> Optional[float]:
    """Extract a dollar price from user input (e.g., '$2.5M', '$500,000')."""
    # Millions shorthand: $2.5M, $3M, 2.5 million
    m_match = re.search(r'\$?([\d,.]+)\s*[Mm](?:illion)?', text)
    if m_match:
        val = float(m_match.group(1).replace(',', '')) * 1_000_000
        return val
    # Explicit dollar amount: $500,000 or $500000
    d_match = re.search(r'\$([\d,]+)', text)
    if d_match:
        return float(d_match.group(1).replace(',', ''))
    return None


def _extract_unit(text: str) -> Optional[str]:
    """Extract a unit/apartment identifier (e.g., '4B', 'unit 12', 'apt 3A')."""
    match = re.search(
        r'\b(?:unit|apt|apartment|suite|#)?\s*([0-9]{1,4}[A-Za-z]?)\b',
        text, re.IGNORECASE
    )
    if match:
        return match.group(1).upper()
    return None


def _extract_report_period(text: str) -> str:
    """Extract report period keyword (weekly, monthly, quarterly, daily)."""
    for period in ('weekly', 'monthly', 'quarterly', 'daily', 'annual', 'yearly'):
        if period in text.lower():
            return period
    return 'weekly'


def _extract_property_type(text: str) -> Optional[str]:
    """Extract property type from user input."""
    types = {
        'multifamily': 'multifamily',
        'multi-family': 'multifamily',
        'single family': 'single_family',
        'commercial': 'commercial',
        'mixed use': 'mixed_use',
        'mixed-use': 'mixed_use',
        'condo': 'condo',
        'co-op': 'coop',
        'coop': 'coop',
    }
    text_lower = text.lower()
    for key, value in types.items():
        if key in text_lower:
            return value
    return 'multifamily'  # Default for PM context


# ---------------------------------------------------------------------------
# Intent Pattern Definitions
# ---------------------------------------------------------------------------
#
# Structure: List of (priority, pattern_list, bot, action, param_extractor)
#
# priority:         lower = higher priority (checked first)
# pattern_list:     list of regex patterns; any match triggers this rule
# bot:              bot name to route to
# action:           capability/action to invoke
# param_extractor:  callable(text) → dict of params, or None

IntentPattern = Tuple[int, List[str], str, str, Optional[Any]]

INTENT_PATTERNS: List[IntentPattern] = [

    # -------------------------------------------------------------------
    # PIPELINE TRIGGERS — highest priority, matched first
    # -------------------------------------------------------------------
    (
        1,
        [
            r'(full|complete)\s+(due\s+diligence|dd|diligence)',
            r'dd\s+package',
            r'acquisition\s+package\s+for',
        ],
        "deal", "prospect_and_outreach",
        lambda t: {
            "company": _extract_company_name(t),
            "address": _extract_address(t),
            "pipeline": "new_acquisition_dd",
        }
    ),
    (
        2,
        [
            r'(research\s+and\s+outreach|prospect\s+and\s+outreach|find\s+and\s+contact)',
            r'(reach\s+out\s+to|contact)\s+[A-Z]',
        ],
        "deal", "prospect_and_outreach",
        lambda t: {
            "company": _extract_company_name(t),
            "pipeline": "deal_outreach",
        }
    ),
    (
        3,
        [
            r'(build|create|generate)\s+.*(lead\s+list|leads).*\s+(add|push|sync)\s+.*(hubspot|crm)',
            r'find\s+.*companies.*add.*hubspot',
        ],
        "scout", "build_lead_list",
        lambda t: {
            "region": _extract_region(t),
            "pipeline": "lead_to_crm",
        }
    ),
    (
        4,
        [
            r'(full|complete)\s+(compliance\s+audit|audit)',
            # Negative lookahead keeps "audit this property's expenses" out of the
            # compliance pipeline — that is a costbeat request, not a violations pull.
            r'(?!.*(?:budget|expense|spend|operating\s+cost))(audit|inspect)\s+.*property',
            r'compliance\s+check\s+on',
        ],
        "compliance", "full_audit",
        lambda t: {
            "address": _extract_address(t),
            "pipeline": "property_audit",
        }
    ),
    (
        5,
        [
            r'(daily|weekly)\s+ops\s+(run|sequence|routine)',
            r'run\s+the\s+(morning|daily|weekly)\s+routine',
        ],
        "report", "send_weekly_kpi",
        lambda t: {
            "pipeline": "weekly_ops_rhythm",
        }
    ),

    # -------------------------------------------------------------------
    # SCOUT BOT — Lead generation & property intelligence
    # -------------------------------------------------------------------
    (
        10,
        [
            r'find\s+.*(property\s+management|pm|prop\s+mgmt)\s+companies',
            r'search\s+.*(leads?|prospects?|targets?)',
            r'(lead\s+gen|lead\s+generation)\s+for',
            r'(pm|property\s+management)\s+companies\s+in',
            r'who\s+(manages?|owns?)\s+properties\s+in',
            r'acquisition\s+targets?\s+in',
        ],
        "scout", "search_leads",
        lambda t: {
            "region": _extract_region(t),
            "property_type": _extract_property_type(t),
        }
    ),
    (
        11,
        [
            r'enrich\s+(lead|contact|company)',
            r'(get|pull|fetch)\s+more\s+(data|info|details)\s+on',
            r'deep\s+dive\s+on\s+[A-Z]',
        ],
        "scout", "enrich_lead",
        lambda t: {
            "company": _extract_company_name(t),
            "address": _extract_address(t),
        }
    ),
    (
        12,
        [
            r'push\s+to\s+(hubspot|crm)',
            r'add\s+(lead|contact|company)\s+to\s+(hubspot|crm)',
            r'sync\s+(lead|contact)\s+to\s+hubspot',
            r'create\s+(hubspot|crm)\s+(contact|deal|record)',
        ],
        "scout", "push_to_hubspot",
        lambda t: {
            "company": _extract_company_name(t),
        }
    ),
    (
        13,
        [
            r'(who\s+owns?|ownership\s+of|check\s+ownership)',
            r'beneficial\s+owner',
            r'acris\s+(lookup|search|check)',
        ],
        "scout", "check_ownership",
        lambda t: {
            "address": _extract_address(t),
            "company": _extract_company_name(t),
        }
    ),
    (
        14,
        [
            r'(property\s+intel|intel\s+on|intelligence\s+on)',
            r'(what\s+do\s+we\s+know\s+about|tell\s+me\s+about)\s+\d+',
        ],
        "scout", "property_intel",
        lambda t: {
            "address": _extract_address(t),
        }
    ),

    # -------------------------------------------------------------------
    # BROKER BOT — Transaction execution & deal documentation
    # -------------------------------------------------------------------
    (
        20,
        [
            r'(draft|generate|create|write)\s+(an?\s+)?loi',
            r'letter\s+of\s+intent',
            r'make\s+(an?\s+)?offer\s+(on|for)',
        ],
        "broker", "generate_loi",
        lambda t: {
            "address": _extract_address(t),
            "price": _extract_price(t),
            "company": _extract_company_name(t),
        }
    ),
    (
        21,
        [
            r'(draft|generate|create)\s+(a\s+)?psa',
            r'purchase\s+(and\s+sale|agreement|contract)',
            r'sale\s+agreement\s+for',
        ],
        "broker", "generate_psa",
        lambda t: {
            "address": _extract_address(t),
            "price": _extract_price(t),
        }
    ),
    (
        22,
        [
            r'(build|create|generate|run)\s+(a\s+)?proforma',
            r'pro[- ]forma\s+for',
            r'(financial\s+model|underwriting)\s+for',
        ],
        "broker", "build_proforma",
        lambda t: {
            "address": _extract_address(t),
            "company": _extract_company_name(t),
        }
    ),
    (
        23,
        [
            r'(cap\s+rate|capitalization\s+rate)\s+(analysis|for|on)',
            r'(noi|net\s+operating\s+income)\s+(calc|calculate|for)',
            r'analyze\s+(the\s+)?financials\s+(of|for)',
        ],
        "broker", "analyze_cap_rate",
        lambda t: {
            "address": _extract_address(t),
            "price": _extract_price(t),
        }
    ),
    (
        24,
        [
            r'(draft|generate|create)\s+(an?\s+)?nda',
            r'non[- ]disclosure\s+agreement',
            r'confidentiality\s+agreement',
        ],
        "broker", "draft_nda",
        lambda t: {
            "company": _extract_company_name(t),
        }
    ),
    (
        25,
        [
            r'(deal\s+memo|deal\s+summary|investment\s+memo)',
            r'executive\s+(summary|memo)\s+(for|on)',
        ],
        "broker", "generate_deal_memo",
        lambda t: {
            "address": _extract_address(t),
            "company": _extract_company_name(t),
        }
    ),

    # -------------------------------------------------------------------
    # COMPLIANCE BOT — NYC regulatory & violation management
    # -------------------------------------------------------------------
    (
        30,
        [
            r'(check|look\s+up|pull|get)\s+(hpd|violations?)',
            r'(hpd|housing\s+violations?)\s+(at|for|on)',
            r'violations?\s+(at|for)\s+\d+',
        ],
        "compliance", "check_hpd",
        lambda t: {
            "address": _extract_address(t),
        }
    ),
    (
        31,
        [
            r'(check|look\s+up|pull)\s+(dob|permits?|open\s+permits?)',
            r'(dob|department\s+of\s+buildings?)\s+(violations?|permits?)',
            r'open\s+permits?\s+(at|for|on)',
        ],
        "compliance", "check_dob",
        lambda t: {
            "address": _extract_address(t),
        }
    ),
    (
        32,
        [
            r'(local\s+law\s+97|ll97|ll\s*97)',
            r'carbon\s+(emissions?|exposure|liability)',
            r'emissions?\s+(compliance|report|check)',
        ],
        "compliance", "check_ll97",
        lambda t: {
            "address": _extract_address(t),
        }
    ),
    (
        33,
        [
            r'(ecb|environmental\s+control\s+board)\s+(violations?|check|fines?)',
        ],
        "compliance", "check_ecb",
        lambda t: {
            "address": _extract_address(t),
        }
    ),
    (
        34,
        [
            r'(elevator|lift)\s+(inspection|cert|certification|status)',
            r'check\s+(the\s+)?elevator\s+(cert|record)',
        ],
        "compliance", "check_elevator",
        lambda t: {
            "address": _extract_address(t),
        }
    ),
    (
        35,
        [
            r'(boiler|boilers?)\s+(inspection|reg|registration|cert)',
            r'check\s+(the\s+)?boiler\s+(cert|record|registration)',
        ],
        "compliance", "check_boiler",
        lambda t: {
            "address": _extract_address(t),
        }
    ),
    (
        36,
        [
            # Word boundaries matter here: without them "rating" matches inside
            # "operating", pulling every operating-budget request into compliance.
            r'(compliance\s+)?\b(score|scorecard|grade|rating)\b',
            r'what\'?s?\s+(the\s+)?compliance\s+(status|score)',
            r'(remediation|violation)\s+plan',
        ],
        "compliance", "property_scorecard",
        lambda t: {
            "address": _extract_address(t),
        }
    ),

    # -------------------------------------------------------------------
    # FRONTDESK BOT — Tenant operations & maintenance
    # -------------------------------------------------------------------
    (
        40,
        [
            r'(create|open|submit|file|log)\s+(a\s+)?(maintenance|repair|work)\s+(ticket|order|request)',
            r'tenant\s+(in|at)\s+unit\s+\w+\s+(says?|reports?|has)',
            r'(leak|heat|hot\s+water|hvac|plumbing|electrical|repair)\s+(issue|problem|out|broken)',
            r'unit\s+\w+\s+(has|reports?|says?|need)',
        ],
        "frontdesk", "create_ticket",
        lambda t: {
            "unit": _extract_unit(t),
            "address": _extract_address(t),
            "description": t,
        }
    ),
    (
        41,
        [
            r'(dispatch|send|schedule)\s+(a\s+)?vendor',
            r'(call|contact)\s+(plumber|electrician|handyman|super|contractor)',
            r'vendor\s+dispatch\s+for',
        ],
        "frontdesk", "vendor_dispatch",
        lambda t: {
            "unit": _extract_unit(t),
            "address": _extract_address(t),
            "description": t,
        }
    ),
    (
        42,
        [
            r'(emergency|urgent|fire|flood|gas\s+leak|burst\s+pipe)',
            r'escalate\s+(this\s+)?(immediately|now|urgent)',
            r'(call|page)\s+(on[- ]call|emergency)',
        ],
        "frontdesk", "emergency_escalate",
        lambda t: {
            "unit": _extract_unit(t),
            "address": _extract_address(t),
            "description": t,
            "priority": "emergency",
        }
    ),
    (
        43,
        [
            r'(message|text|email|contact)\s+(tenant|resident)',
            r'send\s+.*(tenant|resident)\s+(message|notice|notification)',
            r'notify\s+(tenant|resident)\s+in\s+unit',
        ],
        "frontdesk", "tenant_message",
        lambda t: {
            "unit": _extract_unit(t),
            "address": _extract_address(t),
            "message": t,
        }
    ),
    (
        44,
        [
            r'(ticket|work\s+order)\s+status',
            r'(what\'?s?\s+the\s+)?status\s+(of\s+)?(ticket|work\s+order)',
            r'open\s+(tickets?|work\s+orders?)',
        ],
        "frontdesk", "ticket_status",
        lambda t: {
            "address": _extract_address(t),
            "unit": _extract_unit(t),
        }
    ),

    # -------------------------------------------------------------------
    # REPORT BOT — Financial reporting & KPI analytics
    # -------------------------------------------------------------------
    (
        50,
        [
            r'(send|generate|create|run)\s+(the\s+)?(weekly|week\'?s?)\s+kpi',
            r'weekly\s+(report|summary|update|kpi)',
            r'(kpi|performance)\s+report\s+for\s+(this\s+)?week',
        ],
        "report", "send_weekly_kpi",
        lambda t: {}
    ),
    (
        51,
        [
            r'(monthly|month\'?s?)\s+(financials?|p&l|profit|report)',
            r'(noi|net\s+operating\s+income)\s+(report|summary)',
            r'monthly\s+(report|summary|kpis?)',
        ],
        "report", "monthly_financials",
        lambda t: {}
    ),
    (
        52,
        [
            r'(occupancy|vacancy)\s+(dashboard|report|rate|summary)',
            r'(what\'?s?\s+(our|the)\s+)?occupancy',
            r'portfolio\s+occupancy',
        ],
        "report", "occupancy_dashboard",
        lambda t: {}
    ),
    (
        53,
        [
            r'(collections?|rent\s+collection)\s+(report|status|summary)',
            r'(delinquent|late|past\s+due)\s+(tenants?|rent|payments?)',
            r'who\s+(hasn\'?t?\s+paid|owes\s+rent)',
        ],
        "report", "collections_report",
        lambda t: {}
    ),
    (
        54,
        [
            r'(acquisition|deal)\s+(pipeline|funnel)\s+(report|status|update)',
            r'(where\s+(are\s+)?we\s+(in\s+)?the\s+pipeline|pipeline\s+update)',
        ],
        "report", "acquisition_pipeline",
        lambda t: {}
    ),
    (
        55,
        [
            r'(investor|LP)\s+(memo|update|report|summary)',
            r'(write|draft|create)\s+(an?\s+)?investor\s+(memo|update)',
        ],
        "report", "investor_memo",
        lambda t: {}
    ),

    # -------------------------------------------------------------------
    # INDEX BOT — Document intelligence & file organization
    # -------------------------------------------------------------------
    (
        60,
        [
            r'(organize|sort|clean\s+up)\s+(google\s+drive|drive|files?|folder)',
            r'(run|start)\s+(the\s+)?indexer',
            r'index\s+(the\s+)?(drive|documents?|files?)',
            r'(new\s+files?|organize)\s+(in\s+)?(google\s+)?drive',
        ],
        "index", "run_indexer",
        lambda t: {}
    ),
    (
        61,
        [
            r'(organize|clean\s+up)\s+(the\s+)?folder\s+for',
            r'(sort|organize)\s+files?\s+(for|in)',
        ],
        "index", "organize_folder",
        lambda t: {
            "address": _extract_address(t),
            "company": _extract_company_name(t),
        }
    ),
    (
        62,
        [
            r'(abstract|extract\s+data\s+from)\s+(the\s+)?lease',
            r'lease\s+(abstraction|extraction|data)',
            r'(pull|get)\s+(key\s+terms?\s+from|data\s+from)\s+(the\s+)?lease',
        ],
        "index", "extract_lease_data",
        lambda t: {
            "address": _extract_address(t),
        }
    ),
    (
        63,
        [
            r'(search|find|look\s+up)\s+(in\s+)?(the\s+)?documents?',
            r'(find|where\s+is)\s+(the\s+)?(lease|contract|agreement|psa)\s+for',
        ],
        "index", "search_documents",
        lambda t: {
            "query": t,
            "address": _extract_address(t),
        }
    ),
    (
        64,
        [
            r'(expiring|expired|renewal|renewing)\s+(documents?|leases?|contracts?|certs?)',
            r'(what|which)\s+documents?\s+(expire|are\s+up)',
            r'(flag|find)\s+expiring',
        ],
        "index", "flag_expiring",
        lambda t: {}
    ),

    # -------------------------------------------------------------------
    # DEAL BOT — Acquisition outreach & relationship management
    # -------------------------------------------------------------------
    (
        70,
        [
            r'(research|deep\s+dive\s+on|tell\s+me\s+about)\s+[A-Z][A-Za-z\s]+(?:LLC|Inc|Corp|Management|Properties|Realty|Group)',
            r'what\s+do\s+we\s+know\s+about\s+[A-Z]',
            r'(background|intel)\s+on\s+[A-Z]',
        ],
        "deal", "research_target",
        lambda t: {
            "company": _extract_company_name(t),
        }
    ),
    (
        71,
        [
            r'(build|create|generate)\s+(a\s+)?battlecard',
            r'competitive\s+(analysis|profile|card)\s+for',
        ],
        "deal", "build_battlecard",
        lambda t: {
            "company": _extract_company_name(t),
        }
    ),
    (
        72,
        [
            r'(draft|write|create)\s+(an?\s+)?(outreach|cold)\s+email',
            r'(write|draft)\s+(an?\s+)?(email|message)\s+to',
            r'outreach\s+email\s+(for|to)',
        ],
        "deal", "draft_email",
        lambda t: {
            "company": _extract_company_name(t),
        }
    ),
    (
        73,
        [
            r'(log|record|track)\s+outreach',
            r'(update|mark)\s+(deal\s+)?stage',
            r'(log|note|add)\s+(this\s+)?(to\s+)?(hubspot|crm)',
        ],
        "deal", "log_outreach",
        lambda t: {
            "company": _extract_company_name(t),
        }
    ),
    (
        74,
        [
            r'(follow[- ]up|follow\s+up)\s+(sequence|schedule|cadence)',
            r'(set\s+up|create)\s+follow[- ]up',
            r'(next\s+steps?|follow[- ]up\s+plan)\s+for',
        ],
        "deal", "follow_up_sequence",
        lambda t: {
            "company": _extract_company_name(t),
        }
    ),

    # -------------------------------------------------------------------
    # CONCIERGE BOT — Document template catalog, download & auto-fill
    # -------------------------------------------------------------------
    (
        80,
        [
            r'(fill\s+(in|out)|auto[- ]?fill|generate)\s+(a\s+|the\s+)?(work\s+order|.*\s+(form|template))',
            r'(fill\s+(in|out))\s+.*\s+(request|form)',
        ],
        "concierge", "generate_document",
        lambda t: {"query": t}
    ),
    (
        81,
        [
            r'(download|get|send\s+me)\s+.*(agreement|form|template|cover\s+sheet|proxy|checklist|application)',
            r'(camelot|management)\s+(agreement|template)\s+for',
        ],
        "concierge", "download_template",
        lambda t: {"query": t}
    ),
    (
        82,
        [
            r'(what\s+form|which\s+(form|template|document)|do\s+we\s+have\s+.*(form|template))',
            r'(find|look\s+up|search\s+for)\s+.*(template|form|document)',
            r'(document|template)\s+(library|catalog|list)',
        ],
        "concierge", "list_templates",
        lambda t: {"query": t}
    ),

    # -------------------------------------------------------------------
    # COSTBEAT BOT — Operating-budget cost-beat analysis & fee proposal
    # -------------------------------------------------------------------
    (
        90,
        [
            r'(list|show\s+me|pull\s+up)\s+.*(cost[- ]?beat|budget|savings)\s+analys[ei]s',
            r'(cost[- ]?beat|savings)\s+analys[ei]s\s+(list|history)',
        ],
        "costbeat", "list_analyses",
        lambda t: {"query": t}
    ),
    (
        91,
        [
            r'cost[- ]?beat',
            r'(beat|undercut)\s+.*(budget|expenses?|operating\s+costs?)',
        ],
        "costbeat", "analyze_budget",
        lambda t: {"query": t, "address": _extract_address(t)}
    ),
    (
        92,
        [
            r'(review|analyz|analys|check|look\s+at)\w*\s+(this\s+|the\s+|their\s+)?(operating\s+)?budget',
            r'(budget|expenses?)\s+(review|analysis|comparison)',
            r'(where|how)\s+can\s+(we|they)\s+save\s+.*(money|expenses?|budget|operating)',
        ],
        "costbeat", "analyze_budget",
        lambda t: {"query": t, "address": _extract_address(t)}
    ),
    (
        93,
        [
            r'(audit|review)\s+(this\s+|the\s+|their\s+)?(building|property)?\s*\'?s?\s*(operating\s+)?(expenses?|spend|costs?)',
            r'(are|is)\s+(this|their|the)\s+.*(expenses?|costs?|budget)\s+.*(high|too\s+high|out\s+of\s+line)',
            r'(expense|cost)\s+(comparison|benchmark\w*)\s+(against|vs\.?|versus)',
        ],
        "costbeat", "analyze_budget",
        lambda t: {"query": t, "address": _extract_address(t)}
    ),
    (
        94,
        [
            r'(compare|benchmark)\s+.*(against|vs\.?|versus|to)\s+.*(our|camelot|portfolio|comparable)',
            r'(what|how much)\s+(would|should)\s+.*(cost|run)\s+.*(under|with)\s+camelot',
        ],
        "costbeat", "analyze_budget",
        lambda t: {"query": t, "address": _extract_address(t)}
    ),
    (
        95,
        [
            r'(increase|raise|justify|uplift)\s+.*(management\s+fee|mgmt\s+fee)',
            r'management\s+fee\s+(increase|uplift|bump)',
            r'(one[- ]?time|cost[- ]?recovery)\s+fee',
            r'(savings[- ]?capture|fee\s+proposal)',
        ],
        "costbeat", "build_fee_proposal",
        lambda t: {"query": t}
    ),

    # -------------------------------------------------------------------
    # PERSEUS — Periodic management-report variance & per-period proposal
    #
    # Sits below CostBeat's 90–99 band on purpose. CostBeat owns forward-
    # looking budget questions ("review this budget"); Perseus owns
    # backward-looking actuals ("how did Q2 land"). The discriminator is
    # tense and the presence of a period, so these patterns all require a
    # period word, a variance word, or "actual".
    # -------------------------------------------------------------------
    (
        100,
        [
            r'(list|show\s+me|pull\s+up)\s+.*(variance|quarterly)\s+report',
            r'(variance|quarterly)\s+report\s+(list|history)',
            r'(prior|previous|last)\s+(quarters?|periods?)\s+.*(variance|report)',
        ],
        "perseus", "list_period_reports",
        lambda t: {"query": t}
    ),
    (
        101,
        [
            r'(quarterly|monthly|period)\s+variance',
            r'management\s+report\s+variance',
            r'variance\s+(report|analysis)\s+for',
        ],
        "perseus", "analyze_period_report",
        lambda t: {"query": t, "address": _extract_address(t)}
    ),
    (
        102,
        [
            r'actuals?\s+(vs\.?|versus|against|to)\s+budget',
            r'budget\s+(vs\.?|versus)\s+actuals?',
            r'(how|where)\s+(are|did)\s+(we|they|it)\s+.*track\w*\s+(against|vs\.?|to)\s+(the\s+)?budget',
            r'(are|is)\s+(we|they)\s+(over|under)\s+budget',
        ],
        "perseus", "budget_variance",
        lambda t: {"query": t, "address": _extract_address(t)}
    ),
    (
        103,
        [
            r'\bmds\s+(report|statement|export)',
            r'(upload|analyz|review)\w*\s+(the\s+|this\s+)?(q[1-4]|quarter\w*|monthly)\s+.*(actuals?|report|numbers)',
            r'(how\s+did|where\s+did)\s+(q[1-4]|the\s+quarter|the\s+month)\s+.*(land|come\s+in|end\s+up)',
        ],
        "perseus", "analyze_period_report",
        lambda t: {"query": t, "address": _extract_address(t)}
    ),
    (
        104,
        [
            r'(this|last|the)\s+(quarter|period)\'?s?\s+(savings|fee)\s+(proposal|opportunity)',
            r'(per[- ]?period|quarterly)\s+fee\s+proposal',
        ],
        "perseus", "build_fee_proposal",
        lambda t: {"query": t}
    ),
]


# ---------------------------------------------------------------------------
# Core Classification Function
# ---------------------------------------------------------------------------

def classify_intent(user_input: str) -> RoutingDecision | RouterError:
    """
    Classify natural-language user input into a structured routing decision.

    Args:
        user_input: Raw text from the Camelot team member.

    Returns:
        RoutingDecision if a match is found, RouterError if unclassifiable.

    Examples:
        >>> classify_intent("Find property management companies in Connecticut")
        RoutingDecision(bot_name='scout', action='search_leads', params={'region': 'CT', ...})

        >>> classify_intent("Check violations for 123 Main Street")
        RoutingDecision(bot_name='compliance', action='check_hpd', params={'address': '123 Main Street'})

        >>> classify_intent("Draft an LOI for 456 Park Ave at $2.5M")
        RoutingDecision(bot_name='broker', action='generate_loi', params={'address': '456 Park Ave', 'price': 2500000.0})
    """
    if not user_input or not user_input.strip():
        return RouterError(
            message="Empty input received.",
            suggestions=["Try: 'Find PM companies in Queens'", "Try: 'Check violations for 123 Main St'"],
            raw_input=user_input,
        )

    normalized = user_input.strip()
    logger.info("Classifying intent for: '%s'", normalized[:80])

    # Sort by priority (ascending) to check high-priority rules first
    sorted_patterns = sorted(INTENT_PATTERNS, key=lambda x: x[0])

    for priority, patterns, bot_name, action, param_extractor in sorted_patterns:
        for pattern in patterns:
            if re.search(pattern, normalized, re.IGNORECASE):
                params = {}
                if param_extractor:
                    try:
                        params = param_extractor(normalized)
                        # Remove None values
                        params = {k: v for k, v in params.items() if v is not None}
                    except Exception as exc:
                        logger.warning("Param extraction failed for '%s': %s", pattern, exc)

                pipeline = params.pop("pipeline", None)

                logger.info(
                    "Matched → bot=%s action=%s params=%s pipeline=%s (priority=%d)",
                    bot_name, action, params, pipeline, priority
                )

                return RoutingDecision(
                    bot_name=bot_name,
                    action=action,
                    params=params,
                    confidence=_compute_confidence(priority),
                    rationale=_build_rationale(bot_name, action, params, pattern),
                    pipeline=pipeline,
                    raw_input=normalized,
                )

    # No match found
    logger.warning("No intent match for: '%s'", normalized[:80])
    return RouterError(
        message=f"Could not determine intent for: \"{normalized[:100]}\"",
        suggestions=_suggest_alternatives(normalized),
        raw_input=normalized,
    )


def _compute_confidence(priority: int) -> float:
    """
    Map priority tier to a confidence score.

    Priority 1–5  → pipeline triggers   → 0.98
    Priority 10–19 → scout patterns      → 0.92
    Priority 20–29 → broker patterns     → 0.92
    Priority 30–39 → compliance patterns → 0.95 (more specific)
    Priority 40–49 → frontdesk patterns  → 0.88
    Priority 50–59 → report patterns     → 0.90
    Priority 60–69 → index patterns      → 0.85
    Priority 70–79 → deal patterns       → 0.88
    Priority 80–89 → concierge patterns  → 0.87
    Priority 90–99 → costbeat patterns   → 0.90
    Priority 100–109 → perseus patterns  → 0.90
    """
    if priority <= 5:
        return 0.98
    elif 10 <= priority <= 19:
        return 0.92
    elif 20 <= priority <= 29:
        return 0.92
    elif 30 <= priority <= 39:
        return 0.95
    elif 40 <= priority <= 49:
        return 0.88
    elif 50 <= priority <= 59:
        return 0.90
    elif 60 <= priority <= 69:
        return 0.85
    elif 70 <= priority <= 79:
        return 0.88
    elif 80 <= priority <= 89:
        return 0.87
    elif 90 <= priority <= 99:
        return 0.90
    elif 100 <= priority <= 109:
        return 0.90
    return 0.75


def _build_rationale(bot_name: str, action: str, params: Dict, pattern: str) -> str:
    """Build a human-readable explanation of the routing decision."""
    param_str = ", ".join(f"{k}={v!r}" for k, v in params.items()) if params else "no params extracted"
    return (
        f"Routed to {bot_name.upper()} → {action}. "
        f"Matched pattern: /{pattern}/. "
        f"Extracted params: {param_str}."
    )


def _suggest_alternatives(text: str) -> List[str]:
    """Provide contextual suggestions when routing fails."""
    text_lower = text.lower()
    suggestions = []

    if any(w in text_lower for w in ('property', 'building', 'address')):
        suggestions.append("Try: 'Check violations for [address]'")
        suggestions.append("Try: 'Run full compliance audit on [address]'")
    if any(w in text_lower for w in ('company', 'management', 'operator')):
        suggestions.append("Try: 'Research [Company Name]'")
        suggestions.append("Try: 'Find PM companies in [region]'")
    if any(w in text_lower for w in ('report', 'kpi', 'numbers')):
        suggestions.append("Try: 'Send weekly KPI report'")
        suggestions.append("Try: 'Generate occupancy dashboard'")
    if any(w in text_lower for w in ('tenant', 'unit', 'lease')):
        suggestions.append("Try: 'Create a maintenance ticket for unit [X]'")
        suggestions.append("Try: 'Message tenant in unit [X]'")
    if any(w in text_lower for w in ('variance', 'actual', 'quarter', 'mds', 'over budget')):
        suggestions.append("Try: 'How did Q2 land against budget for [address]?'")
        suggestions.append("Try: 'Run the quarterly variance report on [address]'")
    if any(w in text_lower for w in ('form', 'template', 'document', 'agreement', 'cover sheet')):
        suggestions.append("Try: 'Do we have a form for [situation]?'")
        suggestions.append("Try: 'Download the COI tracking form'")
    if any(w in text_lower for w in ('budget', 'expense', 'savings', 'fee', 'operating cost')):
        suggestions.append("Try: 'Review this operating budget for savings'")
        suggestions.append("Try: 'Run a cost-beat analysis on [address]'")

    if not suggestions:
        suggestions = [
            "Try: 'Find PM companies in Queens'",
            "Try: 'Check violations for 123 Main St'",
            "Try: 'Draft an LOI for [address] at $[price]'",
            "Try: 'Send weekly KPI report'",
            "Try: 'Research [Company Name] for acquisition'",
        ]
    return suggestions


# ---------------------------------------------------------------------------
# CLI-friendly pretty print for debugging
# ---------------------------------------------------------------------------

def explain_routing(decision: RoutingDecision | RouterError) -> str:
    """Format a routing decision as a human-readable string."""
    if isinstance(decision, RouterError):
        lines = [f"[ERROR] {decision.message}"]
        if decision.suggestions:
            lines.append("Suggestions:")
            for s in decision.suggestions:
                lines.append(f"  • {s}")
        return "\n".join(lines)

    lines = [
        f"[ROUTING → {decision.bot_name.upper()} Bot]",
        f"Action:     {decision.action}",
        f"Params:     {decision.params}",
        f"Confidence: {decision.confidence:.0%}",
    ]
    if decision.pipeline:
        lines.append(f"Pipeline:   {decision.pipeline}")
    lines.append(f"Rationale:  {decision.rationale}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    test_cases = [
        "Find property management companies in Connecticut",
        "Check violations for 123 Main Street Brooklyn",
        "Draft an LOI for 456 Park Ave at $2.5M",
        "Create a maintenance ticket for unit 4B leak",
        "Send weekly KPI report",
        "Organize new files in Google Drive",
        "Research and outreach to ABC Property Management",
        "Full compliance audit on 789 Eastern Pkwy",
        "Build a battlecard for Metro Management LLC",
        "What's our occupancy looking like?",
        "Who owns 500 Grand Ave in Brooklyn?",
        "Generate a proforma for the Bronx portfolio",
        "Tenant in 12C says heat has been out for two days",
        "Push Metro Management to HubSpot as a new deal",
        "Generate investor memo for Q4",
        "This is not a recognizable command at all",
    ]

    print("\n" + "=" * 60)
    print("CAMELOT OS ROUTER — SELF TEST")
    print("=" * 60)

    for case in test_cases:
        print(f"\nINPUT:  {case}")
        result = classify_intent(case)
        print(explain_routing(result))
        print("-" * 60)
