"""
response_templates.py — Front Desk Bot Response Template Library
Camelot Property Management Services Corp / Front Desk Bot

Canned response templates for every category/urgency combination.
Uses f-string substitution for resident name, unit, building, ticket number, ETA.

All templates are signed: "Camelot Property Management Services — Your Front Desk Team"

Author: Camelot OS
"""

from typing import Optional

# ---------------------------------------------------------------------------
# Template signature
# ---------------------------------------------------------------------------

SIGNATURE = "\n\nWarm regards,\nCamelot Property Management Services — Your Front Desk Team"
PORTAL_LINK = "https://residents.camelot.nyc"  # Replace with actual portal URL


# ---------------------------------------------------------------------------
# Template builder
# ---------------------------------------------------------------------------

def get_response(
    category: str,
    urgency: str,
    resident_name: str = "Resident",
    unit: str = "",
    building_address: str = "",
    ticket_number: str = "",
    eta: str = "",
    extra: Optional[dict] = None,
) -> str:
    """
    Retrieve and populate the appropriate response template.

    Args:
        category:          Classification category (emergency/maintenance/rent/lease/amenity/complaint/other)
        urgency:           Classification urgency (emergency/urgent/routine)
        resident_name:     Resident's name for personalization
        unit:              Unit number (e.g., "4B")
        building_address:  Full building address
        ticket_number:     CAM-YYYY-NNNN ticket number
        eta:               Human-readable ETA string (e.g., "within 2 hours")
        extra:             Additional template variables dict

    Returns:
        Fully substituted response string ready to send.
    """
    extra = extra or {}

    # Normalize inputs
    category = category.lower().strip()
    urgency = urgency.lower().strip()

    # Find template function
    key = f"{category}_{urgency}"
    template_fn = TEMPLATES.get(key) or TEMPLATES.get(f"{category}_routine") or TEMPLATES.get("other_routine")

    if template_fn is None:
        return _generic_acknowledgment(resident_name, ticket_number, eta)

    try:
        body = template_fn(
            resident_name=resident_name,
            unit=unit,
            building_address=building_address,
            ticket_number=ticket_number,
            eta=eta or _default_eta(urgency),
            extra=extra,
        )
    except KeyError as e:
        body = _generic_acknowledgment(resident_name, ticket_number, eta)

    return body + SIGNATURE


# ---------------------------------------------------------------------------
# Template functions
# ---------------------------------------------------------------------------

def _emergency_emergency(resident_name, unit, building_address, ticket_number, eta, extra):
    return f"""Dear {resident_name},

We received your emergency message regarding {extra.get('issue_summary', 'an urgent situation')} at {building_address}{f', Unit {unit}' if unit else ''}.

IMMEDIATE ACTIONS:

1. If there is an immediate risk to life — CALL 911 NOW.
2. If you smell gas: do NOT turn lights on or off. Leave the building immediately and call Con Edison at 1-800-752-6633.
3. If there is flooding: shut off the water supply valve if safe to do so.
4. If there is fire or smoke: evacuate immediately using the stairs (not elevator). Pull the fire alarm and call 911.

Our on-call maintenance team has been ALERTED and is responding. Your emergency ticket has been opened:

Ticket Number: {ticket_number}
Priority: EMERGENCY
Target Response: Within 30 minutes

We will contact you directly as soon as possible. If you cannot reach our emergency line, please call (212) 555-0199."""


def _emergency_urgent(resident_name, unit, building_address, ticket_number, eta, extra):
    return _emergency_emergency(resident_name, unit, building_address, ticket_number, eta, extra)


def _maintenance_emergency(resident_name, unit, building_address, ticket_number, eta, extra):
    return _emergency_emergency(resident_name, unit, building_address, ticket_number, eta, extra)


def _maintenance_urgent(resident_name, unit, building_address, ticket_number, eta, extra):
    return f"""Dear {resident_name},

Thank you for contacting Camelot Property Management. We've received your urgent maintenance request for Unit {unit or '[your unit]'} at {building_address}.

Ticket Number: {ticket_number}
Status: URGENT — In Queue
Estimated Response Time: {eta}

Our maintenance team has been notified and will contact you within 2 hours to confirm arrival time. Please ensure someone is available to provide access during the scheduled window.

If the situation worsens or becomes an emergency (flooding, gas smell, fire), please call 911 immediately, then reach our 24/7 emergency line at (212) 555-0199.

You can track your ticket status at {PORTAL_LINK} using your ticket number."""


def _maintenance_routine(resident_name, unit, building_address, ticket_number, eta, extra):
    return f"""Dear {resident_name},

Thank you for submitting your maintenance request. We've received it and created a service ticket for Unit {unit or '[your unit]'} at {building_address}.

Ticket Number: {ticket_number}
Status: Open — Pending Assignment
Estimated Response: {eta}

Our maintenance coordinator will reach out within 24 hours to schedule a convenient time. You can track your ticket status online at {PORTAL_LINK}.

Is there a preferred time for us to access your unit? If so, please reply to this message with your availability.

Thank you for your patience."""


def _rent_routine(resident_name, unit, building_address, ticket_number, eta, extra):
    return f"""Dear {resident_name},

Thank you for reaching out regarding your account at {building_address}{f', Unit {unit}' if unit else ''}.

Your rent payment can be made online through our resident portal:
{PORTAL_LINK}

We accept: ACH bank transfer (free), credit/debit card (processing fee applies), and certified check or money order (in-person at the management office).

If you have a specific question about your balance, late fees, or payment history, please log in to your portal account or reply to this message with details and we'll have your account manager follow up directly.

Reference: {ticket_number}"""


def _rent_urgent(resident_name, unit, building_address, ticket_number, eta, extra):
    return f"""Dear {resident_name},

Thank you for contacting us regarding your account at {building_address}{f', Unit {unit}' if unit else ''}.

We understand this may be time-sensitive. Our accounts receivable team will review your inquiry and respond within {eta}.

Reference Ticket: {ticket_number}

For immediate questions about your balance or to arrange a payment plan, please contact our Accounts Receivable team directly at ar@camelot.nyc or (212) 555-0110.

If you're experiencing financial hardship, please let us know — we may be able to discuss payment arrangements."""


def _lease_routine(resident_name, unit, building_address, ticket_number, eta, extra):
    return f"""Dear {resident_name},

Thank you for contacting us regarding your lease at {building_address}{f', Unit {unit}' if unit else ''}.

Our leasing team has received your inquiry and will follow up within {eta}.

Reference Ticket: {ticket_number}

In the meantime, you can review your current lease documents in your resident portal at {PORTAL_LINK}.

For lease renewals: We typically send renewal offers 90 days before your lease expiration. If your lease is approaching its end date and you haven't received a renewal offer, please reply and we'll expedite that for you.

For move-out inquiries: Please note that NYC law requires a minimum written notice period. We'll provide you with the specific requirements for your unit.

Our leasing office is available Monday–Friday, 9am–5pm at leasing@camelot.nyc."""


def _lease_urgent(resident_name, unit, building_address, ticket_number, eta, extra):
    return f"""Dear {resident_name},

We received your urgent lease inquiry for {building_address}{f', Unit {unit}' if unit else ''}.

Ticket Number: {ticket_number}

Our leasing team will contact you within {eta}. For urgent situations (lease expiring imminently, emergency vacate), please call our leasing office directly at (212) 555-0111 during business hours.

If this involves a legal notice (court date, marshal notice), please contact our office immediately at mgr@camelot.nyc — do not ignore legal notices."""


def _amenity_routine(resident_name, unit, building_address, ticket_number, eta, extra):
    return f"""Dear {resident_name},

Thank you for reaching out about building amenities at {building_address}.

{extra.get('amenity_info', 'Our team will provide you with the information you need.')}

Reference: {ticket_number}

For package pick-up: Packages are held in the package room in the lobby. You'll receive a notification when a package arrives. Please pick up within 5 business days.

For amenity hours and availability, please visit your resident portal at {PORTAL_LINK} or contact the management office.

Is there anything specific we can help clarify?"""


def _complaint_routine(resident_name, unit, building_address, ticket_number, eta, extra):
    return f"""Dear {resident_name},

Thank you for bringing this to our attention. We take all resident concerns seriously and appreciate you reaching out.

Your concern has been logged and assigned to our team:

Ticket Number: {ticket_number}
Status: Open — Under Review
Target Response: {eta}

We will investigate the matter you've described and follow up with you directly within {eta}. If this involves a noise or neighbor complaint, we may need to gather additional information before taking action.

If the situation requires immediate attention (e.g., a safety concern), please do not hesitate to call us at (212) 555-0199.

Your comfort and wellbeing are our priority."""


def _complaint_urgent(resident_name, unit, building_address, ticket_number, eta, extra):
    return f"""Dear {resident_name},

We've received your message and want you to know your concern is being taken seriously.

Ticket Number: {ticket_number}
Priority: URGENT
Target Response: {eta}

A member of our management team will follow up with you within {eta}. We understand this situation is frustrating, and we're committed to resolving it promptly.

If you'd like to speak with someone directly, please call (212) 555-0199 or email mgr@camelot.nyc."""


def _other_routine(resident_name, unit, building_address, ticket_number, eta, extra):
    return _generic_acknowledgment(resident_name, ticket_number, eta)


# ---------------------------------------------------------------------------
# Generic fallback
# ---------------------------------------------------------------------------

def _generic_acknowledgment(resident_name: str, ticket_number: str, eta: str) -> str:
    return f"""Dear {resident_name},

Thank you for contacting Camelot Property Management Services. We've received your message and a member of our team will follow up with you {eta or 'within 24 hours'}.

Reference Ticket: {ticket_number or 'Pending'}

If your situation requires immediate attention or is a safety emergency, please call our 24/7 line at (212) 555-0199 or call 911.

You can also track your request at {PORTAL_LINK}."""


def _default_eta(urgency: str) -> str:
    return {
        "emergency": "within 30 minutes",
        "urgent": "within 2 hours",
        "routine": "within 24 hours",
    }.get(urgency, "within 24 hours")


# ---------------------------------------------------------------------------
# Special: Renewal reminder (outbound)
# ---------------------------------------------------------------------------

def lease_renewal_reminder(
    resident_name: str,
    unit: str,
    building_address: str,
    lease_expiration_date: str,
    renewal_offer_details: Optional[str] = None,
) -> str:
    """Generate an outbound lease renewal reminder."""
    details = renewal_offer_details or (
        "Please log in to your resident portal to review your renewal options: "
        f"{PORTAL_LINK}"
    )
    return f"""Dear {resident_name},

We hope you're enjoying your home at {building_address}, Unit {unit}!

This is a friendly reminder that your current lease is scheduled to expire on {lease_expiration_date}. We'd love to have you continue as a valued resident and wanted to reach out early to discuss your renewal options.

{details}

If you'd like to discuss your renewal or have any questions, please don't hesitate to reply to this message or contact our leasing team at leasing@camelot.nyc.

We look forward to continuing to be your home.{SIGNATURE}"""


# ---------------------------------------------------------------------------
# Special: Package notification (outbound)
# ---------------------------------------------------------------------------

def package_notification(
    resident_name: str,
    unit: str,
    building_address: str,
    carrier: str = "a carrier",
    num_packages: int = 1,
) -> str:
    """Generate outbound package arrival notification."""
    pkg_word = "package" if num_packages == 1 else f"{num_packages} packages"
    return f"""Dear {resident_name},

Good news! {pkg_word} from {carrier} {"has" if num_packages == 1 else "have"} arrived for Unit {unit} at {building_address}.

Your {pkg_word} {"is" if num_packages == 1 else "are"} being held securely in the package room in the lobby. Please pick up within 5 business days.

Building hours for package pickup: Monday–Sunday, 8am–8pm.

If you have any questions, reply to this message.{SIGNATURE}"""


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

TEMPLATES = {
    # Emergency
    "emergency_emergency": _emergency_emergency,
    "emergency_urgent": _emergency_urgent,
    "emergency_routine": _emergency_emergency,  # Always escalate emergencies

    # Maintenance
    "maintenance_emergency": _maintenance_emergency,
    "maintenance_urgent": _maintenance_urgent,
    "maintenance_routine": _maintenance_routine,

    # Rent
    "rent_emergency": _rent_urgent,
    "rent_urgent": _rent_urgent,
    "rent_routine": _rent_routine,

    # Lease
    "lease_emergency": _lease_urgent,
    "lease_urgent": _lease_urgent,
    "lease_routine": _lease_routine,

    # Amenity
    "amenity_emergency": _maintenance_urgent,
    "amenity_urgent": _maintenance_urgent,
    "amenity_routine": _amenity_routine,

    # Complaint
    "complaint_emergency": _complaint_urgent,
    "complaint_urgent": _complaint_urgent,
    "complaint_routine": _complaint_routine,

    # Other
    "other_emergency": _emergency_emergency,
    "other_urgent": _maintenance_urgent,
    "other_routine": _other_routine,
}


# ---------------------------------------------------------------------------
# CLI / quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    examples = [
        ("emergency", "emergency", {"issue_summary": "gas smell in kitchen"}),
        ("maintenance", "urgent", {}),
        ("maintenance", "routine", {}),
        ("rent", "routine", {}),
        ("lease", "routine", {}),
        ("complaint", "urgent", {}),
    ]

    for category, urgency, extra in examples:
        print(f"\n{'='*60}")
        print(f"TEMPLATE: {category.upper()} / {urgency.upper()}")
        print("=" * 60)
        response = get_response(
            category=category,
            urgency=urgency,
            resident_name="Jane Smith",
            unit="4B",
            building_address="123 Main Street, Bronx, NY 10452",
            ticket_number="CAM-2026-0042",
            extra=extra,
        )
        print(response)
