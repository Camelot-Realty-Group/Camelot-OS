"""
storage.py — CostBeat Bot Supabase persistence
===============================================
Thin Supabase REST wrapper plus CRUD for the `costbeat_analyses` table.

Uses the PostgREST endpoint with the service-role key, matching the pattern in
`orchestrator/memory.py` and `report_bot/investor_update.py`. Both CostBeat
tables have RLS enabled; the service key bypasses it. See supabase_schema.sql.

Author: Camelot OS
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("costbeat_bot.storage")


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
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for CostBeat "
                "Bot to read portfolio benchmarks and store analyses."
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


# ---------------------------------------------------------------------------
# costbeat_analyses
# ---------------------------------------------------------------------------

LIST_COLUMNS = (
    "id,property_name,address,unit_count,building_type,market,total_budget,"
    "total_savings,savings_pct,recommended_fee_model,status,created_at"
)


def save_analysis(table: str, record: dict[str, Any]) -> dict[str, Any]:
    """Insert one analysis record and return it (including its generated id)."""
    saved = SupabaseREST().insert(table, record)
    logger.info("Stored CostBeat analysis %s for '%s'", saved.get("id"), record.get("property_name"))
    return saved


def list_analyses(table: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent analyses, newest first, without the heavy jsonb columns."""
    return SupabaseREST().select(
        table, {"select": LIST_COLUMNS, "order": "created_at.desc", "limit": str(limit)}
    )


def get_analysis(table: str, analysis_id: str) -> Optional[dict[str, Any]]:
    """Return one full analysis record, or None if the id is unknown."""
    rows = SupabaseREST().select(
        table, {"select": "*", "id": f"eq.{analysis_id}", "limit": "1"}
    )
    return rows[0] if rows else None
