"""
Tests for utils/spire_client.py.

Uses `responses` to mock all HTTP — the real Spire API is never called.
Credentials used here are dummy test values only, never real secrets.
"""
import time

import pytest
import responses

from utils.spire_client import (
    SpireAuthError,
    SpireClient,
    SpireNotConfigured,
    SpireRequestError,
    spire_budget_to_parsed_budget,
)

BASE_URL = "https://camelot.spiremds.com/api"


def make_client(**overrides):
    kwargs = dict(api_key="test-key", client_secret="test-secret", base_url=BASE_URL)
    kwargs.update(overrides)
    return SpireClient(**kwargs)


# ---------------------------------------------------------------------------
# Construction / configuration
# ---------------------------------------------------------------------------

def test_missing_env_vars_raise_not_configured(monkeypatch):
    monkeypatch.delenv("SPIRE_API_KEY", raising=False)
    monkeypatch.delenv("SPIRE_CLIENT_SECRET", raising=False)
    with pytest.raises(SpireNotConfigured):
        SpireClient()


def test_construction_from_env_vars(monkeypatch):
    monkeypatch.setenv("SPIRE_API_KEY", "env-key")
    monkeypatch.setenv("SPIRE_CLIENT_SECRET", "env-secret")
    client = SpireClient()
    assert client.api_key == "env-key"
    assert client.base_url == "https://camelot.spiremds.com/api"


def test_base_url_defaults_and_env_override(monkeypatch):
    monkeypatch.setenv("SPIRE_API_KEY", "k")
    monkeypatch.setenv("SPIRE_CLIENT_SECRET", "s")
    monkeypatch.delenv("SPIRE_BASE_URL", raising=False)
    assert SpireClient().base_url == "https://camelot.spiremds.com/api"

    monkeypatch.setenv("SPIRE_BASE_URL", "https://custom.example.com/api/")
    assert SpireClient().base_url == "https://custom.example.com/api"


# ---------------------------------------------------------------------------
# Token fetch / cache / expiry-refresh
# ---------------------------------------------------------------------------

@responses.activate
def test_authorize_posts_credentials_and_caches_token():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body="tok-123", status=200)
    client = make_client()

    token1 = client._get_token()
    token2 = client._get_token()

    assert token1 == "tok-123"
    assert token2 == "tok-123"
    # Only one call to /Authorize — the second _get_token used the cache.
    assert len(responses.calls) == 1

    sent_body = responses.calls[0].request.body
    assert b"test-key" in sent_body
    assert b"test-secret" in sent_body


@responses.activate
def test_authorize_failure_raises_auth_error():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body="bad creds", status=400)
    client = make_client()
    with pytest.raises(SpireAuthError):
        client._get_token()


@responses.activate
def test_token_refreshes_after_expiry_margin(monkeypatch):
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body="tok-A", status=200)
    client = make_client()

    assert client._get_token() == "tok-A"

    # Simulate the token being ~14.1 minutes old — inside the 1-minute
    # refresh margin of the 15-minute expiry, so it must be treated as stale.
    client._token_issued_at = time.monotonic() - (14.1 * 60)

    responses.replace(responses.POST, f"{BASE_URL}/Authorize", body="tok-B", status=200)
    assert client._get_token() == "tok-B"
    assert len(responses.calls) == 2


@responses.activate
def test_fresh_token_is_not_dropped_quoted_string():
    # Some APIs return the JWT wrapped in quotes as literal text; strip them.
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body='"tok-quoted"', status=200)
    client = make_client()
    assert client._get_token() == "tok-quoted"


# ---------------------------------------------------------------------------
# 401 triggers re-auth and retries once
# ---------------------------------------------------------------------------

@responses.activate
def test_401_triggers_reauth_and_retries_once():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body="tok-1", status=200)
    responses.add(responses.GET, f"{BASE_URL}/RM/BuildingsList", status=401)
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body="tok-2", status=200)
    responses.add(
        responses.GET, f"{BASE_URL}/RM/BuildingsList", json=[], status=200,
    )

    client = make_client()
    result = client._request("GET", "/RM/BuildingsList")

    assert result == []
    # 2 Authorize calls (initial + re-auth) + 2 BuildingsList calls (401 then success)
    auth_calls = [c for c in responses.calls if c.request.url.endswith("/Authorize")]
    building_calls = [c for c in responses.calls if "/RM/BuildingsList" in c.request.url]
    assert len(auth_calls) == 2
    assert len(building_calls) == 2


@responses.activate
def test_401_after_retry_still_fails_raises():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body="tok-1", status=200)
    responses.add(responses.GET, f"{BASE_URL}/RM/BuildingsList", status=401)
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body="tok-2", status=200)
    responses.add(responses.GET, f"{BASE_URL}/RM/BuildingsList", status=401)

    client = make_client()
    with pytest.raises(SpireRequestError):
        client._request("GET", "/RM/BuildingsList")


@responses.activate
def test_5xx_raises_request_error():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body="tok-1", status=200)
    responses.add(responses.GET, f"{BASE_URL}/RM/BuildingsList", status=500)
    client = make_client()
    with pytest.raises(SpireRequestError):
        client._request("GET", "/RM/BuildingsList")


# ---------------------------------------------------------------------------
# list_buildings()
# ---------------------------------------------------------------------------

@responses.activate
def test_list_buildings_maps_fields():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body="tok", status=200)
    responses.add(
        responses.GET, f"{BASE_URL}/RM/BuildingsList",
        json=[{
            "BuildingRcd": 501,
            "RentalBuildingName": "The Story House",
            "Address": "36 East 22nd Street",
            "City": "New York",
            "State": "NY",
            "ZipCode": "10010",
            "TotalUnits": 34,
            "RentalCompanyRcd": 9001,
        }],
        status=200,
    )
    client = make_client()
    buildings = client.list_buildings()

    assert len(buildings) == 1
    b = buildings[0]
    assert b.building_id == 501
    assert b.name == "The Story House"
    assert "36 East 22nd Street" in b.address
    assert b.unit_count == 34
    assert b.company_rcd == 9001


# ---------------------------------------------------------------------------
# get_budget() — pagination + adapter
# ---------------------------------------------------------------------------

@responses.activate
def test_get_budget_paginates_and_adapts_to_line_items():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body="tok", status=200)
    responses.add(
        responses.GET, f"{BASE_URL}/RM/BuildingsList",
        json=[{"BuildingRcd": 501, "RentalBuildingName": "The Story House",
               "Address": "36 East 22nd", "RentalCompanyRcd": 9001, "TotalUnits": 34}],
        status=200,
    )
    responses.add(
        responses.GET, f"{BASE_URL}/GL/Lookup/GlAccount",
        json=[
            {"Rcd": 1, "AccountNumber": "5010", "GlAccountName": "Superintendent Wages"},
            {"Rcd": 2, "AccountNumber": "5110", "GlAccountName": "Electricity"},
        ],
        status=200,
    )
    responses.add(
        responses.GET, f"{BASE_URL}/GL/Budgets",
        json={
            "PageNumber": 1, "TotalPages": 2, "TotalResults": 2, "ResultsOnThisPage": 1,
            "Results": [{"CompanyRcd": 9001, "Amount": 48000.0, "GLAccountRcd": 1}],
        },
        status=200,
    )
    responses.add(
        responses.GET, f"{BASE_URL}/GL/Budgets",
        json={
            "PageNumber": 2, "TotalPages": 2, "TotalResults": 2, "ResultsOnThisPage": 1,
            "Results": [{"CompanyRcd": 9001, "Amount": 14200.0, "GLAccountRcd": 2}],
        },
        status=200,
    )

    client = make_client()
    items = client.get_budget(501, 2026)

    assert len(items) == 2
    assert {i.account_code for i in items} == {"1", "2"}
    wages = next(i for i in items if i.account_code == "1")
    assert wages.amount == 48000.0
    assert "Superintendent Wages" in wages.label


@responses.activate
def test_get_budget_unresolvable_company_raises():
    responses.add(responses.POST, f"{BASE_URL}/Authorize", body="tok", status=200)
    responses.add(responses.GET, f"{BASE_URL}/RM/BuildingsList", json=[], status=200)

    client = make_client()
    with pytest.raises(SpireRequestError):
        client.get_budget(999, 2026)


# ---------------------------------------------------------------------------
# JSON -> ParsedBudget adapter
# ---------------------------------------------------------------------------

def test_adapter_produces_parsed_budget_expense_lines():
    from utils.spire_client import SpireLineItem

    items = [
        SpireLineItem(account_code="5010", label="5010 Superintendent Wages", amount=48000.0),
        SpireLineItem(account_code="5110", label="5110 Electricity", amount=14200.0),
    ]
    parsed = spire_budget_to_parsed_budget(items, building_name="The Story House", year=2026)

    assert parsed.source_format == "spire_api"
    assert len(parsed.expense_lines) == 2
    assert parsed.total_expense == pytest.approx(62200.0)
    labels = {ln.raw_label for ln in parsed.lines}
    assert "5010 Superintendent Wages" in labels
