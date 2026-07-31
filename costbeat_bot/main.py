"""
main.py — CostBeat Bot Entry Point
Camelot Property Management Services Corp

Ingests a building's operating budget, compares every expense line against
Camelot's own portfolio comparables, computes addressable savings with an
evidence-backed recommendation per line, and prices two ways for Camelot to
capture a share of the value created.

Usage:
    python main.py --serve                # Start API server (default port 8005)
    python main.py --serve --port 8005

Author: Camelot OS
"""

import argparse
import logging
import os
import sys
from pathlib import Path

BOT_DIR = Path(__file__).parent.resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from config_loader import load_config, reports_output_dir

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", load_config().get("log_level", "INFO")).upper()
LOG_FILE = os.getenv("LOG_FILE", "logs/costbeat_bot.log")
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("costbeat_bot.main")

BUILDING_TYPES = ("condo", "co-op", "rental", "mixed-use")


# ---------------------------------------------------------------------------
# API server (FastAPI)
# ---------------------------------------------------------------------------

def run_api_server(host: str = "0.0.0.0", port: int = 8005) -> None:
    """
    Start the CostBeat Bot FastAPI server.

    Endpoints:
        GET  /health
        GET  /                              — branded upload/analysis page
        POST /analyze                       — multipart budget upload → full analysis
        GET  /analyses                      — list stored analyses
        GET  /analyses/{id}                 — one full stored analysis
        GET  /analyses/{id}/report.pdf      — regenerate and stream the branded PDF
    """
    try:
        import uvicorn
        from fastapi import FastAPI, File, Form, HTTPException, UploadFile
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    except ImportError:
        logger.error("FastAPI/uvicorn not installed. Run: pip install -r requirements.txt")
        sys.exit(1)

    import analyzer
    import benchmarks as benchmarks_module
    import fee_engine
    import parser as budget_parser
    import report_generator
    import storage

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

    cfg = load_config()
    analyses_table = cfg["supabase"]["analyses_table"]

    app = FastAPI(
        title="Camelot CostBeat Bot",
        version="1.0.0",
        description=(
            "Operating-budget cost-beat analysis against Camelot portfolio "
            "comparables, with a savings-capture fee proposal."
        ),
    )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _analysis_from_record(record: dict) -> analyzer.CostBeatAnalysis:
        """
        Rebuild a CostBeatAnalysis from a stored row so the PDF can be
        regenerated without re-uploading the budget. The fee proposal is
        recomputed from it, which is deterministic and reproduces the
        originally issued figures.
        """
        restored = analyzer.CostBeatAnalysis(
            property_name=record.get("property_name") or "",
            address=record.get("address") or "",
            unit_count=int(record.get("unit_count") or 0),
            building_type=record.get("building_type") or "",
            market=record.get("market") or "",
        )
        for item in record.get("line_items") or []:
            restored.lines.append(analyzer.CostBeatLine(
                category=item["category"],
                label=item["label"],
                current_budget=float(item["current_budget"]),
                target=float(item["target"]),
                savings=float(item["savings"]),
                savings_pct=float(item["savings_pct"]),
                evidence=item["evidence"],
                recommendation=item["recommendation"],
                comp_count=int(item.get("comp_count", 0)),
                at_market=bool(item.get("at_market")),
                addressed=bool(item.get("addressed", True)),
                scope_protected=bool(item.get("scope_protected")),
                source_labels=item.get("source_labels") or [],
            ))
        restored.benchmark_coverage = sum(
            1 for ln in restored.lines if ln.comp_count > 0
        )
        return restored

    def _render(analysis, proposal, analysis_id) -> str:
        return report_generator.generate_report(
            analysis, proposal, reports_output_dir(), analysis_id=analysis_id
        )

    # ── Routes ────────────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "Camelot CostBeat Bot",
            "supabase_configured": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY")),
            "llm_configured": bool(os.getenv("OPENAI_API_KEY")),
        }

    @app.get("/", response_class=HTMLResponse)
    async def upload_page():
        page = BOT_DIR / "templates" / "upload_form.html"
        if not page.exists():
            raise HTTPException(status_code=500, detail="upload_form.html is missing from templates/.")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @app.post("/analyze")
    async def analyze_budget(
        budget_file: UploadFile = File(..., description="Operating budget (.xlsx, .csv, .pdf)"),
        property_name: str = Form(...),
        address: str = Form(""),
        unit_count: int = Form(...),
        building_type: str = Form(""),
        market: str = Form(""),
        notes: str = Form(""),
        created_by: str = Form("api"),
    ):
        """Run the full pipeline: parse → benchmark → analyze → price → persist → render."""
        if unit_count <= 0:
            raise HTTPException(status_code=400, detail="unit_count must be greater than zero.")
        if building_type and building_type not in BUILDING_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"building_type must be one of {list(BUILDING_TYPES)}.",
            )

        content = await budget_file.read()
        try:
            parsed = budget_parser.parse_budget(content, budget_file.filename or "budget")
        except budget_parser.BudgetParseError as exc:
            audit_event(bot="costbeat", action="analyze_budget", outcome="error",
                        detail={"property_name": property_name, "reason": "parse_failed"})
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            comps = benchmarks_module.fetch_benchmarks(unit_count, building_type or None, market or None)
        except storage.SupabaseUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        analysis = analyzer.analyze(
            parsed, comps,
            property_name=property_name,
            address=address,
            unit_count=unit_count,
            building_type=building_type,
            market=market,
        )
        proposal = fee_engine.build_proposal(analysis)

        record = {
            **{k: v for k, v in analysis.as_dict().items()
               if k in {"property_name", "address", "unit_count", "building_type", "market",
                        "total_budget", "total_target", "total_savings", "savings_pct"}},
            "uploaded_filename": budget_file.filename,
            "one_time_fee": round(proposal.one_time.camelot_year1, 2),
            "mgmt_fee_uplift_monthly": round(proposal.uplift.monthly_amount, 2),
            "mgmt_fee_uplift_annual": round(proposal.uplift.camelot_annual_ongoing, 2),
            "recommended_fee_model": proposal.recommended_model,
            "line_items": [ln.as_dict() for ln in analysis.lines],
            "not_addressed": [ln.as_dict() for ln in analysis.not_addressed_lines],
            "notes": notes,
            "status": "needs_review" if analysis.needs_review else "draft",
            "created_by": created_by,
        }

        analysis_id = None
        try:
            saved = storage.save_analysis(analyses_table, record)
            analysis_id = saved.get("id")
        except Exception as exc:
            # A storage outage must not throw away a completed analysis — return
            # it with the failure surfaced so the team can retry the save.
            logger.exception("Could not persist analysis: %s", exc)
            analysis.parse_warnings.append(f"Analysis was not saved to Supabase: {exc}")

        pdf_path = _render(analysis, proposal, analysis_id)

        audit_event(
            bot="costbeat", action="analyze_budget",
            detail={
                "analysis_id": analysis_id,
                "property_name": property_name,
                "unit_count": unit_count,
                "uploaded_filename": budget_file.filename,
                "total_budget": round(analysis.total_budget, 2),
                "total_savings": round(analysis.total_savings, 2),
                "recommended_fee_model": proposal.recommended_model,
            },
        )

        return JSONResponse({
            "status": "success",
            "analysis_id": analysis_id,
            "source_format": parsed.source_format,
            "analysis": analysis.as_dict(),
            "fee_proposal": proposal.as_dict(),
            "next_steps": report_generator.next_steps(analysis, proposal),
            "pdf_path": pdf_path,
            "report_url": f"/analyses/{analysis_id}/report.pdf" if analysis_id else None,
        })

    @app.get("/analyses")
    async def get_analyses(limit: int = 50):
        try:
            return {"analyses": storage.list_analyses(analyses_table, limit=limit)}
        except storage.SupabaseUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/analyses/{analysis_id}")
    async def get_analysis_detail(analysis_id: str):
        try:
            record = storage.get_analysis(analyses_table, analysis_id)
        except storage.SupabaseUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not record:
            raise HTTPException(status_code=404, detail=f"Unknown analysis: {analysis_id}")
        return record

    @app.get("/analyses/{analysis_id}/report.pdf")
    async def get_analysis_pdf(analysis_id: str):
        try:
            record = storage.get_analysis(analyses_table, analysis_id)
        except storage.SupabaseUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not record:
            raise HTTPException(status_code=404, detail=f"Unknown analysis: {analysis_id}")

        restored = _analysis_from_record(record)
        proposal = fee_engine.build_proposal(restored)
        pdf_path = _render(restored, proposal, analysis_id)

        audit_event(bot="costbeat", action="download_report",
                    detail={"analysis_id": analysis_id})
        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=os.path.basename(pdf_path),
        )

    logger.info("Starting CostBeat Bot API server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level=LOG_LEVEL.lower())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Camelot CostBeat Bot — budget cost-beat analysis & savings-capture fee proposal"
    )
    parser.add_argument("--serve", action="store_true", help="Start the API server")
    parser.add_argument("--host", default="0.0.0.0", help="API server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8005, help="API server port (default: 8005)")

    args = parser.parse_args()

    if args.serve:
        run_api_server(host=args.host, port=args.port)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
