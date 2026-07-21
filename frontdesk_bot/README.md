# Front Desk Bot
## Camelot Property Management Services Corp

AI-powered resident and owner communication handler. Processes inbound messages via email, SMS, and web chat — classifies urgency, creates maintenance tickets, and sends threaded responses automatically.

---

## Features

| Module | Function |
|--------|---------|
| `message_classifier.py` | Rule-based classifier: category + urgency + sentiment |
| `response_templates.py` | Canned response library for every category/urgency |
| `ticket_manager.py` | Supabase-backed ticket creation and status management |
| `twilio_handler.py` | Twilio SMS inbound webhook + outbound SMS |
| `email_handler.py` | IMAP polling + SMTP threaded responses |
| `main.py` | Polling loop + FastAPI webhook server |

---

## Quick Start

```bash
# 1. Install dependencies
pip install requests fastapi uvicorn

# 2. Configure environment
cp .env.example .env
# Fill in IMAP, SMTP, Twilio, and Supabase credentials

# 3. Create Supabase tickets table (run SQL from ticket_manager.py CREATE_TABLE_SQL)

# 4. Test message classifier
python main.py --test-classify "My heat isn't working and it's freezing"

# 5. Run email polling loop
python main.py

# 6. Or run single check (for cron)
python main.py --once

# 7. Start webhook server (for Twilio SMS + web chat)
python main.py --serve --port 8001
```

---

## Twilio Webhook Configuration

Set your Twilio SMS webhook URL to:
```
https://your-server.com/sms/inbound
POST method
```

---

## Escalation Protocol

| Urgency | Trigger | Actions |
|---------|---------|---------|
| **Emergency** | Fire, gas, flood, no heat (Oct–Apr), CO | Auto-respond + SMS on-call + create P0 ticket + email manager |
| **Urgent** | No hot water, lockout, elevator, pest | Auto-respond with 2hr ETA + create P1 ticket |
| **Routine** | All other requests | Auto-respond with 24hr ETA + create P2 ticket |

---

## Ticket Format

`CAM-YYYY-NNNN` — e.g., `CAM-2026-0042`

Stored in Supabase `tickets` table. Status lifecycle:
**Open → Assigned → In Progress → Resolved → Closed**

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `IMAP_USER` / `IMAP_PASSWORD` | Email inbox credentials |
| `SMTP_USER` / `SMTP_PASSWORD` | Email sending credentials |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Twilio sending number (E.164) |
| `ONCALL_PHONE` | On-call maintenance phone (E.164) |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service key |
| `ESCALATION_EMAIL` | Manager escalation email |
| `EMAIL_POLL_INTERVAL` | Seconds between inbox checks (default: 60) |
