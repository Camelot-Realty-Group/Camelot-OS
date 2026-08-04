"""Tests for scout_bot/lead_hunt.py — candidate building assembly and the
run_lead_hunt() orchestration. All Supabase and NYC Open Data calls are
mocked; no real HTTP requests are made.
"""
from unittest.mock import patch

import pytest

import lead_hunt
import storage


# ---------------------------------------------------------------------------
# build_candidates() — pure function, no network/DB
# ---------------------------------------------------------------------------

def test_build_candidates_flags_self_managed_building_as_high_priority():
    registrations = [{
        "bbl": "1001230001",
        "bin": "1012345",
        "registrationid": "999",
        "housenumber": "123",
        "streetname": "MAIN ST",
        "boro": "MANHATTAN",
        "unitsres": "120",
        "corporationname": "",
        "managingagentbusinessname": "",
        "yearbuilt": "1920",
    }]
    violation_counts = {"1001230001": {"total": 60, "open": 15}}
    dof_records = {"1001230001": {"fullval": "5000000", "avtot": "2000000", "bldgcl": "D4"}}

    candidates = lead_hunt.build_candidates(registrations, violation_counts, dof_records)

    assert len(candidates) == 1
    c = candidates[0]
    assert c["bbl"] == "1001230001"
    assert c["current_management"] == "Unknown / Self-Managed"
    assert c["lead_category"] == "unmanaged_building"
    assert c["lead_priority"] == "HIGH"
    assert c["score"] >= 75
    assert c["grade"] == "A"


def test_build_candidates_known_large_firm_scores_lower():
    registrations = [{
        "bbl": "1001230002",
        "housenumber": "456",
        "streetname": "PARK AVE",
        "boro": "MANHATTAN",
        "unitsres": "120",
        "managingagentbusinessname": "Related Companies",
        "yearbuilt": "1920",
    }]
    violation_counts = {"1001230002": {"total": 60, "open": 15}}
    dof_records = {}

    candidates = lead_hunt.build_candidates(registrations, violation_counts, dof_records)
    c = candidates[0]
    assert c["lead_category"] == "hpd_violation_building"
    assert c["current_management"] == "Related Companies"
    # Management points drop from 20 (self-managed) to 5 (known large firm),
    # so overall score should be meaningfully lower than the self-managed case.
    assert c["score"] < 95


def test_build_candidates_dedupes_by_bbl():
    registrations = [
        {"bbl": "1001230003", "housenumber": "1", "streetname": "A ST", "boro": "NY"},
        {"bbl": "1001230003", "housenumber": "1", "streetname": "A ST", "boro": "NY"},
    ]
    candidates = lead_hunt.build_candidates(registrations, {}, {})
    assert len(candidates) == 1


def test_build_candidates_skips_rows_without_bbl():
    registrations = [{"housenumber": "1", "streetname": "NO BBL ST"}]
    # Falls back to boroid+block+lot concatenation; if all are empty this
    # yields an empty string, which should be skipped rather than inserted.
    candidates = lead_hunt.build_candidates(registrations, {}, {})
    assert all(c["bbl"] for c in candidates)


# ---------------------------------------------------------------------------
# run_lead_hunt() — orchestration, with storage + Socrata fully mocked
# ---------------------------------------------------------------------------

FAKE_REGISTRATIONS = [{
    "bbl": "1009990001",
    "housenumber": "10",
    "streetname": "TEST ST",
    "boro": "MANHATTAN",
    "unitsres": "80",
    "corporationname": "",
    "managingagentbusinessname": "",
    "yearbuilt": "1930",
}]


@patch("lead_hunt.fetch_dof_property", return_value={})
@patch("lead_hunt.fetch_hpd_violation_counts", return_value={"1009990001": {"total": 30, "open": 5}})
@patch("lead_hunt.fetch_hpd_registrations", return_value=FAKE_REGISTRATIONS)
@patch("lead_hunt.storage")
def test_run_lead_hunt_creates_scan_and_upserts_qualifying_buildings(
    mock_storage, mock_reg, mock_viol, mock_dof
):
    mock_storage.get_scan_for_today.return_value = None
    mock_storage.create_scan.return_value = {"id": "scan-123"}
    mock_storage.upsert_building.return_value = {"id": "bld-1"}
    mock_storage.complete_scan.return_value = {"id": "scan-123", "status": "completed"}

    result = lead_hunt.run_lead_hunt(triggered_by="manual", min_score=0, dry_run=False)

    assert result["status"] == "completed"
    assert result["scan_id"] == "scan-123"
    assert result["inserted"] == 1
    mock_storage.create_scan.assert_called_once()
    mock_storage.upsert_building.assert_called_once()
    mock_storage.complete_scan.assert_called_once_with("scan-123", results_count=1, status="completed")


@patch("lead_hunt.fetch_dof_property", return_value={})
@patch("lead_hunt.fetch_hpd_violation_counts", return_value={})
@patch("lead_hunt.fetch_hpd_registrations", return_value=FAKE_REGISTRATIONS)
@patch("lead_hunt.storage")
def test_run_lead_hunt_is_idempotent_per_day(mock_storage, mock_reg, mock_viol, mock_dof):
    mock_storage.get_scan_for_today.return_value = {"id": "existing-scan", "results_count": 5}

    result = lead_hunt.run_lead_hunt(triggered_by="cron")

    assert result["status"] == "skipped_duplicate"
    assert result["scan_id"] == "existing-scan"
    mock_storage.create_scan.assert_not_called()


@patch("lead_hunt.fetch_dof_property", return_value={})
@patch("lead_hunt.fetch_hpd_violation_counts", return_value={"1009990001": {"total": 30, "open": 5}})
@patch("lead_hunt.fetch_hpd_registrations", return_value=FAKE_REGISTRATIONS)
def test_run_lead_hunt_dry_run_does_not_touch_storage(mock_reg, mock_viol, mock_dof):
    with patch("lead_hunt.storage") as mock_storage:
        result = lead_hunt.run_lead_hunt(triggered_by="manual", min_score=0, dry_run=True)

        mock_storage.create_scan.assert_not_called()
        mock_storage.upsert_building.assert_not_called()
        assert result["leads"] is not None
        assert len(result["leads"]) == 1


@patch("lead_hunt.fetch_dof_property", return_value={})
@patch("lead_hunt.fetch_hpd_violation_counts", return_value={"1009990001": {"total": 0, "open": 0}})
@patch("lead_hunt.fetch_hpd_registrations", return_value=FAKE_REGISTRATIONS)
@patch("lead_hunt.storage")
def test_run_lead_hunt_filters_below_min_score(mock_storage, mock_reg, mock_viol, mock_dof):
    mock_storage.get_scan_for_today.return_value = None
    mock_storage.create_scan.return_value = {"id": "scan-456"}
    mock_storage.complete_scan.return_value = {}

    result = lead_hunt.run_lead_hunt(triggered_by="manual", min_score=99, dry_run=False)

    assert result["qualified"] == 0
    assert result["inserted"] == 0
    mock_storage.upsert_building.assert_not_called()
