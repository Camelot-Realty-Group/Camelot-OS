"""
lead_hunt.py — Scout Bot Daily Lead Hunt
=========================================
Rebuild of the Supabase pg_cron job `camelot-daily-lead-hunt`
(`0 11 * * *`, jobid 1) as a real, callable endpoint.

That cron job used to POST to `app.settings.lead_hunt_function_url`, which
was never configured — no endpoint existed. This module supplies one:
`run_lead_hunt()`, wired to `POST /lead-hunt/run` in `main.py`.

What it does
------------
1. Opens a `scout_scans` row (idempotent — at most one scan per calendar
   day per `triggered_by` source; a second call the same day returns the
   existing scan instead of creating a duplicate).
2. Queries NYC Open Data (Socrata, free, no key required) for HPD
   registrations/violations and DOF property records across the configured
   boroughs, reusing the same endpoints and pagination/retry approach as
   `collectors/hpd_buildings.py`.
3. Scores every candidate building with the port of camelot-scout-v6's
   `src/lib/scoring.ts` `calculateScore()` — see `scoring.py`.
4. Upserts qualifying candidates (score >= `min_lead_score`) into
   `scout_buildings`, keyed by `bbl`, tagged with `lead_source`,
   `lead_category`, `lead_priority`, `lead_run_id`.
5. Closes out the `scout_scans` row with a result count.

This is a fresh, direct-to-Supabase pipeline — separate from `main.py`'s
existing generic collector -> HubSpot pipeline (`scout_leads` table), which
is untouched. See README.md "Two Lead Pipelines" section for why they are
kept apart.

Author: Camelot OS
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

import storage
from scoring import calculate_score, grade_for_score

# NOTE: scout_bot has its own `utils/` subpackage (parsing.py, filters.py,
# emailer.py), which shadows the repo-root `utils/` package (audit_log.py,
# spire_client.py, ...) whenever scout_bot's own directory is on sys.path
# ahead of the repo root — a plain `sys.path.insert` retry does NOT fix this
# once `utils` is already cached in sys.modules as scout_bot/utils. Load
# audit_log.py directly by file path instead, so this works regardless of
# import order or whether the process was started from the repo root or
# from inside scout_bot/ (e.g. `python scout_bot/main.py --serve`).
try:
    from utils.audit_log import audit_event
except ImportError:  # pragma: no cover - fallback when scout_bot/utils shadows repo-root utils
    import importlib.util
    from pathlib import Path

    _audit_log_path = Path(__file__).parent.parent / "utils" / "audit_log.py"
    _spec = importlib.util.spec_from_file_location("camelot_os_root_audit_log", _audit_log_path)
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    audit_event = _module.audit_event

logger = logging.getLogger("scout_bot.lead_hunt")

SOCRATA_BASE = "https://data.cityofnewyork.us/resource"
HPD_REGISTRATIONS_ENDPOINT = f"{SOCRATA_BASE}/tesw-yqqr.json"
HPD_VIOLATIONS_ENDPOINT = f"{SOCRATA_BASE}/wvxf-dwi5.json"
DOF_PROPERTY_ENDPOINT = f"{SOCRATA_BASE}/64uk-42ks.json"

PAGE_SIZE = 1000
DEFAULT_MAX_RECORDS = 2000
DEFAULT_TIMEOUT = 20

KNOWN_LARGE_FIRMS = [
    "firstservice residential", "related companies", "brookfield", "greystar",
    "equity residential", "avalon bay", "avalonbay", "cushman & wakefield",
    "cbre", "jll", "rudin management", "sl green", "vornado", "tishman speyer",
    "silverstein", "extell", "lefrak", "rose associates", "glenwood management",
]

BOROUGH_CODES = {
    "MANHATTAN": "1", "BRONX": "2", "BROOKLYN": "3", "QUEENS": "4", "STATEN ISLAND": "5",
}


class LeadHuntError(RuntimeError):
    """Raised for unrecoverable errors during a lead-hunt run."""


def _socrata_headers() -> dict[str, str]:
    token = os.getenv("SOCRATA_APP_TOKEN", "").strip()
    return {"X-App-Token": token} if token else {}


def _fetch_with_retry(url: str, params: dict[str, Any], attempts: int = 3) -> list[dict[str, Any]]:
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url, params=params, headers=_socrata_headers(), timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning("Socrata rate-limited (429); backing off %ss", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:  # noqa: PERF203
            last_exc = exc
            wait = 2 ** attempt
            logger.warning("Socrata request failed (attempt %d/%d): %s; retrying in %ss", attempt, attempts, exc, wait)
            time.sleep(wait)
    raise LeadHuntError(f"Socrata request to {url} failed after {attempts} attempts: {last_exc}")


def _paginate(url: str, base_params: dict[str, Any], max_records: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while len(out) < max_records:
        params = dict(base_params)
        params["$limit"] = min(PAGE_SIZE, max_records - len(out))
        params["$offset"] = offset
        batch = _fetch_with_retry(url, params)
        if not batch:
            break
        out.extend(batch)
        offset += len(batch)
        if len(batch) < params["$limit"]:
            break
        time.sleep(0.5)  # polite delay between pages
    return out


def _is_self_managed(managing_agent: Optional[str], corp_name: Optional[str]) -> bool:
    text = f"{managing_agent or ''} {corp_name or ''}".strip().lower()
    if not text:
        return True
    indicators = ["self", "owner", "n/a", "none", "individual"]
    if any(ind in text for ind in indicators):
        return True
    return not any(firm in text for firm in KNOWN_LARGE_FIRMS) and len(text) < 4


def _known_large_firm(managing_agent: Optional[str]) -> bool:
    text = (managing_agent or "").lower()
    return any(firm in text for firm in KNOWN_LARGE_FIRMS)


def fetch_hpd_registrations(boroughs: list[str], recent_days: int, max_records: int) -> list[dict[str, Any]]:
    """Buildings registered/re-registered with HPD in the last `recent_days`."""
    cutoff = (date.today() - timedelta(days=recent_days)).isoformat()
    all_rows: list[dict[str, Any]] = []
    for borough in boroughs:
        code = BOROUGH_CODES.get(borough.upper())
        if not code:
            continue
        params = {
            "$where": f"lastregistrationdate >= '{cutoff}'",
            "boroid": code,
        }
        try:
            rows = _paginate(HPD_REGISTRATIONS_ENDPOINT, params, max_records)
        except LeadHuntError as exc:
            logger.error("HPD registrations fetch failed for %s: %s", borough, exc)
            continue
        all_rows.extend(rows)
    return all_rows


def fetch_hpd_violation_counts(bbls: list[str]) -> dict[str, dict[str, int]]:
    """Batched open/total violation counts keyed by BBL."""
    counts: dict[str, dict[str, int]] = {}
    if not bbls:
        return counts
    chunk = 500
    for i in range(0, len(bbls), chunk):
        subset = bbls[i:i + chunk]
        bbl_list = ",".join(f"'{b}'" for b in subset)
        params = {
            "$select": "bbl,count(*) as total,sum(case when violationstatus='Open' then 1 else 0 end) as open_count",
            "$where": f"bbl in({bbl_list})",
            "$group": "bbl",
        }
        try:
            rows = _fetch_with_retry(HPD_VIOLATIONS_ENDPOINT, params)
        except LeadHuntError as exc:
            logger.error("HPD violation counts fetch failed: %s", exc)
            continue
        for row in rows:
            bbl = row.get("bbl")
            if bbl:
                counts[bbl] = {
                    "total": int(float(row.get("total", 0) or 0)),
                    "open": int(float(row.get("open_count", 0) or 0)),
                }
    return counts


def fetch_dof_property(bbls: list[str]) -> dict[str, dict[str, Any]]:
    """Market/assessed value + building class per BBL from DOF property records."""
    out: dict[str, dict[str, Any]] = {}
    if not bbls:
        return out
    chunk = 500
    for i in range(0, len(bbls), chunk):
        subset = bbls[i:i + chunk]
        bbl_list = ",".join(f"'{b}'" for b in subset)
        params = {"$where": f"bbl in({bbl_list})", "$limit": len(subset)}
        try:
            rows = _fetch_with_retry(DOF_PROPERTY_ENDPOINT, params)
        except LeadHuntError as exc:
            logger.error("DOF property fetch failed: %s", exc)
            continue
        for row in rows:
            bbl = row.get("bbl")
            if bbl:
                out[bbl] = row
    return out


def _building_age(year_built: Optional[str]) -> Optional[int]:
    try:
        yb = int(year_built)
        if yb <= 0:
            return None
        return date.today().year - yb
    except (TypeError, ValueError):
        return None


def build_candidates(
    registrations: list[dict[str, Any]],
    violation_counts: dict[str, dict[str, int]],
    dof_records: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge the three NYC Open Data sources into scout_buildings-shaped rows
    with a computed lead score, ready for upsert."""
    candidates: list[dict[str, Any]] = []
    seen_bbls: set[str] = set()

    for reg in registrations:
        bbl = reg.get("bbl") or reg.get("boroid", "") + str(reg.get("block", "")) + str(reg.get("lot", ""))
        if not bbl or bbl in seen_bbls:
            continue
        seen_bbls.add(bbl)

        managing_agent = reg.get("managingagentbusinessname") or reg.get("corporationname")
        self_managed = _is_self_managed(managing_agent, reg.get("corporationname"))
        large_firm = _known_large_firm(managing_agent)

        vc = violation_counts.get(bbl, {"total": 0, "open": 0})
        dof = dof_records.get(bbl, {})

        units = None
        for key in ("unitsres", "unitstotal", "numbldgs"):
            if reg.get(key):
                try:
                    units = int(float(reg[key]))
                    break
                except (TypeError, ValueError):
                    continue

        year_built = dof.get("yearbuilt") or reg.get("yearbuilt")
        age_years = _building_age(year_built)

        factors = {
            "hpd_violations_total": vc["total"],
            "hpd_violations_open": vc["open"],
            "units": units or 0,
            "self_managed_or_unknown": self_managed,
            "known_large_firm": large_firm,
            "building_age_years": age_years,
            "recent_dob_permits": 0,          # not queried in this pass — see README caveats
            "energy_star_score": None,        # LL97 not queried in this pass
            "site_eui": None,
            "ecb_violation_count": 0,
            "ecb_penalty_total": 0,
            "active_housing_litigation": False,
            "rent_stabilized": False,
        }
        score = calculate_score(factors)
        grade = grade_for_score(score)

        candidate = {
            "bbl": bbl,
            "bin": reg.get("bin"),
            "hpd_building_id": reg.get("registrationid") or reg.get("buildingid"),
            "address": " ".join(
                filter(None, [reg.get("housenumber"), reg.get("streetname")])
            ) or reg.get("address"),
            "borough": reg.get("boro") or reg.get("borough"),
            "units": units,
            "year_built": int(year_built) if str(year_built or "").isdigit() else None,
            "current_management": managing_agent or "Unknown / Self-Managed",
            "violations_count": vc["total"],
            "open_violations_count": vc["open"],
            "market_value": dof.get("fullval") or dof.get("market_value"),
            "assessed_value": dof.get("avtot") or dof.get("assessed_value"),
            "building_class": dof.get("bldgcl") or reg.get("buildingclass"),
            "score": score,
            "grade": grade,
            "lead_source": "nyc_open_data_hpd_dof",
            "lead_category": "unmanaged_building" if self_managed else "hpd_violation_building",
            "lead_priority": "HIGH" if score >= 75 else ("MEDIUM" if score >= 50 else "LOW"),
            "lead_pitch_angle": (
                "Self-managed / no professional agent on file — cold outreach on "
                "operational relief and violation remediation."
                if self_managed
                else f"{vc['open']} open HPD violations — cost-of-inaction outreach."
            ),
            "lead_found_at": datetime.now(timezone.utc).isoformat(),
            "status": "discovered",
            "pipeline_stage": "discovered",
        }
        candidates.append(candidate)

    return candidates


def run_lead_hunt(
    triggered_by: str = "cron",
    boroughs: Optional[list[str]] = None,
    recent_days: int = 90,
    max_records: int = DEFAULT_MAX_RECORDS,
    min_score: int = 40,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute one lead-hunt run.

    Idempotency: if a `scout_scans` row already exists for today from the
    same `triggered_by` source, that scan is returned unchanged rather than
    running (and billing Socrata calls) twice. Pass a distinct
    `triggered_by` (e.g. "manual") to force a second run on the same day.
    """
    boroughs = boroughs or ["MANHATTAN", "BROOKLYN", "QUEENS", "BRONX", "STATEN ISLAND"]

    existing = None
    if not dry_run:
        try:
            existing = storage.get_scan_for_today(triggered_by=triggered_by)
        except storage.SupabaseUnavailable:
            raise
    if existing:
        logger.info("Lead hunt already ran today (scan %s); skipping duplicate run.", existing.get("id"))
        audit_event(
            bot="scout",
            action="lead_hunt_run_skipped_duplicate",
            detail={"scan_id": existing.get("id"), "triggered_by": triggered_by},
        )
        return {
            "status": "skipped_duplicate",
            "scan_id": existing.get("id"),
            "results_count": existing.get("results_count"),
        }

    scan_id = None
    if not dry_run:
        scan = storage.create_scan(
            name=f"Daily Lead Hunt — {date.today().isoformat()}",
            created_by=triggered_by,
            filters={"boroughs": boroughs, "recent_days": recent_days, "min_score": min_score},
        )
        scan_id = scan.get("id")

    logger.info("Fetching HPD registrations for %s (recent_days=%d)...", boroughs, recent_days)
    registrations = fetch_hpd_registrations(boroughs, recent_days, max_records)
    bbls = [r.get("bbl") for r in registrations if r.get("bbl")]

    logger.info("Fetching HPD violation counts for %d BBLs...", len(bbls))
    violation_counts = fetch_hpd_violation_counts(bbls)

    logger.info("Fetching DOF property records for %d BBLs...", len(bbls))
    dof_records = fetch_dof_property(bbls)

    candidates = build_candidates(registrations, violation_counts, dof_records)
    qualified = [c for c in candidates if c["score"] >= min_score]
    if scan_id:
        for c in qualified:
            c["lead_run_id"] = scan_id

    inserted = 0
    errors: list[str] = []
    if not dry_run:
        for candidate in qualified:
            try:
                storage.upsert_building(candidate)
                inserted += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to upsert building %s: %s", candidate.get("bbl"), exc)
                errors.append(f"{candidate.get('bbl')}: {exc}")

        if scan_id:
            storage.complete_scan(scan_id, results_count=inserted, status="completed" if not errors else "completed_with_errors")

    audit_event(
        bot="scout",
        action="lead_hunt_run",
        detail={
            "scan_id": scan_id,
            "triggered_by": triggered_by,
            "candidates_found": len(candidates),
            "qualified": len(qualified),
            "inserted": inserted,
            "errors": len(errors),
            "dry_run": dry_run,
        },
    )

    return {
        "status": "completed" if not errors else "completed_with_errors",
        "scan_id": scan_id,
        "candidates_found": len(candidates),
        "qualified": len(qualified),
        "inserted": inserted,
        "errors": errors,
        "leads": qualified if dry_run else None,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_lead_hunt(triggered_by="manual", dry_run=True, max_records=200)
    print(result)
