"""Tests for utils/audit_log.py."""
import json

from utils import audit_log


def test_audit_event_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
    ev = audit_log.audit_event(
        bot="concierge", action="generate_document",
        detail={"template_id": "work-order-request-form"},
    )
    assert ev["bot"] == "concierge"
    files = list(tmp_path.glob("audit_*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["action"] == "generate_document"
    assert parsed["detail"]["template_id"] == "work-order-request-form"
    assert parsed["outcome"] == "success"


def test_audit_events_append(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
    for i in range(3):
        audit_log.audit_event(bot="test", action=f"a{i}")
    events = audit_log.read_events()
    assert [e["action"] for e in events] == ["a0", "a1", "a2"]


def test_audit_never_raises_on_bad_dir(monkeypatch):
    # Point at an unwritable location; audit_event must not raise
    monkeypatch.setenv("AUDIT_LOG_DIR", "/proc/definitely/not/writable")
    ev = audit_log.audit_event(bot="test", action="x")
    assert ev["action"] == "x"


def test_read_events_missing_month(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
    assert audit_log.read_events("1999-01") == []
