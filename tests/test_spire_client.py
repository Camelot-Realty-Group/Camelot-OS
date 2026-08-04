"""
Tests for utils/spire_client.py.

Spire is Camelot's property-management system of record; this client is the
one thing standing between a bot process and a bearer token that only lives
for 15 minutes. The rules these tests exist to protect: never call Spire with
a stale/expired token, never call Spire at all without credentials (fail with
a caught, expected error instead), recover from exactly one 401 by
re-authenticating and retrying, and convert Spire's raw JSON into the same
flat {account_code, label, amount} shape every bot's file parser already
produces — never call the real API.
"""
import time

import pytest
import responses

from utils import spire_client
from utils.spire_client import (
    SpireAPIError,
    SpireClient,
    SpireLineItem,
    SpireNotConfigured,
    is_configured,
    line_items_to_dicts,
)

BASE_URL = "https://camelot.spiremds.com/api"


@pytest.fixture(autouse=True)
def _spire_env(monkeypatch):
    """Every test gets working credentials unless it deliberately unsets them."""
    monkeypatch.setenv("SPIRE_API_KEY", "test-api-key")
    monkeypatch.setenv("SPIRE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.delenv("SPIRE_BASE_URL", raising=False)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_missing_credentials_raise_spire_not_configured_not_a_crash(monkeypatch):
    """
    A building manager without Spire credentials set must see a caught
    "use file upload" condition, never an unhandled exception that would
    surface as a 500 to the bot's UI.
    """
    monkeypatch.delenv("SPIRE_API_KEY", raising=False)
    monkeypatch.delenv("SPIRE_CLIENT_SECRET", raising=False)
    with pytest.raises(SpireNotConfigured):
        SpireClient()


def test_is_configured_reflects_env_state(monkeypatch):
    assert is_configured() is True
    monkeypatch.delenv("SPIRE_CLIENT_SECRET", raising=False)
    assert is_configured() is False


def test_custom_base_url_is_respected(monkeypatch):
    monkeypatch.setenv("SPIRE_BASE_URL", "https://staging.spiremds.com/api/")
    client = SpireClient()
    # trailing slash stripped so path joins never produce a double slash
    assert client.base_url == "https://staging.spiremds.com/api"


# ---------------------------------------------------------------------------
# Token fetch / cache / expiry-refresh
# ---------------------------------------------------------------------------

@responses.activate
def test_authenticate_posts_credentials_and_caches_raw_token_string():
    """
    POST /Authorize returns the bearer token AS the response body (a raw
    string), not wrapped in an object — per the live Spire Swagger spec. The
    client must accept that shape and cache the token rather than re-auth on
    every call.
    """
    responses.add(
        responses.POST, f"{BASE_URL}/Authorize",
        json="a-raw-jwt-token", status=200,
    )
    responses.add(
        responses.GET, f"{BASE_URL}/RM/BuildingsList",
        json=[], status=200,
    )

    client = SpireClient()
    client.list_buildings()
    client.list_buildings()

    auth_calls = [c for c in responses.calls if c.request.url.endswith("/Authorize")]
    assert len(auth_calls) == 1, "second call must reuse the cached token, not re-authenticate"
    assert client._token == "a-raw-jwt-token"


@responses.activate
def test_authenticate_never_logs_or_leaks_credentials_in_error(caplog):
    """Credentials must never appear in a raised error message."""
    responses.add(
        responses.POST, f"{BASE_URL}/Authorize",
        status=500,
    )
    client = SpireClient()
    with pytest.raises(SpireAPIError) as exc_info:
        client._authenticate()
    assert "test-client-secret" not in str(exc_info.value)
    assert "test-api-key" not in str(exc_info.value)


@responses.activate
def test_token_refreshes_automatically_before_15_minute_expiry(monkeypatch):
    """
    A token issued more than (15 min - 60s refresh margin) ago must trigger a
    fresh /Authorize call rather than being sent stale.
    """
    responses.add(
        responses.POST, f"{BASE_URL}/Authorize",
        json="token-1", status=200,
    )
    client = SpireClient()
    client._authenticate()
    assert client._token == "token-1"

    # Simulate 14 minutes having passed — inside the 60s refresh margin of expiry.
    client._token_issued_at = time.monotonic() - (14 * 60)
    assert client._token_expired() is True

    responses.replace(responses.POST, f"{BASE_URL}/Authorize", json="token-2", status=200)
    responses.add(responses.GET, f"{BASE_URL}/RM/BuildingsList", json=[], status=200)
    client.list_buildings()
    assert client._token == "token-2"


def test_token_not_expired_well_within_ttl():
    client = SpireClient()
    client._token = "still-fresh"
    client._token_issued_at = time.monotonic() - 60  # 1 minute old
    assert client._token_expired() is False


# ---------------------------------------------------------------------------
# 401 → re-authenticate → retry once
# ---------------------------------------------------------------------------

@responses.activate
def test_401_triggers_one_reauth_and_retry_then_succeeds():
    """
    A 401 on any authenticated call must clear the cached token, re-authenticate
    once, and retry the original request once — transparently, so callers never
    see the 401 if the retry succeeds.
    """
    responses.add(responses.POST, f"{BASE_URL}/Authorize", json="token-1", status=200)
    responses.add(
        responses.GET, f"{BASE_URL}/RM/BuildingsList",
        json={"detail": "expired"}, status=401,
    )
    responses.add(responses.POST, f"{BASE_URL}/Authorize", json="token-2", status=200)
    responses.add(
        responses.GET, f"{BASE_URL}/RM/BuildingsList",
        json=[{"CoopCondoCompanyRcd": 42, "RentalBuildingName": "Test Tower",
               "Address": "1 Test Plaza", "TotalUnits": 10, "BuildingRcd": 1}],
        status=200,
    )

    client = SpireClient()
    buildings = client.list_buildings()

    assert len(buildings) == 1
    assert buildings[0].building_id == "42"
    assert client._token == "token-2"
    auth_calls = [c for c in responses.calls if c.request.url.endswith("/Authorize")]
    assert len(auth_calls) == 2


@responses.activate
def test_second_consecutive_401_surfaces_as_spire_api_error():
    """If re-authenticating doesn't fix the 401, the client must give up and
    raise rather than looping forever."""
    responses.add(responses.POST, f"{BASE_URL}/Authorize", json="token-1", status=200)
    responses.add(responses.GET, f"{BASE_URL}/RM/BuildingsList", status=401)
    responses.add(responses.POST, f"{BASE_URL}/Authorize", json="token-2", status=200)
    responses.add(responses.GET, f"{BASE_URL}/RM/BuildingsList", status=401)

    client = SpireClient()
    with pytest.raises(SpireAPIError):
        client.list_buildings()

    auth_calls = [c for c in responses.calls if c.request.url.endswith("/Authorize")]
    assert len(auth_calls) == 2, "must not retry a third time"


# ---------------------------------------------------------------------------
# list_buildings()
# ---------------------------------------------------------------------------

@responses.activate
def test_list_buildings_prefers_coop_condo_company_over_rental_company():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", json="tok", status=200)
    responses.add(
        responses.GET, f"{BASE_URL}/RM/BuildingsList",
        json=[{
            "CoopCondoCompanyRcd": 100, "RentalCompanyRcd": 200,
            "RentalBuildingName": "Both Co", "Address": "5 Both St",
            "TotalUnits": 20, "BuildingRcd": 9,
        }],
        status=200,
    )
    client = SpireClient()
    buildings = client.list_buildings()
    assert buildings[0].building_id == "100"


@responses.activate
def test_list_buildings_skips_rows_with_neither_company_id():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", json="tok", status=200)
    responses.add(
        responses.GET, f"{BASE_URL}/RM/BuildingsList",
        json=[
            {"RentalBuildingName": "No Company Rcd"},
            {"CoopCondoCompanyRcd": 55, "RentalBuildingName": "Valid Building"},
        ],
        status=200,
    )
    client = SpireClient()
    buildings = client.list_buildings()
    assert len(buildings) == 1
    assert buildings[0].name == "Valid Building"


# ---------------------------------------------------------------------------
# get_budget() — pagination + account join
# ---------------------------------------------------------------------------

@responses.activate
def test_get_budget_paginates_and_joins_account_labels():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", json="tok", status=200)
    responses.add(
        responses.GET, f"{BASE_URL}/GL/Lookup/GlAccount",
        json=[
            {"Rcd": 1, "AccountNumber": "5100", "GlAccountName": "Electricity"},
            {"Rcd": 2, "AccountNumber": "5200", "GlAccountName": "Water & Sewer"},
        ],
        status=200,
    )
    responses.add(
        responses.GET, f"{BASE_URL}/GL/Budgets",
        json={
            "PageNumber": 1, "TotalPages": 2, "TotalResults": 2, "ResultsOnThisPage": 1,
            "Results": [{"GLAccountRcd": 1, "Amount": 12000.0}],
        },
        status=200,
    )
    responses.add(
        responses.GET, f"{BASE_URL}/GL/Budgets",
        json={
            "PageNumber": 2, "TotalPages": 2, "TotalResults": 2, "ResultsOnThisPage": 1,
            "Results": [{"GLAccountRcd": 2, "Amount": 9000.0}],
        },
        status=200,
    )

    client = SpireClient()
    items = client.get_budget("42", 2026)

    assert len(items) == 2
    by_code = {i.account_code: i for i in items}
    assert by_code["5100"].label == "Electricity"
    assert by_code["5100"].amount == 12000.0
    assert by_code["5200"].amount == 9000.0

    budget_calls = [c for c in responses.calls if "/GL/Budgets" in c.request.url]
    assert len(budget_calls) == 2, "must follow TotalPages rather than stopping at page 1"


# ---------------------------------------------------------------------------
# get_gl_actuals() — per-account rollup, skips zero-activity accounts
# ---------------------------------------------------------------------------

@responses.activate
def test_get_gl_actuals_sums_net_change_per_account_and_skips_zero_activity():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", json="tok", status=200)
    responses.add(
        responses.GET, f"{BASE_URL}/GL/Lookup/GlAccount",
        json=[
            {"Rcd": 1, "AccountNumber": "5100", "GlAccountName": "Electricity"},
            {"Rcd": 2, "AccountNumber": "5200", "GlAccountName": "Water & Sewer"},
        ],
        status=200,
    )
    responses.add(
        responses.GET, f"{BASE_URL}/GL/GLSummary",
        json=[{"NetChange": 4000.0}, {"NetChange": 4100.0}],
        status=200,
    )
    responses.add(
        responses.GET, f"{BASE_URL}/GL/GLSummary",
        json=[{"NetChange": 0.0}],
        status=200,
    )

    client = SpireClient()
    items = client.get_gl_actuals("42", "2026-01-01", "2026-03-31")

    assert len(items) == 1, "the zero-net-change account must be dropped"
    assert items[0].account_code == "5100"
    assert items[0].amount == pytest.approx(8100.0)


# ---------------------------------------------------------------------------
# Adapter — SpireLineItem → plain dict
# ---------------------------------------------------------------------------

def test_line_items_to_dicts_produces_the_shared_line_item_shape():
    items = [
        SpireLineItem(account_code="5100", label="Electricity", amount=12000.0),
        SpireLineItem(account_code="5200", label="Water & Sewer", amount=9000.0),
    ]
    dicts = line_items_to_dicts(items)
    assert dicts == [
        {"account_code": "5100", "label": "Electricity", "amount": 12000.0},
        {"account_code": "5200", "label": "Water & Sewer", "amount": 9000.0},
    ]


def test_line_items_to_dicts_handles_empty_list():
    assert line_items_to_dicts([]) == []


# ---------------------------------------------------------------------------
# CostBeat-side compatibility surface
#
# costbeat_bot/main.py imports SpireAuthError, SpireRequestError, and
# spire_budget_to_parsed_budget from this module. These names predate the
# CostBeat/Perseus consolidation of utils/spire_client.py into one shared
# file; they are kept as aliases/adapters here so CostBeat keeps working
# unchanged against the consolidated client.
# ---------------------------------------------------------------------------

def test_costbeat_error_aliases_map_to_spire_api_error():
    from utils.spire_client import SpireAuthError, SpireRequestError

    assert SpireAuthError is SpireAPIError
    assert SpireRequestError is SpireAPIError


def test_spire_budget_to_parsed_budget_produces_costbeat_shape():
    from utils.spire_client import spire_budget_to_parsed_budget

    items = [
        SpireLineItem(account_code="5010", label="5010 Superintendent Wages", amount=48000.0),
        SpireLineItem(account_code="5110", label="5110 Electricity", amount=14200.0),
    ]
    parsed = spire_budget_to_parsed_budget(items, building_name="The Story House", year=2026)

    assert parsed.filename == "spire:The Story House:2026"
    assert parsed.source_format == "spire_api"
    assert len(parsed.lines) == 2
    assert {ln.account_code for ln in parsed.lines} == {"5010", "5110"}
    assert sum(ln.amount for ln in parsed.lines) == pytest.approx(62200.0)
