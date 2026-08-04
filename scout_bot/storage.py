"""
storage.py — Scout Bot Supabase persistence
============================================
Thin Supabase REST (PostgREST) wrapper for the tables the Lead Hunt and
Merlin Inbox features read and write:

    scout_scans               — one row per lead-hunt run
    scout_buildings           — upserted lead/building records
    scout_outreach_log        — outbound outreach history (read-only here)
    merlin_inbound_messages   — captured inbound replies (insert)
    merlin_outbound_messages  — sent-message ledger used for reply matching (read-only here)

Follows the same pattern as `costbeat_bot/storage.py`: a minimal
`requests`-based PostgREST client using the service-role key (bypasses RLS),
with retries on 429/5xx. Both `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` are
required; when missing, `SupabaseUnavailable` is raised and calling routes
should surface it as an HTTP 503 (see main.py).

Author: Camelot OS
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("scout_bot.storage")


class SupabaseUnavailable(RuntimeError):
    """Raised when SUPABASE_URL / SUPABASE_SERVICE_KEY are not configured."""


def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PATCH"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = _make_session()


class SupabaseREST:
    """Minimal PostgREST client scoped to a single Supabase project."""

    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL", "").rstrip("/")
        key = os.getenv("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            raise SupabaseUnavailable(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for Scout Bot's "
                "Lead Hunt / Merlin Inbox features to read or write scout_buildings, "
                "scout_scans, scout_outreach_log, and merlin_*_messages."
            )
        self.base = f"{url}/rest/v1"
        self.key = key

    def _headers(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def select(self, table: str, params: dict[str, Any], timeout: int = 20) -> list[dict[str, Any]]:
        resp = SESSION.get(f"{self.base}/{table}", headers=self._headers(), params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def insert(self, table: str, row: dict[str, Any], timeout: int = 20) -> dict[str, Any]:
        resp = SESSION.post(
            f"{self.base}/{table}",
            headers=self._headers({"Prefer": "return=representation"}),
            json=row,
            timeout=timeout,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if isinstance(rows, list) and rows else {}

    def upsert(
        self,
        table: str,
        row: dict[str, Any],
        on_conflict: str,
        timeout: int = 20,
    ) -> dict[str, Any]:
        """Insert-or-update using PostgREST's `Prefer: resolution=merge-duplicates`
        with `on_conflict` naming the unique/PK column(s)."""
        resp = SESSION.post(
            f"{self.base}/{table}",
            headers=self._headers(
                {"Prefer": "return=representation,resolution=merge-duplicates"}
            ),
            params={"on_conflict": on_conflict},
            json=row,
            timeout=timeout,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if isinstance(rows, list) and rows else {}

    def patch(
        self,
        table: str,
        match: dict[str, str],
        updates: dict[str, Any],
        timeout: int = 20,
    ) -> list[dict[str, Any]]:
        params = dict(match)
        resp = SESSION.patch(
            f"{self.base}/{table}",
            headers=self._headers({"Prefer": "return=representation"}),
            params=params,
            json=updates,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# scout_scans
# ---------------------------------------------------------------------------

def get_scan_for_today(triggered_by: str = "cron") -> Optional[dict[str, Any]]:
    """Return today's lead-hunt scan row, if one already ran (idempotency check)."""
    today = date.today().isoformat()
    rows = SupabaseREST().select(
        "scout_scans",
        {
            "select": "id,name,status,started_at,completed_at,results_count",
            "created_by": f"eq.{triggered_by}",
            "started_at": f"gte.{today}",
            "order": "started_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


def create_scan(name: str, created_by: str = "cron", filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Create a new scout_scans row marking the start of a lead-hunt run."""
    row = {
        "name": name,
        "status": "running",
        "filters": filters or {},
        "created_by": created_by,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    return SupabaseREST().insert("scout_scans", row)


def complete_scan(scan_id: str, results_count: int, status: str = "completed") -> dict[str, Any]:
    rows = SupabaseREST().patch(
        "scout_scans",
        {"id": f"eq.{scan_id}"},
        {
            "status": status,
            "results_count": results_count,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return rows[0] if rows else {}


# ---------------------------------------------------------------------------
# scout_buildings
# ---------------------------------------------------------------------------

def upsert_building(record: dict[str, Any]) -> dict[str, Any]:
    """Upsert a scout_buildings row keyed on `bbl` (unique per NYC tax lot).

    Callers should always populate `bbl` — it is the natural dedup key for
    lead-hunt candidates sourced from NYC Open Data.
    """
    if not record.get("bbl"):
        raise ValueError("upsert_building requires a `bbl` value for dedup")
    return SupabaseREST().upsert("scout_buildings", record, on_conflict="bbl")


def list_buildings_by_bbls(bbls: list[str]) -> list[dict[str, Any]]:
    if not bbls:
        return []
    in_clause = "(" + ",".join(bbls) + ")"
    return SupabaseREST().select(
        "scout_buildings",
        {"select": "id,bbl,address,lead_run_id", "bbl": f"in.{in_clause}"},
    )


# ---------------------------------------------------------------------------
# scout_outreach_log — read-only here (used by Merlin Inbox for reply matching)
# ---------------------------------------------------------------------------

def find_outreach_by_email(contact_email: str, limit: int = 5) -> list[dict[str, Any]]:
    """Most recent outreach log entries sent to a given contact email."""
    return SupabaseREST().select(
        "scout_outreach_log",
        {
            "select": "id,building_id,contact_email,contact_name,subject,status,sent_at",
            "contact_email": f"eq.{contact_email}",
            "order": "sent_at.desc",
            "limit": str(limit),
        },
    )


def mark_outreach_replied(outreach_id: str) -> list[dict[str, Any]]:
    return SupabaseREST().patch(
        "scout_outreach_log",
        {"id": f"eq.{outreach_id}"},
        {"status": "replied", "replied_at": datetime.now(timezone.utc).isoformat()},
    )


def update_building_outreach_status(
    building_id: str,
    outreach_status: str,
    outreach_last_reply: Optional[str] = None,
) -> list[dict[str, Any]]:
    updates: dict[str, Any] = {"outreach_status": outreach_status}
    if outreach_last_reply:
        updates["outreach_last_reply"] = outreach_last_reply
    return SupabaseREST().patch("scout_buildings", {"id": f"eq.{building_id}"}, updates)


# ---------------------------------------------------------------------------
# merlin_outbound_messages — read-only here (thread lookup for reply matching)
# ---------------------------------------------------------------------------

def find_outbound_by_thread(thread_id: str) -> list[dict[str, Any]]:
    return SupabaseREST().select(
        "merlin_outbound_messages",
        {
            "select": "id,building_id,gmail_message_id,thread_id,to_addresses,subject,sent_at",
            "thread_id": f"eq.{thread_id}",
            "order": "sent_at.desc",
        },
    )


# ---------------------------------------------------------------------------
# merlin_inbound_messages
# ---------------------------------------------------------------------------

def inbound_message_exists(message_id: str) -> bool:
    """Check by provider message id (mapped to the `gmail_message_id` column)
    whether this inbound message has already been logged — the idempotency
    guard for repeated /merlin/poll-inbox calls."""
    rows = SupabaseREST().select(
        "merlin_inbound_messages",
        {"select": "id", "gmail_message_id": f"eq.{message_id}", "limit": "1"},
    )
    return bool(rows)


def insert_inbound_message(record: dict[str, Any]) -> dict[str, Any]:
    """Insert a captured inbound reply. Relies on the `gmail_message_id`
    UNIQUE constraint as a second idempotency layer (insert races are
    resolved by the DB, not just the app-level `inbound_message_exists`
    check)."""
    return SupabaseREST().insert("merlin_inbound_messages", record)
