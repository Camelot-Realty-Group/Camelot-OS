"""
main.py — Concierge Bot Entry Point
Camelot Property Management Services Corp

Serves the Camelot Document Template Concierge: lists every branded
template in the library, serves its pre-built docx/pdf/fillable-PDF
files, and — for templates with a merge-tag master under masters/ —
auto-fills a Word document from user-supplied answers.

Usage:
    python main.py --serve                # Start API server (default port 8004)
    python main.py --list                 # Print the template catalog to stdout

Author: Camelot OS
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

BOT_DIR = Path(__file__).parent.resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from templates_registry import LIBRARY_DIR, get_template, list_categories, list_templates

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "logs/concierge_bot.log")
Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("concierge_bot.main")


# ---------------------------------------------------------------------------
# API server (FastAPI)
# ---------------------------------------------------------------------------

def run_api_server(host: str = "0.0.0.0", port: int = 8004) -> None:
    """
    Start the Concierge Bot FastAPI server.

    Endpoints:
        GET  /health
        GET  /templates                          — list all templates (optional ?category=)
        GET  /templates/categories                — list category slugs
        GET  /templates/{template_id}              — full metadata incl. fields
        GET  /templates/{template_id}/download      — download a file (?fmt=docx|pdf|fillable)
        POST /templates/{template_id}/generate      — auto-fill a merge-tag master with answers
    """
    try:
        import uvicorn
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse, StreamingResponse
        from pydantic import BaseModel
    except ImportError:
        logger.error("FastAPI/uvicorn/pydantic not installed. Run: pip install -r requirements.txt")
        sys.exit(1)

    import io

    import docgen

    # Audit trail (repo root on path when run from root or via Docker PYTHONPATH)
    try:
        from utils.audit_log import audit_event
    except ImportError:
        sys.path.insert(0, str(BOT_DIR.parent))
        try:
            from utils.audit_log import audit_event
        except ImportError:
            def audit_event(**kwargs):  # degrade gracefully outside repo layout
                logger.info("AUDIT (fallback): %s", kwargs)
                return kwargs

    app = FastAPI(title="Camelot Concierge Bot", version="1.0.0",
                  description="Document template catalog, download, and auto-fill service.")

    class GenerateRequest(BaseModel):
        answers: dict

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "Camelot Concierge Bot", "template_count": len(list_templates())}

    @app.get("/templates")
    async def get_templates(category: str = None):
        return {"templates": list_templates(category=category)}

    @app.get("/templates/categories")
    async def get_categories():
        return {"categories": list_categories()}

    @app.get("/templates/{template_id}")
    async def get_template_detail(template_id: str):
        meta = get_template(template_id)
        if not meta:
            raise HTTPException(status_code=404, detail=f"Unknown template: {template_id}")
        return {"id": template_id, **meta}

    @app.get("/templates/{template_id}/download")
    async def download_template(template_id: str, fmt: str = "pdf"):
        meta = get_template(template_id)
        if not meta:
            raise HTTPException(status_code=404, detail=f"Unknown template: {template_id}")

        filename_map = {
            "docx": meta.get("docx"),
            "pdf": meta.get("pdf"),
            "fillable": meta.get("fillable_pdf"),
        }
        filename = filename_map.get(fmt)
        if not filename:
            raise HTTPException(
                status_code=404,
                detail=f"No '{fmt}' version available for '{template_id}'. "
                       f"Available: {[k for k, v in filename_map.items() if v]}",
            )

        file_path = BOT_DIR / LIBRARY_DIR / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"File missing on disk: {filename}")

        audit_event(bot="concierge", action="download_template",
                    detail={"template_id": template_id, "fmt": fmt, "file": filename})
        return FileResponse(path=str(file_path), filename=filename)

    @app.post("/templates/{template_id}/generate")
    async def generate_template(template_id: str, body: GenerateRequest):
        meta = get_template(template_id)
        if not meta:
            raise HTTPException(status_code=404, detail=f"Unknown template: {template_id}")
        if not meta.get("has_autofill"):
            raise HTTPException(
                status_code=400,
                detail=f"'{template_id}' isn't wired for auto-fill yet. "
                       f"Download the branded docx/pdf or fillable PDF instead via "
                       f"/templates/{template_id}/download.",
            )
        try:
            docx_bytes = docgen.generate_docx(template_id, body.answers)
        except docgen.TemplateNotAutofillable as exc:
            audit_event(bot="concierge", action="generate_document", outcome="denied",
                        detail={"template_id": template_id, "reason": "not_autofillable"})
            raise HTTPException(status_code=400, detail=str(exc))
        except FileNotFoundError as exc:
            audit_event(bot="concierge", action="generate_document", outcome="error",
                        detail={"template_id": template_id, "reason": "master_missing"})
            raise HTTPException(status_code=500, detail=str(exc))

        audit_event(bot="concierge", action="generate_document",
                    detail={"template_id": template_id,
                            "fields_answered": sorted(body.answers.keys())})
        return StreamingResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{template_id}.docx"'},
        )

    logger.info(f"Starting Concierge Bot API server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level=LOG_LEVEL.lower())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Camelot Concierge Bot — document template catalog, download & auto-fill"
    )
    parser.add_argument("--serve", action="store_true", help="Start the API server")
    parser.add_argument("--host", default="0.0.0.0", help="API server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8004, help="API server port (default: 8004)")
    parser.add_argument("--list", action="store_true", help="Print the template catalog as JSON and exit")

    args = parser.parse_args()

    if args.list:
        print(json.dumps(list_templates(), indent=2))
        return

    if args.serve:
        run_api_server(host=args.host, port=args.port)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
