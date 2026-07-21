# Concierge Bot

Document template catalog, download, and guided auto-fill service for
Camelot's branded document library — 23 templates across Property
Management Agreements, Admin & Compliance, Leasing & Sales, Board &
Governance, Reports & Financials, and Project & Property Management.

Not to be confused with **Front Desk Bot** (`frontdesk_bot/`), which
handles tenant/resident communications, maintenance tickets, and
emergency escalation. This bot is document-focused: it helps a team
member (or, via the orchestrator, a natural-language request) find,
download, or fill in a Camelot form.

## What it does

- **Catalog** — every template's title, category, description, and
  which formats exist for it (branded Word doc, branded PDF, and/or a
  genuinely fillable PDF with real form fields).
- **Download** — serves the pre-built file for a template in whichever
  format was requested.
- **Auto-fill** — for templates with a merge-tag master under
  `masters/` (currently just the Work Order Request Form), accepts a
  dict of answers and returns a filled Word document via `docxtpl`.

Everything else in the library can still be downloaded and filled by
hand — either typing into the fillable PDF's real form fields, or
editing the branded Word doc directly. Auto-fill is an incremental
add-on per template, not a requirement for a template to be usable.

## Running

```bash
pip install -r ../requirements.txt
python main.py --serve            # API on :8004
python main.py --list             # dump the template catalog as JSON
```

## API

| Method | Path                              | Description                              |
|--------|------------------------------------|-------------------------------------------|
| GET    | `/health`                          | Health check                              |
| GET    | `/templates?category=`             | List templates, optionally by category    |
| GET    | `/templates/categories`            | List category slugs                       |
| GET    | `/templates/{id}`                  | Full metadata incl. field schema           |
| GET    | `/templates/{id}/download?fmt=`    | `fmt=docx\|pdf\|fillable`                  |
| POST   | `/templates/{id}/generate`         | `{"answers": {...}}` → filled .docx        |

## Adding auto-fill to another template

1. Take the template's branded `.docx` and replace each blank cell with
   a `{{ field_key }}` tag matching a key in that template's `fields`
   list in `templates_registry.py`.
2. Save it under `masters/<template-id>.docx`.
3. Set `"has_autofill": True` and `"master_docx": "<template-id>.docx"`
   on that entry in `templates_registry.py`.

That's the whole loop — `docgen.py` and the `/generate` endpoint are
generic and don't need per-template code.
