"""
spire_client.py — Shared Spire MDS API client
==============================================
Camelot uses "Spire" (camelot.spiremds.com) as its property-management system
of record. This module is the one client both CostBeat Bot and Perseus import
so a building's budget and GL actuals can be pulled directly from Spire
instead of requiring a staff member to export and upload a file every period.

Endpoint names and parameters below were read directly from Spire's live
public Swagger spec (`GET https://camelot.spiremds.com/api/swagger/docs/v2`,
tag `SPIREAPI` v2) rather than guessed:

* `POST /Authorize` — body `{"APIKey": ..., "ClientSecret": ...}`. The response
  body IS the bearer token (a raw JSON string, not a wrapper object). Swagger
  states it expires after 15 minutes and no refresh token is issued.
* `GET /RM/BuildingsList?SearchCriteria=` — richest buildings listing exposed
  by the spec: building name/address/unit counts plus the `CoopCondoCompanyRcd`
  / `RentalCompanyRcd` fields needed to key the GL endpoints below. (The spec
  also exposes a slimmer `/RM/Lookup/Building` and a `/PM/Lookup/Company`, but
  neither carries address/unit counts, so BuildingsList is the one used for the
  bot UI dropdown.)
* `GET /GL/Budgets?Page=&Year=&CompanyRcd=&GlAccountRcd=` — paginated (250
  rows/page) annual budget lines, keyed by `GLAccountRcd` with an `Amount`.
  There is no separate GL account resolution info on each row, so the account
  number/name is joined in from `GET /GL/Lookup/GlAccount?GlChartRcd=`.
* `GET /GL/GLSummary?CompanyRcd=&GlAccountRcd=&PeriodFrom=&PeriodTo=` — actual
  GL activity (Debits/Credits/NetChange) for ONE account over a date range.
  There is no "all accounts in one call" actuals endpoint in the spec, so
  `get_gl_actuals()` first resolves the company's chart of accounts via
  `GL/Lookup/GlAccount`, then calls GLSummary once per account and rolls the
  results up into the same {account_code, label, amount} shape the budget
  adapter produces, so downstream code never needs to know two calls were made.

Auth/session handling:
* Credentials come from env vars `SPIRE_API_KEY` / `SPIRE_CLIENT_SECRET`
  (`SPIRE_BASE_URL` optional, defaults to `https://camelot.spiremds.com/api`).
  Missing credentials raise `SpireNotConfigured` — a caught, expected condition
  the bot UI turns into "Spire not configured — use file upload" rather than a
  500.
* The bearer token is cached in memory with its issue time and refreshed
  automatically ~1 minute before the ~15-minute expiry, and once transparently
  on any 401 (the request is retried once with a fresh token; a second 401
  is surfaced as a `SpireAPIError`).
* All calls go through `requests` with a 10s timeout and one retry with
  backoff on network errors / 5xx responses (via `urllib3.util.retry.Retry`,
  matching the pattern already used in `perseus_bot/storage.py`).

CONSOLIDATION NOTE
------------------
This file was independently added on both the `feature/costbeat-bot` and
`feature/perseus-bot` branches (each bot needs the same Spire budget/actuals
pull). The two copies have been reconciled into this single shared module as
part of merging both branches into `main`. This version keeps Perseus's
building-id-is-company-rcd model and `is_configured()` / `line_items_to_dicts()`
helpers, plus `SpireError` / `SpireAuthError` / `SpireRequestError` aliases and
the `spire_budget_to_parsed_budget()` adapter so CostBeat's `main.py` keeps
working unchanged against this shared client.

Author: Camelot OS
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("utils.spire_client")

DEFAULT_BASE_URL = "https://camelot.spiremds.com/api"
TOKEN_TTL_SECONDS = 15 * 60          # Spire's documented bearer token lifetime
TOKEN_REFRESH_MARGIN_SECONDS = 60    # re-auth this long before it actually expires
REQUEST_TIMEOUT_SECONDS = 10


class SpireError(Exception):
    """Base class for all Spire client errors — always caught, never crashes a bot."""


class SpireNotConfigured(SpireError):
    """
    Raised when SPIRE_API_KEY / SPIRE_CLIENT_SECRET are not set.

    This is an expected, caught condition — callers should catch it and show
    "Spire not configured — use file upload" rather than let it propagate into
    a 500.
    """


class SpireAPIError(SpireError):
    """Raised when Spire responds with an error the client code can't recover from."""


# Backwards-compatible aliases used by costbeat_bot/main.py and its tests.
# SpireAuthError/SpireRequestError were the costbeat-side names for the same
# failure modes SpireAPIError now covers on this consolidated client.
SpireAuthError = SpireAPIError
SpireRequestError = SpireAPIError


def _make_session() -> requests.Session:
    """One retrying session per process, matching perseus_bot/storage.py's pattern."""
    session = requests.Session()
    retry = Retry(
        total=1,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION = _make_session()


# ---------------------------------------------------------------------------
# Line-item shape shared with every bot's existing parser output
# ---------------------------------------------------------------------------

@dataclass
class SpireLineItem:
    """
    The same {account_code, label, amount} shape each bot's file parser
    already produces, so the rest of the pipeline (category normalizer,
    benchmark comparison, analyzer, fee engine, report generator) is reused
    completely unchanged regardless of whether the figures came from a file
    upload or from Spire.
    """

    account_code: str
    label: str
    amount: float

    def as_dict(self) -> dict[str, Any]:
        return {"account_code": self.account_code, "label": self.label, "amount": self.amount}


@dataclass
class SpireBuilding:
    """One Spire-managed building, for populating the bot UI's building dropdown."""

    building_id: str          # the CompanyRcd to use for budget/GL calls
    name: str
    address: str = ""
    unit_count: int = 0
    building_rcd: Optional[int] = None
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "building_id": self.building_id,
            "name": self.name,
            "address": self.address,
            "unit_count": self.unit_count,
            "building_rcd": self.building_rcd,
        }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SpireClient:
    """
    Thin authenticated wrapper around the Spire MDS REST API.

    Construct one per request (or reuse — token caching is instance-level, not
    global, so each bot process typically keeps one long-lived instance). Raises
    `SpireNotConfigured` at construction time if the required env vars are
    missing, so callers can degrade to file upload before making any network
    call.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        api_key = api_key if api_key is not None else os.getenv("SPIRE_API_KEY", "")
        client_secret = (
            client_secret if client_secret is not None else os.getenv("SPIRE_CLIENT_SECRET", "")
        )
        if not api_key or not client_secret:
            raise SpireNotConfigured(
                "SPIRE_API_KEY and SPIRE_CLIENT_SECRET must be set to pull "
                "budget/actuals from Spire. Use file upload instead."
            )
        self._api_key = api_key
        self._client_secret = client_secret
        self.base_url = (base_url or os.getenv("SPIRE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

        self._token: Optional[str] = None
        self._token_issued_at: float = 0.0

    # ── Auth ─────────────────────────────────────────────────────────────

    def _token_expired(self) -> bool:
        if not self._token:
            return True
        age = time.monotonic() - self._token_issued_at
        return age >= (TOKEN_TTL_SECONDS - TOKEN_REFRESH_MARGIN_SECONDS)

    def _authenticate(self) -> None:
        """
        POST /Authorize. The response body IS the bearer token — a raw JSON
        string, not a wrapper object — per the live Spire Swagger spec.
        Credentials are never logged.
        """
        url = f"{self.base_url}/Authorize"
        try:
            resp = _SESSION.post(
                url,
                json={"APIKey": self._api_key, "ClientSecret": self._client_secret},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise SpireAPIError(f"Could not reach Spire to authenticate: {exc}") from exc

        if resp.status_code != 200:
            raise SpireAPIError(
                f"Spire authentication failed with status {resp.status_code}."
            )

        try:
            token = resp.json()
        except ValueError:
            token = resp.text

        token = (token or "").strip().strip('"')
        if not token:
            raise SpireAPIError("Spire authentication returned an empty token.")

        self._token = token
        self._token_issued_at = time.monotonic()
        logger.info("Authenticated with Spire (token cached for ~%ds).", TOKEN_TTL_SECONDS)

    def _ensure_token(self) -> str:
        if self._token_expired():
            self._authenticate()
        return self._token  # type: ignore[return-value]

    # ── HTTP core ────────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        _retried_after_401: bool = False,
    ) -> Any:
        """
        Issue one authenticated request, retrying once on a 401 with a fresh
        token, and once with backoff on a network error via the session's
        Retry adapter (5xx is retried there too).
        """
        token = self._ensure_token()
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            resp = _SESSION.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise SpireAPIError(f"Spire request to {path} failed: {exc}") from exc

        if resp.status_code == 401 and not _retried_after_401:
            logger.info("Spire returned 401 for %s — re-authenticating and retrying once.", path)
            self._token = None
            return self._request(
                method, path, params=params, json_body=json_body, _retried_after_401=True
            )

        if resp.status_code == 401:
            raise SpireAPIError(f"Spire request to {path} failed authentication twice.")

        if not resp.ok:
            raise SpireAPIError(
                f"Spire request to {path} failed with status {resp.status_code}: {resp.text[:300]}"
            )

        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise SpireAPIError(f"Spire response for {path} was not valid JSON: {exc}") from exc

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, json_body: Optional[dict[str, Any]] = None) -> Any:
        return self._request("POST", path, json_body=json_body)

    # ── Convenience methods ──────────────────────────────────────────────

    def list_buildings(self, search: str = "") -> list[SpireBuilding]:
        """
        Return Camelot's managed buildings via `GET /RM/BuildingsList`, for
        populating a dropdown in the bot UI.

        Each Spire building can carry a rental-side company (`RentalCompanyRcd`)
        and/or a coop/condo-side company (`CoopCondoCompanyRcd`) — one of the
        two is used as the building's `building_id` for the GL calls below,
        preferring the coop/condo company when both are present since that is
        the entity actually holding a GL budget for most Camelot buildings.
        """
        params = {"SearchCriteria": search} if search else {}
        rows = self._get("/RM/BuildingsList", params=params) or []

        buildings: list[SpireBuilding] = []
        for row in rows:
            company_rcd = row.get("CoopCondoCompanyRcd") or row.get("RentalCompanyRcd")
            if not company_rcd:
                continue
            name = row.get("RentalBuildingName") or row.get("BuildingNumber") or ""
            address = row.get("Address") or row.get("Address1") or ""
            unit_count = (
                row.get("TotalUnits")
                or row.get("Units")
                or row.get("NumberOfUnits")
                or 0
            )
            buildings.append(
                SpireBuilding(
                    building_id=str(company_rcd),
                    name=name,
                    address=address,
                    unit_count=int(unit_count or 0),
                    building_rcd=row.get("BuildingRcd"),
                    raw=row,
                )
            )
        logger.info("Spire returned %d building(s) for search=%r.", len(buildings), search)
        return buildings

    def _gl_accounts(self, gl_chart_rcd: Optional[int] = None) -> dict[int, dict[str, str]]:
        """
        Resolve `GET /GL/Lookup/GlAccount` into {GlAccountRcd: {number, name}}
        so budget/actuals rows (which only carry the numeric Rcd) can be
        labeled the way an uploaded report already is.
        """
        params = {"GlChartRcd": gl_chart_rcd} if gl_chart_rcd else {}
        rows = self._get("/GL/Lookup/GlAccount", params=params) or []
        return {
            row["Rcd"]: {
                "number": row.get("AccountNumber") or "",
                "name": row.get("GlAccountName") or "",
            }
            for row in rows
            if row.get("Rcd") is not None
        }

    def get_budget(self, building_id: str, year: int) -> list[SpireLineItem]:
        """
        Return the annual budget line items for a building via
        `GET /GL/Budgets?Page=&Year=&CompanyRcd=`, paginated 250 rows/page,
        joined against `GL/Lookup/GlAccount` for the account number/label.
        """
        company_rcd = int(building_id)
        accounts = self._gl_accounts()

        items: list[SpireLineItem] = []
        page = 1
        while True:
            payload = self._get(
                "/GL/Budgets",
                params={"Page": page, "Year": year, "CompanyRcd": company_rcd},
            ) or {}
            rows = payload.get("Results") or []
            for row in rows:
                account_rcd = row.get("GLAccountRcd")
                account = accounts.get(account_rcd, {})
                items.append(
                    SpireLineItem(
                        account_code=account.get("number") or str(account_rcd or ""),
                        label=account.get("name") or f"GL account {account_rcd}",
                        amount=float(row.get("Amount") or 0.0),
                    )
                )
            total_pages = int(payload.get("TotalPages") or 1)
            if page >= total_pages:
                break
            page += 1

        logger.info(
            "Spire budget for CompanyRcd=%s year=%s: %d line item(s).",
            company_rcd, year, len(items),
        )
        return items

    def get_gl_actuals(
        self, building_id: str, period_start: str, period_end: str
    ) -> list[SpireLineItem]:
        """
        Return actual GL activity for a building over [period_start, period_end]
        (each an ISO "YYYY-MM-DD" date string), for the quarterly variance use
        case.

        The Swagger spec's `GET /GL/GLSummary` takes exactly one `GlAccountRcd`
        per call rather than returning every account at once, so this method
        resolves the company's chart of accounts first, then calls GLSummary
        once per account and rolls each account's `NetChange` entries up into
        one line item — the same shape `get_budget()` and the file parsers
        produce. Accounts with no activity in the period are skipped.
        """
        company_rcd = int(building_id)
        accounts = self._gl_accounts()

        items: list[SpireLineItem] = []
        for account_rcd, account in accounts.items():
            rows = self._get(
                "/GL/GLSummary",
                params={
                    "CompanyRcd": company_rcd,
                    "GlAccountRcd": account_rcd,
                    "PeriodFrom": period_start,
                    "PeriodTo": period_end,
                },
            ) or []
            net_change = sum(float(r.get("NetChange") or 0.0) for r in rows)
            if not net_change:
                continue
            items.append(
                SpireLineItem(
                    account_code=account.get("number") or str(account_rcd),
                    label=account.get("name") or f"GL account {account_rcd}",
                    amount=abs(net_change),
                )
            )

        logger.info(
            "Spire GL actuals for CompanyRcd=%s [%s, %s]: %d line item(s) with activity.",
            company_rcd, period_start, period_end, len(items),
        )
        return items


# ---------------------------------------------------------------------------
# Adapter — Spire line items → the ParsedReport-compatible shape
# ---------------------------------------------------------------------------

def line_items_to_dicts(items: list[SpireLineItem]) -> list[dict[str, Any]]:
    """
    Convert `SpireClient` output into the plain {account_code, label, amount}
    dict shape used as the common hand-off point into each bot's downstream
    pipeline (category normalizer, benchmark comparison, analyzer, fee engine,
    report generator) — kept intentionally free of any bot-specific dataclass
    so this module has no import-time dependency on either bot's package.
    """
    return [item.as_dict() for item in items]


def is_configured() -> bool:
    """True when the env vars needed to construct a SpireClient are present."""
    return bool(os.getenv("SPIRE_API_KEY")) and bool(os.getenv("SPIRE_CLIENT_SECRET"))


# ---------------------------------------------------------------------------
# Adapter: Spire JSON -> costbeat_bot's internal ParsedBudget/BudgetLine shape
# ---------------------------------------------------------------------------
#
# Kept here (rather than only in costbeat_bot) so CostBeat's main.py, which
# imports this function directly from utils.spire_client, keeps working
# unchanged post-consolidation. Mirrors perseus_bot/spire_adapter.py's
# approach of converting the shared SpireLineItem/dict shape into a bot's own
# parser dataclasses so every downstream module (benchmarks, analyzer,
# fee_engine, report_generator) is reused completely unchanged whether the
# figures came from an uploaded file or from Spire.

def spire_budget_to_parsed_budget(
    line_items: list[SpireLineItem], *, building_name: str, year: int,
):
    """
    Convert SpireClient.get_budget() output into a costbeat_bot ParsedBudget,
    so analyzer.analyze() can consume it exactly like a file upload.

    Imports costbeat_bot.parser lazily so this shared utils module has no
    hard dependency on any one bot's package layout.
    """
    try:
        from costbeat_bot.parser import BudgetLine, ParsedBudget
    except ImportError:
        from parser import BudgetLine, ParsedBudget  # costbeat_bot/parser.py, run from bot dir

    budget = ParsedBudget(
        filename=f"spire:{building_name}:{year}",
        source_format="spire_api",
    )
    for item in line_items:
        budget.lines.append(
            BudgetLine(
                raw_label=item.label,
                amount=item.amount,
                account_code=item.account_code or None,
                section="expense",
            )
        )
    budget.reconcile()
    return budget
