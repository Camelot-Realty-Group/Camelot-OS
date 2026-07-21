"""
audit_log.py — Camelot OS Audit Trail
Camelot Property Management Services Corp.

Structured, append-only audit logging for every consequential bot action
(document generation, routing decisions, outbound messages, downloads).

Why this exists: compliance guidance for AI in property management (HUD
2024 AI guidance, Fair Housing enforcement practice) expects operators to
be able to show WHAT an AI system did, WHEN, and ON WHOSE REQUEST. A
plain application log is not enough — this module writes one structured
JSON line per event to a dedicated audit file that is never truncated by
log rotation of the application logs.

Usage:
    from utils.audit_log import audit_event

    audit_event(
        bot="concierge",
        action="generate_document",
        detail={"template_id": "work-order-request-form"},
        actor="api",            # who initiated: api | cli | orchestrator | scheduler
        outcome="success",      # success | denied | error
    )

Events are written to $AUDIT_LOG_DIR/audit_YYYY-MM.jsonl (default:
logs/audit/). Writing never raises — an audit failure must not take
down the action being audited — but failures are logged to the standard
logger so they are visible.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("camelot.audit")

_LOCK = threading.Lock()


def _audit_dir() -> Path:
    return Path(os.getenv("AUDIT_LOG_DIR", "logs/audit"))


def audit_event(
    bot: str,
    action: str,
    detail: Optional[Dict[str, Any]] = None,
    actor: str = "api",
    outcome: str = "success",
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record one audit event. Returns the event dict (useful for tests and
    for echoing an audit reference back to callers).

    Never raises: on any failure the event is logged to the application
    logger instead, and the event dict is still returned.
    """
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "bot": bot,
        "action": action,
        "actor": actor,
        "outcome": outcome,
        "session_id": session_id,
        "detail": detail or {},
    }
    try:
        directory = _audit_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"audit_{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl"
        line = json.dumps(event, ensure_ascii=False, default=str)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as exc:  # noqa: BLE001 — auditing must never break the action
        logger.error("Audit write failed (%s); event: %s", exc, event)
    return event


def read_events(month: Optional[str] = None) -> list:
    """
    Read audit events for a month ("YYYY-MM", default current month).
    Returns a list of event dicts (empty if no file yet).
    """
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    path = _audit_dir() / f"audit_{month}.jsonl"
    if not path.exists():
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed audit line: %.80s", line)
    return events
