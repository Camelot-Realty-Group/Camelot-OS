# Camelot OS — Perseus

Quarterly management-report variance engine. Upload one building's period actuals
and Perseus answers two questions that are usually collapsed into one:

1. **Did the building hit its own budget?** Line-by-line variance against the
   prorated budget share, with anything more than 10% over flagged for review.
2. **Is the building paying a market price?** The same lines annualized and
   compared to the Camelot portfolio average per unit — independent of the
   building's budget, because a budget can be wrong.

Only the second question produces a savings number. Beating a plan is not the
same as paying a market price, so a category over its own budget is a flag for
the manager, not a line in the fee proposal.

Each period is priced on its own. Nothing carries forward from a prior quarter,
nothing nets against a CostBeat annual-budget proposal for the same building, and
no running total accumulates across reports.

---

## Perseus vs. CostBeat Bot

| | **CostBeat Bot** | **Perseus** |
|---|---|---|
| Input | A proposed annual budget | A period's actuals (usually a quarter) |
| Cadence | Once, at budget time | Every period, ongoing |
| Compared to | Percentile of the comp range | Portfolio **average** per unit |
| Own-budget variance | Not applicable | Reported line by line |
| Fee proposal | Prices the year | Prices that period, standalone |

They share a taxonomy and a comparables table. They are deliberately separate
bots: CostBeat argues about a plan, Perseus reports on what happened.

---

## Architecture

```
perseus_bot/
├── main.py                    ← CLI + FastAPI HTTP entry point (port 8006)
├── parser.py                  ← Excel / CSV / MDS-PDF actuals parser
├── variance_engine.py         ← own-budget variance + portfolio-average gap
├── benchmarks.py              ← portfolio comparables, average-based targets
├── fee_engine.py              ← per-period fee proposal (one-time vs. uplift)
├── report_generator.py        ← client-facing PDF (reportlab)
├── expense_taxonomy.py        ← GL label → 16 expense categories
├── storage.py                 ← Supabase REST reads/writes
├── config_loader.py           ← config.yaml + env overrides
├── config.yaml                ← thresholds, fee percentages, categories
├── supabase_schema.sql        ← run manually in the Supabase SQL editor
├── templates/upload_form.html ← upload page + results view
├── .env.example
└── Dockerfile
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create the table

Open `perseus_bot/supabase_schema.sql`, paste it into the Supabase SQL editor,
and run it. Perseus never creates tables at runtime.

### 3. Configure environment

```bash
cp perseus_bot/.env.example .env
# SUPABASE_URL and SUPABASE_SERVICE_KEY are required.
# OPENAI_API_KEY is optional and affects prose only.
```

### 4. Run

```bash
python perseus_bot/main.py --serve --port 8006
```

Then open <http://localhost:8006/> and upload a period report.

---

## What Perseus accepts

**Excel / CSV** — four layouts are recognized without configuration:

- **Columnar** — a header row naming Actual / Budget / YTD / Variance columns,
  in any order and under most common wordings.
- **GL hierarchy** — MDS-style exports with account codes and section headers
  (`5100 · Electricity`), rolled up to categories.
- **Flat** — two columns: a label and an amount.

**PDF** — MDS period reports. Perseus reads the structured tables first
(`Label | Current Period | YTD | Budget | Variance`) and falls back to the text
layer. **If the columns cannot be identified with confidence, Perseus raises a
parse error rather than guessing which column is the actual spend** — the message
asks for an Excel export instead. No figure in a Perseus report is ever inferred
from an ambiguous layout.

Rows whose figure count does not match the header are skipped with a warning
listed in the report's coverage disclosure.

---

## Where the budget baseline comes from

In precedence order:

1. **An uploaded budget file** — an explicit upload is never silently ignored.
2. **A CostBeat analysis on file** for the same building, matched by name then
   address. Its `current_budget` per category becomes the annual baseline.
3. **The budget column inside the uploaded report**, annualized.

If none of the three is available, Perseus refuses to produce a variance section
rather than comparing actuals to zero.

---

## The fee proposal

Both options are priced on every report and one is recommended:

| Option | Formula | When it is recommended |
|---|---|---|
| **One-time fee** | 33% of the period's identified savings | Savings come mostly from one-off vendor switches and rebids, which run themselves once made |
| **Management-fee uplift** | 15% of annualized savings, billed monthly | Savings sit mostly in lines that need standing oversight — insurance remarketing, utility audits, staffing, assessments |

The split is driven by which categories the savings came from, not by which
number is larger. A period with no addressable savings returns both options at
$0 and says so.

Percentages live in `config.yaml` under `fees`.

---

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/` | Upload form + recent reports |
| `POST` | `/analyze` | Multipart upload → analysis, stored row, PDF |
| `GET` | `/reports` | Recent reports (`?limit=`, `?property_name=`) |
| `GET` | `/reports/{id}` | One stored report as JSON |
| `GET` | `/reports/{id}/report.pdf` | Regenerate and download the PDF |

### `POST /analyze`

Multipart form fields:

| Field | Required | Notes |
|---|---|---|
| `actual_file` | ✓ | `.xlsx`, `.xls`, `.csv`, or `.pdf` |
| `property_name` | ✓ | |
| `year` | ✓ | |
| `unit_count` | ✓ | Drives the per-unit comparison |
| `quarter` | | `Q1`–`Q4`; omit for non-quarterly cadences |
| `cadence` | | `quarterly` (default), `monthly`, `semiannual`, `annual` |
| `address` | | Improves CostBeat baseline matching |
| `building_type` | | Narrows the comparable set |
| `market` | | Narrows the comparable set |
| `budget_file` | | Annual budget, if not already in Supabase or the report |
| `notes` | | Stored with the row |
| `created_by` | | Stored with the row |

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SUPABASE_URL` | ✓ | — | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | ✓ | — | Service role key (RLS is service-role-only) |
| `OPENAI_API_KEY` | | — | Rephrases recommendation prose only; never produces a figure |
| `PERSEUS_OUTPUT_DIR` | | `output/perseus` | Where PDFs are written |
| `LOG_LEVEL` | | `INFO` | Python log level |
| `LOG_FILE` | | `logs/perseus_bot.log` | Log file path |

---

## Notes on the numbers

- **Annualizing.** A period's spend is multiplied by the number of periods in the
  year. For gas, electricity, HVAC, and water the report says plainly that even
  proration overstates or understates a seasonal line — Perseus flags the
  distortion rather than inventing a seasonal curve it cannot support.
- **Thin comp sets.** Below `min_comps_for_confident_target` comparables, the
  portfolio average is blended with the building's own spend, which pulls the
  target toward the subject and reduces the claimed saving. The comp count is
  printed next to every line.
- **Targets never exceed current spend.** A building already below the portfolio
  average shows no saving, not a negative one.
- **Scope-protected categories.** Sprinkler and fire alarm, elevator
  maintenance, and taxes and bank fees are rebid-only: the recommendation never
  suggests reducing scope on a life-safety or statutory line.

---

*Camelot Property Management Services Corp — Camelot OS v1.0*
