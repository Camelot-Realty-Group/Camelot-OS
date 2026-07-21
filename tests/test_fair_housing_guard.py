"""Tests for utils/fair_housing_guard.py."""
from utils.fair_housing_guard import check_text


def test_clean_text_passes():
    r = check_text(
        "Sunny 2-bedroom near the A train with laundry in building. "
        "Doorman, elevator, pets considered. $3,200/month."
    )
    assert r.is_clean
    assert not r.findings


def test_no_kids_blocks():
    r = check_text("Beautiful unit, no kids please")
    assert not r.is_clean
    assert any(f.category == "familial_status" and f.severity == "block" for f in r.findings)


def test_adults_only_blocks():
    r = check_text("Adults-only building")
    assert any(f.severity == "block" for f in r.findings)


def test_voucher_refusal_blocks():
    for phrase in ("No Section 8", "no vouchers", "Section 8 not accepted"):
        r = check_text(phrase)
        assert any(f.category == "source_of_income" for f in r.findings), phrase


def test_disability_exclusion_blocks():
    r = check_text("Sorry, no wheelchairs — walk-up building")
    assert any(f.category == "disability" and f.severity == "block" for f in r.findings)


def test_ideal_tenant_phrasing_flags_review():
    r = check_text("Perfect for young professionals!")
    assert not r.is_clean
    assert all(f.severity == "review" for f in r.findings)
    assert not r.blocking


def test_steering_language_review():
    r = check_text("Located in a safe neighborhood with an exclusive community feel")
    cats = {f.category for f in r.findings}
    assert "steering" in cats


def test_empty_and_none_are_clean():
    assert check_text("").is_clean
    assert check_text(None).is_clean


def test_case_insensitive():
    assert not check_text("NO KIDS").is_clean
