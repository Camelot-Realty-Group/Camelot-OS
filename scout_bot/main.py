"""
main.py
--------
Scout Bot — Master Orchestrator
Camelot Property Management Services Corp.

Execution flow:
  1. Load config from config.yaml
  2. Configure rotating file + stream logging
  3. Run all collectors in parallel (ThreadPoolExecutor)
  4. Deduplicate and score all collected leads
  5. Enrich top-N leads with Apollo.io + Prospeo
  6. Push enriched leads to HubSpot CRM (via Node.js subprocess)
  7. Generate PDF daily digest + leads CSV + enriched contacts CSV
  8. Send daily email report to Camelot team
  9. Write run summary to logs

Usage:
  python main.py                  # Full run
  python main.py --dry-run        # Collect + process only (no HubSpot, no email)
  python main.py --no-enrichment  # Skip enrichment step
  python main.py --no-hubspot     # Skip HubSpot push
  python main.py --no-email       # Skip email send
  python main.py --serve                # Start API server (default port 8007)
  python main.py --serve --port 8007     # Lead Hunt / Merlin Inbox HTTP endpoints

API mode (--serve) exposes two additional, independent features used by the
Supabase pg_cron jobs `camelot-daily-lead-hunt` and `merlin-inbox-poll` (see
lead_hunt.py and merlin_inbox.py):
  POST /lead-hunt/run       — NYC Open Data scan -> scored candidates -> scout_buildings
  POST /merlin/poll-inbox   — poll outreach mailbox for replies -> merlin_inbound_messages
  GET  /health              — service + dependency configuration status
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# ---------------------------------------------------------------------------
# Resolve project root and configure sys.path
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Imports — after path is set
# ---------------------------------------------------------------------------

from collectors import bizbuysell, bizquest, loopnet, nyc_rfps, jobs_signals, hpd_buildings
from enrichment.enricher import enrich_leads_batch
from reports.pdf_generator import generate_lead_report
from reports.csv_exporter import export_leads_csv, export_enriched_csv
from utils.filters import process_leads
from utils.emailer import send_daily_report, send_alert

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_DIR = PROJECT_ROOT / "logs"
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

HUBSPOT_SCRIPT = PROJECT_ROOT / "integrations" / "hubspot_client.js"

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """Load and return the central config.yaml.

    Args:
        path: Path to config.yaml.

    Returns:
        Config dict.

    Raises:
        FileNotFoundError: If config file does not exist.
        yaml.YAMLError: If config file is malformed.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            "Copy config.yaml.example or ensure config.yaml is present."
        )
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)
    return cfg or {}


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def configure_logging(log_level: str = "INFO") -> None:
    """Configure root logger with rotating file handler + console handler.

    Log file: ``logs/scout_YYYY-MM-DD.log``

    Args:
        log_level: Logging level string (e.g. ``"INFO"``, ``"DEBUG"``).
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"scout_{date.today().isoformat()}.log"

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove existing handlers to avoid duplicate output on re-runs
    root.handlers.clear()

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(numeric_level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(numeric_level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


logger = logging.getLogger("scout_bot.main")


# ---------------------------------------------------------------------------
# Collector runner
# ---------------------------------------------------------------------------

def run_collectors(
    regions: List[str],
) -> List[Dict[str, Any]]:
    """Run all collectors in parallel using ThreadPoolExecutor.

    Each collector's ``collect()`` function is submitted as a separate thread.
    Results are merged and returned in a single flat list.

    Args:
        regions: List of target region codes (e.g. ``["NY", "FL", "CT", "NJ"]``).

    Returns:
        Combined list of raw lead dicts from all collectors.
    """
    # Map collector name → callable
    collector_tasks = {
        "BizBuySell":   lambda: bizbuysell.collect(regions=regions),
        "BizQuest":     lambda: bizquest.collect(regions=regions),
        "LoopNet":      lambda: loopnet.collect(),
        "NYC RFPs":     lambda: nyc_rfps.collect(),
        "Job Signals":  lambda: jobs_signals.collect(),
        "HPD Buildings": lambda: hpd_buildings.collect(),
    }

    all_leads: List[Dict[str, Any]] = []
    collector_stats: Dict[str, int] = {}

    logger.info("Starting %d collectors in parallel …", len(collector_tasks))

    with ThreadPoolExecutor(max_workers=len(collector_tasks), thread_name_prefix="collector") as executor:
        future_map = {
            executor.submit(fn): name
            for name, fn in collector_tasks.items()
        }

        for future in as_completed(future_map):
            name = future_map[future]
            try:
                leads = future.result()
                count = len(leads) if leads else 0
                collector_stats[name] = count
                all_leads.extend(leads or [])
                logger.info("✓ %-18s → %3d leads", name, count)
            except Exception as exc:  # noqa: BLE001
                logger.error("✗ %-18s → ERROR: %s", name, exc)
                collector_stats[name] = 0

    logger.info(
        "All collectors done. Raw leads: %d  |  Breakdown: %s",
        len(all_leads),
        " | ".join(f"{k}:{v}" for k, v in collector_stats.items()),
    )
    return all_leads


# ---------------------------------------------------------------------------
# HubSpot push (via Node.js subprocess)
# ---------------------------------------------------------------------------

def push_to_hubspot(leads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Push enriched leads to HubSpot CRM by invoking the Node.js client.

    Serialises the leads list to JSON, calls
    ``node integrations/hubspot_client.js '<json>'``,
    and parses the returned JSON result.

    Args:
        leads: List of enriched Scout lead dicts (with ``contacts`` populated).

    Returns:
        Result dict ``{"success": bool, "results": [...], "error": str|None}``.
    """
    if not HUBSPOT_SCRIPT.exists():
        logger.error("HubSpot client script not found: %s", HUBSPOT_SCRIPT)
        return {"success": False, "error": "hubspot_client.js not found", "results": []}

    # Serialise leads — convert date objects to ISO strings
    def _serialise(obj: Any) -> Any:
        if isinstance(obj, date):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    payload = json.dumps({"leads": leads}, default=_serialise)

    logger.info(
        "Pushing %d leads to HubSpot via Node.js …", len(leads)
    )

    try:
        result = subprocess.run(
            ["node", str(HUBSPOT_SCRIPT), payload],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(PROJECT_ROOT),
        )

        if result.returncode != 0:
            logger.error(
                "HubSpot push failed (exit %d):\nSTDERR: %s",
                result.returncode,
                result.stderr[:1000],
            )
            return {
                "success": False,
                "error": result.stderr[:500],
                "results": [],
            }

        output = result.stdout.strip()
        if not output:
            logger.warning("HubSpot push returned empty output.")
            return {"success": True, "results": []}

        try:
            parsed = json.loads(output)
            result_count = len(parsed.get("results", []))
            logger.info(
                "HubSpot push complete. %d leads processed.", result_count
            )
            return parsed
        except json.JSONDecodeError:
            logger.warning(
                "HubSpot script output was not valid JSON:\n%s", output[:300]
            )
            return {"success": True, "results": [], "raw_output": output[:300]}

    except subprocess.TimeoutExpired:
        logger.error("HubSpot push timed out (>300s).")
        return {"success": False, "error": "Timeout", "results": []}
    except FileNotFoundError:
        logger.error(
            "Node.js not found. Ensure Node.js >= 16 is installed and in PATH."
        )
        return {"success": False, "error": "node not found", "results": []}
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error during HubSpot push: %s", exc)
        return {"success": False, "error": str(exc), "results": []}


# ---------------------------------------------------------------------------
# Run summary logger
# ---------------------------------------------------------------------------

def _log_run_summary(
    raw_count: int,
    filtered_count: int,
    enriched_count: int,
    hubspot_result: Optional[Dict[str, Any]],
    pdf_size: int,
    csv_size: int,
    email_sent: bool,
    elapsed: float,
) -> None:
    """Log a structured run summary to the logger.

    Args:
        raw_count: Total raw leads before dedup/filter.
        filtered_count: Leads after dedup + score filter.
        enriched_count: Number of leads enriched.
        hubspot_result: Result from HubSpot push.
        pdf_size: PDF report size in bytes.
        csv_size: CSV export size in bytes.
        email_sent: Whether the email was sent successfully.
        elapsed: Total run time in seconds.
    """
    hs_status = "SKIPPED"
    if hubspot_result is not None:
        hs_status = "OK" if hubspot_result.get("success") else "FAILED"
        if hubspot_result.get("error"):
            hs_status += f" ({hubspot_result['error'][:80]})"

    lines = [
        "=" * 62,
        "  SCOUT BOT RUN SUMMARY",
        f"  Date:            {date.today().isoformat()}",
        f"  Elapsed:         {elapsed:.1f}s",
        "  -" * 31,
        f"  Raw leads:       {raw_count}",
        f"  After filter:    {filtered_count}",
        f"  Enriched:        {enriched_count}",
        f"  HubSpot:         {hs_status}",
        f"  PDF:             {pdf_size:,} bytes",
        f"  CSV:             {csv_size:,} bytes",
        f"  Email sent:      {'YES' if email_sent else 'NO'}",
        "=" * 62,
    ]
    for line in lines:
        logger.info(line)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Execute a full Scout Bot run.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code: 0 on success, 1 on critical failure.
    """
    run_start = datetime.now()

    # --- Load config ---
    try:
        cfg = load_config()
    except Exception as exc:
        # Can't log to file yet (logging not configured); print and exit
        print(f"[FATAL] Failed to load config: {exc}", file=sys.stderr)
        return 1

    # --- Configure logging ---
    configure_logging(cfg.get("log_level", "INFO"))
    logger.info("Scout Bot starting — %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Config: %s", CONFIG_PATH)
    logger.info("Dry run: %s | Enrichment: %s | HubSpot: %s | Email: %s",
                args.dry_run, not args.no_enrichment,
                not args.no_hubspot and not args.dry_run,
                not args.no_email and not args.dry_run)

    regions: List[str] = cfg.get("regions", ["NY", "FL", "CT", "NJ"])
    min_score: int = cfg.get("min_lead_score", 40)
    max_enrichments: int = cfg.get("max_enrichments_per_run", 20)
    recipients: List[str] = cfg.get("report_recipients", [])

    # -------------------------------------------------------------------------
    # Step 1: Collect
    # -------------------------------------------------------------------------
    logger.info("STEP 1: Running collectors …")
    try:
        raw_leads = run_collectors(regions)
    except Exception as exc:
        logger.critical("Collector stage failed: %s", exc)
        return 1

    if not raw_leads:
        logger.warning("No leads collected from any source. Exiting.")
        send_alert(
            subject=f"Scout Bot — No Leads Collected — {date.today()}",
            body="The Scout Bot run on {date.today()} returned zero leads from all collectors.",
            to_emails=recipients or None,
        )
        return 0

    # -------------------------------------------------------------------------
    # Step 2: Deduplicate, tag, score, filter
    # -------------------------------------------------------------------------
    logger.info("STEP 2: Processing leads (tag → dedup → score → filter) …")
    try:
        filtered_leads = process_leads(raw_leads, min_score=min_score)
    except Exception as exc:
        logger.error("Lead processing failed: %s", exc)
        filtered_leads = raw_leads  # fall through with raw data

    logger.info("Filtered: %d → %d leads (min_score=%d)",
                len(raw_leads), len(filtered_leads), min_score)

    # -------------------------------------------------------------------------
    # Step 3: Enrich top leads
    # -------------------------------------------------------------------------
    enriched_leads: List[Dict[str, Any]] = filtered_leads

    if not args.no_enrichment and not args.dry_run:
        logger.info("STEP 3: Enriching top %d leads …", max_enrichments)
        try:
            enriched_leads = enrich_leads_batch(filtered_leads, max_enrichments=max_enrichments)
        except Exception as exc:
            logger.error("Enrichment stage failed: %s", exc)
            enriched_leads = filtered_leads
    else:
        logger.info("STEP 3: Enrichment SKIPPED.")

    enriched_count = sum(1 for l in enriched_leads if l.get("contacts"))

    # -------------------------------------------------------------------------
    # Step 4: Push to HubSpot
    # -------------------------------------------------------------------------
    hubspot_result: Optional[Dict[str, Any]] = None

    if not args.no_hubspot and not args.dry_run:
        logger.info("STEP 4: Pushing to HubSpot CRM …")
        leads_with_contacts = [l for l in enriched_leads if l.get("contacts")]
        if leads_with_contacts:
            try:
                hubspot_result = push_to_hubspot(leads_with_contacts)
            except Exception as exc:
                logger.error("HubSpot push stage failed: %s", exc)
                hubspot_result = {"success": False, "error": str(exc), "results": []}
        else:
            logger.info("No leads with contacts to push to HubSpot.")
            hubspot_result = {"success": True, "results": [], "note": "No contacts to push"}
    else:
        logger.info("STEP 4: HubSpot push SKIPPED.")

    # -------------------------------------------------------------------------
    # Step 5: Generate reports
    # -------------------------------------------------------------------------
    logger.info("STEP 5: Generating PDF and CSV reports …")

    pdf_bytes: Optional[bytes] = None
    csv_bytes: Optional[bytes] = None
    enriched_csv_bytes: Optional[bytes] = None

    try:
        pdf_bytes = generate_lead_report(enriched_leads)
        logger.info("PDF generated: %d bytes", len(pdf_bytes))
    except Exception as exc:
        logger.error("PDF generation failed: %s", exc)

    try:
        csv_bytes = export_leads_csv(enriched_leads)
        logger.info("Leads CSV: %d bytes", len(csv_bytes))
    except Exception as exc:
        logger.error("CSV export failed: %s", exc)

    try:
        enriched_csv_bytes = export_enriched_csv(enriched_leads)
        logger.info("Enriched CSV: %d bytes", len(enriched_csv_bytes))
    except Exception as exc:
        logger.error("Enriched CSV export failed: %s", exc)

    # Save reports to disk
    reports_dir = PROJECT_ROOT / "reports" / "output"
    reports_dir.mkdir(parents=True, exist_ok=True)

    today_str = date.today().isoformat()

    if pdf_bytes:
        pdf_path = reports_dir / f"Scout_Daily_Report_{today_str}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        logger.info("PDF saved: %s", pdf_path)

    if csv_bytes:
        csv_path = reports_dir / f"Scout_Leads_{today_str}.csv"
        csv_path.write_bytes(csv_bytes)
        logger.info("Leads CSV saved: %s", csv_path)

    if enriched_csv_bytes:
        enc_path = reports_dir / f"Scout_Leads_Enriched_{today_str}.csv"
        enc_path.write_bytes(enriched_csv_bytes)
        logger.info("Enriched CSV saved: %s", enc_path)

    # -------------------------------------------------------------------------
    # Step 6: Send email
    # -------------------------------------------------------------------------
    email_sent = False

    if not args.no_email and not args.dry_run:
        logger.info("STEP 6: Sending daily email report …")
        try:
            email_sent = send_daily_report(
                to_emails=recipients or None,
                leads_df=enriched_leads,
                pdf_bytes=pdf_bytes,
                csv_bytes=csv_bytes,
                enriched_csv_bytes=enriched_csv_bytes,
            )
        except Exception as exc:
            logger.error("Email send failed: %s", exc)
    else:
        logger.info("STEP 6: Email send SKIPPED.")

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    elapsed = (datetime.now() - run_start).total_seconds()
    _log_run_summary(
        raw_count=len(raw_leads),
        filtered_count=len(filtered_leads),
        enriched_count=enriched_count,
        hubspot_result=hubspot_result,
        pdf_size=len(pdf_bytes) if pdf_bytes else 0,
        csv_size=len(csv_bytes) if csv_bytes else 0,
        email_sent=email_sent,
        elapsed=elapsed,
    )

    return 0


# ---------------------------------------------------------------------------
# API server (--serve) — Lead Hunt + Merlin Inbox endpoints
# ---------------------------------------------------------------------------

def run_api_server(host: str = "0.0.0.0", port: int = 8007) -> None:
    """
    Start the Scout Bot FastAPI server.

    This exposes the two features that rebuild the previously-nonfunctional
    Supabase pg_cron jobs `camelot-daily-lead-hunt` and `merlin-inbox-poll`
    (both were POSTing to unconfigured `app.settings.*_function_url`
    values with no endpoint behind them):

        GET  /health              — service + dependency configuration status
        POST /lead-hunt/run       — run (or return today's already-run) lead hunt
        POST /merlin/poll-inbox   — poll the outreach mailbox for replies

    It does NOT touch the existing CLI batch pipeline (collectors ->
    enrichment -> HubSpot -> PDF/CSV -> email) defined above in this file —
    that keeps working exactly as before via `python main.py`.
    """
    try:
        import uvicorn
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import JSONResponse
    except ImportError:
        logger.error("FastAPI/uvicorn not installed. Run: pip install -r requirements.txt")
        sys.exit(1)

    _config = load_config()

    # lead_hunt.py and merlin_inbox.py each resolve utils/audit_log.py
    # themselves (scout_bot/utils/ shadows the repo-root utils/ package by
    # name — see the NOTE at the top of each module), so no audit_log
    # import or sys.path fix-up is needed here.
    import lead_hunt
    import merlin_inbox
    import storage

    app = FastAPI(
        title="Camelot Scout Bot",
        version="1.0.0",
        description=(
            "Lead generation and property intelligence. Daily NYC Open Data lead "
            "hunt and outreach-reply inbox polling for Camelot Property Management "
            "Services Corp."
        ),
    )

    def _socrata_configured() -> bool:
        # Socrata works anonymously; a token only raises the rate limit.
        return True

    def _merlin_inbox_configured() -> bool:
        return bool(
            os.getenv("MERLIN_IMAP_HOST") and os.getenv("MERLIN_IMAP_USER") and os.getenv("MERLIN_IMAP_PASSWORD")
        )

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "Camelot Scout Bot",
            "supabase_configured": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY")),
            "socrata_app_token_configured": bool(os.getenv("SOCRATA_APP_TOKEN")),
            "merlin_inbox_configured": _merlin_inbox_configured(),
            "hubspot_configured": bool(os.getenv("HUBSPOT_ACCESS_TOKEN")),
        }

    _lh_cfg = _config.get("lead_hunt", {}) or {}

    @app.post("/lead-hunt/run")
    async def lead_hunt_run(
        triggered_by: str = "cron",
        boroughs: Optional[List[str]] = None,
        recent_days: Optional[int] = None,
        min_score: Optional[int] = None,
        dry_run: bool = False,
    ):
        """Run the daily lead hunt. Idempotent per calendar day per
        `triggered_by` — a second call the same day with the same
        `triggered_by` returns the existing scan rather than re-running.

        Any parameter left unset falls back to the `lead_hunt` section of
        config.yaml, so cron can POST with no body and still get the
        configured boroughs/thresholds.
        """
        try:
            result = lead_hunt.run_lead_hunt(
                triggered_by=triggered_by,
                boroughs=boroughs if boroughs is not None else _lh_cfg.get("boroughs"),
                recent_days=recent_days if recent_days is not None else _lh_cfg.get("recent_days", 90),
                min_score=min_score if min_score is not None else _lh_cfg.get("min_lead_score", 40),
                dry_run=dry_run,
            )
            return JSONResponse(result)
        except storage.SupabaseUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except lead_hunt.LeadHuntError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.post("/merlin/poll-inbox")
    async def merlin_poll_inbox(since: Optional[str] = None, dry_run: bool = False):
        """Poll the configured outreach mailbox for new replies, match them
        to scout_buildings / scout_outreach_log, and log them idempotently
        to merlin_inbound_messages.
        """
        try:
            result = merlin_inbox.poll_inbox(since=since, dry_run=dry_run)
            return JSONResponse(result)
        except merlin_inbox.InboxUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except storage.SupabaseUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    logger.info("Starting Scout Bot API server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout_bot",
        description="Scout Bot — Camelot Property Management Services Corp.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Collect and process leads only; skip enrichment, HubSpot, and email.",
    )
    parser.add_argument(
        "--no-enrichment",
        action="store_true",
        default=False,
        help="Skip the Apollo.io / Prospeo enrichment step.",
    )
    parser.add_argument(
        "--no-hubspot",
        action="store_true",
        default=False,
        help="Skip pushing leads to HubSpot CRM.",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        default=False,
        help="Skip sending the daily email report.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(CONFIG_PATH),
        help=f"Path to config.yaml (default: {CONFIG_PATH})",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        default=False,
        help="Start the FastAPI server (Lead Hunt + Merlin Inbox endpoints) instead of the CLI batch run.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="API server host when --serve is used (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8007,
        help="API server port when --serve is used (default: 8007)",
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()

    if args.config != str(CONFIG_PATH):
        CONFIG_PATH = Path(args.config)

    if args.serve:
        run_api_server(host=args.host, port=args.port)
        sys.exit(0)

    exit_code = run(args)
    sys.exit(exit_code)
