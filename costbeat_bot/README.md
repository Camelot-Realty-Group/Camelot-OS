# CostBeat Bot

Takes a building's operating budget and answers one question line by line:
**what would this building cost to run under Camelot?** Every target is
anchored to a named Camelot-managed building of comparable size with its
actual annual cost cited, and the report closes with two priced ways for
Camelot to capture a share of the savings it creates.

Aimed at prospective clients, boards weighing a management change, and
owners reviewing their current agent.

## What it does

- **Parse** — reads a budget from `.xlsx`, `.csv`, or `.pdf`. Handles GL
  account-hierarchy exports (Income/Expense sections, nested category →
  sub-category → account rows, subtotal rows) as well as flat
  label/amount sheets. Subtotal and income rows are excluded so nothing
  is double-counted. Lines are reconciled against the declared expense
  total; a mismatch or a PDF source flags the analysis `needs_review`.
- **Benchmark** — maps arbitrary GL labels onto a fixed sixteen-category
  taxonomy, then pulls comparables from Supabase `portfolio_benchmarks`
  within a unit-count band, relaxing building type and market until comps
  are found. Everything is normalised per unit before comparison.
- **Analyze** — sets a conservative target per category, computes the
  addressable saving, and states the specific mechanism that gets there.
- **Price** — computes a one-time cost-recovery fee and a permanent
  management-fee uplift, and recommends one based on whether the savings
  need standing oversight to hold.
- **Report** — renders a Camelot-branded landscape PDF and persists the
  analysis to Supabase `costbeat_analyses`.

## Running

```bash
pip install -r ../requirements.txt
cp .env.example .env              # fill in SUPABASE_URL + SUPABASE_SERVICE_KEY
python main.py --serve            # API + upload form on :8005
python main.py --serve --port 9005
```

The Supabase tables must exist first — run `supabase_schema.sql`
manually in the Supabase SQL editor. There is no migration runner in
this repo.

## API

| Method | Path                              | Description                                   |
|--------|-----------------------------------|-----------------------------------------------|
| GET    | `/health`                         | Health check, reports Supabase/LLM config     |
| GET    | `/`                               | Branded upload + results page                 |
| POST   | `/analyze`                        | multipart budget upload → full analysis       |
| GET    | `/analyses?limit=`                | List stored analyses                          |
| GET    | `/analyses/{id}`                  | One full stored analysis                      |
| GET    | `/analyses/{id}/report.pdf`       | Regenerate and stream the branded PDF         |

`POST /analyze` takes `budget_file` plus form fields `property_name`,
`unit_count` (both required), and optional `address`, `building_type`
(`condo` | `co-op` | `rental` | `mixed-use`), `market`, `notes`,
`created_by`.

## `portfolio_benchmarks` ships empty

The whole analysis is built from Camelot's own portfolio data, and that
table starts with zero rows. Until ops seeds it from the MDS monthly
management reports, every expense line reports as *"Not addressed —
needs records/vendor-bid review"* and the savings total is $0. That is
the correct output, not a failure — the bot will not benchmark against
industry averages or published surveys to fill the gap.

The seed row format and the exact sixteen category keys are documented in
the comment block at the bottom of `supabase_schema.sql`.

## Where the numbers come from

The target for a category sits at `target_percentile_of_comp_range`
between the cheapest and dearest comp on a per-unit basis, scaled to the
subject's unit count. With fewer than `min_comps_for_confident_target`
comps it is averaged with the subject's own spend, and it is always
capped at what the building already pays. A category within
`at_market_threshold_pct` of its benchmark reports at 0%. All four knobs
live in `config.yaml`.

The chain is deliberately biased toward understating savings. A figure
that survives a board's scrutiny is worth more than a bigger one that
does not.

## The LLM is cosmetic

`OPENAI_API_KEY` is optional. When set, the model is asked to tighten the
"How We Get There" prose — after every dollar figure is already fixed,
and with an explicit instruction not to introduce or restate a number or
to propose reducing safety scope. Any failure, or no key, leaves the
deterministic per-category mechanism text in place. No figure on the
report ever originates from a model.

## Protected scope

Categories listed under `analysis.no_scope_reduction_categories` in
`config.yaml` — fire/life-safety, elevator safety, and statutory lines —
are marked with an asterisk on the report. The only moves offered on them
are rebidding at identical scope, auditing the billing, or correcting the
record. Testing frequency, inspection scope, and coverage are never
reduced to produce a saving.

## Files

| File                     | Role                                                      |
|--------------------------|-----------------------------------------------------------|
| `main.py`                | FastAPI app, routes, CLI                                  |
| `parser.py`              | Budget file → `ParsedBudget`                              |
| `benchmarks.py`          | GL label → taxonomy, and comparable lookup                |
| `analyzer.py`            | Targets, savings, and the mechanism per line              |
| `fee_engine.py`          | The two fee options and the recommendation                |
| `report_generator.py`    | Branded PDF                                               |
| `storage.py`             | Supabase REST client and analysis persistence             |
| `config_loader.py`       | Cached `config.yaml` access                               |
| `templates/upload_form.html` | Branded upload page and inline results               |
| `supabase_schema.sql`    | Tables, indexes, RLS — run manually                       |
| `skill_definition.md`    | The bot's operating rules and rubric                      |
