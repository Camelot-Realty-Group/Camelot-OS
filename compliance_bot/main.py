"""
main.py — Compliance Bot Entry Point
Camelot Property Management Services Corp

Orchestrates daily/weekly compliance scans across the Camelot portfolio
and dispatches alert emails to the operations team.

Usage:
    python main.py                    # Run full scan + send alerts
    python main.py --dry-run          # Run scan, print digest, no email
    python main.py --building <bbl>   # Scan single building by BBL
    python main.py --report           # Generate and print Markdown report only

Author: Camelot OS
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "logs/compliance_bot.log")

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("compliance_bot.main")


# ---------------------------------------------------------------------------
# Portfolio loader
# ---------------------------------------------------------------------------

def load_portfolio(portfolio_path: str) -> list[dict]:
    """
    Load portfolio from a JSON file.
    Falls back to a sample portfolio for demo/development purposes.

    JSON format:
    [
        {
            "building_id": "CAM-001",
            "address": "123 Main St, Bronx, NY 10452",
            "bbl": "2025010012",
            "bin": "2000001",
            "gross_sq_ft": 25000,
            "asset_type": "multifamily",
            "electricity_kwh": 150000,
            "natural_gas_kbtu": 1000000
        },
        ...
    ]
    """
    path = Path(portfolio_path)
    if path.exists():
        logger.info(f"Loading portfolio from {portfolio_path}")
        with open(path) as f:
            return json.load(f)
    else:
        logger.warning(f"Portfolio file not found: {portfolio_path}. Using sample data.")
        return _sample_portfolio()


def _sample_portfolio() -> list[dict]:
    """Sample portfolio for development/testing."""
    return [
        {
            "building_id": "CAM-552",
            "address": "552 [Address], New York, NY",
            "bbl": "3000010001",
            "bin": "3000001",
            "gross_sq_ft": 32000,
            "asset_type": "multifamily",
            "electricity_kwh": 210000,
            "natural_gas_kbtu": 1450000,
        },
        {
            "building_id": "CAM-100",
            "address": "100 Sample Ave, Bronx, NY 10452",
            "bbl": "2040050030",
            "bin": "2000100",
            "gross_sq_ft": 15000,
            "asset_type": "multifamily",
            "electricity_kwh": 90000,
            "natural_gas_kbtu": 800000,
        },
    ]


# ---------------------------------------------------------------------------
# Main scan runner
# ---------------------------------------------------------------------------

def run(
    portfolio: list[dict],
    dry_run: bool = False,
    recipients: list[str] | None = None,
    include_hpd: bool = True,
    include_dob: bool = True,
    include_ll97: bool = True,
    include_rent_stab: bool = True,
) -> int:
    """
    Execute compliance scan and dispatch alerts.

    Returns:
        Exit code: 0 = success, 1 = errors, 2 = critical issues found.
    """
    from compliance_bot.alerts import (
        run_compliance_scan,
        generate_alert_digest,
        send_compliance_alert,
    )

    logger.info("=" * 60)
    logger.info("CAMELOT COMPLIANCE BOT — SCAN START")
    logger.info(f"Portfolio size: {len(portfolio)} buildings")
    logger.info("=" * 60)

    # Run the scan
    scan_result = run_compliance_scan(
        portfolio,
        include_hpd=include_hpd,
        include_dob=include_dob,
        include_ll97=include_ll97,
        include_rent_stab=include_rent_stab,
    )

    # Print digest
    digest = generate_alert_digest(scan_result)
    print(digest)

    # Save digest to file
    digest_path = f"logs/compliance_digest_{scan_result.scan_timestamp[:10]}.txt"
    with open(digest_path, "w", encoding="utf-8") as f:
        f.write(digest)
    logger.info(f"Digest saved to {digest_path}")

    # Send email
    if not dry_run:
        sent = send_compliance_alert(
            scan_result,
            recipients=recipients,
            send_only_if_issues=False,
        )
        if sent:
            logger.info("Alert email sent successfully")
        else:
            logger.error("Alert email failed — check SMTP configuration")
    else:
        logger.info("DRY RUN: email not sent")

    # Exit code based on findings
    if scan_result.errors:
        return 1
    if scan_result.critical_count > 0:
        return 2
    return 0


# ---------------------------------------------------------------------------
# API server (FastAPI) — used by the orchestrator (bot_registry :8003)
# ---------------------------------------------------------------------------

def run_api_server(host: str = "0.0.0.0", port: int = 8003) -> None:
    """
    Start the Compliance Bot API server expected by the orchestrator and
    docker-compose (health check at /health).

    Endpoints:
        GET  /health
        POST /scan            — body: {"portfolio": [...], "dry_run": true}
                                 (omit portfolio to use PORTFOLIO_JSON/sample)
        GET  /building/{bbl}   — on-demand single-building scan (dry run)
    """
    try:
        import uvicorn
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel
    except ImportError:
        logger.error("FastAPI/uvicorn not installed. Run: pip install -r requirements.txt")
        sys.exit(1)

    from compliance_bot.alerts import run_compliance_scan, generate_alert_digest

    app = FastAPI(title="Camelot Compliance Bot", version="1.0.0")

    class ScanRequest(BaseModel):
        portfolio: list | None = None
        dry_run: bool = True
        include_hpd: bool = True
        include_dob: bool = True
        include_ll97: bool = True
        include_rent_stab: bool = True

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "Camelot Compliance Bot"}

    @app.post("/scan")
    async def scan(body: ScanRequest):
        portfolio = body.portfolio or load_portfolio(os.getenv("PORTFOLIO_JSON", "portfolio.json"))
        result = run_compliance_scan(
            portfolio,
            include_hpd=body.include_hpd,
            include_dob=body.include_dob,
            include_ll97=body.include_ll97,
            include_rent_stab=body.include_rent_stab,
        )
        return JSONResponse({
            "scan_timestamp": result.scan_timestamp,
            "buildings_scanned": len(portfolio),
            "critical_count": result.critical_count,
            "errors": result.errors,
            "digest": generate_alert_digest(result),
        })

    @app.get("/building/{bbl}")
    async def building_scan(bbl: str):
        portfolio = load_portfolio(os.getenv("PORTFOLIO_JSON", "portfolio.json"))
        subset = [b for b in portfolio if b.get("bbl") == bbl] or [{"address": bbl, "bbl": bbl}]
        result = run_compliance_scan(subset)
        return JSONResponse({
            "bbl": bbl,
            "scan_timestamp": result.scan_timestamp,
            "critical_count": result.critical_count,
            "digest": generate_alert_digest(result),
        })

    logger.info(f"Starting Compliance Bot API server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level=LOG_LEVEL.lower())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Camelot Compliance Bot — Portfolio regulatory compliance scanner"
    )
    parser.add_argument(
        "--portfolio",
        default=os.getenv("PORTFOLIO_JSON", "portfolio.json"),
        help="Path to portfolio JSON file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run scan but do not send email",
    )
    parser.add_argument(
        "--building",
        metavar="BBL",
        help="Scan a single building by BBL",
    )
    parser.add_argument(
        "--skip-hpd", action="store_true", help="Skip HPD violation checks"
    )
    parser.add_argument(
        "--skip-dob", action="store_true", help="Skip DOB violation/permit checks"
    )
    parser.add_argument(
        "--skip-ll97", action="store_true", help="Skip LL97 emissions checks"
    )
    parser.add_argument(
        "--skip-rs", action="store_true", help="Skip rent stabilization checks"
    )
    parser.add_argument(
        "--recipients",
        nargs="+",
        default=None,
        help="Email recipients (overrides defaults)",
    )
    parser.add_argument(
        "--serve", action="store_true", help="Start the API server (used by orchestrator)"
    )
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"), help="API server host")
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("PORT", "8003")), help="API server port"
    )

    args = parser.parse_args()

    if args.serve:
        run_api_server(host=args.host, port=args.port)
        return

    portfolio = load_portfolio(args.portfolio)

    # Single building override
    if args.building:
        portfolio = [b for b in portfolio if b.get("bbl") == args.building]
        if not portfolio:
            # Create minimal record for on-demand scan
            portfolio = [{"address": args.building, "bbl": args.building}]
        logger.info(f"Single-building scan: {args.building}")

    exit_code = run(
        portfolio=portfolio,
        dry_run=args.dry_run,
        recipients=args.recipients,
        include_hpd=not args.skip_hpd,
        include_dob=not args.skip_dob,
        include_ll97=not args.skip_ll97,
        include_rent_stab=not args.skip_rs,
    )

    logger.info(f"Compliance Bot exit code: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
