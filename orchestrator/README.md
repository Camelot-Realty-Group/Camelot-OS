# Camelot OS — Master Orchestrator

> **The central AI command layer for Camelot Property Management Services Corp.**
> Routes any request from a team member to the right specialist bot, chains bots into pipelines, and maintains conversation context across sessions.

---

## System Architecture

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                        CAMELOT OS ORCHESTRATOR                              ║
║                    api_server.py  |  Port 8000                              ║
║                                                                             ║
║   Input: /chat (REST)  /pipeline (REST)  /ws/{session_id} (WebSocket)       ║
║                                                                             ║
║   ┌──────────────────────────────────────────────────────────────────────┐  ║
║   │  router.py — Intent Classifier                                       │  ║
║   │  classify_intent(user_input) → RoutingDecision                       │  ║
║   │  Rule-based pattern matching, 70+ intent patterns                    │  ║
║   └──────────────────────────────────────────────────────────────────────┘  ║
║                                                                             ║
║   ┌──────────────────────────────────────────────────────────────────────┐  ║
║   │  pipeline.py — Multi-Bot Executor                                    │  ║
║   │  run_pipeline("lead_to_crm", params) → PipelineResult               │  ║
║   │  Sequential bot chaining with retry logic                           │  ║
║   └──────────────────────────────────────────────────────────────────────┘  ║
║                                                                             ║
║   ┌──────────────────────────────────────────────────────────────────────┐  ║
║   │  memory.py — Session Context (Supabase-backed)                       │  ║
║   │  OrchestratorMemory: add_message, get_history, clear_session         │  ║
║   └──────────────────────────────────────────────────────────────────────┘  ║
╚══════════════════════════════════════════════════════════════════════════════╝
            │               │             │              │
     ┌──────▼──┐      ┌─────▼───┐  ┌─────▼──┐   ┌──────▼──┐
     │  SCOUT  │      │ BROKER  │  │COMPLNCE│   │FRONTDESK│
     │  :local │      │  :local │  │  :8003 │   │  :8001  │
     └─────────┘      └─────────┘  └────────┘   └─────────┘
            │               │             │              │
     ┌──────▼──┐      ┌─────▼───┐  ┌─────▼──┐
     │  INDEX  │      │ REPORT  │  │  DEAL  │
     │  :8002  │      │  :local │  │  :local│
     └─────────┘      └─────────┘  └────────┘

Data Layer:
  ┌────────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────────┐
  │ Supabase   │  │ HubSpot CRM  │  │  AppFolio   │  │ Google Drive │
  │ (sessions, │  │ (deals,      │  │ (properties,│  │ (documents,  │
  │  leads,    │  │  contacts,   │  │  leases,    │  │  leases,     │
  │  tickets,  │  │  outreach)   │  │  financials)│  │  reports)    │
  │  violations│  └──────────────┘  └─────────────┘  └──────────────┘
  └────────────┘
  ┌────────────────────────────┐  ┌──────────────────────────────────┐
  │ NYC Open Data              │  │ External APIs                    │
  │ HPD · DOB · ECB · ACRIS   │  │ CoStar · OpenAI · Twilio · SG   │
  └────────────────────────────┘  └──────────────────────────────────┘
```

---

## Bot Directory

| Bot | Domain | API | Key Capabilities |
|-----|---------|-----|-----------------|
| **Scout** | Lead generation & property intelligence | Local subprocess | `search_leads`, `enrich_lead`, `push_to_hubspot`, `check_ownership` |
| **Broker** | Transaction execution & deal docs | Local subprocess | `generate_loi`, `build_proforma`, `analyze_cap_rate`, `draft_nda` |
| **Compliance** | NYC regulatory compliance | `:8003` | `check_hpd`, `check_dob`, `check_ll97`, `full_audit`, `property_scorecard` |
| **Front Desk** | Tenant ops & maintenance | `:8001` | `create_ticket`, `vendor_dispatch`, `emergency_escalate`, `tenant_message` |
| **Index** | Document intelligence & Drive org | `:8002` | `run_indexer`, `extract_lease_data`, `search_documents`, `flag_expiring` |
| **Report** | Financial reporting & KPI analytics | Local subprocess | `send_weekly_kpi`, `monthly_financials`, `investor_memo`, `deal_memo` |
| **Deal** | Acquisition outreach & CRM | Local subprocess | `research_target`, `build_battlecard`, `draft_email`, `log_outreach` |

---

## Named Pipelines

| Pipeline | Description | Bots Involved | Est. Time |
|----------|-------------|---------------|-----------|
| `lead_to_crm` | Find leads, enrich, push to HubSpot | Scout × 3 steps | ~2 min |
| `property_audit` | Full compliance audit + scorecard | Compliance + Report | ~90s |
| `deal_outreach` | Research target → battlecard → email → CRM | Deal × 4 steps | ~2.5 min |
| `new_acquisition_dd` | Full due diligence package | Scout + Compliance + Broker + Report | ~4 min |
| `weekly_ops_rhythm` | Weekly KPI + new leads + compliance sweep | Report + Scout + Compliance | ~3 min |
| `lease_audit` | Extract lease data, flag expiring, occupancy report | Index + Report | ~90s |

---

## API Reference

### Base URL
```
http://localhost:8000        (local dev)
https://api.camelotpms.com  (production)
```

### POST /chat
Route a natural-language request to the appropriate bot.

**Request:**
```json
{
  "session_id": "user-nyc-ops-001",
  "user_input": "Find property management companies in Westchester with 100+ units",
  "execute": true
}
```

**Response:**
```json
{
  "session_id": "user-nyc-ops-001",
  "request_id": "a1b2c3d4",
  "bot_name": "scout",
  "action": "search_leads",
  "params": {"region": "Westchester, NY", "property_type": "multifamily"},
  "confidence": 0.92,
  "pipeline": null,
  "rationale": "Routed to SCOUT → search_leads. Matched pattern...",
  "response": {"status": "dispatched", "message": "Scout received search_leads..."},
  "error": null,
  "duration_ms": 145.2,
  "timestamp": "2026-04-19T14:00:00Z"
}
```

### POST /pipeline
Execute a named multi-bot pipeline.

**Request:**
```json
{
  "pipeline_name": "property_audit",
  "params": {"address": "123 Eastern Pkwy, Brooklyn, NY"},
  "session_id": "user-ops-001"
}
```

**Response:**
```json
{
  "pipeline_name": "property_audit",
  "pipeline_id": "pip-abc123",
  "status": "completed",
  "steps_total": 4,
  "steps_succeeded": 4,
  "steps_failed": 0,
  "final_output": { "compliance_score": 52, "risk_level": "HIGH" },
  "error": null,
  "duration_seconds": 87.3,
  "timestamp": "2026-04-19T14:01:30Z"
}
```

### GET /bots
List all registered bots with capabilities.

**Response:** `{"bots": [...], "total": 7, "timestamp": "..."}`

### GET /status
Health check all bots.

**Response:**
```json
{
  "orchestrator": "online",
  "timestamp": "2026-04-19T14:00:00Z",
  "bots": [
    {"id": "scout", "name": "Scout", "status": "local", "latency_ms": null},
    {"id": "frontdesk", "name": "Front Desk", "status": "online", "latency_ms": 12.4},
    ...
  ],
  "bots_online": 6,
  "bots_offline": 0
}
```

### GET /pipelines
List all available named pipelines.

### GET /history/{session_id}
Retrieve conversation history for a session. Query param: `?last_n=20`

### DELETE /session/{session_id}
Clear all history for a session.

### WebSocket /ws/{session_id}
Streaming chat interface.

**Client sends:**
```json
{"user_input": "Find PM companies in Queens", "execute": true}
```

**Server emits sequence:**
```json
{"type": "progress", "data": {"message": "Analyzing your request..."}}
{"type": "routing",  "data": {"bot": "scout", "action": "search_leads", ...}}
{"type": "progress", "data": {"message": "Dispatching to Scout bot..."}}
{"type": "response", "data": {"content": "...", "bot": "scout", "duration_ms": 210}}
{"type": "done"}
```

---

## Local Development Setup

### Prerequisites
- Python 3.11+
- Docker & Docker Compose
- Supabase account (or local Supabase via `npx supabase start`)

### 1. Clone and install

```bash
git clone https://github.com/camelotpms/camelot-os.git
cd camelot-os

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install orchestrator dependencies
pip install fastapi uvicorn[standard] httpx pydantic supabase rich python-dotenv pyyaml
```

### 2. Configure environment

```bash
cp orchestrator/.env.example orchestrator/.env
# Edit .env and fill in your API keys
nano orchestrator/.env
```

**Minimum required for local dev:**
```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-key
OPENAI_API_KEY=sk-...
NYC_OPEN_DATA_APP_TOKEN=your-token   # Free at data.cityofnewyork.us
```

### 3. Bootstrap Supabase schema

```bash
# Open Supabase SQL editor and run:
cat orchestrator/memory.py | grep -A 50 "SCHEMA_SQL"
# Or run the bootstrap helper:
python -c "from orchestrator.memory import bootstrap_schema; bootstrap_schema()"
```

### 4. Start the API server

```bash
cd camelot-os
uvicorn orchestrator.api_server:app --host 0.0.0.0 --port 8000 --reload
```

Visit `http://localhost:8000` to confirm the API is running.
Open `orchestrator/dashboard.html` in your browser and update `API_BASE` to `http://localhost:8000`.

### 5. Use the CLI

```bash
# Single command
python orchestrator/cli.py "Find PM companies in Westchester"

# Interactive mode
python orchestrator/cli.py --interactive

# List all bots
python orchestrator/cli.py --bots

# Run a pipeline
python orchestrator/cli.py --pipeline lead_to_crm --params '{"region": "CT"}'
```

---

## Docker Deployment

### Full stack with Docker Compose

```bash
# Build all images
docker-compose -f orchestrator/docker-compose.yml build

# Start all services
docker-compose -f orchestrator/docker-compose.yml up -d

# Check status
docker-compose -f orchestrator/docker-compose.yml ps

# View logs
docker-compose -f orchestrator/docker-compose.yml logs -f orchestrator

# Stop
docker-compose -f orchestrator/docker-compose.yml down
```

Services started:
- `orchestrator` → `http://localhost:8000`
- `frontdesk_bot` → `http://localhost:8001`
- `index_bot` → `http://localhost:8002`
- `compliance_bot` → `http://localhost:8003`

---

## Deployment Guide

### Option A: Render (Recommended for simplicity)

1. Push the `camelot-os` repo to GitHub
2. Log into [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Set:
   - **Root Directory**: `orchestrator`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn api_server:app --host 0.0.0.0 --port $PORT`
5. Add all environment variables from `.env.example` in the Render dashboard
6. Deploy

For separate bot services, create additional Render Web Services for each bot.

### Option B: Railway

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

Set environment variables:
```bash
railway variables set SUPABASE_URL=... SUPABASE_SERVICE_KEY=... OPENAI_API_KEY=...
```

Railway auto-detects Python/FastAPI and handles the rest.

### Option C: AWS (ECS Fargate)

1. Build and push Docker images to ECR:
```bash
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_URL
docker build -t camelot-orchestrator -f orchestrator/Dockerfile .
docker tag camelot-orchestrator:latest $ECR_URL/camelot-orchestrator:latest
docker push $ECR_URL/camelot-orchestrator:latest
```
2. Create ECS Task Definition using the docker-compose.yml as reference
3. Create ECS Service with ALB target group
4. Store secrets in AWS Secrets Manager / Parameter Store
5. Set up Route 53 → ALB for `api.camelotpms.com`

---

## Environment Variables Reference

See [`.env.example`](.env.example) for the complete list with descriptions.

**Minimum required to run:**

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |
| `OPENAI_API_KEY` | OpenAI API key (GPT-4o) |
| `NYC_OPEN_DATA_APP_TOKEN` | NYC Open Data app token (free) |
| `PORT` | API server port (default: 8000) |

**Full integration (all bots):** also requires `HUBSPOT_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `APPFOLIO_API_KEY`, `TWILIO_*`, `SENDGRID_API_KEY`, `COSTAR_API_KEY`.

---

## File Structure

```
orchestrator/
├── api_server.py          # FastAPI REST API + WebSocket server
├── cli.py                 # Command-line interface (Rich-powered)
├── router.py              # Intent classifier — 70+ patterns, 7 bots
├── pipeline.py            # Multi-bot pipeline executor + named pipelines
├── memory.py              # Supabase-backed conversation memory
├── bot_registry.py        # Bot metadata, capabilities, endpoints
├── skill_definition.md    # Master system prompt / orchestrator spec
├── dashboard.html         # Single-file web dashboard
├── config.yaml            # Master configuration file
├── .env.example           # All environment variables (fill in → .env)
├── docker-compose.yml     # Full stack Docker deployment
├── README.md              # This file
└── prompts/
    ├── master_orchestrator.txt
    ├── scout.txt
    ├── broker.txt
    ├── compliance.txt
    ├── frontdesk.txt
    ├── index.txt
    ├── report.txt
    └── deal.txt
```

---

## Contributing

1. All new intent patterns go in `router.py` → `INTENT_PATTERNS`
2. New named pipelines go in `pipeline.py` → `build_named_pipeline()` and `NAMED_PIPELINES`
3. New bots go in `bot_registry.py` → `BOTS` dict
4. Bot prompts go in `prompts/` as `.txt` files
5. Run `python orchestrator/router.py` to test intent classification before committing

---

## License

Proprietary — Camelot Property Management Services Corp. All rights reserved.
