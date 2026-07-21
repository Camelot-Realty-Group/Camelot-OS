"""End-to-end render test for broker_bot/deal_memo_generator.py.

This module previously failed to import on Python < 3.12 (f-string
expressions containing backslashes); these tests guard the fix.
"""
import importlib.util
from pathlib import Path

SPEC_PATH = Path(__file__).parent.parent / "broker_bot" / "deal_memo_generator.py"


def _load():
    spec = importlib.util.spec_from_file_location("dmg", SPEC_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _sample(m, **overrides):
    pd = m.PropertyData(
        address="123 Main St, Brooklyn NY", borough_or_county="Brooklyn",
        asset_type="Multifamily", year_built=1962, total_units=24,
        gross_sq_ft=21000, lot_sq_ft=5000, zoning="R6", unit_mix=[],
        parking="None", recent_renovations="Roof 2024",
        description=overrides.get("description", "Well-maintained walk-up."),
    )
    fin = m.Financials(
        asking_price=5_200_000, proposed_price=4_900_000,
        gross_scheduled_income=612_000, physical_vacancy_pct=3.0,
        credit_loss_pct=1.0, other_income=12_000, real_estate_taxes=98_000,
        insurance=41_000, utilities=52_000, repairs_maintenance=48_000,
        management_fee_pct=6.0, payroll=30_000,
    )
    md = m.MarketData(
        submarket="Bed-Stuy", avg_market_rent_1br=2500, avg_market_rent_2br=3200,
        avg_market_rent_3br=3900, vacancy_rate_pct=3.1, avg_cap_rate_pct=5.9,
        avg_price_per_unit=210_000, rent_growth_yoy_pct=3.4,
        population_growth=None, employment_drivers="Healthcare, education",
        comparable_sales_summary=overrides.get("comps", "Three comps avg 205k/unit."),
        market_commentary=None,
    )
    return pd, fin, md


def test_module_imports_on_this_python():
    _load()


def test_memo_renders_with_all_sections():
    m = _load()
    memo = m.generate_deal_memo(*_sample(m))
    assert "INVESTMENT DEAL MEMO" in memo
    assert "Property Description" in memo
    assert "Comparable Sales Summary" in memo
    assert "Front Desk Bot" in memo  # post-rename branding


def test_memo_renders_without_optional_sections():
    m = _load()
    pd, fin, md = _sample(m)
    pd.description = None
    md.comparable_sales_summary = None
    memo = m.generate_deal_memo(pd, fin, md)
    assert "INVESTMENT DEAL MEMO" in memo
    assert "Property Description" not in memo
    assert "Comparable Sales Summary" not in memo
