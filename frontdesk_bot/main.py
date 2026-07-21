"""
main.py — Front Desk Bot Entry Point
Camelot Property Management Services Corp

Runs the Front Desk Bot polling loop: checks email inbox every N seconds,
processes messages, creates tickets, and sends responses.

Optionally exposes a webhook endpoint for Twilio SMS inbound.

Usage:
    python main.py                        # Start email polling loop
    python main.py --once                 # Single email check (cron mode)
    python main.py --serve                # Start webhook server for SMS + web chat
    python main.py --test-classify "msg"  # Test message classifier

Author: Camelot OS
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "logs/frontdesk_bot.log")
Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("frontdesk_bot.main")

POLL_INTERVAL_SECONDS = int(os.getenv("EMAIL_POLL_INTERVAL", "60"))


# ---------------------------------------------------------------------------
# Email polling loop
# ---------------------------------------------------------------------------

def run_email_loop(once: bool = False) -> None:
    """
    Poll IMAP inbox and process all unread messages.
    Runs continuously unless once=True.
    """
    from frontdesk_bot.email_handler import check_inbound_email, process_inbound_email_pipeline

    logger.info("Front Desk Bot email loop starting")

    while True:
        try:
            logger.info("Checking inbox...")
            emails = check_inbound_email(mark_as_read=True, max_messages=20)

            if emails:
                logger.info(f"Processing {len(emails)} new email(s)")
                for parsed_email in emails:
                    try:
                        result = process_inbound_email_pipeline(parsed_email)
                        logger.info(
                            f"Email processed: from={result.get('from')}, "
                            f"ticket={result.get('ticket_number')}, "
                            f"urgency={result.get('classification', {}).get('urgency')}, "
                            f"response_sent={result.get('response_sent')}"
                        )
                    except Exception as e:
                        logger.error(f"Error processing email from {parsed_email.from_address}: {e}")
            else:
                logger.debug("No new messages")

        except Exception as e:
            logger.error(f"Email poll error: {e}")

        if once:
            logger.info("--once mode: exiting after single check")
            break

        logger.debug(f"Sleeping {POLL_INTERVAL_SECONDS}s until next check...")
        time.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Webhook server (FastAPI)
# ---------------------------------------------------------------------------

def run_webhook_server(host: str = "0.0.0.0", port: int = 8001) -> None:
    """
    Start a FastAPI webhook server to handle:
    - POST /sms/inbound — Twilio SMS webhook
    - POST /chat/message — Web chat inbound
    - GET  /health — Health check
    """
    try:
        import uvicorn
        from fastapi import FastAPI, Form, Request, Response
        from fastapi.responses import JSONResponse, PlainTextResponse
    except ImportError:
        logger.error("FastAPI/uvicorn not installed. Run: pip install fastapi uvicorn")
        sys.exit(1)

    from frontdesk_bot.twilio_handler import (
        receive_sms_webhook,
        process_inbound_sms,
        twiml_response,
    )
    from frontdesk_bot.message_classifier import classify_message
    from frontdesk_bot.response_templates import get_response
    from frontdesk_bot.ticket_manager import create_ticket

    app = FastAPI(title="Camelot Front Desk Bot", version="1.0.0")

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "Camelot Front Desk Bot"}

    @app.post("/sms/inbound")
    async def sms_inbound(request: Request):
        """Twilio SMS inbound webhook."""
        form_data = await request.form()
        payload = dict(form_data)
        try:
            result = process_inbound_sms(payload)
            # Return TwiML (Twilio reads this as an immediate response)
            xml = twiml_response("")  # Empty response — we send async via send_sms
            return Response(content=xml, media_type="application/xml")
        except Exception as e:
            logger.error(f"SMS inbound error: {e}")
            xml = twiml_response("Sorry, we encountered an error. Please call (212) 555-0199.")
            return Response(content=xml, media_type="application/xml")

    @app.post("/chat/message")
    async def chat_message(request: Request):
        """Web chat inbound message handler."""
        body = await request.json()
        message_text = body.get("message", "")
        resident_name = body.get("resident_name", "Resident")
        unit = body.get("unit", "")
        building_address = body.get("building_address", "")
        resident_id = body.get("resident_id", "")

        if not message_text:
            return JSONResponse({"error": "No message provided"}, status_code=400)

        classification = classify_message(message_text)

        ticket = create_ticket(
            resident_id=resident_id or "chat_user",
            unit=unit,
            category=classification.category,
            description=message_text,
            urgency=classification.urgency,
            channel="chat",
        )
        ticket_number = ticket.get("ticket_number", "")

        response = get_response(
            category=classification.category,
            urgency=classification.urgency,
            resident_name=resident_name,
            unit=unit,
            building_address=building_address,
            ticket_number=ticket_number,
        )

        # Fair-housing guardrail: screen outbound text before it reaches a
        # resident. Blocking findings replace the message with a neutral
        # acknowledgment and flag the ticket for human review.
        try:
            from utils.fair_housing_guard import check_text
            guard = check_text(response)
            if guard.blocking:
                logger.warning(
                    "Fair-housing guard blocked outbound chat response "
                    f"(ticket {ticket_number}): "
                    f"{[f.matched for f in guard.blocking]}"
                )
                response = (
                    f"Hi {resident_name}, we've received your request and created "
                    f"ticket #{ticket_number}. A member of our team will follow up "
                    "with you directly."
                )
        except ImportError:
            pass  # repo-root utils not importable in this runtime; skip screen

        return JSONResponse({
            "response": response,
            "ticket_number": ticket_number,
            "urgency": classification.urgency,
            "category": classification.category,
        })

    @app.get("/tickets/open")
    async def open_tickets(building_id: str = ""):
        """Return open tickets for a building."""
        from frontdesk_bot.ticket_manager import get_open_tickets
        tickets = get_open_tickets(building_id=building_id or None)
        return JSONResponse({"tickets": tickets, "count": len(tickets)})

    logger.info(f"Starting Front Desk Bot webhook server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level=LOG_LEVEL.lower())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Camelot Front Desk Bot — Resident communication handler"
    )
    parser.add_argument("--once", action="store_true", help="Check email once then exit (for cron)")
    parser.add_argument("--serve", action="store_true", help="Start webhook API server")
    parser.add_argument("--host", default="0.0.0.0", help="Webhook server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8001, help="Webhook server port (default: 8001)")
    parser.add_argument("--test-classify", metavar="MESSAGE", help="Test message classifier on a string")

    args = parser.parse_args()

    if args.test_classify:
        from frontdesk_bot.message_classifier import classify_message, describe_classification
        result = classify_message(args.test_classify)
        print(describe_classification(result))
        return

    if args.serve:
        run_webhook_server(host=args.host, port=args.port)
    else:
        run_email_loop(once=args.once)


if __name__ == "__main__":
    main()
