"""
storage.py — Perseus Supabase persistence
=========================================
Thin Supabase REST wrapper plus:

* CRUD for `perseus_variance_reports` — one row per period analysed per
  building, so a history accumulates even though each period's fee ask is
  priced on its own.
* A read-only lookup into CostBeat Bot's `costbeat_analyses` for a building's
  already-uploaded annual budget, which Perseus uses as its variance baseline.

Uses the PostgREST endpoint with the service-role key, matching the pattern in
`orchestrator/memory.py` and `costbeat_bot/storage.py`. Every Perseus table has
RLS enabled; the service key bypasses it. See supabase_schema.sql.

Author: Camelot OS
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("perseus_bot.storage")


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
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for Perseus to "
                "read portfolio benchmarks and store variance reports."
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
# perseus_variance_reports
# ---------------------------------------------------------------------------

LIST_COLUMNS = (
    "id,property_name,address,quarter,year,budget_source,total_budget_period,"
    "total_actual_period,budget_variance,budget_variance_pct,"
    "portfolio_savings_opportunity,recommended_fee_model,status,created_at"
)


def save_report(table: str, record: dict[str, Any]) -> dict[str, Any]:
    """Insert one variance report and return it (including its generated id)."""
    saved = SupabaseREST().insert(table, record)
    logger.info(
        "Stored Perseus variance report %s for '%s' %s %s",
        saved.get("id"), record.get("property_name"),
        record.get("quarter"), record.get("year"),
    )
    return saved


def list_reports(table: str, limit: int = 50, property_name: str = "") -> list[dict[str, Any]]:
    """Return recent reports, newest first, without the heavy jsonb columns."""
    params: dict[str, str] = {
        "select": LIST_COLUMNS,
        "order": "created_at.desc",
        "limit": str(limit),
    }
    if property_name:
        params["property_name"] = f"ilike.*{property_name}*"
    return SupabaseREST().select(table, params)


def get_report(table: str, report_id: str) -> Optional[dict[str, Any]]:
    """Return one full variance report record, or None if the id is unknown."""
    rows = SupabaseREST().select(table, {"select": "*", "id": f"eq.{report_id}", "limit": "1"})
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# costbeat_analyses — budget baseline lookup
# ---------------------------------------------------------------------------

def find_costbeat_analysis(
    table: str,
    property_name: str,
    address: str = "",
) -> Optional[dict[str, Any]]:
    """
    Find the most recent CostBeat annual-budget analysis for a building.

    Matching is by property name first, then by address, both case-insensitive
    substring matches — the same building arrives as "245 E 87th" from one staff
    member and "245 East 87th Street" from another. Only rows that actually
    carry `line_items` are returned; an analysis with no parsed budget in it is
    no use as a baseline.

    Returns None when CostBeat is not deployed, its table does not exist, or it
    holds nothing for this building. The caller then falls back to an uploaded
    annual budget file.
    """
    client = SupabaseREST()
    columns = "id,property_name,address,unit_count,building_type,market,total_budget,line_items,created_at"

    for key, value in (("property_name", property_name), ("address", address)):
        if not value:
            continue
        try:
            rows = client.select(table, {
                "select": columns,
                key: f"ilike.*{value}*",
                "order": "created_at.desc",
                "limit": "5",
            })
        except requests.HTTPError as exc:
            # CostBeat's PR may not have merged, so its table may not exist.
            # That is an expected state, not an error worth failing the run for.
            logger.info("Could not read '%s' by %s (%s) — using uploaded baseline.", table, key, exc)
            return None

        for row in rows:
            if row.get("line_items"):
                logger.info(
                    "Matched CostBeat analysis %s on %s for budget baseline.", row.get("id"), key
                )
                return row

    logger.info("No CostBeat analysis with line items found for '%s'.", property_name or address)
    return None
