"""
twilio_handler.py — Twilio SMS Integration
Camelot Property Management Services Corp / Front Desk Bot

Handles inbound and outbound SMS via Twilio REST API.
Inbound messages are parsed, classified, and auto-responded.

Required env vars:
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_FROM_NUMBER  (E.164 format, e.g. +12125550100)

Author: Camelot OS
"""

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from base64 import b64encode
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")

TWILIO_MESSAGES_URL = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
SMS_MAX_LENGTH = 1600  # Twilio SMS max (standard is 160; long SMS auto-concatenates)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


_session = _build_session()


# ---------------------------------------------------------------------------
# Send SMS
# ---------------------------------------------------------------------------

def send_sms(
    to_number: str,
    message: str,
    from_number: Optional[str] = None,
    shorten_if_needed: bool = True,
) -> dict:
    """
    Send an SMS via Twilio.

    Args:
        to_number:       Recipient phone number in E.164 format (e.g., +12125551234)
        message:         Message body text
        from_number:     Sending number (defaults to TWILIO_FROM_NUMBER env var)
        shorten_if_needed: Truncate message if over SMS_MAX_LENGTH

    Returns:
        Twilio response dict with sid, status, to, from, body.

    Raises:
        EnvironmentError: If Twilio credentials are not configured.
        ValueError: If to_number is not in E.164 format.
        RuntimeError: If Twilio API returns an error.
    """
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        raise EnvironmentError(
            "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set as environment variables"
        )

    sender = from_number or TWILIO_FROM_NUMBER
    if not sender:
        raise EnvironmentError("TWILIO_FROM_NUMBER must be set as an environment variable")

    # Validate E.164 format
    to_clean = _normalize_phone(to_number)
    if not to_clean:
        raise ValueError(f"Invalid phone number format: {to_number}. Use E.164 (e.g., +12125551234)")

    # Truncate if needed
    if shorten_if_needed and len(message) > SMS_MAX_LENGTH:
        message = message[: SMS_MAX_LENGTH - 20] + "... [msg truncated]"
        logger.warning(f"SMS message truncated to {SMS_MAX_LENGTH} chars for {to_clean}")

    payload = {
        "To": to_clean,
        "From": sender,
        "Body": message,
    }

    logger.info(f"Sending SMS to {to_clean} ({len(message)} chars)")

    try:
        resp = _session.post(
            TWILIO_MESSAGES_URL,
            data=payload,
            auth=HTTPBasicAuth(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"SMS sent: SID={result.get('sid')}, Status={result.get('status')}")
        return {
            "sid": result.get("sid"),
            "status": result.get("status"),
            "to": result.get("to"),
            "from": result.get("from"),
            "body": result.get("body"),
            "date_created": result.get("date_created"),
            "error_code": result.get("error_code"),
            "error_message": result.get("error_message"),
        }
    except requests.exceptions.HTTPError as e:
        error_body = {}
        try:
            error_body = resp.json()
        except Exception:
            pass
        logger.error(
            f"Twilio SMS send failed: {resp.status_code} — "
            f"Code {error_body.get('code')}: {error_body.get('message')}"
        )
        raise RuntimeError(
            f"Twilio error {error_body.get('code', 'unknown')}: "
            f"{error_body.get('message', str(e))}"
        ) from e
    except requests.RequestException as e:
        logger.error(f"Network error sending SMS to {to_clean}: {e}")
        raise


def send_sms_safe(to_number: str, message: str) -> Optional[dict]:
    """
    Non-raising wrapper for send_sms. Returns None on failure.
    Useful for fire-and-forget notification sends.
    """
    try:
        return send_sms(to_number, message)
    except Exception as e:
        logger.error(f"send_sms_safe: failed to send to {to_number}: {e}")
        return None


# ---------------------------------------------------------------------------
# Receive / parse inbound webhook
# ---------------------------------------------------------------------------

def receive_sms_webhook(request_body: dict, validate_signature: bool = False, x_twilio_signature: str = "", request_url: str = "") -> dict:
    """
    Parse an inbound Twilio SMS webhook payload.

    Args:
        request_body:        URL-decoded form fields from Twilio POST webhook.
                             Typically: {'From': '+1...', 'Body': '...', 'To': '+1...', ...}
        validate_signature:  If True, validate X-Twilio-Signature header.
        x_twilio_signature:  Twilio signature header value (required if validating).
        request_url:         Full webhook URL (required if validating).

    Returns:
        Parsed message dict with keys:
            from_number, to_number, body, num_media, media_urls,
            message_sid, account_sid, from_city, from_state, from_zip, from_country,
            raw (original dict)

    Raises:
        ValueError: If signature validation fails.
    """
    if validate_signature:
        _validate_twilio_signature(request_body, x_twilio_signature, request_url)

    # Extract standard Twilio fields
    from_number = request_body.get("From", "")
    to_number = request_body.get("To", "")
    body = request_body.get("Body", "").strip()
    message_sid = request_body.get("MessageSid", "")
    account_sid = request_body.get("AccountSid", "")
    num_media = int(request_body.get("NumMedia", "0") or 0)

    # Extract media URLs (MMS)
    media_urls = []
    for i in range(num_media):
        url = request_body.get(f"MediaUrl{i}")
        if url:
            media_urls.append(url)

    parsed = {
        "from_number": from_number,
        "to_number": to_number,
        "body": body,
        "message_sid": message_sid,
        "account_sid": account_sid,
        "num_media": num_media,
        "media_urls": media_urls,
        "from_city": request_body.get("FromCity", ""),
        "from_state": request_body.get("FromState", ""),
        "from_zip": request_body.get("FromZip", ""),
        "from_country": request_body.get("FromCountry", ""),
        "raw": request_body,
    }

    logger.info(
        f"Inbound SMS received: From={from_number}, "
        f"Chars={len(body)}, Media={num_media}"
    )
    return parsed


def process_inbound_sms(
    webhook_payload: dict,
    building_lookup_fn=None,
    resident_lookup_fn=None,
) -> dict:
    """
    Full inbound SMS processing pipeline:
    parse → classify → look up resident → create ticket → auto-respond.

    Args:
        webhook_payload:      Raw Twilio webhook form fields.
        building_lookup_fn:   Optional callable: (phone_number) → building_id
        resident_lookup_fn:   Optional callable: (phone_number) → {name, unit, resident_id}

    Returns:
        Processing result dict with: message, classification, ticket, response_sent.
    """
    from frontdesk_bot.message_classifier import classify_message
    from frontdesk_bot.ticket_manager import create_ticket
    from frontdesk_bot.response_templates import get_response

    parsed = receive_sms_webhook(webhook_payload)
    from_number = parsed["from_number"]
    body = parsed["body"]

    if not body:
        logger.info(f"Empty SMS from {from_number} — ignoring")
        return {"message": parsed, "classification": None, "ticket": None, "response_sent": False}

    # Classify message
    classification = classify_message(body)

    # Look up resident info
    resident_info = {}
    if resident_lookup_fn:
        try:
            resident_info = resident_lookup_fn(from_number) or {}
        except Exception as e:
            logger.warning(f"Resident lookup failed for {from_number}: {e}")

    resident_name = resident_info.get("name", "Resident")
    unit = resident_info.get("unit", "")
    resident_id = resident_info.get("resident_id", from_number)
    building_id = None

    if building_lookup_fn:
        try:
            building_id = building_lookup_fn(from_number)
        except Exception as e:
            logger.warning(f"Building lookup failed for {from_number}: {e}")

    building_address = resident_info.get("building_address", "")

    # Create ticket
    ticket = create_ticket(
        resident_id=resident_id,
        unit=unit,
        category=classification.category,
        description=body,
        urgency=classification.urgency,
        building_id=building_id,
        channel="sms",
    )
    ticket_number = ticket.get("ticket_number", "")

    # Generate response
    response_text = get_response(
        category=classification.category,
        urgency=classification.urgency,
        resident_name=resident_name,
        unit=unit,
        building_address=building_address,
        ticket_number=ticket_number,
        extra={"issue_summary": body[:80]},
    )

    # Trim response for SMS (keep under 1600 chars)
    sms_response = _trim_for_sms(response_text)

    # Send response
    sent = send_sms_safe(from_number, sms_response)
    response_sent = bool(sent and sent.get("sid"))

    # Emergency escalation: alert on-call team
    if classification.urgency == "emergency":
        _alert_oncall(from_number, unit, building_address, body, ticket_number)

    return {
        "message": parsed,
        "classification": {
            "category": classification.category,
            "urgency": classification.urgency,
            "sentiment": classification.sentiment,
        },
        "ticket": ticket,
        "response_sent": response_sent,
        "response_sid": sent.get("sid") if sent else None,
    }


# ---------------------------------------------------------------------------
# TwiML response (for real-time webhook responses)
# ---------------------------------------------------------------------------

def twiml_response(message: str) -> str:
    """
    Generate a TwiML XML response for synchronous Twilio webhook reply.
    Use this when you want Twilio to send a reply immediately (vs async send_sms).

    Args:
        message: Response message text

    Returns:
        TwiML XML string
    """
    # Escape XML special chars
    safe_message = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{safe_message}</Message>
</Response>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_phone(number: str) -> Optional[str]:
    """Normalize a phone number to E.164 format."""
    if not number:
        return None
    # Already E.164
    if number.startswith("+"):
        digits = "".join(filter(str.isdigit, number))
        return f"+{digits}" if len(digits) >= 10 else None
    # US number without +1
    digits = "".join(filter(str.isdigit, number))
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def _trim_for_sms(text: str, max_chars: int = 1550) -> str:
    """Trim a response to fit SMS constraints, preserving meaning."""
    if len(text) <= max_chars:
        return text
    # Trim at last sentence boundary before max_chars
    trimmed = text[:max_chars]
    last_period = trimmed.rfind(".")
    if last_period > max_chars - 200:
        trimmed = trimmed[: last_period + 1]
    return trimmed + "\n\n— Camelot Property Mgmt"


def _validate_twilio_signature(
    params: dict,
    signature: str,
    url: str,
) -> None:
    """
    Validate Twilio X-Twilio-Signature to prevent webhook spoofing.
    Raises ValueError if signature is invalid.
    """
    if not TWILIO_AUTH_TOKEN:
        logger.warning("Cannot validate Twilio signature — TWILIO_AUTH_TOKEN not set")
        return

    # Build validation string: URL + sorted params
    validation_str = url
    for key in sorted(params.keys()):
        validation_str += key + params[key]

    expected_sig = b64encode(
        hmac.new(
            TWILIO_AUTH_TOKEN.encode("utf-8"),
            validation_str.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")

    if not hmac.compare_digest(expected_sig, signature):
        raise ValueError("Invalid Twilio webhook signature — possible spoofing attempt")

    logger.debug("Twilio webhook signature validated successfully")


def _alert_oncall(
    from_number: str,
    unit: str,
    building_address: str,
    message: str,
    ticket_number: str,
) -> None:
    """Send emergency alert SMS to on-call maintenance."""
    oncall_number = os.getenv("ONCALL_PHONE", "")
    if not oncall_number:
        logger.warning("ONCALL_PHONE not configured — cannot send emergency alert to on-call team")
        return

    alert = (
        f"🚨 CAMELOT EMERGENCY ALERT\n"
        f"From: {from_number} | Unit: {unit or 'Unknown'}\n"
        f"Building: {building_address or 'Unknown'}\n"
        f"Ticket: {ticket_number}\n"
        f"Message: {message[:200]}\n\n"
        f"Respond immediately. Call resident back or dispatch."
    )
    result = send_sms_safe(oncall_number, alert)
    if result:
        logger.info(f"Emergency alert sent to on-call ({oncall_number}): SID {result.get('sid')}")
    else:
        logger.error(f"Failed to send emergency alert to on-call {oncall_number}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 3:
        print("Usage: python twilio_handler.py <to_number> <message>")
        print("Example: python twilio_handler.py +12125551234 'Test message from Camelot'")
        sys.exit(1)

    to = sys.argv[1]
    msg = " ".join(sys.argv[2:])
    result = send_sms(to, msg)
    print(f"Sent: SID={result.get('sid')}, Status={result.get('status')}")
