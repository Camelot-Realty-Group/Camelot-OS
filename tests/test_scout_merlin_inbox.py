"""Tests for scout_bot/merlin_inbox.py — intent classification, reply
matching, and poll_inbox() orchestration. All IMAP and Supabase calls are
mocked; no real mailbox or network calls are made.
"""
from unittest.mock import MagicMock, patch

import pytest

import merlin_inbox


# ---------------------------------------------------------------------------
# classify_intent() — pure function
# ---------------------------------------------------------------------------

def test_classify_unsubscribe_high_confidence():
    intent, confidence = merlin_inbox.classify_intent(
        "Re: outreach", "Please unsubscribe me, stop emailing this address."
    )
    assert intent == "unsubscribe"
    assert confidence == "HIGH"


def test_classify_meeting_request():
    intent, confidence = merlin_inbox.classify_intent(
        "Re: outreach", "Happy to schedule a call, let's meet next week — available to talk Tuesday."
    )
    assert intent == "meeting_request"
    assert confidence == "HIGH"


def test_classify_objection():
    intent, confidence = merlin_inbox.classify_intent(
        "Re: outreach", "Not interested, we already have a management company. Please stop contacting us."
    )
    assert intent == "objection"
    assert confidence == "HIGH"


def test_classify_positive_single_keyword_medium_confidence():
    intent, confidence = merlin_inbox.classify_intent("Re: outreach", "Sounds good, thanks.")
    assert intent == "positive"
    assert confidence == "MEDIUM"


def test_classify_junk_out_of_office():
    intent, confidence = merlin_inbox.classify_intent(
        "Automatic reply: Out of Office", "I am currently out of office and will respond when I return."
    )
    assert intent == "junk"


def test_classify_other_when_no_keywords_match():
    intent, confidence = merlin_inbox.classify_intent("Question", "What is the square footage of the lobby?")
    assert intent == "other"
    assert confidence == "LOW"


# ---------------------------------------------------------------------------
# match_to_building()
# ---------------------------------------------------------------------------

@patch("merlin_inbox.storage")
def test_match_by_thread_id_takes_priority(mock_storage):
    mock_storage.find_outbound_by_thread.return_value = [{"building_id": "bld-1"}]
    message = merlin_inbox.InboxMessage(
        message_id="m1", thread_id="t1", from_address="owner@example.com",
        subject="Re: hi", body_text="", snippet="", received_at="2026-08-04T10:00:00+00:00",
    )
    match = merlin_inbox.match_to_building(message)
    assert match["building_id"] == "bld-1"
    assert match["matched_via"] == "thread_id"
    mock_storage.find_outreach_by_email.assert_not_called()


@patch("merlin_inbox.storage")
def test_match_falls_back_to_contact_email(mock_storage):
    mock_storage.find_outbound_by_thread.return_value = []
    mock_storage.find_outreach_by_email.return_value = [{"building_id": "bld-2", "id": "outreach-9"}]
    message = merlin_inbox.InboxMessage(
        message_id="m2", thread_id="unknown-thread", from_address="owner2@example.com",
        subject="Re: hi", body_text="", snippet="", received_at="2026-08-04T10:00:00+00:00",
    )
    match = merlin_inbox.match_to_building(message)
    assert match["building_id"] == "bld-2"
    assert match["outreach_id"] == "outreach-9"
    assert match["matched_via"] == "contact_email"


@patch("merlin_inbox.storage")
def test_match_returns_none_when_nothing_found(mock_storage):
    mock_storage.find_outbound_by_thread.return_value = []
    mock_storage.find_outreach_by_email.return_value = []
    message = merlin_inbox.InboxMessage(
        message_id="m3", thread_id="", from_address="stranger@example.com",
        subject="hi", body_text="", snippet="", received_at="2026-08-04T10:00:00+00:00",
    )
    assert merlin_inbox.match_to_building(message) is None


# ---------------------------------------------------------------------------
# poll_inbox() — orchestration with a fake provider + mocked storage
# ---------------------------------------------------------------------------

class _FakeProvider:
    def __init__(self, messages):
        self._messages = messages

    def fetch_since(self, since):
        return self._messages


def _msg(**kw):
    base = dict(
        message_id="msg-1", thread_id="thread-1", from_address="owner@example.com",
        subject="Re: Camelot outreach", body_text="Sounds good, tell me more.",
        snippet="Sounds good, tell me more.", received_at="2026-08-04T10:00:00+00:00",
    )
    base.update(kw)
    return merlin_inbox.InboxMessage(**base)


@patch("merlin_inbox.storage")
def test_poll_inbox_logs_new_matched_message(mock_storage):
    mock_storage.inbound_message_exists.return_value = False
    mock_storage.find_outbound_by_thread.return_value = [{"building_id": "bld-1"}]
    mock_storage.find_outreach_by_email.return_value = []
    mock_storage.insert_inbound_message.return_value = {"id": "im-1"}

    provider = _FakeProvider([_msg()])
    result = merlin_inbox.poll_inbox(provider=provider)

    assert result["fetched"] == 1
    assert result["logged"] == 1
    assert result["skipped_duplicate"] == 0
    assert result["unmatched"] == 0
    mock_storage.insert_inbound_message.assert_called_once()
    mock_storage.update_building_outreach_status.assert_called_once()


@patch("merlin_inbox.storage")
def test_poll_inbox_skips_already_logged_message(mock_storage):
    mock_storage.inbound_message_exists.return_value = True

    provider = _FakeProvider([_msg()])
    result = merlin_inbox.poll_inbox(provider=provider)

    assert result["skipped_duplicate"] == 1
    assert result["logged"] == 0
    mock_storage.insert_inbound_message.assert_not_called()


@patch("merlin_inbox.storage")
def test_poll_inbox_counts_unmatched_but_still_logs(mock_storage):
    mock_storage.inbound_message_exists.return_value = False
    mock_storage.find_outbound_by_thread.return_value = []
    mock_storage.find_outreach_by_email.return_value = []
    mock_storage.insert_inbound_message.return_value = {"id": "im-2"}

    provider = _FakeProvider([_msg(message_id="msg-2", from_address="unknown@example.com")])
    result = merlin_inbox.poll_inbox(provider=provider)

    assert result["unmatched"] == 1
    assert result["logged"] == 1
    mock_storage.update_building_outreach_status.assert_not_called()


@patch("merlin_inbox.storage")
def test_poll_inbox_dry_run_does_not_write(mock_storage):
    provider = _FakeProvider([_msg()])
    result = merlin_inbox.poll_inbox(provider=provider, dry_run=True)

    mock_storage.insert_inbound_message.assert_not_called()
    mock_storage.inbound_message_exists.assert_not_called()
    assert result["messages"] is not None
    assert len(result["messages"]) == 1


def test_imap_provider_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("MERLIN_IMAP_HOST", raising=False)
    monkeypatch.delenv("MERLIN_IMAP_USER", raising=False)
    monkeypatch.delenv("MERLIN_IMAP_PASSWORD", raising=False)
    with pytest.raises(merlin_inbox.InboxUnavailable):
        merlin_inbox.ImapInboxProvider()
