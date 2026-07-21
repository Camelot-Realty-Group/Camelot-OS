"""Tests for orchestrator/bot_registry.py."""
import bot_registry


def test_eight_bots_registered():
    assert len(bot_registry.BOTS) == 8
    assert set(bot_registry.BOTS) == {
        "scout", "broker", "compliance", "frontdesk",
        "index", "concierge", "report", "deal",
    }


def test_no_stale_concierge_tenant_ops():
    """The 'concierge' key must be the document bot, not the old tenant-ops bot."""
    concierge = bot_registry.get_bot("concierge")
    assert "template" in concierge["description"].lower()
    assert concierge["entry_point"] == "concierge_bot/main.py"
    frontdesk = bot_registry.get_bot("frontdesk")
    assert "tenant" in frontdesk["description"].lower()
    assert frontdesk["entry_point"] == "frontdesk_bot/main.py"


def test_api_endpoints_unique_ports():
    apis = bot_registry.get_bots_with_api()
    ports = [bot_registry.BOTS[name]["api_port"] for name in apis]
    assert len(ports) == len(set(ports)), f"duplicate API ports: {ports}"


def test_get_bot_case_insensitive():
    assert bot_registry.get_bot("Scout") is bot_registry.get_bot("scout")
    assert bot_registry.get_bot("nonexistent") is None


def test_validate_action():
    assert bot_registry.validate_action("concierge", "generate_document")
    assert not bot_registry.validate_action("concierge", "create_ticket")
    assert bot_registry.validate_action("frontdesk", "create_ticket")


def test_summary_shape():
    summary = bot_registry.get_bot_summary()
    assert len(summary) == 8
    for item in summary:
        assert {"id", "name", "description", "capabilities", "icon"} <= set(item)
