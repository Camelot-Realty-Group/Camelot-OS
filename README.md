# Camelot OS

**AI-Powered Operating System for Camelot Property Management Services Corp**

Camelot OS is a suite of 9 specialist AI bots controlled by a central orchestrator, replacing fragmented manual workflows with an intelligent, automated platform.

---

## Architecture

```
                    ┌─────────────────────────────┐
                    │     CAMELOT OS ORCHESTRATOR  │
                    │   (FastAPI + Router + Memory)│
                    └──────────────┬──────────────┘
     ┌──────────┬──────────────┼──────────────┬──────────┬────────────┐
  SCOUT      BROKER       COMPLIANCE      FRONTDESK   INDEX      CONCIERGE
Lead Gen   Brokerage      Violations      Residents   Files    Doc Templates
     ┌──────────────┬─────┴────────┬──────────────┐
                  REPORT         DEAL         COSTBEAT
                Analytics      Roll-Up     Budget Savings
```

## The 9 Bots

| Bot | Role | Stack |
|-----|------|-------|
| **Scout** | Lead generation & property intelligence | Python |
| **Broker** | Brokerage operations & deal analysis | Python + Node.js |
| **Compliance** | HPD/DOB/LL97 regulatory monitoring | Python |
| **Front Desk** | Resident & owner communications | Python + Node.js |
| **Index** | Google Drive file organization (MDS codes) | Python |
| **Concierge** | Document template catalog, download & auto-fill | Python |
| **Report** | Owner statements, KPI dashboards, investor updates | Python |
| **Deal** | Roll-up acquisition outreach pipeline | Python + Node.js |
| **CostBeat** | Budget savings analysis & fee justification | Python |

## Quick Start

```bash
# 1. Clone
git clone https://github.com/dgoldoff-hue/camelot-os.git
cd camelot-os

# 2. Set up environment
cp orchestrator/.env.example .env
# Fill in all API keys

# 3. Run full stack
cd orchestrator
docker-compose up --build -d

# 4. Access dashboard
open http://localhost:8000/dashboard
```

## Documentation

See `Camelot_OS_User_Manual.pdf` for the complete 69-page technical reference covering installation, all 7 bots, API reference, security, and deployment.

## Stack

- **Python 3.11** — all bots
- **Node.js 18** — HubSpot integrations
- **FastAPI** — orchestrator + bot APIs
- **Supabase** — tickets, sessions, leads
- **Google Drive API** — Index Bot
- **HubSpot API** — CRM across Scout, Broker, Deal
- **Twilio** — Front Desk SMS
- **Apollo.io + Prospeo** — Scout enrichment
- **Docker Compose** — deployment
- **Make.com** — Index Bot automation

## Team

| Person | Role |
|--------|------|
| David Goldoff | CEO / System Owner |
| Sam Lodge | Indexing Lead |
| Carl Harkien | HubSpot Admin |
| Luigi | Operations |
| Eleni Palmeri | Brokerage |

---

*Proprietary — Internal Use Only. All rights reserved, Camelot Property Management Services Corp.*
