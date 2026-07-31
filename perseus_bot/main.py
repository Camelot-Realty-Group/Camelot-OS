"""
main.py — Perseus Entry Point
Camelot Property Management Services Corp

Ingests a building's periodic management/actuals report, compares it to that
building's own budget prorated to the period, independently compares the same
period's annualized run-rate to Camelot's portfolio averages, and prices a
standalone fee proposal for the savings that period identified.

Usage:
    python main.py --serve                # Start API server (default port 8006)
    python main.py --serve --port 8006

Author: Camelot OS
"""

import argparse
import logging
import os
import sys
from pathlib import Path

BOT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = BOT_DIR.parent
# Perseus imports its own modules as `perseus_bot.x`, so the repo root goes on
# the path rather than the bot directory. That keeps generic module names like
# `parser` and `storage` from colliding with another bot's when both are loaded.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perseus_bot.config_loader import load_config, reports_output_dir

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", load_config().get("log_level", "INFO")).upper()
LOG_FILE = os.getenv("LOG_FILE", "logs/perseus_bot.log")
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("perseus_bot.main")

BUILDING_TYPES = ("condo", "co-op", "rental", "mixed-use")
QUARTERS = ("Q1", "Q2", "Q3", "Q4")


# ---------------------------------------------------------------------------
# API server (FastAPI)
# ---------------------------------------------------------------------------

def run_api_server(host: str = "0.0.0.0", port: int = 8006) -> None:
    """
    Start the Perseus FastAPI server.

    Endpoints:
        GET  /health
        GET  /                            — branded upload page
        POST /analyze                     — multipart actuals upload → full analysis
        GET  /reports                     — list stored variance reports
        GET  /reports/{id}                — one full stored report
        GET  /reports/{id}/report.pdf     — regenerate and stream the branded PDF
    """
    try:
        import uvicorn
        from fastapi import FastAPI, File, Form, HTTPException, UploadFile
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    except ImportError:
        logger.error("FastAPI/uvicorn not installed. Run: pip install -r requirements.txt")
        sys.exit(1)

    from perseus_bot import benchmarks as benchmarks_module
    from perseus_bot import fee_engine, parser, report_generator, storage, variance_engine

    try:
        from utils.audit_log import audit_event
    except ImportError:
        def audit_event(**kwargs):  # degrade gracefully outside the repo layout
            logger.info("AUDIT (fallback): %s", kwargs)
            return kwargs

    cfg = load_config()
    reports_table = cfg["supabase"]["reports_table"]
    costbeat_table = cfg["supabase"]["costbeat_analyses_table"]

    app = FastAPI(
        title="Camelot Perseus",
        version="1.0.0",
        description=(
            "Quarterly management-report variance engine — actuals against the "
            "building's own budget and against Camelot's portfolio averages, with a "
            "standalone per-period savings/fee proposal."
        ),
    )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _resolve_baseline(
        parsed: parser.ParsedReport,
        budget_content: bytes,
        budget_filename: str,
        property_name: str,
        address: str,
        cadence: str,
    ) -> tuple[variance_engine.BudgetBaseline, int]:
        """
        Establish the budget baseline and, where the CostBeat record supplies it,
        the building's unit count.

        Order of preference:
          1. A budget file uploaded with this request — an explicit choice by the
             person running the report wins over anything on file.
          2. The most recent CostBeat annual-budget analysis for the building.
          3. A budget column inside the uploaded actuals report itself.

        With none of the three, the request fails. Perseus does not estimate a
        budget it was not given.
        """
        costbeat_units = 0
        costbeat_row = None
        try:
            costbeat_row = storage.find_costbeat_analysis(
                costbeat_table, property_name, address
            )
        except storage.SupabaseUnavailable:
            logger.info("Supabase not configured — skipping CostBeat baseline lookup.")
        except Exception as exc:
            logger.warning("CostBeat baseline lookup failed (%s) — continuing.", exc)

        if costbeat_row:
            costbeat_units = int(costbeat_row.get("unit_count") or 0)

        if budget_content:
            budget = parser.parse_annual_budget(budget_content, budget_filename)
            baseline = variance_engine.baseline_from_annual_budget(budget, cadence)
            if costbeat_row:
                baseline.warnings.append(
                    "A CostBeat annual-budget analysis is also on file for this "
                    "building. The budget uploaded with this report was used instead."
                )
            return baseline, costbeat_units

        if costbeat_row:
            return variance_engine.baseline_from_costbeat(costbeat_row, cadence), costbeat_units

        if parsed.carries_budget:
            return variance_engine.baseline_from_report_columns(parsed, cadence), 0

        raise variance_engine.MissingBaselineError(
            "No budget baseline is available for this building. Perseus found no "
            "CostBeat annual-budget analysis on file, the uploaded report has no "
            "budget column, and no budget file was uploaded with it. Upload the "
            "building's annual budget alongside the actuals and run this again."
        )

    def _analysis_from_record(record: dict) -> variance_engine.VarianceAnalysis:
        """
        Rebuild a VarianceAnalysis from a stored row so the PDF can be regenerated
        without re-uploading the report. The fee proposal is recomputed from it,
        which is deterministic and reproduces the originally issued figures.
        """
        cadence = record.get("cadence") or "quarterly"
        quarter = record.get("quarter") or ""
        year = int(record.get("year") or 0)

        restored = variance_engine.VarianceAnalysis(
            property_name=record.get("property_name") or "",
            address=record.get("address") or "",
            period_label=variance_engine.period_label(quarter, year, cadence),
            quarter=quarter,
            year=year,
            cadence=cadence,
            unit_count=int(record.get("unit_count") or 0),
            building_type=record.get("building_type") or "",
            market=record.get("market") or "",
            baseline=variance_engine.BudgetBaseline(
                source=record.get("budget_source") or variance_engine.BUDGET_SOURCE_UPLOADED,
                cadence=cadence,
                costbeat_analysis_id=record.get("linked_costbeat_analysis_id"),
                origin_label=record.get("uploaded_filename") or "",
            ),
            source_format=record.get("source_format") or "",
            uploaded_filename=record.get("uploaded_filename") or "",
        )

        for item in record.get("line_items") or []:
            restored.lines.append(variance_engine.VarianceLine(
                category=item["category"],
                label=item["label"],
                actual_period=float(item.get("actual_period") or 0.0),
                budget_period=(
                    None if item.get("budget_period") is None
                    else float(item["budget_period"])
                ),
                budget_variance=float(item.get("budget_variance") or 0.0),
                budget_variance_pct=float(item.get("budget_variance_pct") or 0.0),
                budget_flag=item.get("budget_flag") or variance_engine.FLAG_NO_BASELINE,
                annualized_actual=float(item.get("annualized_actual") or 0.0),
                portfolio_average_annual=float(item.get("portfolio_average_annual") or 0.0),
                portfolio_target_annual=float(item.get("portfolio_target_annual") or 0.0),
                portfolio_savings_annual=float(item.get("portfolio_savings_annual") or 0.0),
                portfolio_savings_period=float(item.get("portfolio_savings_period") or 0.0),
                portfolio_gap_pct=float(item.get("portfolio_gap_pct") or 0.0),
                comp_count=int(item.get("comp_count") or 0),
                evidence=item.get("evidence") or "",
                recommendation=item.get("recommendation") or "",
                at_portfolio_average=bool(item.get("at_portfolio_average")),
                addressed=bool(item.get("addressed", True)),
                scope_protected=bool(item.get("scope_protected")),
                seasonal=bool(item.get("seasonal")),
                source_labels=item.get("source_labels") or [],
            ))

        restored.benchmark_coverage = sum(1 for ln in restored.lines if ln.comp_count > 0)
        return restored

    def _render(analysis, proposal, report_id) -> str:
        return report_generator.generate_report(
            analysis, proposal, reports_output_dir(), report_id=report_id
        )

    # ── Routes ────────────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "Camelot Perseus",
            "supabase_configured": bool(
                os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY")
            ),
            "llm_configured": bool(os.getenv("OPENAI_API_KEY")),
        }

    @app.get("/", response_class=HTMLResponse)
    async def upload_page():
        page = BOT_DIR / "templates" / "upload_form.html"
        if not page.exists():
            raise HTTPException(
                status_code=500, detail="upload_form.html is missing from templates/."
            )
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @app.post("/analyze")
    async def analyze_period(
        actual_file: UploadFile = File(
            ..., description="Period actuals or MDS report (.xlsx, .csv, .pdf)"
        ),
        property_name: str = Form(...),
        address: str = Form(""),
        quarter: str = Form("Q1"),
        year: int = Form(...),
        cadence: str = Form("quarterly"),
        unit_count: int = Form(0),
        building_type: str = Form(""),
        market: str = Form(""),
        notes: str = Form(""),
        created_by: str = Form("api"),
        budget_file: UploadFile = File(
            None, description="Optional annual budget, used when none is on file"
        ),
    ):
        """Run the full pipeline: parse → baseline → benchmark → compare → price → persist → render."""
        quarter = (quarter or "").strip().upper()
        if cadence != "annual" and quarter not in QUARTERS:
            raise HTTPException(
                status_code=400, detail=f"quarter must be one of {list(QUARTERS)}."
            )
        if year < 2000 or year > 2100:
            raise HTTPException(status_code=400, detail="year must be a four-digit calendar year.")
        if building_type and building_type not in BUILDING_TYPES:
            raise HTTPException(
                status_code=400, detail=f"building_type must be one of {list(BUILDING_TYPES)}."
            )
        try:
            from perseus_bot.config_loader import periods_per_year
            periods_per_year(cadence)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        content = await actual_file.read()
        try:
            parsed = parser.parse_report(content, actual_file.filename or "actuals")
        except parser.ReportParseError as exc:
            audit_event(bot="perseus", action="analyze_period", outcome="error",
                        detail={"property_name": property_name, "reason": "parse_failed"})
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        budget_content = b""
        budget_filename = ""
        if budget_file is not None and budget_file.filename:
            budget_content = await budget_file.read()
            budget_filename = budget_file.filename

        try:
            baseline, costbeat_units = _resolve_baseline(
                parsed, budget_content, budget_filename, property_name, address, cadence
            )
        except variance_engine.MissingBaselineError as exc:
            audit_event(bot="perseus", action="analyze_period", outcome="error",
                        detail={"property_name": property_name, "reason": "no_baseline"})
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except parser.ReportParseError as exc:
            raise HTTPException(
                status_code=400, detail=f"Budget baseline file: {exc}"
            ) from exc

        units = unit_count or costbeat_units
        comps: dict = {}
        if units > 0:
            try:
                comps = benchmarks_module.fetch_benchmarks(
                    units, building_type or None, market or None
                )
            except storage.SupabaseUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        else:
            baseline.warnings.append(
                "No unit count was supplied and none was found on file, so the "
                "portfolio comparison could not run. The budget variance below "
                "stands on its own."
            )

        analysis = variance_engine.analyze(
            parsed, baseline, comps,
            property_name=property_name,
            address=address,
            unit_count=units,
            quarter=quarter,
            year=year,
            cadence=cadence,
            building_type=building_type,
            market=market,
        )
        proposal = fee_engine.build_proposal(analysis)

        record = {
            "property_name": property_name,
            "address": address,
            "quarter": quarter,
            "year": year,
            "cadence": cadence,
            "unit_count": units,
            "building_type": building_type,
            "market": market,
            "budget_source": baseline.source,
            "linked_costbeat_analysis_id": baseline.costbeat_analysis_id,
            "uploaded_filename": actual_file.filename,
            "source_format": parsed.source_format,
            "total_budget_period": round(analysis.total_budget_period, 2),
            "total_actual_period": round(analysis.total_actual_period, 2),
            "budget_variance": round(analysis.budget_variance, 2),
            "budget_variance_pct": round(analysis.budget_variance_pct, 4),
            "portfolio_savings_opportunity": round(analysis.portfolio_savings_annual, 2),
            "portfolio_savings_period": round(analysis.portfolio_savings_period, 2),
            "one_time_fee": round(proposal.one_time.camelot_amount, 2),
            "mgmt_fee_uplift_monthly": round(proposal.uplift.monthly_amount, 2),
            "mgmt_fee_uplift_annual": round(proposal.uplift.camelot_amount, 2),
            "recommended_fee_model": proposal.recommended_model,
            "line_items": [ln.as_dict() for ln in analysis.lines],
            "flagged_categories": [ln.as_dict() for ln in analysis.flagged_lines],
            "notes": notes,
            "status": "needs_review" if analysis.needs_review else "draft",
            "created_by": created_by,
        }

        report_id = None
        try:
            saved = storage.save_report(reports_table, record)
            report_id = saved.get("id")
        except Exception as exc:
            # A storage outage must not throw away a completed analysis — return
            # it with the failure surfaced so the team can retry the save.
            logger.exception("Could not persist variance report: %s", exc)
            analysis.parse_warnings.append(f"Report was not saved to Supabase: {exc}")

        pdf_path = _render(analysis, proposal, report_id)

        audit_event(
            bot="perseus", action="analyze_period",
            detail={
                "report_id": report_id,
                "property_name": property_name,
                "period": analysis.period_label,
                "unit_count": units,
                "uploaded_filename": actual_file.filename,
                "budget_source": baseline.source,
                "budget_variance": round(analysis.budget_variance, 2),
                "portfolio_savings_annual": round(analysis.portfolio_savings_annual, 2),
                "recommended_fee_model": proposal.recommended_model,
            },
        )

        return JSONResponse({
            "status": "success",
            "report_id": report_id,
            "source_format": parsed.source_format,
            "budget_source": baseline.source,
            "budget_source_description": baseline.describe(),
            "analysis": analysis.as_dict(),
            "fee_proposal": proposal.as_dict(),
            "summary": report_generator.summary_paragraphs(analysis, proposal),
            "next_steps": report_generator.next_steps(analysis, proposal),
            "pdf_path": pdf_path,
            "report_url": f"/reports/{report_id}/report.pdf" if report_id else None,
        })

    @app.get("/reports")
    async def get_reports(limit: int = 50, property_name: str = ""):
        try:
            return {
                "reports": storage.list_reports(
                    reports_table, limit=limit, property_name=property_name
                )
            }
        except storage.SupabaseUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/reports/{report_id}")
    async def get_report_detail(report_id: str):
        try:
            record = storage.get_report(reports_table, report_id)
        except storage.SupabaseUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not record:
            raise HTTPException(status_code=404, detail=f"Unknown report: {report_id}")
        return record

    @app.get("/reports/{report_id}/report.pdf")
    async def get_report_pdf(report_id: str):
        try:
            record = storage.get_report(reports_table, report_id)
        except storage.SupabaseUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not record:
            raise HTTPException(status_code=404, detail=f"Unknown report: {report_id}")

        restored = _analysis_from_record(record)
        proposal = fee_engine.build_proposal(restored)
        pdf_path = _render(restored, proposal, report_id)

        audit_event(bot="perseus", action="download_report", detail={"report_id": report_id})
        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=os.path.basename(pdf_path),
        )

    logger.info("Starting Perseus API server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level=LOG_LEVEL.lower())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    arg_parser = argparse.ArgumentParser(
        description=(
            "Camelot Perseus — periodic management-report variance analysis and "
            "standalone per-period fee proposal"
        )
    )
    arg_parser.add_argument("--serve", action="store_true", help="Start the API server")
    arg_parser.add_argument("--host", default="0.0.0.0", help="API server host (default: 0.0.0.0)")
    arg_parser.add_argument("--port", type=int, default=8006, help="API server port (default: 8006)")

    args = arg_parser.parse_args()

    if args.serve:
        run_api_server(host=args.host, port=args.port)
    else:
        arg_parser.print_help()


if __name__ == "__main__":
    main()
