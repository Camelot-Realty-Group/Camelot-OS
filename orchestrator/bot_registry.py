"""
bot_registry.py — Camelot OS Bot Registry

Central registry of all specialist bots in the Camelot OS ecosystem.
Each entry defines the bot's metadata, capabilities, entry points, and
API configuration. Used by the router, pipeline executor, and API server.
"""

from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Master Bot Registry
# ---------------------------------------------------------------------------

BOTS: Dict[str, Dict[str, Any]] = {
    "scout": {
        "name": "Scout",
        "description": (
            "Lead generation and property intelligence. Finds property management "
            "companies ripe for acquisition, enriches leads with ownership data, "
            "violation history, revenue estimates, and pushes to HubSpot CRM."
        ),
        "capabilities": [
            "search_leads",          # Search for PM companies by region, size, criteria
            "enrich_lead",           # Deep-enrich a specific company with all available data
            "push_to_hubspot",       # Create/update HubSpot contact and deal records
            "property_intel",        # Pull property-level data (units, ownership, financials)
            "market_comp",           # Comparable market analysis for a submarket
            "build_lead_list",       # Batch lead generation with filtering
            "check_ownership",       # Identify beneficial ownership via ACRIS
        ],
        "data_sources": [
            "NYC Open Data (HPD, DOB, ACRIS)",
            "CoStar",
            "StreetEasy",
            "HubSpot CRM",
            "Google Maps API",
            "NYC PLUTO dataset",
            "PropertyRadar",
        ],
        "entry_point": "scout_bot/main.py",
        "api_endpoint": None,          # Runs as local subprocess
        "api_port": None,
        "health_check": None,
        "timeout_seconds": 60,
        "requires_env": [
            "HUBSPOT_API_KEY",
            "COSTAR_API_KEY",
            "GOOGLE_MAPS_API_KEY",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
        ],
        "icon": "🔭",
        "color": "#4A90D9",
    },

    "broker": {
        "name": "Broker",
        "description": (
            "Transaction execution and deal documentation. Generates LOIs, PSAs, "
            "proformas, and deal memos. Analyzes cap rates, NOI, and DSCR. "
            "Handles all deal-stage documentation and financial underwriting."
        ),
        "capabilities": [
            "generate_loi",           # Generate Letter of Intent for acquisition
            "generate_psa",           # Draft Purchase and Sale Agreement
            "build_proforma",         # Build financial proforma for a property/portfolio
            "analyze_cap_rate",       # Cap rate and NOI analysis
            "calculate_dscr",         # Debt service coverage ratio calculation
            "draft_nda",              # Generate NDA for acquisition conversations
            "generate_deal_memo",     # Executive deal summary memo
            "rent_roll_analysis",     # Analyze rent roll for occupancy and revenue
        ],
        "data_sources": [
            "NYC ACRIS (recorded transactions)",
            "CoStar Comps",
            "Internal deal database (Supabase)",
            "AppFolio rent rolls",
            "LoopNet",
        ],
        "entry_point": "broker_bot/main.py",
        "api_endpoint": None,
        "api_port": None,
        "health_check": None,
        "timeout_seconds": 45,
        "requires_env": [
            "OPENAI_API_KEY",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
            "COSTAR_API_KEY",
        ],
        "icon": "⚖️",
        "color": "#D4A017",
    },

    "compliance": {
        "name": "Compliance",
        "description": (
            "NYC regulatory compliance and violation management. Checks HPD violations, "
            "DOB open permits, ECB violations, Local Law 97 carbon exposure, elevator/"
            "boiler certifications. Generates compliance scorecards and remediation plans."
        ),
        "capabilities": [
            "check_hpd",              # HPD violation lookup by address or BIN
            "check_dob",              # DOB open permits and violations
            "check_ecb",              # ECB (Environmental Control Board) violations
            "check_ll97",             # Local Law 97 carbon emissions exposure
            "check_elevator",         # Elevator inspection certification status
            "check_boiler",           # Boiler registration and inspection
            "full_audit",             # Comprehensive compliance audit (all checks)
            "property_scorecard",     # Compliance score with remediation priorities
            "track_remediation",      # Track open items to closure
        ],
        "data_sources": [
            "NYC HPD Open Data API",
            "NYC DOB BIS API",
            "NYC ECB Open Data",
            "NYC LL97 Carbon Benchmarking Database",
            "NYC DOB NOW",
        ],
        "entry_point": "compliance_bot/main.py",
        "api_endpoint": "http://compliance_bot:8003",
        "api_port": 8003,
        "health_check": "http://compliance_bot:8003/health",
        "timeout_seconds": 90,
        "requires_env": [
            "NYC_OPEN_DATA_APP_TOKEN",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
        ],
        "icon": "🏛️",
        "color": "#E74C3C",
    },

    "frontdesk": {
        "name": "Front Desk",
        "description": (
            "Tenant operations and maintenance management. Creates and routes maintenance "
            "tickets, handles tenant communication, dispatches vendors, manages lease "
            "inquiries, and escalates emergencies. Integrates with AppFolio and Buildium."
        ),
        "capabilities": [
            "create_ticket",          # Create maintenance work order
            "route_ticket",           # Route ticket to correct vendor/staff
            "tenant_message",         # Send message to tenant
            "lease_inquiry",          # Answer lease-related questions
            "emergency_escalate",     # Escalate emergency to on-call staff
            "vendor_dispatch",        # Dispatch vendor with work order details
            "ticket_status",          # Check status of open tickets
            "tenant_portal_update",   # Update tenant-facing portal
        ],
        "data_sources": [
            "AppFolio API",
            "Buildium API",
            "Twilio (SMS/voice)",
            "SendGrid (email)",
            "Internal vendor database (Supabase)",
        ],
        "entry_point": "frontdesk_bot/main.py",
        "api_endpoint": "http://frontdesk_bot:8001",
        "api_port": 8001,
        "health_check": "http://frontdesk_bot:8001/health",
        "timeout_seconds": 30,
        "requires_env": [
            "APPFOLIO_API_KEY",
            "APPFOLIO_CLIENT_ID",
            "BUILDIUM_API_KEY",
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "SENDGRID_API_KEY",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
        ],
        "icon": "🏠",
        "color": "#27AE60",
    },

    "concierge": {
        "name": "Concierge",
        "description": (
            "Document template concierge. Catalogs, serves, and (for wired templates) "
            "auto-fills Camelot's 23 branded document templates — management agreements, "
            "compliance forms, leasing packages, board governance, financial reports, "
            "and property-management paperwork."
        ),
        "capabilities": [
            "list_templates",         # List/search the template catalog, optionally by category
            "get_template",           # Full metadata + field schema for one template
            "download_template",      # Serve a docx/pdf/fillable-PDF file
            "generate_document",      # Auto-fill a merge-tag master with answers (wired templates only)
        ],
        "data_sources": [
            "Camelot Template Library (bundled under concierge_bot/library/)",
            "Merge-tag masters (concierge_bot/masters/)",
        ],
        "entry_point": "concierge_bot/main.py",
        "api_endpoint": "http://concierge_bot:8004",
        "api_port": 8004,
        "health_check": "http://concierge_bot:8004/health",
        "timeout_seconds": 30,
        "requires_env": [],
        "icon": "📝",
        "color": "#2E86C1",
    },

    "index": {
        "name": "Index",
        "description": (
            "Document intelligence and file organization. Organizes Google Drive, "
            "extracts data from leases/PSAs/vendor contracts, creates searchable "
            "indexes, tags documents by property and entity, flags expiring documents."
        ),
        "capabilities": [
            "run_indexer",            # Full Drive indexing run
            "organize_folder",        # Organize files in a specific folder
            "extract_lease_data",     # Abstract key data from lease documents
            "search_documents",       # Full-text search across indexed documents
            "flag_expiring",          # Identify documents expiring within N days
            "tag_document",           # Apply metadata tags to a document
            "generate_index",         # Create structured index/manifest of a folder
            "extract_contract_terms", # Extract key terms from vendor/service contracts
        ],
        "data_sources": [
            "Google Drive API",
            "Google Docs API",
            "OpenAI GPT-4 (document parsing)",
            "Supabase (document index storage)",
        ],
        "entry_point": "index_bot/main.py",
        "api_endpoint": "http://index_bot:8002",
        "api_port": 8002,
        "health_check": "http://index_bot:8002/health",
        "timeout_seconds": 120,
        "requires_env": [
            "GOOGLE_SERVICE_ACCOUNT_JSON",
            "GOOGLE_DRIVE_ROOT_FOLDER_ID",
            "OPENAI_API_KEY",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
        ],
        "icon": "📁",
        "color": "#8E44AD",
    },

    "report": {
        "name": "Report",
        "description": (
            "Financial reporting and KPI analytics. Generates weekly/monthly KPI reports, "
            "occupancy dashboards, NOI summaries, collections reports, investor memos, "
            "and acquisition pipeline updates. Pulls from Supabase, HubSpot, and AppFolio."
        ),
        "capabilities": [
            "send_weekly_kpi",        # Generate and send weekly KPI summary
            "monthly_financials",     # Full monthly P&L and NOI report
            "occupancy_dashboard",    # Occupancy snapshot across portfolio
            "collections_report",     # Rent collections status and delinquency
            "acquisition_pipeline",   # Deal pipeline status and conversion metrics
            "property_scorecard",     # Compliance + financial score for a property
            "investor_memo",          # Investor-ready portfolio update memo
            "deal_memo",              # Executive summary for a specific deal
        ],
        "data_sources": [
            "Supabase (all operational data)",
            "HubSpot CRM (pipeline data)",
            "AppFolio (financial/occupancy data)",
            "Compliance Bot outputs",
            "SendGrid (report delivery)",
        ],
        "entry_point": "report_bot/main.py",
        "api_endpoint": None,
        "api_port": None,
        "health_check": None,
        "timeout_seconds": 60,
        "requires_env": [
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
            "HUBSPOT_API_KEY",
            "APPFOLIO_API_KEY",
            "SENDGRID_API_KEY",
            "REPORT_RECIPIENT_EMAILS",
        ],
        "icon": "📊",
        "color": "#E67E22",
    },

    "deal": {
        "name": "Deal",
        "description": (
            "Acquisition outreach and relationship management. End-to-end deal sourcing — "
            "researches acquisition targets, builds competitive battlecards, drafts "
            "personalized outreach emails, manages follow-up cadences, logs to HubSpot."
        ),
        "capabilities": [
            "prospect_and_outreach",  # Full research + draft outreach for a target
            "research_target",        # Deep research on a specific PM company
            "build_battlecard",       # Competitive positioning card for a target
            "draft_email",            # Draft personalized outreach email
            "log_outreach",           # Log outreach activity to HubSpot
            "follow_up_sequence",     # Build multi-touch follow-up sequence
            "track_responses",        # Monitor and log response activity
            "deal_stage_update",      # Update deal stage in HubSpot pipeline
        ],
        "data_sources": [
            "HubSpot CRM",
            "OpenAI GPT-4 (research synthesis)",
            "NYC Open Data (property portfolio data)",
            "LinkedIn (company research)",
            "Google Search API",
            "Supabase (internal deal history)",
        ],
        "entry_point": "deal_bot/main.py",
        "api_endpoint": None,
        "api_port": None,
        "health_check": None,
        "timeout_seconds": 90,
        "requires_env": [
            "OPENAI_API_KEY",
            "HUBSPOT_API_KEY",
            "GOOGLE_SEARCH_API_KEY",
            "GOOGLE_SEARCH_CX",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
        ],
        "icon": "🤝",
        "color": "#1ABC9C",
    },

    "perseus": {
        "name": "Perseus",
        "description": (
            "Quarterly management-report variance engine — compares actuals to a "
            "building's own budget and to Camelot's portfolio averages, and produces "
            "a standalone per-period savings/fee proposal."
        ),
        "capabilities": [
            "analyze_period_report",  # Parse a period's actuals (Excel or MDS PDF) and analyze
            "budget_variance",        # Line-by-line actual vs. prorated budget share
            "portfolio_comparison",   # Annualized run-rate vs. portfolio average per unit
            "flag_overruns",          # Categories running over their prorated budget share
            "build_fee_proposal",     # One-time fee vs. management-fee uplift for the period
            "generate_variance_pdf",  # Client-facing variance + proposal PDF
            "list_period_reports",    # Prior periods for a building
        ],
        "data_sources": [
            "Uploaded management reports (Excel/CSV exports, MDS PDFs)",
            "Supabase portfolio_benchmarks (cross-portfolio category comparables)",
            "Supabase costbeat_analyses (budget baseline, when on file)",
            "Supabase perseus_variance_reports (period history)",
        ],
        "entry_point": "perseus_bot/main.py",
        "api_endpoint": "http://perseus_bot:8006",
        "api_port": 8006,
        "health_check": "http://perseus_bot:8006/health",
        "timeout_seconds": 120,
        "requires_env": [
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
            "OPENAI_API_KEY",
        ],
        "icon": "📉",
        "color": "#34495E",
    },
}


# ---------------------------------------------------------------------------
# Registry Access Helpers
# ---------------------------------------------------------------------------

def get_bot(bot_name: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve a bot's full metadata by name (case-insensitive).

    Args:
        bot_name: The canonical bot name (e.g., "scout", "Scout", "SCOUT")

    Returns:
        Bot metadata dict, or None if not found.
    """
    return BOTS.get(bot_name.lower())


def get_bot_capabilities(bot_name: str) -> List[str]:
    """
    Return the list of capabilities for a given bot.

    Args:
        bot_name: The canonical bot name.

    Returns:
        List of capability strings, or empty list if bot not found.
    """
    bot = get_bot(bot_name)
    if not bot:
        logger.warning("Bot '%s' not found in registry.", bot_name)
        return []
    return bot.get("capabilities", [])


def list_all_bots() -> List[str]:
    """Return a list of all registered bot names."""
    return list(BOTS.keys())


def get_bots_with_api() -> Dict[str, str]:
    """
    Return bots that expose an HTTP API endpoint.

    Returns:
        Dict mapping bot_name → api_endpoint URL.
    """
    return {
        name: meta["api_endpoint"]
        for name, meta in BOTS.items()
        if meta.get("api_endpoint") is not None
    }


def get_bot_summary() -> List[Dict[str, Any]]:
    """
    Return a summary list of all bots for API responses and UI rendering.

    Returns:
        List of dicts with name, description, capabilities count, icon, color.
    """
    return [
        {
            "id": name,
            "name": meta["name"],
            "description": meta["description"],
            "capabilities": meta["capabilities"],
            "capability_count": len(meta["capabilities"]),
            "icon": meta.get("icon", "🤖"),
            "color": meta.get("color", "#888888"),
            "has_api": meta.get("api_endpoint") is not None,
            "api_endpoint": meta.get("api_endpoint"),
        }
        for name, meta in BOTS.items()
    ]


def validate_action(bot_name: str, action: str) -> bool:
    """
    Validate that a bot supports a given action/capability.

    Args:
        bot_name: The bot to check.
        action: The action/capability string.

    Returns:
        True if the bot supports the action, False otherwise.
    """
    capabilities = get_bot_capabilities(bot_name)
    is_valid = action in capabilities
    if not is_valid:
        logger.warning(
            "Action '%s' not found in bot '%s' capabilities: %s",
            action, bot_name, capabilities
        )
    return is_valid
