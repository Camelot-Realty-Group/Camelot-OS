"""
merlin_inbox.py — Merlin Inbox Poll
=====================================
Rebuild of the Supabase pg_cron job `merlin-inbox-poll` (`*/10 * * * *`,
jobid 2) as a real, callable endpoint. That job used to POST to
`app.settings.merlin_inbox_function_url`, which was never configured.
This module supplies `poll_inbox()`, wired to `POST /merlin/poll-inbox`.

IMPORTANT — no real mailbox integration exists in either source codebase
----------------------------------------------------------------------------
Neither camelot-scout-v6 nor Camelot-OS has working inbound-email code
anywhere:
  - camelot-scout-v6's outbound send path is Resend (`server.js`,
    `RESEND_API_KEY`), and its Resend webhook handler only processes
    delivery-status events (sent/delivered/opened/clicked/bounced/
    complained) — there is no inbound-reply capture route at all.
  - Supabase migration `008_merlin_inbox.sql` names its dedup column
    `gmail_message_id` / `thread_id`, which reads as an intended Gmail API
    integration, but no Gmail API client, OAuth flow, or credentials exist
    anywhere in either repo to back that naming.
  - Camelot-OS's `scout_bot/utils/emailer.py` sends outbound mail over
    plain SMTP (`SMTP_HOST=smtp.gmail.com` is only the *example* value in
    `.env.example` — the real mailbox provider was never confirmed).
  - This session's only available email connector (`gcal` / "Gmail with
    Calendar") is the *user's own interactive session* connector — it is
    not usable for unattended, scheduled, server-side polling of a
    dedicated outreach mailbox (e.g. leads-bot@camelot.nyc / merlin@camelot.nyc).

Given that, this module is written against a clean, provider-agnostic
`InboxProvider` interface with exactly one concrete implementation, IMAP
(`ImapInboxProvider`), because:
  - It requires only the same `SMTP_*`-style credentials Scout Bot already
    has a convention for (most providers, including Gmail with an App
    Password, expose the same mailbox over IMAP on port 993).
  - It needs no new OAuth app registration to stand up.

**Before this goes live, confirm which mailbox actually receives replies
to outreach sent by `scout_outreach_log` / `merlin_outbound_messages`
(e.g. merlin@camelot.nyc per the `scout_team` seed data) and supply real
`MERLIN_IMAP_*` credentials.** If the real provider turns out to be Gmail
API (OAuth service account / domain-wide delegation) or a Resend inbound
webhook instead of IMAP, swap in a new `InboxProvider` subclass — the
matching/logging logic below does not change.

Reply matching
--------------
1. Fetch unseen messages from the provider since the last successful poll
   (`MERLIN_LAST_POLL_AT`-style watermark, held as the latest
   `received_at` already logged in `merlin_inbound_messages`).
2. Match each message to an outreach thread by, in order:
     a. `thread_id` / In-Reply-To / References header match against
        `merlin_outbound_messages.thread_id`
     b. Sender address match against `scout_outreach_log.contact_email`
3. Classify intent with simple keyword heuristics (positive / objection /
   meeting_request / unsubscribe / junk / other) — a placeholder for a
   future LLM classifier; kept deterministic and mock-testable for now.
4. Insert into `merlin_inbound_messages` (idempotent on `gmail_message_id`
   — really "provider message id"; the column name is inherited from the
   existing migration and not renamed here to avoid an unrelated schema
   change).
5. Update `scout_buildings.outreach_status` / `outreach_last_reply` and
   `scout_outreach_log.status` for the matched building/outreach row.

Author: Camelot OS
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
import re
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Optional, Protocol

import storage

# NOTE: see the identical comment in lead_hunt.py — scout_bot's own
# `utils/` subpackage shadows the repo-root `utils/` package, so a plain
# sys.path.insert retry does not work once `utils` is cached in
# sys.modules. Load audit_log.py directly by file path instead.
try:
    from utils.audit_log import audit_event
except ImportError:  # pragma: no cover - fallback when scout_bot/utils shadows repo-root utils
    import importlib.util
    from pathlib import Path

    _audit_log_path = Path(__file__).parent.parent / "utils" / "audit_log.py"
    _spec = importlib.util.spec_from_file_location("camelot_os_root_audit_log", _audit_log_path)
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    audit_event = _module.audit_event

logger = logging.getLogger("scout_bot.merlin_inbox")


class InboxUnavailable(RuntimeError):
    """Raised when no inbox provider is configured (e.g. MERLIN_IMAP_* unset)."""


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

class InboxMessage(dict):
    """A normalized inbound message. Keys: message_id, thread_id, from_address,
    subject, body_text, snippet, received_at (ISO8601 str)."""


class InboxProvider(Protocol):
    def fetch_since(self, since: Optional[str]) -> list[InboxMessage]:
        """Return normalized unseen messages received after `since` (ISO8601),
        or all unseen messages if `since` is None."""
        ...


# ---------------------------------------------------------------------------
# IMAP implementation
# ---------------------------------------------------------------------------

INTENT_KEYWORDS = {
    "unsubscribe": ["unsubscribe", "remove me", "stop emailing", "opt out", "opt-out"],
    "objection": ["not interested", "no thank", "already have", "please stop", "do not contact"],
    "meeting_request": ["schedule a call", "set up a call", "meet", "available to talk", "calendly", "book a time"],
    "positive": ["interested", "tell me more", "sounds good", "let's talk", "please send", "yes,"],
    "junk": ["undeliverable", "out of office", "auto-reply", "automatic reply", "mailer-daemon"],
}


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def classify_intent(subject: str, body: str) -> tuple[str, str]:
    """Deterministic keyword classifier. Returns (intent, confidence).

    A placeholder for a future LLM-based classifier — kept simple and
    fully unit-testable without network calls. `confidence` is HIGH when a
    keyword hit is unambiguous, MEDIUM otherwise, LOW when no keywords match.
    """
    text = f"{subject}\n{body}".lower()
    for intent in ("unsubscribe", "junk", "objection", "meeting_request", "positive"):
        keywords = INTENT_KEYWORDS[intent]
        hits = sum(1 for kw in keywords if kw in text)
        if hits >= 2:
            return intent, "HIGH"
        if hits == 1:
            return intent, "MEDIUM"
    return "other", "LOW"


class ImapInboxProvider:
    """Fetches unseen messages from a single mailbox over IMAP.

    Env vars (mirrors the SMTP_* convention already used by
    `utils/emailer.py`, so the same mailbox's credentials can usually be
    reused if it exposes IMAP too — confirm with whoever owns the mailbox):
        MERLIN_IMAP_HOST      e.g. imap.gmail.com
        MERLIN_IMAP_PORT      default 993
        MERLIN_IMAP_USER
        MERLIN_IMAP_PASSWORD  (App Password if Gmail w/ 2FA)
        MERLIN_IMAP_MAILBOX   default "INBOX"
    """

    def __init__(self) -> None:
        self.host = os.getenv("MERLIN_IMAP_HOST", "").strip()
        self.port = int(os.getenv("MERLIN_IMAP_PORT", "993"))
        self.user = os.getenv("MERLIN_IMAP_USER", "").strip()
        self.password = os.getenv("MERLIN_IMAP_PASSWORD", "").strip()
        self.mailbox = os.getenv("MERLIN_IMAP_MAILBOX", "INBOX").strip()
        if not (self.host and self.user and self.password):
            raise InboxUnavailable(
                "MERLIN_IMAP_HOST, MERLIN_IMAP_USER, and MERLIN_IMAP_PASSWORD must be "
                "set for Merlin Inbox Poll to check for replies. No real mailbox "
                "integration has been confirmed yet — see merlin_inbox.py module "
                "docstring before setting these in production."
            )

    def fetch_since(self, since: Optional[str]) -> list[InboxMessage]:
        messages: list[InboxMessage] = []
        conn = imaplib.IMAP4_SSL(self.host, self.port)
        try:
            conn.login(self.user, self.password)
            conn.select(self.mailbox)
            status, data = conn.search(None, "UNSEEN")
            if status != "OK":
                logger.warning("IMAP search returned status=%s", status)
                return messages
            ids = data[0].split()
            for msg_id in ids:
                status, msg_data = conn.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                parsed = email.message_from_bytes(raw)

                message_id = parsed.get("Message-ID", "").strip("<>")
                thread_id = parsed.get("References", "") or parsed.get("In-Reply-To", "")
                thread_id = thread_id.strip("<>").split()[0] if thread_id else message_id
                _, from_addr = parseaddr(parsed.get("From", ""))
                subject = _decode(parsed.get("Subject", ""))
                body = _extract_body(parsed)

                try:
                    received_dt = parsedate_to_datetime(parsed.get("Date", ""))
                    if received_dt.tzinfo is None:
                        received_dt = received_dt.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    received_dt = datetime.now(timezone.utc)

                if since:
                    since_dt = datetime.fromisoformat(since)
                    if received_dt <= since_dt:
                        continue

                messages.append(InboxMessage(
                    message_id=message_id or f"no-id-{msg_id.decode()}",
                    thread_id=thread_id,
                    from_address=from_addr,
                    subject=subject,
                    body_text=body,
                    snippet=body[:280].strip(),
                    received_at=received_dt.isoformat(),
                ))
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn.logout()
        return messages


# ---------------------------------------------------------------------------
# Matching + persistence
# ---------------------------------------------------------------------------

def match_to_building(message: InboxMessage) -> Optional[dict[str, Any]]:
    """Try to resolve a message to a building via thread match, then
    sender-email match against scout_outreach_log. Returns a dict with
    building_id / outreach_id when found, else None."""
    thread_id = message.get("thread_id")
    if thread_id:
        outbound = storage.find_outbound_by_thread(thread_id)
        if outbound:
            return {"building_id": outbound[0].get("building_id"), "outreach_id": None, "matched_via": "thread_id"}

    from_address = (message.get("from_address") or "").lower()
    if from_address:
        outreach_rows = storage.find_outreach_by_email(from_address, limit=1)
        if outreach_rows:
            row = outreach_rows[0]
            return {"building_id": row.get("building_id"), "outreach_id": row.get("id"), "matched_via": "contact_email"}

    return None


def poll_inbox(
    provider: Optional[InboxProvider] = None,
    since: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Poll the configured inbox once, match replies, and log them.

    Idempotency: each fetched message's provider id is checked against
    `merlin_inbound_messages.gmail_message_id` before insert (both an
    app-level check via `storage.inbound_message_exists` and the DB's
    UNIQUE constraint as a backstop against races between overlapping
    polls, e.g. if the */10 min cron overlaps a manual trigger).
    """
    provider = provider or ImapInboxProvider()

    try:
        messages = provider.fetch_since(since)
    except InboxUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Inbox fetch failed: %s", exc)
        audit_event(bot="scout", action="merlin_poll_inbox", outcome="error", detail={"error": str(exc)})
        raise

    logged = 0
    skipped_duplicate = 0
    unmatched = 0
    results: list[dict[str, Any]] = []

    for message in messages:
        message_id = message.get("message_id")
        if not dry_run and storage.inbound_message_exists(message_id):
            skipped_duplicate += 1
            continue

        match = match_to_building(message)
        intent, confidence = classify_intent(message.get("subject", ""), message.get("body_text", ""))

        record = {
            "building_id": match.get("building_id") if match else None,
            "gmail_message_id": message_id,
            "thread_id": message.get("thread_id"),
            "from_address": message.get("from_address"),
            "subject": message.get("subject"),
            "snippet": message.get("snippet"),
            "body_text": message.get("body_text"),
            "intent": intent,
            "confidence": confidence,
            "received_at": message.get("received_at"),
        }

        if not match:
            unmatched += 1

        if dry_run:
            results.append(record)
            continue

        try:
            storage.insert_inbound_message(record)
            logged += 1
            if match and match.get("building_id"):
                storage.update_building_outreach_status(
                    match["building_id"],
                    outreach_status="replied",
                    outreach_last_reply=message.get("received_at"),
                )
            if match and match.get("outreach_id"):
                storage.mark_outreach_replied(match["outreach_id"])
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to log inbound message %s: %s", message_id, exc)

    audit_event(
        bot="scout",
        action="merlin_poll_inbox",
        detail={
            "fetched": len(messages),
            "logged": logged,
            "skipped_duplicate": skipped_duplicate,
            "unmatched": unmatched,
            "dry_run": dry_run,
        },
    )

    return {
        "status": "completed",
        "fetched": len(messages),
        "logged": logged,
        "skipped_duplicate": skipped_duplicate,
        "unmatched": unmatched,
        "messages": results if dry_run else None,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        print(poll_inbox())
    except InboxUnavailable as exc:
        print(f"Merlin Inbox not configured: {exc}")
