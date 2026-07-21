"""
deal_memo_generator.py — Investment Deal Memo Generator

Generates professional investment deal memos for Camelot acquisition candidates.
Covers multifamily, mixed-use, and commercial assets across the Camelot footprint.

Author: Camelot OS / Broker Bot
"""

import logging
from datetime import date
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PropertyData:
    address: str
    borough_or_county: str
    asset_type: str               # Multifamily | Mixed-Use | Commercial
    year_built: Optional[int]
    total_units: Optional[int]
    gross_sq_ft: Optional[int]
    lot_sq_ft: Optional[int]
    zoning: Optional[str]
    unit_mix: Optional[str]       # e.g. "8 x 1BR, 12 x 2BR, 4 x 3BR"
    parking: Optional[str]
    recent_renovations: Optional[str]
    description: Optional[str]


@dataclass
class Financials:
    asking_price: float
    proposed_price: Optional[float]     # Camelot's offer price

    # Income
    gross_scheduled_income: float       # GSI (100% occupancy)
    physical_vacancy_pct: float = 5.0   # %
    credit_loss_pct: float = 1.0        # %
    other_income: float = 0.0           # laundry, parking, fees

    # Expenses (annual)
    real_estate_taxes: float = 0.0
    insurance: float = 0.0
    utilities: float = 0.0
    repairs_maintenance: float = 0.0
    management_fee_pct: float = 5.0     # % of EGI
    payroll: float = 0.0
    admin: float = 0.0
    reserves_per_unit: float = 250.0    # capital reserves / unit / year

    # Financing assumptions (optional)
    loan_amount: Optional[float] = None
    interest_rate: Optional[float] = None    # annual %
    amortization_years: Optional[int] = None
    equity_invested: Optional[float] = None


@dataclass
class MarketData:
    submarket: str
    avg_market_rent_1br: Optional[float] = None
    avg_market_rent_2br: Optional[float] = None
    avg_market_rent_3br: Optional[float] = None
    vacancy_rate_pct: Optional[float] = None
    avg_cap_rate_pct: Optional[float] = None
    avg_price_per_unit: Optional[float] = None
    rent_growth_yoy_pct: Optional[float] = None
    population_growth: Optional[str] = None
    employment_drivers: Optional[str] = None
    comparable_sales_summary: Optional[str] = None
    market_commentary: Optional[str] = None


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate_deal_memo(
    property_data: PropertyData,
    financials: Financials,
    market_data: MarketData,
    memo_date: Optional[date] = None,
    prepared_by: str = "Broker Bot / Camelot Realty Group",
    confidential: bool = True,
) -> str:
    """
    Generate a full investment deal memo in Markdown.

    Args:
        property_data:  Physical property details.
        financials:     Income, expense, and financing data.
        market_data:    Submarket context and comps.
        memo_date:      Date of memo (defaults to today).
        prepared_by:    Author attribution line.
        confidential:   Add confidentiality header if True.

    Returns:
        Full deal memo as a Markdown string.
    """
    if memo_date is None:
        memo_date = date.today()

    offer_price = financials.proposed_price or financials.asking_price

    # --- Financial calculations ---
    vacancy_amount = financials.gross_scheduled_income * (financials.physical_vacancy_pct / 100)
    credit_loss_amount = financials.gross_scheduled_income * (financials.credit_loss_pct / 100)
    effective_gross_income = (
        financials.gross_scheduled_income
        - vacancy_amount
        - credit_loss_amount
        + financials.other_income
    )

    mgmt_fee = effective_gross_income * (financials.management_fee_pct / 100)
    reserves = (financials.reserves_per_unit * (property_data.total_units or 1))
    total_expenses = (
        financials.real_estate_taxes
        + financials.insurance
        + financials.utilities
        + financials.repairs_maintenance
        + mgmt_fee
        + financials.payroll
        + financials.admin
        + reserves
    )
    noi = effective_gross_income - total_expenses

    # Ratios
    cap_rate = (noi / offer_price * 100) if offer_price > 0 else None
    price_per_unit = (offer_price / property_data.total_units) if property_data.total_units else None
    price_per_sqft = (offer_price / property_data.gross_sq_ft) if property_data.gross_sq_ft else None
    grm = (offer_price / financials.gross_scheduled_income) if financials.gross_scheduled_income > 0 else None
    expense_ratio = (total_expenses / effective_gross_income * 100) if effective_gross_income > 0 else None

    # Debt service / cash-on-cash (if financing data provided)
    annual_debt_service = 0.0
    cash_on_cash = None
    dscr = None
    if (financials.loan_amount and financials.interest_rate and financials.amortization_years):
        monthly_rate = financials.interest_rate / 100 / 12
        n = financials.amortization_years * 12
        monthly_payment = (
            financials.loan_amount * monthly_rate * (1 + monthly_rate) ** n
            / ((1 + monthly_rate) ** n - 1)
        )
        annual_debt_service = monthly_payment * 12
        dscr = noi / annual_debt_service if annual_debt_service > 0 else None

    if financials.equity_invested and financials.equity_invested > 0:
        cash_flow_before_tax = noi - annual_debt_service
        cash_on_cash = (cash_flow_before_tax / financials.equity_invested) * 100

    # Estimated 5-year IRR (simplified):
    # Assumption: 2.5% rent growth/yr, exit at same cap rate in yr 5
    irr_est = _estimate_irr(noi, offer_price, financials.equity_invested, annual_debt_service)

    # --- Build memo sections ---

    confidential_header = (
        "> **CONFIDENTIAL — FOR INTERNAL USE ONLY**\n> \n"
        "> This document contains non-public financial projections and proprietary analysis "
        "prepared exclusively for Camelot Property Management Services Corp and its affiliates. "
        "Do not distribute without authorization.\n\n"
        if confidential else ""
    )

    # Unit mix section
    unit_mix_section = ""
    if property_data.unit_mix:
        unit_mix_section = f"\n**Unit Mix:** {property_data.unit_mix}"

    # Comparable pricing context
    comp_context = ""
    if market_data.avg_price_per_unit and price_per_unit:
        diff_pct = ((price_per_unit - market_data.avg_price_per_unit) / market_data.avg_price_per_unit) * 100
        direction = "above" if diff_pct > 0 else "below"
        comp_context = (
            f"\n\nAt **${price_per_unit:,.0f}/unit**, the proposed acquisition price is "
            f"**{abs(diff_pct):.1f}% {direction}** the submarket average of "
            f"${market_data.avg_price_per_unit:,.0f}/unit."
        )

    # Cap rate context
    cap_context = ""
    if market_data.avg_cap_rate_pct and cap_rate:
        spread = cap_rate - market_data.avg_cap_rate_pct
        direction = "above" if spread > 0 else "below"
        cap_context = (
            f" The implied cap rate of **{cap_rate:.2f}%** is "
            f"{abs(spread):.2f}% {direction} the submarket average of {market_data.avg_cap_rate_pct:.2f}%."
        )

    memo = f"""{confidential_header}# INVESTMENT DEAL MEMO

**Property:** {property_data.address}  
**Date:** {memo_date.strftime("%B %d, %Y")}  
**Prepared By:** {prepared_by}  
**Asset Type:** {property_data.asset_type}

---

## 1. EXECUTIVE SUMMARY

Camelot Realty Group is evaluating the acquisition of **{property_data.address}**, a 
{property_data.total_units or "N/A"}-unit {property_data.asset_type.lower()} property 
located in **{property_data.borough_or_county}**, {_state_from_county(property_data.borough_or_county)}.

The property is offered at **${financials.asking_price:,.0f}**
{f" (${price_per_unit:,.0f}/unit)" if price_per_unit else ""}.
{f"Camelot's proposed acquisition price is **${offer_price:,.0f}**." if financials.proposed_price else ""}

At the proposed price, the asset yields a **{f'{cap_rate:.2f}%' if cap_rate else 'N/A'} 
going-in cap rate** on underwritten NOI of **${noi:,.0f}**. 
{cap_context}

{f"Estimated 5-Year Levered IRR: **{irr_est:.1f}%**" if irr_est else ""}

**Recommendation:** {"Proceed to LOI" if cap_rate and cap_rate >= 5.0 else "Negotiate price" if cap_rate and cap_rate >= 4.0 else "Further diligence required"}

---

## 2. PROPERTY OVERVIEW

| Attribute | Detail |
|-----------|--------|
| Address | {property_data.address} |
| Borough / County | {property_data.borough_or_county} |
| Asset Type | {property_data.asset_type} |
| Year Built | {property_data.year_built or "N/A"} |
| Total Units | {property_data.total_units or "N/A"} |
| Gross Sq Ft | {f"{property_data.gross_sq_ft:,}" if property_data.gross_sq_ft else "N/A"} |
| Lot Sq Ft | {f"{property_data.lot_sq_ft:,}" if property_data.lot_sq_ft else "N/A"} |
| Zoning | {property_data.zoning or "N/A"} |
| Parking | {property_data.parking or "N/A"} |
{unit_mix_section}

{f"**Recent Renovations:** {property_data.recent_renovations}" if property_data.recent_renovations else ""}

{f"### Property Description\n\n{property_data.description}" if property_data.description else ""}

---

## 3. MARKET ANALYSIS

### Submarket: {market_data.submarket}

{market_data.market_commentary or f"The {market_data.submarket} submarket is part of Camelot's core geographic focus."}

| Market Metric | Value |
|---------------|-------|
| Submarket Vacancy Rate | {f"{market_data.vacancy_rate_pct:.1f}%" if market_data.vacancy_rate_pct else "N/A"} |
| Avg Market Rent (1BR) | {f"${market_data.avg_market_rent_1br:,.0f}/mo" if market_data.avg_market_rent_1br else "N/A"} |
| Avg Market Rent (2BR) | {f"${market_data.avg_market_rent_2br:,.0f}/mo" if market_data.avg_market_rent_2br else "N/A"} |
| Avg Market Rent (3BR) | {f"${market_data.avg_market_rent_3br:,.0f}/mo" if market_data.avg_market_rent_3br else "N/A"} |
| Avg Price/Unit (Comps) | {f"${market_data.avg_price_per_unit:,.0f}" if market_data.avg_price_per_unit else "N/A"} |
| Avg Cap Rate (Comps) | {f"{market_data.avg_cap_rate_pct:.2f}%" if market_data.avg_cap_rate_pct else "N/A"} |
| YoY Rent Growth | {f"{market_data.rent_growth_yoy_pct:.1f}%" if market_data.rent_growth_yoy_pct else "N/A"} |

{f"**Employment Drivers:** {market_data.employment_drivers}" if market_data.employment_drivers else ""}

{f"### Comparable Sales Summary\n\n{market_data.comparable_sales_summary}" if market_data.comparable_sales_summary else ""}

---

## 4. FINANCIAL ANALYSIS

### 4a. Proposed Acquisition Price

| Metric | Value |
|--------|-------|
| Asking Price | ${financials.asking_price:,.0f} |
| **Proposed Price** | **${offer_price:,.0f}** |
| Price per Unit | {f"${price_per_unit:,.0f}" if price_per_unit else "N/A"} |
| Price per Sq Ft | {f"${price_per_sqft:,.2f}" if price_per_sqft else "N/A"} |
| Gross Rent Multiplier | {f"{grm:.2f}x" if grm else "N/A"} |

### 4b. Income & Expense Summary (Year 1 Stabilized)

| Line Item | Annual Amount |
|-----------|--------------|
| **Gross Scheduled Income (GSI)** | **${financials.gross_scheduled_income:,.0f}** |
| Less: Vacancy ({financials.physical_vacancy_pct:.1f}%) | (${vacancy_amount:,.0f}) |
| Less: Credit Loss ({financials.credit_loss_pct:.1f}%) | (${credit_loss_amount:,.0f}) |
| Plus: Other Income | ${financials.other_income:,.0f} |
| **Effective Gross Income (EGI)** | **${effective_gross_income:,.0f}** |
| Real Estate Taxes | (${financials.real_estate_taxes:,.0f}) |
| Insurance | (${financials.insurance:,.0f}) |
| Utilities | (${financials.utilities:,.0f}) |
| Repairs & Maintenance | (${financials.repairs_maintenance:,.0f}) |
| Management Fee ({financials.management_fee_pct:.1f}% of EGI) | (${mgmt_fee:,.0f}) |
| Payroll | (${financials.payroll:,.0f}) |
| Administrative | (${financials.admin:,.0f}) |
| Capital Reserves (${financials.reserves_per_unit:,.0f}/unit) | (${reserves:,.0f}) |
| **Total Operating Expenses** | **(${total_expenses:,.0f})** |
| **Net Operating Income (NOI)** | **${noi:,.0f}** |
| **Expense Ratio** | **{f"{expense_ratio:.1f}%" if expense_ratio else "N/A"}** |

### 4c. Return Metrics

| Metric | Value |
|--------|-------|
| Going-In Cap Rate | {f"{cap_rate:.2f}%" if cap_rate else "N/A"} |
{f"| Annual Debt Service | ${annual_debt_service:,.0f} |" if annual_debt_service else ""}
{f"| DSCR | {dscr:.2f}x |" if dscr else ""}
{f"| Cash-on-Cash Return | {cash_on_cash:.2f}% |" if cash_on_cash else ""}
{f"| Estimated 5-Year IRR | {irr_est:.1f}% |" if irr_est else ""}

{_debt_section(financials) if financials.loan_amount else "### 4d. All-Cash Acquisition\n\nNo financing assumed in this analysis."}

{comp_context}

---

## 5. INVESTMENT THESIS

1. **Stable Cash Flow:** The property generates underwritten NOI of ${noi:,.0f} 
   ({f"{cap_rate:.2f}%" if cap_rate else "N/A"} going-in cap rate), consistent with Camelot's 
   minimum acquisition criteria for {property_data.borough_or_county} assets.

2. **Camelot OS Integration:** Upon acquisition, Camelot's proprietary OS will be deployed 
   for property management, resident communications (Front Desk Bot), compliance monitoring 
   (Compliance Bot), and owner reporting (Report Bot), immediately improving operational 
   efficiency and NOI.

3. **Portfolio Synergy:** This acquisition adds to Camelot's existing concentration in 
   {market_data.submarket}, providing management scale economies and reduced per-unit costs.

4. **Upside Potential:** 
   {f"- Submarket rents growing at {market_data.rent_growth_yoy_pct:.1f}% YoY." if market_data.rent_growth_yoy_pct else "- Market rent growth provides revenue upside."}
   - Operational efficiencies through Camelot OS can reduce management overhead.
   - Any below-market leases represent rent-to-market upside at lease renewal.

---

## 6. RISK FACTORS

| Risk | Severity | Mitigation |
|------|----------|------------|
| Interest rate environment | Moderate | All-cash / conservative LTV if financed |
| Regulatory (rent stabilization) | Moderate | Verify stabilized status during DD; Camelot has HCR expertise |
| Capital needs (age {property_data.year_built or "unknown"}) | Moderate | Full engineering inspection during DD |
| Vacancy during transition | Low | Camelot management in place on Day 1 |
| NYC/local tax assessment increases | Low–Moderate | Tax cert appeal program standard for all acquisitions |
| Environmental (pre-1980 construction) | Low–Moderate | Phase I required; Phase II if flagged |

---

## 7. RECOMMENDATION

{"**PROCEED TO LOI** — The asset meets Camelot's acquisition criteria. A non-binding LOI should be submitted promptly." if cap_rate and cap_rate >= 5.0 else "**NEGOTIATE PRICE** — The current asking price results in a sub-5.0% cap rate. Camelot should submit an LOI at a re-priced offer to achieve target returns." if cap_rate and cap_rate >= 3.5 else "**FURTHER DILIGENCE REQUIRED** — Additional underwriting data needed before a recommendation can be made."}

**Next Steps:**
1. Eleni Palmeri to review and authorize LOI submission
2. Broker Bot to generate LOI via `loi_generator.py`
3. Add to HubSpot Brokerage Pipeline
4. Schedule property tour and request rent roll / trailing 12 P&L from seller

---

*Prepared by: {prepared_by}*  
*Date: {memo_date.strftime("%B %d, %Y")}*

> **Disclaimer:** This deal memo contains forward-looking financial projections based on 
> available market data and management assumptions. Projections are not guaranteed. 
> This document does not constitute legal, tax, or investment advice. 
> All figures should be independently verified during due diligence.
> Camelot Property Management Services Corp and its affiliates make no warranty 
> as to the accuracy or completeness of the information herein.
"""
    logger.info(f"Generated deal memo for {property_data.address} — NOI ${noi:,.0f}, Cap {f'{cap_rate:.2f}%' if cap_rate else 'N/A'}")
    return memo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state_from_county(county: str) -> str:
    """Infer state from borough/county name."""
    county_lower = county.lower()
    if any(b in county_lower for b in ["manhattan", "bronx", "brooklyn", "queens", "staten island"]):
        return "NY"
    if "westchester" in county_lower:
        return "NY"
    if any(s in county_lower for s in ["fairfield", "hartford", "new haven"]):
        return "CT"
    if any(s in county_lower for s in ["essex", "hudson", "bergen", "union"]):
        return "NJ"
    if "miami" in county_lower or "palm beach" in county_lower or "broward" in county_lower:
        return "FL"
    return "NY"


def _estimate_irr(noi: float, price: float, equity: Optional[float], debt_service: float) -> Optional[float]:
    """
    Estimate 5-year levered IRR using simplified DCF.
    Assumptions: 2.5% annual NOI growth, same exit cap rate, 65% LTV if no equity provided.
    """
    if price <= 0:
        return None
    if equity is None:
        equity = price * 0.35  # assume 35% equity
    if equity <= 0:
        return None

    try:
        # Project 5 years of cash flows
        growth_rate = 0.025  # 2.5% annual NOI growth
        exit_cap = noi / price  # use going-in cap rate as exit cap

        cash_flows = [-equity]
        running_noi = noi
        for year in range(1, 6):
            running_noi *= (1 + growth_rate)
            cf = running_noi - debt_service
            if year < 5:
                cash_flows.append(cf)
            else:
                exit_value = running_noi / exit_cap
                loan_balance = _estimate_loan_balance(price - equity, year)
                net_proceeds = exit_value - loan_balance - (exit_value * 0.03)  # 3% closing costs
                cash_flows.append(cf + net_proceeds)

        # Newton's method IRR
        irr = _newton_irr(cash_flows)
        return round(irr * 100, 1) if irr else None
    except Exception as e:
        logger.debug(f"IRR estimation failed: {e}")
        return None


def _estimate_loan_balance(loan_amount: float, years: int) -> float:
    """Rough loan balance estimate (assumes 30yr amort, 6.5% rate)."""
    if loan_amount <= 0:
        return 0.0
    monthly_rate = 0.065 / 12
    n = 30 * 12
    monthly_payment = loan_amount * monthly_rate * (1 + monthly_rate) ** n / ((1 + monthly_rate) ** n - 1)
    balance = loan_amount
    for _ in range(years * 12):
        interest = balance * monthly_rate
        balance -= (monthly_payment - interest)
    return max(0.0, balance)


def _newton_irr(cash_flows: list, guess: float = 0.10, tol: float = 1e-6, max_iter: int = 100) -> Optional[float]:
    """Newton–Raphson IRR solver."""
    rate = guess
    for _ in range(max_iter):
        npv = sum(cf / (1 + rate) ** t for t, cf in enumerate(cash_flows))
        dnpv = sum(-t * cf / (1 + rate) ** (t + 1) for t, cf in enumerate(cash_flows))
        if abs(dnpv) < 1e-12:
            return None
        new_rate = rate - npv / dnpv
        if abs(new_rate - rate) < tol:
            return new_rate
        rate = new_rate
    return None


def _debt_section(financials: Financials) -> str:
    if not financials.loan_amount:
        return ""
    return f"""### 4d. Financing Assumptions

| Item | Value |
|------|-------|
| Loan Amount | ${financials.loan_amount:,.0f} |
| Interest Rate | {financials.interest_rate:.2f}% |
| Amortization | {financials.amortization_years} years |
| Equity Invested | {f"${financials.equity_invested:,.0f}" if financials.equity_invested else "N/A"} |
| LTV | {f"{financials.loan_amount / (financials.proposed_price or financials.asking_price) * 100:.1f}%" if (financials.proposed_price or financials.asking_price) > 0 else "N/A"} |
"""


# ---------------------------------------------------------------------------
# CLI / quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    prop = PropertyData(
        address="456 Grand Concourse, Bronx, NY 10452",
        borough_or_county="Bronx",
        asset_type="Multifamily",
        year_built=1963,
        total_units=24,
        gross_sq_ft=19200,
        lot_sq_ft=6000,
        zoning="R7-1",
        unit_mix="4 x Studio, 10 x 1BR, 8 x 2BR, 2 x 3BR",
        parking=None,
        recent_renovations="New roof (2021), boiler replaced (2020)",
        description="Well-maintained walk-up in the heart of the Grand Concourse Historic District.",
    )

    fin = Financials(
        asking_price=4_800_000,
        proposed_price=4_500_000,
        gross_scheduled_income=432_000,
        physical_vacancy_pct=5.0,
        credit_loss_pct=1.0,
        other_income=6_000,
        real_estate_taxes=72_000,
        insurance=18_000,
        utilities=36_000,
        repairs_maintenance=24_000,
        management_fee_pct=5.0,
        payroll=0,
        admin=6_000,
        reserves_per_unit=250,
        loan_amount=3_150_000,
        interest_rate=6.5,
        amortization_years=30,
        equity_invested=1_350_000,
    )

    mkt = MarketData(
        submarket="South Bronx / Grand Concourse",
        avg_market_rent_1br=1800,
        avg_market_rent_2br=2200,
        vacancy_rate_pct=3.5,
        avg_cap_rate_pct=5.5,
        avg_price_per_unit=185_000,
        rent_growth_yoy_pct=3.2,
        employment_drivers="Fordham University, NYC Health + Hospitals, Bronx Zoo, Yankee Stadium",
    )

    memo = generate_deal_memo(prop, fin, mkt)
    print(memo)
