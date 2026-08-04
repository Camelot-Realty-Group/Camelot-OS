-- =============================================================================
-- Perseus — Supabase schema
-- Camelot Property Management Services Corp.
--
-- RUN THIS MANUALLY in the Supabase SQL editor (Dashboard → SQL Editor → New
-- query → paste → Run). Perseus does not create or migrate tables at runtime.
--
-- Perseus also READS two tables it does not own:
--   portfolio_benchmarks  — cross-portfolio category comparables (shared with
--                           CostBeat Bot; see costbeat_bot/supabase_schema.sql)
--   costbeat_analyses     — used as a budget baseline when a building already
--                           has a CostBeat annual-budget analysis on file.
-- Neither is required for Perseus to run: without comparables it reports budget
-- variance only, and without a CostBeat row it falls back to an uploaded budget
-- file or to the budget column inside the uploaded report.
-- =============================================================================


-- =============================================================================
-- perseus_variance_reports — one row per period, per building
--
-- Each row stands alone. Perseus prices only the savings identified in the
-- period the row covers; nothing accumulates across rows and no row is netted
-- against a CostBeat proposal for the same building.
-- =============================================================================
CREATE TABLE IF NOT EXISTS perseus_variance_reports (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Subject building and period
    property_name                 TEXT NOT NULL,
    address                       TEXT,
    quarter                       TEXT,           -- 'Q1'..'Q4', or a month/period label
    year                          INT NOT NULL,
    cadence                       TEXT DEFAULT 'quarterly',
                                                  -- quarterly | monthly | semiannual | annual
    unit_count                    INT,
    building_type                 TEXT,
    market                        TEXT,

    -- Where the budget baseline came from
    budget_source                 TEXT,           -- 'costbeat_analysis' | 'uploaded' | 'report_column' | 'spire'
    linked_costbeat_analysis_id   UUID,           -- set only when budget_source = 'costbeat_analysis'
    uploaded_filename             TEXT,
    source_format                 TEXT,           -- parser layout: columnar | gl_hierarchy | flat | mds_pdf | spire_gl_actuals | spire_budget
    data_source                   TEXT DEFAULT 'upload', -- 'upload' | 'spire' — where the period actuals came from
    spire_building_id              TEXT,           -- Spire CompanyRcd, set only when data_source = 'spire'

    -- Budget vs. actual, for the period reported
    total_budget_period           NUMERIC(14,2),
    total_actual_period           NUMERIC(14,2),
    budget_variance               NUMERIC(14,2),
    budget_variance_pct           NUMERIC(8,4),

    -- Savings against the Camelot portfolio average (annualized run-rate basis)
    portfolio_savings_opportunity NUMERIC(14,2),  -- annualized
    portfolio_savings_period      NUMERIC(14,2),  -- the same savings, this period's share

    -- Fee proposal for THIS period only
    one_time_fee                  NUMERIC(14,2),
    mgmt_fee_uplift_monthly       NUMERIC(14,2),
    mgmt_fee_uplift_annual        NUMERIC(14,2),
    recommended_fee_model         TEXT,           -- 'one_time_fee' | 'mgmt_fee_uplift'

    -- Detail
    line_items                    JSONB DEFAULT '[]'::jsonb,
    flagged_categories            JSONB DEFAULT '[]'::jsonb,
    notes                         TEXT,

    status                        TEXT DEFAULT 'draft',
                                                  -- draft | sent | approved | declined
    created_by                    TEXT,
    created_at                    TIMESTAMPTZ DEFAULT now()
);

-- One building's periods, newest first — the history table on the upload page.
CREATE INDEX IF NOT EXISTS idx_perseus_variance_reports_property_period
    ON perseus_variance_reports (property_name, year, quarter);

CREATE INDEX IF NOT EXISTS idx_perseus_variance_reports_created_at
    ON perseus_variance_reports (created_at DESC);


-- =============================================================================
-- Row Level Security — service role only
--
-- These rows carry building-level expense detail and Camelot's fee proposals.
-- Nothing here should be reachable with an anon key. The service key used by
-- perseus_bot/storage.py bypasses RLS; every other caller is denied.
-- =============================================================================
ALTER TABLE perseus_variance_reports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role full access" ON perseus_variance_reports;
CREATE POLICY "Service role full access" ON perseus_variance_reports
    FOR ALL USING (auth.role() = 'service_role');


-- =============================================================================
-- Column notes
-- =============================================================================
COMMENT ON TABLE perseus_variance_reports IS
    'One management-report period per row. Self-contained: the fee proposal on a row prices only that period''s findings and is never carried forward.';

COMMENT ON COLUMN perseus_variance_reports.budget_source IS
    'costbeat_analysis = baseline pulled from a CostBeat annual-budget analysis; uploaded = a separate budget file was uploaded alongside the actuals; report_column = the budget column inside the uploaded report was used; spire = pulled live from Spire''s GL/Budgets endpoint.';

COMMENT ON COLUMN perseus_variance_reports.data_source IS
    'upload = actuals came from a manually uploaded file (default, always available); spire = actuals were pulled live from Camelot''s Spire property-management API for the period.';

-- =============================================================================
-- Migration note (existing databases): if perseus_variance_reports already
-- exists from before Spire sourcing was added, run just these two lines
-- against it — CREATE TABLE IF NOT EXISTS above is a no-op once the table
-- exists, so new columns need to be added explicitly:
--   ALTER TABLE perseus_variance_reports ADD COLUMN IF NOT EXISTS data_source TEXT DEFAULT 'upload';
--   ALTER TABLE perseus_variance_reports ADD COLUMN IF NOT EXISTS spire_building_id TEXT;
-- =============================================================================

COMMENT ON COLUMN perseus_variance_reports.portfolio_savings_opportunity IS
    'Annualized gap between this building''s run-rate and the Camelot portfolio average per unit. Own-budget overruns are NOT counted here — a building''s budget is a plan, not a market price.';

COMMENT ON COLUMN perseus_variance_reports.flagged_categories IS
    'Categories running more than the configured threshold over their prorated budget share. A flag is a question for the manager, not a saving.';
