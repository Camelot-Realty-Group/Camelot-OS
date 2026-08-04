"""
spire_client.py — Shared client for Camelot's "Spire" property-management API
Camelot Property Management Services Corp.

Spire (camelot.spiremds.com) is Camelot's system of record for buildings,
GL budgets, and GL actuals. This module is shared by every bot that needs to
pull a building's financials directly from Spire instead of requiring a
manual file upload (CostBeat Bot today; Perseus is expected to use it too —
see the note at the bottom of this file about de-duplication).

Live API reference used to build this: the public Swagger docs at
https://camelot.spiremds.com/api/swagger/docs/v2 (no auth required to view).
Confirmed from that spec:

  POST /api/Authorize
      body: {"APIKey": "<key>", "ClientSecret": "<secret>"}
      returns: the raw JWT bearer token AS THE RESPONSE BODY STRING (no
               wrapper object). Token is valid ~15 minutes. No refresh token
               is issued — re-authorize from scratch when it's near expiry.

  All other endpoints require header: Authorization: Bearer <token>

  GET /api/RM/BuildingsList?SearchCriteria=...
      -> array of BuildingListQueryResult: BuildingRcd, RentalBuildingName,
         Address, City, State, ZipCode, TotalUnits, NumberOfUnits, ...
      Used for the buildings dropdown (list_buildings()).

  GET /api/PM/Lookup/Company?SearchCriteria=...
      -> array of CompanyResults: Rcd, CompanyCode, CompanyName, GlChartRcd
      A building's budget/GL activity is booked against a CompanyRcd, not
      directly against the BuildingRcd, so this is used to resolve one from
      the other via the company name/code (see _resolve_company_rcd).

  GET /api/GL/Budgets?Page=&Year=&CompanyRcd=&GlAccountRcd=
      -> BudgetResults { PageNumber, TotalPages, TotalResults,
                         ResultsOnThisPage, Results: [BudgetResults2] }
         BudgetResults2: BudgetDate, CompanyRcd, CompanyCode, CompanyName,
                         SubsidiaryRcd, SubsidiaryCode, SubsidiaryName,
                         Amount, GLAccountRcd
      Paginated at 250/page. Used for get_budget().

  GET /api/GL/GLSummary?CompanyRcd=&GlAccountRcd=&PeriodFrom=&PeriodTo=&FiscalYear=
      -> array of GlSummaryResults: GlAccountRcd, GlAccountNumber, CompanyRcd,
         CompanyName, Period, Debits, Credits, NetChange, RunningBalance, ...
      CompanyRcd AND GlAccountRcd are both required per-account, so a full
      GL actuals pull for a building means looping every GL account on its
      chart. Used for get_gl_actuals().

  GET /api/GL/Lookup/GlAccount?GlChartRcd=&SearchCriteria=
      -> array of GlAccountResults (account number + name) — used to label
         GL account codes returned bare (by rcd) from Budgets/GLSummary.

Credentials: read from environment variables only. NEVER hardcode, log, or
print SPIRE_API_KEY / SPIRE_CLIENT_SECRET, and never place them in test
fixtures or commit messages.

  SPIRE_API_KEY        required
  SPIRE_CLIENT_SECRET  required
  SPIRE_BASE_URL       optional, defaults to https://camelot.spiremds.com/api

NOTE ON DE-DUPLICATION: if this file is added independently on more than one
feature branch before they all merge (e.g. this CostBeat branch and a
Perseus branch), the copies will need to be reconciled/de-duplicated as part
of merging — they should converge on this same module in utils/.

Author: Camelot OS
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("camelot.spire_client")

DEFAULT_BASE_URL = "https://camelot.spiremds.com/api"

# Re-authenticate this many seconds before the token's actual expiry, so a
# request never gets launched with a token that expires mid-flight.
TOKEN_EXPIRY_SECONDS = 15 * 60
TOKEN_REFRESH_MARGIN_SECONDS = 60

REQUEST_TIMEOUT_SECONDS = 10
NETWORK_RETRY_TOTAL = 1
NETWORK_RETRY_BACKOFF = 0.5


class SpireError(Exception):
    """Base class for all Spire client errors — always caught, never crashes the bot."""


class SpireNotConfigured(SpireError):
    """Raised when SPIRE_API_KEY / SPIRE_CLIENT_SECRET are missing from the environment.

    Callers should catch this and fall back to manual file upload with a
    visible "Spire not configured — use file upload" message, not a 500.
    """


class SpireAuthError(SpireError):
    """Raised when /Authorize itself fails (bad credentials, Spire down, etc.)."""


class SpireRequestError(SpireError):
    """Raised when an authenticated request fails after the one allowed retry."""


# ---------------------------------------------------------------------------
# Line-item shape shared by every bot's downstream pipeline
# ---------------------------------------------------------------------------

@dataclass
class SpireLineItem:
    """
    The common shape each bot's own file parser already produces, so the
    rest of the pipeline (category normalizer, benchmark comparison,
    analyzer, fee engine, report generator) is reused unchanged regardless
    of whether the data came from a file upload or from Spire.
    """

    account_code: str
    label: str
    amount: float

    def as_dict(self) -> dict:
        return {"account_code": self.account_code, "label": self.label, "amount": self.amount}


@dataclass
class SpireBuilding:
    """One entry for the bot UI's building dropdown."""

    building_id: int
    name: str
    address: str
    unit_count: Optional[int] = None
    company_rcd: Optional[int] = None
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "building_id": self.building_id,
            "name": self.name,
            "address": self.address,
            "unit_count": self.unit_count,
            "company_rcd": self.company_rcd,
        }


# ---------------------------------------------------------------------------
# SpireClient
# ---------------------------------------------------------------------------

class SpireClient:
    """
    Thin authenticated client for Camelot's Spire property-management API.

    Construction reads credentials from the environment and raises
    SpireNotConfigured (a caught, handled error — never a crash) if they are
    missing, so callers can do:

        try:
            client = SpireClient()
        except SpireNotConfigured:
            # disable the "Pull from Spire" UI option, keep file upload
            ...
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("SPIRE_API_KEY")
        self.client_secret = (
            client_secret if client_secret is not None else os.getenv("SPIRE_CLIENT_SECRET")
        )
        self.base_url = (base_url or os.getenv("SPIRE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

        if not self.api_key or not self.client_secret:
            raise SpireNotConfigured(
                "Spire is not configured — SPIRE_API_KEY and SPIRE_CLIENT_SECRET "
                "must both be set. Use file upload instead."
            )

        self._session = session or self._build_session()
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._token_issued_at: float = 0.0

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=NETWORK_RETRY_TOTAL,
            backoff_factor=NETWORK_RETRY_BACKOFF,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=None,  # retry on GET and POST alike
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.mount("http://", HTTPAdapter(max_retries=retry))
        return session

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _token_is_fresh(self) -> bool:
        if not self._token:
            return False
        age = time.monotonic() - self._token_issued_at
        return age < (TOKEN_EXPIRY_SECONDS - TOKEN_REFRESH_MARGIN_SECONDS)

    def _authorize(self) -> str:
        """POST /Authorize and cache the raw JWT string. Never logs credentials or the token."""
        url = f"{self.base_url}/Authorize"
        try:
            response = self._session.post(
                url,
                json={"APIKey": self.api_key, "ClientSecret": self.client_secret},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise SpireAuthError(f"Could not reach Spire to authorize: {exc}") from exc

        if response.status_code != 200:
            raise SpireAuthError(
                f"Spire authorization failed (HTTP {response.status_code})."
            )

        # The response body IS the token (a raw string), not a JSON wrapper object.
        token = response.text.strip().strip('"')
        if not token:
            raise SpireAuthError("Spire authorization returned an empty token.")

        self._token = token
        self._token_issued_at = time.monotonic()
        logger.info("Spire: obtained a new bearer token.")
        return token

    def _get_token(self) -> str:
        with self._lock:
            if self._token_is_fresh():
                return self._token  # type: ignore[return-value]
            return self._authorize()

    # ------------------------------------------------------------------
    # Request plumbing
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, *, params: Optional[dict] = None,
                 _retried_after_401: bool = False) -> Any:
        token = self._get_token()
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            response = self._session.request(
                method, url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise SpireRequestError(f"Spire request to {path} failed: {exc}") from exc

        if response.status_code == 401 and not _retried_after_401:
            # Token may have expired early or been invalidated server-side —
            # re-authenticate once, transparently, and retry the request.
            logger.warning("Spire: got 401 on %s, re-authenticating and retrying once.", path)
            with self._lock:
                self._token = None
            return self._request(method, path, params=params, _retried_after_401=True)

        if response.status_code == 401:
            raise SpireRequestError(f"Spire request to {path} is unauthorized even after re-auth.")

        if response.status_code >= 400:
            raise SpireRequestError(
                f"Spire request to {path} failed (HTTP {response.status_code}): "
                f"{response.text[:300]}"
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise SpireRequestError(f"Spire returned non-JSON response from {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def list_buildings(self, search: Optional[str] = None) -> list[SpireBuilding]:
        """
        GET /RM/BuildingsList — Camelot's managed buildings, for the bot's
        building-picker dropdown.
        """
        params = {"SearchCriteria": search} if search else {}
        raw = self._request("GET", "/RM/BuildingsList", params=params) or []

        buildings: list[SpireBuilding] = []
        for row in raw:
            unit_count = (
                row.get("TotalUnits")
                or row.get("NumberOfUnits")
                or row.get("Units")
            )
            address_parts = [
                row.get("Address") or row.get("Address1") or "",
                row.get("City") or "",
                row.get("State") or "",
                row.get("ZipCode") or "",
            ]
            address = ", ".join(p for p in address_parts if p)
            buildings.append(
                SpireBuilding(
                    building_id=row.get("BuildingRcd") or row.get("ID"),
                    name=row.get("RentalBuildingName") or row.get("BuildingNumber") or "",
                    address=address,
                    unit_count=int(unit_count) if unit_count else None,
                    company_rcd=row.get("RentalCompanyRcd") or row.get("CoopCondoCompanyRcd"),
                    raw=row,
                )
            )
        return buildings

    def _resolve_company_rcd(self, building_id: int) -> int:
        """
        Budgets/GL activity in Spire are booked to a CompanyRcd, not directly
        to a BuildingRcd. Resolve it from the buildings list (RentalCompanyRcd
        / CoopCondoCompanyRcd), matching on BuildingRcd.
        """
        for building in self.list_buildings():
            if building.building_id == building_id and building.company_rcd:
                return building.company_rcd
        raise SpireRequestError(
            f"Could not resolve a CompanyRcd for BuildingRcd={building_id} via Spire."
        )

    def get_budget(self, building_id: int, year: int) -> list[SpireLineItem]:
        """
        GET /GL/Budgets — annual budget line items for a building's company,
        for a given fiscal year. Paginated 250/page; all pages are collected.

        Returns the SAME {account_code, label, amount} shape the file-upload
        parser produces, so it can feed the existing analyzer unchanged.
        """
        company_rcd = self._resolve_company_rcd(building_id)

        # GL account rcds don't come with human-readable labels on the
        # Budgets endpoint itself, so build a lookup once per call.
        account_labels = self._gl_account_labels()

        items: list[SpireLineItem] = []
        page = 1
        while True:
            data = self._request(
                "GET", "/GL/Budgets",
                params={"Page": page, "Year": year, "CompanyRcd": company_rcd},
            ) or {}
            rows = data.get("Results") or []
            for row in rows:
                account_rcd = row.get("GLAccountRcd")
                amount = row.get("Amount")
                if amount is None:
                    continue
                items.append(
                    SpireLineItem(
                        account_code=str(account_rcd) if account_rcd is not None else "",
                        label=account_labels.get(account_rcd, f"GL Account {account_rcd}"),
                        amount=abs(float(amount)),
                    )
                )
            total_pages = data.get("TotalPages") or 1
            if page >= total_pages:
                break
            page += 1

        return items

    def get_gl_actuals(
        self, building_id: int, period_start: str, period_end: str,
    ) -> list[SpireLineItem]:
        """
        GET /GL/GLSummary — actual GL activity for a building's company over
        a date range (period_start/period_end as "YYYY-MM-DD"), for the
        quarterly variance use case. GLSummary is per-GL-account, so every
        account on the company's chart is queried and the results merged.

        Returns the same {account_code, label, amount} shape as get_budget()
        and the file-upload parser (amount = NetChange for the period).
        """
        company_rcd = self._resolve_company_rcd(building_id)
        account_labels = self._gl_account_labels()

        items: list[SpireLineItem] = []
        for account_rcd, label in account_labels.items():
            rows = self._request(
                "GET", "/GL/GLSummary",
                params={
                    "CompanyRcd": company_rcd,
                    "GlAccountRcd": account_rcd,
                    "PeriodFrom": period_start,
                    "PeriodTo": period_end,
                },
            ) or []
            net_change = sum(float(r.get("NetChange") or 0) for r in rows)
            if net_change == 0:
                continue
            items.append(
                SpireLineItem(
                    account_code=str(account_rcd),
                    label=label,
                    amount=abs(net_change),
                )
            )
        return items

    def _gl_account_labels(self) -> dict[int, str]:
        """GET /GL/Lookup/GlAccount — map GlAccountRcd -> a human-readable label."""
        rows = self._request("GET", "/GL/Lookup/GlAccount", params={}) or []
        labels: dict[int, str] = {}
        for row in rows:
            rcd = row.get("Rcd") or row.get("GlAccountRcd")
            if rcd is None:
                continue
            number = row.get("AccountNumber") or row.get("GlAccountNumber") or ""
            name = row.get("GlAccountName") or row.get("Name") or ""
            labels[rcd] = f"{number} {name}".strip() if (number or name) else f"GL Account {rcd}"
        return labels


# ---------------------------------------------------------------------------
# Adapter: Spire JSON -> costbeat_bot's internal ParsedBudget/BudgetLine shape
# ---------------------------------------------------------------------------
#
# This keeps every downstream module (benchmarks, analyzer, fee_engine,
# report_generator) completely untouched — they only ever see a ParsedBudget
# with plain expense BudgetLine rows, whether it came from an uploaded file
# or from Spire.

def spire_budget_to_parsed_budget(
    line_items: list[SpireLineItem], *, building_name: str, year: int,
):
    """
    Convert SpireClient.get_budget() output into a costbeat_bot ParsedBudget,
    so analyzer.analyze() can consume it exactly like a file upload.

    Imports costbeat_bot.parser lazily so this shared utils module has no
    hard dependency on any one bot's package layout.
    """
    from parser import BudgetLine, ParsedBudget  # costbeat_bot/parser.py

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
