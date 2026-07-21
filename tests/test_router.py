"""Tests for orchestrator/router.py intent classification."""
import router


def _route(text):
    result = router.classify_intent(text)
    assert isinstance(result, router.RoutingDecision), f"unroutable: {text}"
    return result


def test_scout_routing():
    r = _route("Find property management companies in Connecticut")
    assert r.bot_name == "scout"
    assert r.params.get("region") == "CT"


def test_compliance_routing_with_address():
    r = _route("Check violations for 123 Main Street Brooklyn")
    assert r.bot_name == "compliance"
    assert "123 Main Street" in r.params.get("address", "")


def test_frontdesk_routing():
    r = _route("Tenant in unit 4B says heat has been out for two days")
    assert r.bot_name == "frontdesk"


def test_broker_loi_routing_extracts_price():
    r = _route("Draft an LOI for 456 Park Ave at $2.5M")
    assert r.bot_name == "broker"
    assert r.action == "generate_loi"
    assert r.params.get("price") == 2_500_000


def test_concierge_download_routing():
    r = _route("Download the board meeting proxy form")
    assert r.bot_name == "concierge"
    assert r.action == "download_template"


def test_concierge_generate_routing():
    r = _route("Fill in the work order request form for unit 4B")
    assert r.bot_name == "concierge"
    assert r.action == "generate_document"


def test_concierge_list_routing():
    r = _route("Do we have a COI tracking form?")
    assert r.bot_name == "concierge"
    assert r.action == "list_templates"


def test_unroutable_returns_error_with_suggestions():
    result = router.classify_intent("xyzzy plugh")
    assert isinstance(result, router.RouterError)
    assert result.suggestions


def test_empty_input():
    result = router.classify_intent("   ")
    assert isinstance(result, router.RouterError)
