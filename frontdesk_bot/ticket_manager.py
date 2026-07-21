"""
ticket_manager.py — Maintenance Ticket Manager
Camelot Property Management Services Corp / Front Desk Bot

Creates, updates, and queries maintenance tickets stored in Supabase.
Ticket format: CAM-YYYY-NNNN

Table schema (Supabase):
    tickets (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        ticket_number   text UNIQUE NOT NULL,
        building_id     text,
        resident_id     text,
        unit            text,
        category        text,
        urgency         text,
        description     text,
        status          text DEFAULT 'Open',
        assigned_to     text,
        notes           jsonb DEFAULT '[]',
        channel         text,
        created_at      timestamptz DEFAULT now(),
        updated_at      timestamptz DEFAULT now(),
        resolved_at     timestamptz
    )

Author: Camelot OS
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
TICKETS_TABLE = "tickets"

VALID_STATUSES = ["Open", "Assigned", "In Progress", "Resolved", "Closed"]
VALID_URGENCIES = ["emergency", "urgent", "routine"]
VALID_CATEGORIES = ["maintenance", "rent", "lease", "amenity", "emergency", "complaint", "other"]


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


_session = _build_session()


def _supabase_headers() -> dict:
    if not SUPABASE_SERVICE_KEY:
        raise EnvironmentError("SUPABASE_SERVICE_KEY is not set")
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


# ---------------------------------------------------------------------------
# Ticket number generator
# ---------------------------------------------------------------------------

def _generate_ticket_number() -> str:
    """
    Generate a unique ticket number in format CAM-YYYY-NNNN.
    Uses a timestamp-based sequence number to reduce collision risk.
    """
    year = datetime.now(timezone.utc).year
    # Use last 4 digits of current millisecond timestamp for uniqueness
    seq = int(time.time() * 1000) % 10000
    return f"CAM-{year}-{seq:04d}"


def _ensure_unique_ticket_number(base_number: str, max_attempts: int = 10) -> str:
    """
    Ensure ticket number is unique by checking Supabase; increment if collision.
    """
    number = base_number
    for attempt in range(max_attempts):
        if not _ticket_number_exists(number):
            return number
        # Increment the sequence portion
        parts = number.rsplit("-", 1)
        seq = int(parts[1]) + 1
        number = f"{parts[0]}-{seq:04d}"
    # Last resort: append timestamp microseconds
    return f"{base_number}-{int(time.time() * 1000000) % 1000:03d}"


def _ticket_number_exists(ticket_number: str) -> bool:
    """Check if a ticket number already exists in Supabase."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False  # Can't check without credentials
    try:
        url = f"{SUPABASE_URL}/rest/v1/{TICKETS_TABLE}"
        params = {"ticket_number": f"eq.{ticket_number}", "select": "ticket_number", "limit": "1"}
        resp = _session.get(url, headers=_supabase_headers(), params=params, timeout=8)
        resp.raise_for_status()
        return len(resp.json()) > 0
    except Exception:
        return False  # On error, assume it doesn't exist and proceed


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def create_ticket(
    resident_id: Optional[str],
    unit: str,
    category: str,
    description: str,
    urgency: str,
    building_id: Optional[str] = None,
    channel: str = "unknown",
    assigned_to: Optional[str] = None,
) -> dict:
    """
    Create a new maintenance/service ticket.

    Args:
        resident_id:  Resident identifier (email, phone, or internal ID)
        unit:         Unit number (e.g., "4B")
        category:     Category from VALID_CATEGORIES
        description:  Full description of the issue
        urgency:      Urgency level from VALID_URGENCIES
        building_id:  Building identifier (BBL, MDS code, or address)
        channel:      Inbound channel ("email", "sms", "chat", "manual")
        assigned_to:  Assignee name or email (optional)

    Returns:
        Ticket dict with ticket_number, id, and all fields.

    Raises:
        ValueError: If category or urgency is invalid.
        RuntimeError: If Supabase insertion fails.
    """
    # Validate inputs
    category = category.lower().strip()
    urgency = urgency.lower().strip()
    if category not in VALID_CATEGORIES:
        logger.warning(f"Unknown category '{category}' — defaulting to 'other'")
        category = "other"
    if urgency not in VALID_URGENCIES:
        logger.warning(f"Unknown urgency '{urgency}' — defaulting to 'routine'")
        urgency = "routine"

    # Generate ticket number
    base_number = _generate_ticket_number()
    ticket_number = _ensure_unique_ticket_number(base_number)

    now = datetime.now(timezone.utc).isoformat()

    ticket = {
        "ticket_number": ticket_number,
        "building_id": building_id,
        "resident_id": resident_id,
        "unit": unit,
        "category": category,
        "urgency": urgency,
        "description": description,
        "status": "Open",
        "assigned_to": assigned_to,
        "notes": json.dumps([]),
        "channel": channel,
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
    }

    # Persist to Supabase
    if SUPABASE_URL and SUPABASE_SERVICE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/{TICKETS_TABLE}"
            resp = _session.post(url, headers=_supabase_headers(), json=ticket, timeout=12)
            resp.raise_for_status()
            created = resp.json()
            if created:
                ticket.update(created[0])
                logger.info(
                    f"Ticket created: {ticket_number} — {urgency.upper()} {category} "
                    f"for unit {unit} (building: {building_id})"
                )
            return ticket
        except requests.RequestException as e:
            logger.error(f"Supabase ticket creation failed: {e}")
            # Return the local dict anyway so callers aren't blocked
            ticket["_supabase_error"] = str(e)
            return ticket
    else:
        logger.warning("SUPABASE credentials not configured — ticket created in memory only")
        return ticket


def update_ticket_status(
    ticket_number: str,
    status: str,
    note: Optional[str] = None,
    updated_by: Optional[str] = None,
) -> dict:
    """
    Update a ticket's status and optionally append a note.

    Args:
        ticket_number:  CAM-YYYY-NNNN identifier
        status:         New status from VALID_STATUSES
        note:           Optional note to append to the ticket log
        updated_by:     Name/email of the person making the update

    Returns:
        Updated ticket dict.

    Raises:
        ValueError: If status is invalid.
        LookupError: If ticket is not found.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}")

    now = datetime.now(timezone.utc).isoformat()

    update_payload: dict = {
        "status": status,
        "updated_at": now,
    }

    if status == "Resolved":
        update_payload["resolved_at"] = now

    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.warning("SUPABASE credentials not set — status update in memory only")
        return {"ticket_number": ticket_number, "status": status, "updated_at": now}

    # Fetch current ticket to update notes
    current = _fetch_ticket(ticket_number)
    if not current:
        raise LookupError(f"Ticket {ticket_number} not found")

    # Append note to notes array
    if note:
        existing_notes = json.loads(current.get("notes") or "[]")
        existing_notes.append({
            "timestamp": now,
            "status": status,
            "note": note,
            "by": updated_by or "system",
        })
        update_payload["notes"] = json.dumps(existing_notes)

    try:
        url = f"{SUPABASE_URL}/rest/v1/{TICKETS_TABLE}"
        params = {"ticket_number": f"eq.{ticket_number}"}
        resp = _session.patch(url, headers=_supabase_headers(), params=params, json=update_payload, timeout=12)
        resp.raise_for_status()
        updated = resp.json()
        result = updated[0] if updated else {**current, **update_payload}
        logger.info(f"Ticket {ticket_number} updated to status: {status}")
        return result
    except requests.RequestException as e:
        logger.error(f"Supabase ticket update failed for {ticket_number}: {e}")
        return {"ticket_number": ticket_number, "status": status, "_error": str(e)}


def get_open_tickets(
    building_id: Optional[str] = None,
    urgency: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """
    Retrieve all open tickets for a building (or all buildings if building_id is None).

    Args:
        building_id:  Filter by building BBL/MDS code (optional)
        urgency:      Filter by urgency: emergency/urgent/routine (optional)
        category:     Filter by category (optional)
        limit:        Max records to return

    Returns:
        List of ticket dicts, sorted by urgency (emergency first) then created_at.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.warning("SUPABASE credentials not set — returning empty ticket list")
        return []

    params: dict = {
        "status": "in.(Open,Assigned,In Progress)",
        "order": "created_at.desc",
        "limit": str(limit),
        "select": "*",
    }

    if building_id:
        params["building_id"] = f"eq.{building_id}"
    if urgency:
        params["urgency"] = f"eq.{urgency.lower()}"
    if category:
        params["category"] = f"eq.{category.lower()}"

    try:
        url = f"{SUPABASE_URL}/rest/v1/{TICKETS_TABLE}"
        resp = _session.get(url, headers=_supabase_headers(), params=params, timeout=12)
        resp.raise_for_status()
        tickets = resp.json()

        # Sort: emergency > urgent > routine, then by created_at
        urgency_order = {"emergency": 0, "urgent": 1, "routine": 2}
        tickets.sort(key=lambda t: (urgency_order.get(t.get("urgency", "routine"), 9), t.get("created_at", "")))

        logger.info(
            f"Retrieved {len(tickets)} open tickets"
            f"{f' for building {building_id}' if building_id else ''}"
        )
        return tickets
    except requests.RequestException as e:
        logger.error(f"Supabase ticket query failed: {e}")
        return []


def get_ticket(ticket_number: str) -> Optional[dict]:
    """Retrieve a single ticket by ticket number."""
    return _fetch_ticket(ticket_number)


def search_tickets(
    resident_id: Optional[str] = None,
    unit: Optional[str] = None,
    status_list: Optional[list[str]] = None,
    limit: int = 50,
) -> list[dict]:
    """
    Search tickets by resident, unit, or status.

    Args:
        resident_id:  Filter by resident identifier
        unit:         Filter by unit number
        status_list:  Filter by one or more statuses
        limit:        Max records

    Returns:
        List of matching ticket dicts.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []

    params: dict = {"order": "created_at.desc", "limit": str(limit), "select": "*"}
    if resident_id:
        params["resident_id"] = f"eq.{resident_id}"
    if unit:
        params["unit"] = f"eq.{unit}"
    if status_list:
        status_in = ",".join(status_list)
        params["status"] = f"in.({status_in})"

    try:
        url = f"{SUPABASE_URL}/rest/v1/{TICKETS_TABLE}"
        resp = _session.get(url, headers=_supabase_headers(), params=params, timeout=12)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error(f"Ticket search failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_ticket(ticket_number: str) -> Optional[dict]:
    """Fetch a single ticket from Supabase."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        url = f"{SUPABASE_URL}/rest/v1/{TICKETS_TABLE}"
        params = {"ticket_number": f"eq.{ticket_number}", "select": "*", "limit": "1"}
        resp = _session.get(url, headers=_supabase_headers(), params=params, timeout=10)
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None
    except requests.RequestException as e:
        logger.error(f"Ticket fetch failed for {ticket_number}: {e}")
        return None


def format_ticket_summary(ticket: dict) -> str:
    """Format a ticket as a human-readable summary."""
    notes_raw = ticket.get("notes") or "[]"
    try:
        notes_list = json.loads(notes_raw) if isinstance(notes_raw, str) else notes_raw
    except json.JSONDecodeError:
        notes_list = []

    urgency_label = {"emergency": "🚨 EMERGENCY", "urgent": "⚠️  URGENT", "routine": "ℹ️  ROUTINE"}.get(
        ticket.get("urgency", "routine"), "ROUTINE"
    )

    lines = [
        f"Ticket: {ticket.get('ticket_number', 'N/A')}",
        f"Status: {ticket.get('status', 'Unknown')}",
        f"Urgency: {urgency_label}",
        f"Category: {ticket.get('category', '').upper()}",
        f"Unit: {ticket.get('unit', 'N/A')} | Building: {ticket.get('building_id', 'N/A')}",
        f"Created: {ticket.get('created_at', 'N/A')[:19]}",
        f"Assigned: {ticket.get('assigned_to') or 'Unassigned'}",
        f"Description: {ticket.get('description', '')[:200]}",
    ]
    if notes_list:
        lines.append(f"Notes ({len(notes_list)}):")
        for note in notes_list[-3:]:  # Show last 3 notes
            lines.append(f"  [{note.get('timestamp', '')[:10]}] {note.get('note', '')[:100]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Supabase schema helper (run once to create table)
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
-- Run this in your Supabase SQL editor to create the tickets table:

CREATE TABLE IF NOT EXISTS tickets (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_number   text UNIQUE NOT NULL,
    building_id     text,
    resident_id     text,
    unit            text,
    category        text NOT NULL,
    urgency         text NOT NULL,
    description     text,
    status          text NOT NULL DEFAULT 'Open',
    assigned_to     text,
    notes           jsonb DEFAULT '[]'::jsonb,
    channel         text DEFAULT 'unknown',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    resolved_at     timestamptz
);

CREATE INDEX IF NOT EXISTS tickets_building_id_idx ON tickets (building_id);
CREATE INDEX IF NOT EXISTS tickets_status_idx ON tickets (status);
CREATE INDEX IF NOT EXISTS tickets_urgency_idx ON tickets (urgency);
CREATE INDEX IF NOT EXISTS tickets_resident_id_idx ON tickets (resident_id);
CREATE INDEX IF NOT EXISTS tickets_created_at_idx ON tickets (created_at DESC);
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    # Demo: create and update a ticket
    ticket = create_ticket(
        resident_id="jane.smith@email.com",
        unit="4B",
        category="maintenance",
        description="Dripping faucet in kitchen. Has been going for 3 days.",
        urgency="routine",
        building_id="2025010012",
        channel="email",
    )
    print("\nCreated ticket:")
    print(format_ticket_summary(ticket))

    # Update status
    if ticket.get("ticket_number"):
        updated = update_ticket_status(
            ticket["ticket_number"],
            status="Assigned",
            note="Assigned to Mike (maintenance) — scheduled for Thursday 10am",
            updated_by="frontdesk@camelot.nyc",
        )
        print("\nUpdated ticket:")
        print(format_ticket_summary(updated))
