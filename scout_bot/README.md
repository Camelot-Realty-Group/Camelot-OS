# Scout Bot — Camelot OS

**Lead generation and property intelligence bot for Camelot Property Management Services Corp.**

Scout Bot runs daily at 7 AM, collecting property-management acquisition targets, RFPs, hiring signals, and unmanaged buildings across New York, New Jersey, Connecticut, and Florida. It enriches the best leads with contact data from Apollo.io and Prospeo, pushes them into HubSpot CRM, and emails a branded PDF + CSV digest to the Camelot team.

Scout Bot also runs a second, independent pipeline via `--serve` mode: a daily NYC Open Data lead hunt and a periodic outreach-mailbox poll ("Merlin Inbox"). See [Two Lead Pipelines](#two-lead-pipelines) below before assuming `scout_leads` is the only table this bot touches.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Two Lead Pipelines](#two-lead-pipelines)
3. [File Structure](#file-structure)
4. [Prerequisites](#prerequisites)
5. [Installation](#installation)
6. [Environment Variables](#environment-variables)
7. [Configuration](#configuration)
8. [Running Scout Bot](#running-scout-bot)
9. [API Server (--serve mode)](#api-server---serve-mode)
10. [Supabase Setup](#supabase-setup)
11. [HubSpot Setup](#hubspot-setup)
12. [Render Deployment](#render-deployment)
13. [Cron Scheduling](#cron-scheduling)
14. [Lead Schema](#lead-schema)
15. [Collector Reference](#collector-reference)
16. [Enrichment Pipeline](#enrichment-pipeline)
17. [Reports & Email](#reports--email)
18. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
                     ┌─────────────────────────────────────────┐
                     │              main.py                     │
                     │        Master Orchestrator               │
                     └──────────────────┬──────────────────────┘
                                        │
           ┌────────────────────────────┼──────────────────────────┐
           │         ThreadPoolExecutor (parallel)                  │
           │                                                        │
  ┌────────┴──────┐  ┌──────────────┐  ┌──────────┐  ┌──────────┐ │
  │  BizBuySell   │  │   BizQuest   │  │  LoopNet │  │ NYC RFPs │ │
  └───────────────┘  └──────────────┘  └──────────┘  └──────────┘ │
  ┌───────────────┐  ┌──────────────┐                              │
  │ Job Signals   │  │ HPD Buildings│                              │
  └───────────────┘  └──────────────┘                              │
           │                                                        │
           └────────────────┬───────────────────────────────────────┘
                            │ raw leads
                            ▼
                   ┌─────────────────┐
                   │  utils/filters  │  tag → dedup → score → filter
                   └────────┬────────┘
                            │ filtered + scored leads
                            ▼
                   ┌─────────────────┐
                   │   enricher.py   │  Apollo.io → Prospeo → merge
                   └────────┬────────┘
                            │ enriched leads (with contacts[])
                   ┌────────┴────────────────────────────────┐
                   │                │                        │
                   ▼                ▼                        ▼
          ┌───────────────┐ ┌─────────────┐      ┌──────────────────┐
          │ HubSpot (Node)│ │ PDF + CSV   │      │  Email (SMTP)    │
          │ hubspot_client│ │  Reports    │      │  Daily Digest    │
          └───────────────┘ └─────────────┘      └──────────────────┘
```

---

## Two Lead Pipelines

Scout Bot contains two separate, non-interacting lead pipelines. They share a codebase and a config file but not a table, a trigger, or an output.

| | CLI Batch Pipeline (original) | Lead Hunt + Merlin Inbox (new) |
|---|---|---|
| **Trigger** | `python main.py` (cron / GitHub Actions / manual) | `POST /lead-hunt/run` and `POST /merlin/poll-inbox` via `--serve` mode |
| **Sources** | BizBuySell, BizQuest, LoopNet, job postings, HPD buildings collector | NYC Open Data (HPD, DOB, DOF, LL97) directly |
| **Storage** | Supabase `scout_leads` (optional — works without Supabase) | Supabase `scout_buildings`, `scout_scans`, `scout_outreach_log`, `merlin_inbound_messages` (**required** — these routes 503 without it) |
| **Contact enrichment** | Apollo.io + Prospeo | None — HPD registration contacts only |
| **Downstream** | HubSpot CRM push + PDF/CSV email digest | Direct-to-Supabase; no HubSpot, no email digest (yet) |
| **Reply handling** | None | Merlin Inbox polls a mailbox and matches replies back to `scout_outreach_log` |
| **Was this pipeline live before this repo's `feature/merlin-leadhunt` branch?** | Yes, in production | No — rebuilding two pg_cron jobs (`camelot-daily-lead-hunt`, `merlin-inbox-poll`) that pointed at endpoints that never existed |

If you are debugging "why didn't my lead show up in HubSpot," you are almost certainly looking at the wrong pipeline if the lead came from the new Supabase tables above — Lead Hunt leads never touch HubSpot.

---

## File Structure

```
scout_bot/
├── main.py                          # Master orchestrator + --serve mode (FastAPI: /health, /lead-hunt/run, /merlin/poll-inbox)
├── config.yaml                      # Central configuration
├── requirements.txt                 # Python dependencies
├── package.json                     # Node.js dependencies
├── .env.example                     # Environment variable template
├── skill_definition.md              # Role, data sources, scoring/matching rules, operating rules
├── storage.py                       # SupabaseREST client for scout_buildings/scout_scans/scout_outreach_log/merlin_inbound_messages
├── scoring.py                       # calculate_score() — ported from camelot-scout-v6's scoring.ts
├── lead_hunt.py                     # NYC Open Data query + score + upsert (POST /lead-hunt/run)
├── merlin_inbox.py                  # Inbox poll + reply matching + logging (POST /merlin/poll-inbox)
│
├── collectors/
│   ├── __init__.py
│   ├── bizbuysell.py                # BizBuySell PM businesses for sale
│   ├── bizquest.py                  # BizQuest PM businesses for sale
│   ├── loopnet.py                   # LoopNet PM service businesses
│   ├── nyc_rfps.py                  # NYC gov RFPs (DCAS, HPD, EDC)
│   ├── jobs_signals.py              # Indeed + ZipRecruiter hiring signals
│   └── hpd_buildings.py            # NYC HPD unmanaged building detector
│
├── enrichment/
│   ├── __init__.py
│   ├── apollo_client.py             # Apollo.io API client
│   ├── prospeo_client.py            # Prospeo API client
│   └── enricher.py                  # Two-source enrichment orchestrator
│
├── integrations/
│   ├── __init__.py
│   └── hubspot_client.js            # HubSpot CRM Node.js integration
│
├── reports/
│   ├── __init__.py
│   ├── pdf_generator.py             # ReportLab PDF generator
│   ├── csv_exporter.py              # CSV export utilities
│   └── output/                      # Generated reports (gitignored)
│
├── utils/
│   ├── __init__.py
│   ├── parsing.py                   # Email/phone/date/address parsing
│   ├── filters.py                   # Dedup, scoring, filtering, tagging
│   └── emailer.py                   # SMTP email sender
│
└── logs/
    └── scout_YYYY-MM-DD.log         # Daily log files (gitignored)
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | |
| Node.js | ≥ 18.0 | For HubSpot integration |
| npm | ≥ 9.0 | Comes with Node.js |
| Apollo.io account | Any tier | For contact enrichment |
| Prospeo account | Any tier | For email finding |
| HubSpot portal | Any tier | Marketing Hub Starter+ recommended |

---

## Installation

### 1. Clone / copy the project

```bash
cd /your/projects/directory
# Files already at camelot_os/scout_bot/
cd camelot_os/scout_bot
```

### 2. Create Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows
```

### 3. Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Install Node.js dependencies

```bash
npm install
```

### 5. Configure environment

```bash
cp .env.example .env
# Edit .env with your real API keys and SMTP credentials
nano .env
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `APOLLO_API_KEY` | Yes* | Apollo.io API key for contact enrichment |
| `PROSPEO_API_KEY` | Yes* | Prospeo API key (default provided) |
| `HUBSPOT_ACCESS_TOKEN` | Yes* | HubSpot Private App token |
| `SMTP_HOST` | Yes* | SMTP server hostname |
| `SMTP_PORT` | No | SMTP port (default: 587) |
| `SMTP_USER` | Yes* | SMTP username |
| `SMTP_PASSWORD` | Yes* | SMTP password or app key |
| `SMTP_FROM` | No | Sender address (default: `leads-bot@camelot.nyc`) |
| `SMTP_USE_TLS` | No | Enable STARTTLS (default: `true`) |
| `SMTP_USE_SSL` | No | Use direct SSL (default: `false`) |
| `SOCRATA_APP_TOKEN` | No | NYC Open Data app token (higher rate limits) |
| `SUPABASE_URL` | No for CLI pipeline / **Yes for --serve mode** | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | No for CLI pipeline / **Yes for --serve mode** | Supabase service role key |
| `MERLIN_IMAP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_MAILBOX` | Yes for `/merlin/poll-inbox` | Outreach mailbox IMAP credentials — **provider unconfirmed, see `.env.example` for the full open question** |

\* Required for the respective feature to work. Scout Bot degrades gracefully when optional credentials are missing — with one exception: `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` are optional for the original CLI batch pipeline but **required** for the newer `/lead-hunt/run` and `/merlin/poll-inbox` endpoints, which return HTTP 503 without them rather than silently no-op'ing.

---

## Configuration

All runtime parameters are in `config.yaml`:

```yaml
regions: [NY, FL, CT, NJ]          # Collector target regions
min_lead_score: 40                  # Minimum quality score (0–100)
max_enrichments_per_run: 20         # API enrichment budget per run
report_recipients:                  # Email digest recipients
  - dgoldoff@camelot.nyc
  - slodge@camelot.nyc
  - luigi@camelot.nyc
  - charkien@camelot.nyc
hubspot:
  pipeline: "Camelot Prospects"
  default_stage: "appointmentscheduled"
log_level: INFO
cron: "0 7 * * *"                   # 7 AM daily

# --serve mode only (Lead Hunt + Merlin Inbox — separate pipeline, see
# "Two Lead Pipelines" above):
lead_hunt:
  buildings_table: scout_buildings
  scans_table: scout_scans
  boroughs: [MANHATTAN, BROOKLYN, QUEENS, BRONX, STATEN ISLAND]
  recent_days: 90
  min_lead_score: 40
  cron_job_name: camelot-daily-lead-hunt   # the paused pg_cron job this endpoint serves
  cron_schedule: "0 11 * * *"              # UTC (~7 AM Eastern)
merlin_inbox:
  provider: imap                           # see .env.example MERLIN_IMAP_* — provider unconfirmed
  cron_job_name: merlin-inbox-poll         # the paused pg_cron job this endpoint serves
  cron_schedule: "*/10 * * * *"            # every 10 minutes
```

---

## Running Scout Bot

### Full run

```bash
cd camelot_os/scout_bot
source .venv/bin/activate
python main.py
```

### Dry run (collect + process only — no HubSpot, no email)

```bash
python main.py --dry-run
```

### Skip enrichment (faster, no API calls to Apollo/Prospeo)

```bash
python main.py --no-enrichment
```

### Skip HubSpot push

```bash
python main.py --no-hubspot
```

### Skip email

```bash
python main.py --no-email
```

### Run individual collectors for testing

```bash
python collectors/bizbuysell.py
python collectors/hpd_buildings.py
python collectors/nyc_rfps.py
```

### Test PDF generation

```bash
python reports/pdf_generator.py
# Writes test PDFs to /tmp/camelot_*_test.pdf
```

---

## API Server (--serve mode)

The CLI batch pipeline above and the API server below are two different entry points into the same `main.py`. `--serve` does not run the collectors/HubSpot/email pipeline; it starts a FastAPI app exposing the Lead Hunt + Merlin Inbox pipeline instead.

```bash
python main.py --serve                       # binds 0.0.0.0:8007
python main.py --serve --host 127.0.0.1 --port 8010
```

### `GET /health`

Returns service status and which dependencies are configured:

```json
{
  "status": "ok",
  "service": "Camelot Scout Bot",
  "supabase_configured": true,
  "socrata_app_token_configured": false,
  "merlin_inbox_configured": true,
  "hubspot_configured": true
}
```

### `POST /lead-hunt/run`

Runs the daily NYC Open Data lead hunt: queries HPD registrations/violations (and related datasets) for the configured boroughs, scores every candidate building (see `scoring.py`), and upserts qualifying ones (score ≥ `min_score`) into Supabase `scout_buildings`, recording the run in `scout_scans`.

Query params (all optional — default from `config.yaml`'s `lead_hunt` section when omitted):

| Param | Type | Default (from config.yaml) |
|---|---|---|
| `triggered_by` | string | `"cron"` |
| `boroughs` | list[string] | `lead_hunt.boroughs` |
| `recent_days` | int | `lead_hunt.recent_days` (90) |
| `min_score` | int | `lead_hunt.min_lead_score` (40) |
| `dry_run` | bool | `false` |

**Idempotent per calendar day per `triggered_by`** — a second call the same day with the same `triggered_by` returns the existing scan's summary instead of re-scanning and re-upserting. Returns HTTP 503 if Supabase is not configured; HTTP 502 on a NYC Open Data failure severe enough to abort the whole run (partial per-borough failures instead just show up in the response's `errors` list and the run still completes).

### `POST /merlin/poll-inbox`

Polls the configured outreach mailbox (see [Environment Variables](#environment-variables) — IMAP only, provider TBD) for messages since `since` (ISO timestamp, defaults to the provider's own "since last successful poll" bookkeeping), matches each one to `scout_outreach_log` by thread id first, then by contact email, classifies its intent, and logs it to `merlin_inbound_messages`.

**Idempotent forever, not just per-day** — keyed on the provider's native message id; a message already logged is never logged twice, however many times or however wide a window this is polled with. Returns HTTP 503 if the mailbox or Supabase is not configured.

---

## Supabase Setup

Supabase is optional but recommended for persistent lead storage and deduplication across runs.

### 1. Create a Supabase project

Go to [supabase.com](https://supabase.com) → New Project → choose region `us-east-1`.

### 2. Create the `scout_leads` table

Run the following SQL in the Supabase SQL editor:

```sql
CREATE TABLE scout_leads (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    source_site     TEXT,
    region          TEXT,
    post_date       DATE,
    days_posted     INTEGER,
    title           TEXT,
    company_name    TEXT,
    lead_type       TEXT,
    category        TEXT,
    score           INTEGER DEFAULT 0,
    raw_location    TEXT,
    link            TEXT UNIQUE,
    email           JSONB DEFAULT '[]',
    phone           JSONB DEFAULT '[]',
    social_media    JSONB DEFAULT '[]',
    tags            JSONB DEFAULT '[]',
    author          TEXT,
    post_description TEXT,
    contacts        JSONB DEFAULT '[]',
    hubspot_company_id TEXT,
    hubspot_deal_id    TEXT,
    run_date        DATE DEFAULT CURRENT_DATE
);

-- Index for deduplication queries
CREATE UNIQUE INDEX scout_leads_link_idx ON scout_leads (link)
    WHERE link IS NOT NULL AND link != '';

CREATE INDEX scout_leads_run_date_idx ON scout_leads (run_date DESC);
CREATE INDEX scout_leads_score_idx    ON scout_leads (score DESC);
CREATE INDEX scout_leads_region_idx   ON scout_leads (region);
```

### 3. Add credentials to .env

```bash
SUPABASE_URL=https://yourproject.supabase.co
SUPABASE_SERVICE_KEY=eyJ...your_service_role_key...
```

---

## HubSpot Setup

### 1. Create a Private App

1. Go to your HubSpot portal → **Settings** → **Integrations** → **Private Apps**
2. Click **Create a private app**
3. Name: `Camelot Scout Bot`
4. Select scopes:
   - `crm.objects.contacts.write`
   - `crm.objects.contacts.read`
   - `crm.objects.companies.write`
   - `crm.objects.companies.read`
   - `crm.objects.deals.write`
   - `crm.objects.deals.read`
   - `crm.schemas.deals.read`
5. Copy the access token → set as `HUBSPOT_ACCESS_TOKEN` in `.env`

### 2. Create the "Camelot Prospects" pipeline

1. HubSpot portal → **CRM** → **Deals** → **Pipeline Settings**
2. Create pipeline named exactly: `Camelot Prospects`
3. Ensure it has an `appointmentscheduled` stage (or the Scout Bot will use the first available stage)

### 3. Create custom deal/company properties (recommended)

In HubSpot portal → **Settings** → **Data Management** → **Properties**:

**Deal properties** (Group: Scout Bot):
- `scout_source_site` — Single-line text — "Lead Source Site"
- `scout_region` — Single-line text — "Lead Region"
- `scout_lead_type` — Single-line text — "Lead Type"
- `scout_score` — Number — "Scout Lead Score"
- `scout_source_url` — Single-line text — "Source URL"
- `scout_run_date` — Date — "Scout Run Date"

**Company properties** (Group: Scout Bot):
- `scout_source_site` — Single-line text
- `scout_category` — Single-line text
- `scout_score` — Number

**Contact properties** (Group: Scout Bot):
- `scout_source` — Single-line text — "Enrichment Source"

---

## Render Deployment

Scout Bot is designed to run as a **Background Worker** or **Cron Job** on [Render](https://render.com).

### Option A: Cron Job (recommended)

1. Create a new **Cron Job** in Render
2. **Name**: `camelot-scout-bot`
3. **Runtime**: Python 3.11
4. **Build command**:
   ```bash
   pip install -r requirements.txt && npm install
   ```
5. **Start command**:
   ```bash
   python main.py
   ```
6. **Schedule**: `0 7 * * *` (7 AM UTC — adjust for Eastern time)
7. Add all environment variables from `.env` in the **Environment** tab

### Option B: Background Worker with internal scheduler

1. Create a **Web Service** (Background Worker type)
2. **Start command**:
   ```bash
   python -c "
   import schedule, time, subprocess, sys
   def run():
       subprocess.run([sys.executable, 'main.py'], check=False)
   schedule.every().day.at('07:00').do(run)
   while True:
       schedule.run_pending()
       time.sleep(60)
   "
   ```

### render.yaml (Infrastructure as Code)

```yaml
services:
  - type: cron
    name: camelot-scout-bot
    runtime: python
    buildCommand: "pip install -r requirements.txt && npm install"
    startCommand: "python main.py"
    schedule: "0 12 * * *"   # 7 AM Eastern = 12 PM UTC
    envVars:
      - key: APOLLO_API_KEY
        sync: false
      - key: PROSPEO_API_KEY
        sync: false
      - key: HUBSPOT_ACCESS_TOKEN
        sync: false
      - key: SMTP_HOST
        sync: false
      - key: SMTP_USER
        sync: false
      - key: SMTP_PASSWORD
        sync: false
```

---

## Cron Scheduling

### System cron (Linux/macOS)

```bash
crontab -e
```

Add:

```cron
# Scout Bot — 7 AM Eastern (12 PM UTC)
0 12 * * * cd /path/to/camelot_os/scout_bot && /path/to/.venv/bin/python main.py >> logs/cron.log 2>&1
```

### GitHub Actions (free, cloud-hosted)

Create `.github/workflows/scout_bot.yml`:

```yaml
name: Scout Bot Daily Run

on:
  schedule:
    - cron: "0 12 * * *"   # 7 AM Eastern
  workflow_dispatch:         # manual trigger

jobs:
  scout:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - name: Install dependencies
        run: |
          cd camelot_os/scout_bot
          pip install -r requirements.txt
          npm install
      - name: Run Scout Bot
        env:
          APOLLO_API_KEY: ${{ secrets.APOLLO_API_KEY }}
          PROSPEO_API_KEY: ${{ secrets.PROSPEO_API_KEY }}
          HUBSPOT_ACCESS_TOKEN: ${{ secrets.HUBSPOT_ACCESS_TOKEN }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
        run: |
          cd camelot_os/scout_bot
          python main.py
```

---

## Lead Schema

Every lead dict produced by Scout Bot conforms to this schema:

```python
{
    "source_site":       str,    # "BizBuySell" | "BizQuest" | "LoopNet" | "NYC Gov — HPD" | ...
    "region":            str,    # "NY" | "FL" | "CT" | "NJ"
    "post_date":         date,   # datetime.date or None
    "days_posted":       int,    # days since posting, or None
    "title":             str,    # listing/posting title
    "post_description":  str,    # full description text
    "author":            str,    # seller/poster name if available
    "company_name":      str,    # PM company name
    "link":              str,    # source URL
    "email":             list,   # extracted email addresses
    "phone":             list,   # extracted phone numbers (formatted)
    "social_media":      list,   # social media URLs
    "category":          str,    # "Business for sale" | "RFP" | "Hiring signal" | "Unmanaged building"
    "lead_type":         str,    # "Acquisition" | "Management mandate" | "Succession" | "Hiring signal" | "Unmanaged building"
    "raw_location":      str,    # location string from source
    "score":             int,    # quality score 0–100
    "tags":              list,   # ["Acquisition", "Succession", ...]
    "contacts":          list,   # populated after enrichment — see below
}
```

**Contact schema** (inside `contacts` list after enrichment):

```python
{
    "name":           str,
    "first_name":     str,
    "last_name":      str,
    "title":          str,    # job title
    "email":          str,
    "phone":          list,
    "linkedin_url":   str,
    "company":        str,
    "city":           str,
    "state":          str,
    "source":         str,    # "Apollo.io" | "Prospeo" | "Apollo.io + Prospeo"
    "seniority":      str,
}
```

---

## Collector Reference

| Collector | Source | Lead Type | Regions |
|---|---|---|---|
| `bizbuysell.py` | bizbuysell.com | Acquisition | NY, FL, CT, NJ |
| `bizquest.py` | bizquest.com | Acquisition | NY, FL, CT, NJ |
| `loopnet.py` | loopnet.com | Acquisition / Management mandate | NYC, Westchester, NJ, CT, FL |
| `nyc_rfps.py` | nyc.gov, edc.nyc | Management mandate (RFP) | NY |
| `jobs_signals.py` | indeed.com, ziprecruiter.com | Hiring signal | NY, NJ, CT, FL |
| `hpd_buildings.py` | data.cityofnewyork.us | Unmanaged building | NY |

### Scoring weights

| Signal | Points |
|---|---|
| Has email | +20 |
| Has phone | +20 |
| Has company name | +15 |
| Posted ≤ 7 days ago | +20 |
| Lead type = Acquisition | +25 |
| Category = RFP | +20 |
| **Max possible** | **100** |

---

## Enrichment Pipeline

Scout Bot enriches the top `max_enrichments_per_run` leads (default 20) after scoring:

1. **Apollo.io** `search_people(company_name, domain)` — finds decision-maker contacts (Owner, Principal, CEO, VP PM, etc.)
2. **Prospeo** `company_search(company_name, domain)` — used when Apollo returns fewer than 2 contacts
3. **Prospeo LinkedIn enrichment** — fills email gaps for Apollo contacts that have a LinkedIn URL but no email
4. **Deduplication** — contacts merged by email; source tagged as `"Apollo.io"`, `"Prospeo"`, or `"Apollo.io + Prospeo"`

Enrichment is rate-limited to 1 request/second per provider to stay within API limits.

---

## Reports & Email

### PDF Daily Digest

Generated by `reports/pdf_generator.py` using ReportLab:
- Camelot branded header (dark navy `#1A2645` + gold `#C9A84C`)
- Summary statistics bar (total leads, by type, avg score)
- Top 50 leads table with colour-coded scores

### CSV Exports

Two CSV files are generated each run:

- `Scout_Leads_YYYY-MM-DD.csv` — one row per lead, all schema fields
- `Scout_Leads_Enriched_YYYY-MM-DD.csv` — one row per contact per lead (flattened)

Both use UTF-8 with BOM for Excel compatibility.

### Email

Sent via SMTP by `utils/emailer.py`:
- Branded HTML email with summary stats + top-30 leads table
- PDF attached
- Both CSVs attached
- Recipients: `dgoldoff@camelot.nyc`, `slodge@camelot.nyc`, `luigi@camelot.nyc`, `charkien@camelot.nyc`

---

## Troubleshooting

### "No leads collected from any source"

- Check your internet connection and that the target sites are accessible
- Run individual collectors in debug mode: `python collectors/bizbuysell.py`
- BizBuySell/BizQuest may have changed their HTML structure — inspect the page and update CSS selectors in the collector

### Apollo.io returns 0 contacts

- Verify `APOLLO_API_KEY` is set and valid
- Apollo requires the company to exist in their database; try a well-known PM company name to confirm the key works
- Check Apollo credit balance in your portal

### HubSpot push fails

- Verify `HUBSPOT_ACCESS_TOKEN` is valid and not expired
- Ensure the Private App has all required CRM scopes
- Verify `Camelot Prospects` pipeline exists with exact name spelling
- Run `node integrations/hubspot_client.js '{"leads":[]}'` to test Node.js connectivity

### Email not sending

- Test SMTP credentials with a standalone test: set env vars and run `python utils/emailer.py` directly (add a quick test call at the bottom)
- For Gmail: use an App Password (not your account password) — [instructions](https://support.google.com/accounts/answer/185833)
- For corporate SMTP: ensure the host, port, and TLS settings match your mail server configuration

### PDF generation fails

- Ensure `reportlab` is installed: `pip install reportlab`
- Run standalone test: `python reports/pdf_generator.py` — outputs to `/tmp/`

### Logs

All logs are written to `logs/scout_YYYY-MM-DD.log`. Check these first for any run failures:

```bash
tail -f logs/scout_$(date +%Y-%m-%d).log
```

---

*Scout Bot — Camelot OS v1.0 | Camelot Property Management Services Corp. | Confidential*
