"""
email_handler.py — Email Handler (IMAP + SMTP)
Camelot Property Management Services Corp / Front Desk Bot

Polls frontdesk@camelot.nyc via IMAP for inbound messages,
classifies them, creates tickets, and sends threaded responses.

Thread convention: Subject line prefixed with "[CAM-YYYY-NNNN] Re: ..."

Author: Camelot OS
"""

import email
import email.header
import imaplib
import logging
import os
import re
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "frontdesk@camelot.nyc")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")
IMAP_MAILBOX = os.getenv("IMAP_MAILBOX", "INBOX")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "frontdesk@camelot.nyc")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "Camelot Front Desk <frontdesk@camelot.nyc>")

# Email signature block
EMAIL_FOOTER = """

---
Camelot Property Management Services — Your Front Desk Team
📧 frontdesk@camelot.nyc | 📞 (212) 555-0199
🌐 https://residents.camelot.nyc

This email was sent in response to your inquiry. Please reply to this thread to keep your request organized.
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ParsedEmail:
    message_id: str
    uid: str
    from_address: str
    from_name: str
    to_address: str
    subject: str
    body_text: str
    body_html: Optional[str]
    date_str: str
    existing_ticket_number: Optional[str]  # If reply to existing ticket
    raw_message: object


# ---------------------------------------------------------------------------
# IMAP — Fetch inbound emails
# ---------------------------------------------------------------------------

def check_inbound_email(
    imap_config: Optional[dict] = None,
    mark_as_read: bool = True,
    max_messages: int = 20,
) -> list[ParsedEmail]:
    """
    Poll the IMAP inbox and parse new (unread) messages.

    Args:
        imap_config:     Optional override dict with keys: host, port, user, password, mailbox.
                         Defaults to environment variables.
        mark_as_read:    If True, mark fetched messages as read in IMAP.
        max_messages:    Maximum number of messages to fetch per poll cycle.

    Returns:
        List of ParsedEmail objects for unread messages.

    Raises:
        imaplib.IMAP4.error: On IMAP connection/auth failure.
    """
    config = imap_config or {}
    host = config.get("host", IMAP_HOST)
    port = int(config.get("port", IMAP_PORT))
    user = config.get("user", IMAP_USER)
    password = config.get("password", IMAP_PASSWORD)
    mailbox = config.get("mailbox", IMAP_MAILBOX)

    if not password:
        raise EnvironmentError("IMAP_PASSWORD is not set")

    parsed_emails: list[ParsedEmail] = []

    try:
        logger.info(f"Connecting to IMAP: {host}:{port} as {user}")
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(user, password)
            imap.select(mailbox)

            # Search for unseen messages
            status, uid_data = imap.uid("search", None, "UNSEEN")
            if status != "OK":
                logger.warning("IMAP search returned non-OK status")
                return []

            uids = uid_data[0].decode().split() if uid_data[0] else []
            # Process most recent first, up to max_messages
            uids = uids[-max_messages:] if len(uids) > max_messages else uids
            uids = list(reversed(uids))

            logger.info(f"Found {len(uids)} unread message(s) in {mailbox}")

            for uid in uids:
                try:
                    status, msg_data = imap.uid("fetch", uid, "(RFC822)")
                    if status != "OK" or not msg_data[0]:
                        continue

                    raw = email.message_from_bytes(msg_data[0][1])
                    parsed = _parse_email_message(raw, uid.decode())

                    if parsed:
                        parsed_emails.append(parsed)

                    if mark_as_read:
                        imap.uid("store", uid, "+FLAGS", "\\Seen")

                except Exception as e:
                    logger.error(f"Failed to parse email UID {uid}: {e}")
                    continue

    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP error: {e}")
        raise

    logger.info(f"Parsed {len(parsed_emails)} inbound email(s)")
    return parsed_emails


def _parse_email_message(msg: email.message.Message, uid: str) -> Optional[ParsedEmail]:
    """Parse a raw email message into ParsedEmail."""
    try:
        message_id = msg.get("Message-ID", "")
        from_raw = msg.get("From", "")
        from_name, from_addr = email.utils.parseaddr(from_raw)
        to_addr = msg.get("To", "")
        subject_raw = msg.get("Subject", "")
        date_str = msg.get("Date", "")

        # Decode subject
        subject = _decode_header_value(subject_raw)

        # Extract body text and HTML
        body_text = ""
        body_html = None
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain" and not body_text:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        body_text = part.get_payload(decode=True).decode(charset, errors="replace")
                    except Exception:
                        body_text = str(part.get_payload())
                elif content_type == "text/html" and not body_html:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        body_html = part.get_payload(decode=True).decode(charset, errors="replace")
                    except Exception:
                        pass
        else:
            charset = msg.get_content_charset() or "utf-8"
            try:
                body_text = msg.get_payload(decode=True).decode(charset, errors="replace")
            except Exception:
                body_text = str(msg.get_payload())

        # Clean up body — strip quoted replies
        body_text = _strip_quoted_reply(body_text).strip()

        # Check if this is a reply to an existing ticket
        existing_ticket = _extract_ticket_number(subject)

        return ParsedEmail(
            message_id=message_id,
            uid=uid,
            from_address=from_addr.lower().strip(),
            from_name=from_name,
            to_address=to_addr,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            date_str=date_str,
            existing_ticket_number=existing_ticket,
            raw_message=msg,
        )
    except Exception as e:
        logger.error(f"Email parse error: {e}")
        return None


# ---------------------------------------------------------------------------
# SMTP — Send responses
# ---------------------------------------------------------------------------

def send_response(
    to_email: str,
    subject: str,
    body: str,
    ticket_number: str,
    from_email: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    cc: Optional[list[str]] = None,
) -> bool:
    """
    Send a formatted response email, threaded by ticket number.

    Args:
        to_email:        Recipient email address
        subject:         Email subject (ticket number will be injected if not present)
        body:            Plain text email body
        ticket_number:   CAM-YYYY-NNNN ticket identifier
        from_email:      Sender email (defaults to SMTP_FROM)
        in_reply_to:     Message-ID of the email being replied to (for threading)
        references:      References header value (for threading)
        cc:              List of CC email addresses

    Returns:
        True if sent successfully, False otherwise.
    """
    if not SMTP_PASSWORD:
        logger.warning("SMTP_PASSWORD not set — cannot send email")
        print(f"[DRY RUN] Would send to {to_email}:\n{body}")
        return False

    # Inject ticket number into subject for threading
    if ticket_number and f"[{ticket_number}]" not in subject:
        # Determine if this is a reply
        if subject.lower().startswith("re:"):
            subject = f"[{ticket_number}] {subject}"
        else:
            subject = f"[{ticket_number}] Re: {subject}"

    sender = from_email or SMTP_FROM
    full_body = body + EMAIL_FOOTER

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to_email
        if cc:
            msg["Cc"] = ", ".join(cc)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references
        msg["X-Ticket-Number"] = ticket_number

        # Plain text part
        msg.attach(MIMEText(full_body, "plain", "utf-8"))

        # HTML part
        html_body = _text_to_html(full_body, ticket_number)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        recipients = [to_email] + (cc or [])

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(sender, recipients, msg.as_string())

        logger.info(f"Response email sent to {to_email} — Subject: {subject}")
        return True

    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending response to {to_email}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending email to {to_email}: {e}")
        return False


def send_escalation_email(
    ticket_number: str,
    issue_summary: str,
    urgency: str,
    resident_email: str,
    resident_name: str,
    unit: str,
    building_address: str,
    additional_recipients: Optional[list[str]] = None,
) -> bool:
    """
    Send an internal escalation email to the management team.
    Used for emergency and urgent tickets requiring human attention.

    Args:
        ticket_number:           CAM-YYYY-NNNN
        issue_summary:           Brief description of the issue
        urgency:                 emergency / urgent / routine
        resident_email:          Resident's email for context
        resident_name:           Resident's name
        unit:                    Unit number
        building_address:        Property address
        additional_recipients:   Extra CC recipients

    Returns:
        True if sent successfully.
    """
    escalation_to = os.getenv("ESCALATION_EMAIL", "mgr@camelot.nyc")
    urgency_prefix = "🚨 EMERGENCY" if urgency == "emergency" else "⚠️ URGENT"
    subject = f"{urgency_prefix} — Ticket {ticket_number}: {issue_summary[:60]}"

    body = (
        f"ESCALATION ALERT — Requires Immediate Attention\n\n"
        f"Ticket: {ticket_number}\n"
        f"Priority: {urgency.upper()}\n"
        f"Building: {building_address}\n"
        f"Unit: {unit}\n"
        f"Resident: {resident_name} ({resident_email})\n\n"
        f"Issue:\n{issue_summary}\n\n"
        f"Please respond to the resident immediately and update the ticket status."
    )

    return send_response(
        to_email=escalation_to,
        subject=subject,
        body=body,
        ticket_number=ticket_number,
        cc=additional_recipients,
    )


# ---------------------------------------------------------------------------
# Full inbound email processing pipeline
# ---------------------------------------------------------------------------

def process_inbound_email_pipeline(
    parsed_email: ParsedEmail,
    building_lookup_fn=None,
    resident_lookup_fn=None,
) -> dict:
    """
    Process a single parsed inbound email:
    classify → lookup resident → create/update ticket → send response.

    Args:
        parsed_email:         ParsedEmail from check_inbound_email()
        building_lookup_fn:   Optional: (email_address) → building_id
        resident_lookup_fn:   Optional: (email_address) → {name, unit, resident_id}

    Returns:
        Processing result dict.
    """
    from frontdesk_bot.message_classifier import classify_message
    from frontdesk_bot.ticket_manager import create_ticket, update_ticket_status, get_ticket
    from frontdesk_bot.response_templates import get_response

    from_email = parsed_email.from_address
    body = parsed_email.body_text
    subject = parsed_email.subject

    if not body.strip():
        logger.info(f"Empty email body from {from_email} — skipping")
        return {"status": "skipped", "reason": "empty body"}

    # Classify message
    classification = classify_message(body)

    # Resident lookup
    resident_info = {}
    if resident_lookup_fn:
        try:
            resident_info = resident_lookup_fn(from_email) or {}
        except Exception as e:
            logger.warning(f"Resident lookup failed for {from_email}: {e}")

    resident_name = resident_info.get("name") or parsed_email.from_name or "Resident"
    unit = resident_info.get("unit", "")
    resident_id = resident_info.get("resident_id", from_email)
    building_address = resident_info.get("building_address", "")
    building_id = None

    if building_lookup_fn:
        try:
            building_id = building_lookup_fn(from_email)
        except Exception:
            pass

    # Handle replies to existing tickets
    ticket_number = parsed_email.existing_ticket_number
    if ticket_number:
        # Append note to existing ticket
        try:
            update_ticket_status(
                ticket_number=ticket_number,
                status="In Progress",
                note=f"Resident reply via email: {body[:500]}",
                updated_by=from_email,
            )
            logger.info(f"Updated existing ticket {ticket_number} with resident reply")
        except (LookupError, Exception) as e:
            logger.warning(f"Could not update ticket {ticket_number}: {e}")
            ticket_number = None  # Fall through to create new ticket

    # Create new ticket if not a reply
    if not ticket_number:
        ticket = create_ticket(
            resident_id=resident_id,
            unit=unit,
            category=classification.category,
            description=f"Subject: {subject}\n\n{body}",
            urgency=classification.urgency,
            building_id=building_id,
            channel="email",
        )
        ticket_number = ticket.get("ticket_number", "")
    else:
        ticket = {"ticket_number": ticket_number}

    # Generate and send response
    response_body = get_response(
        category=classification.category,
        urgency=classification.urgency,
        resident_name=resident_name,
        unit=unit,
        building_address=building_address,
        ticket_number=ticket_number,
        extra={"issue_summary": body[:100]},
    )

    response_sent = send_response(
        to_email=from_email,
        subject=subject,
        body=response_body,
        ticket_number=ticket_number,
        in_reply_to=parsed_email.message_id,
        references=parsed_email.message_id,
    )

    # Emergency escalation
    if classification.urgency == "emergency":
        send_escalation_email(
            ticket_number=ticket_number,
            issue_summary=body[:200],
            urgency="emergency",
            resident_email=from_email,
            resident_name=resident_name,
            unit=unit,
            building_address=building_address,
        )

    return {
        "status": "processed",
        "from": from_email,
        "ticket_number": ticket_number,
        "classification": {
            "category": classification.category,
            "urgency": classification.urgency,
            "sentiment": classification.sentiment,
        },
        "response_sent": response_sent,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_header_value(value: str) -> str:
    """Decode a potentially encoded email header value."""
    try:
        decoded_parts = email.header.decode_header(value)
        result = ""
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                result += part.decode(charset or "utf-8", errors="replace")
            else:
                result += str(part)
        return result.strip()
    except Exception:
        return str(value)


def _extract_ticket_number(subject: str) -> Optional[str]:
    """Extract a CAM-YYYY-NNNN ticket number from an email subject."""
    m = re.search(r"\[?(CAM-\d{4}-\d{4})\]?", subject, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _strip_quoted_reply(text: str) -> str:
    """Remove quoted email reply sections from message body."""
    # Common quoted-reply markers
    patterns = [
        r"\n+On .+ wrote:\n+>.*",           # Gmail-style
        r"\n+From: .+\nSent: .+\nTo: .+\n",  # Outlook-style
        r"\n+_{10,}",                          # Underline separators
        r"\n+[-]{5,}\s*Original Message\s*[-]{5,}",
        r"\n+>+ .+",                           # Quoted lines starting with >
    ]
    result = text
    for pattern in patterns:
        result = re.split(pattern, result, maxsplit=1, flags=re.DOTALL | re.IGNORECASE)[0]
    return result.strip()


def _text_to_html(text: str, ticket_number: str) -> str:
    """Convert plain text to simple HTML for email."""
    GOLD = "#C9A84C"
    NAVY = "#1A2645"

    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>\n")
    )
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; font-size: 14px; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: {NAVY}; padding: 16px 24px; border-bottom: 3px solid {GOLD};">
    <span style="color: {GOLD}; font-size: 16px; font-weight: bold;">Camelot Property Management</span>
    <span style="color: #aaa; font-size: 11px; float: right; padding-top: 4px;">
      Ticket: {ticket_number}
    </span>
  </div>
  <div style="padding: 24px;">
    {escaped}
  </div>
  <div style="background: #f5f5f5; padding: 12px 24px; font-size: 11px; color: #999; border-top: 1px solid #eee;">
    Camelot Property Management Services | frontdesk@camelot.nyc | (212) 555-0199
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    action = sys.argv[1] if len(sys.argv) > 1 else "check"

    if action == "check":
        emails = check_inbound_email(max_messages=5)
        print(f"Found {len(emails)} unread email(s)")
        for e in emails:
            print(f"  From: {e.from_address} | Subject: {e.subject[:60]} | Ticket: {e.existing_ticket_number}")
    elif action == "test-send":
        to = sys.argv[2] if len(sys.argv) > 2 else "test@example.com"
        success = send_response(
            to_email=to,
            subject="Test Maintenance Request",
            body="This is a test response from Camelot Front Desk Bot.",
            ticket_number="CAM-2026-0001",
        )
        print(f"Send result: {'Success' if success else 'Failed'}")
