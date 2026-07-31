-- =============================================================================
-- Camelot OS — CostBeat Bot Supabase Schema
-- =============================================================================
-- This repo has no migration runner. Run this file MANUALLY, once, in the
-- Supabase SQL editor (or via psql against the project) before starting the
-- bot. It is idempotent — re-running it is safe.
--
-- Two tables:
--   costbeat_analyses    — one row per budget analysed, with the issued figures
--   portfolio_benchmarks — Camelot's own managed buildings' actual costs
--
-- IMPORTANT: portfolio_benchmarks ships EMPTY. The entire analysis is built
-- from it, so until ops seeds it from the MDS monthly management reports every
-- expense line will report as "Not addressed — needs records/vendor-bid
-- review". That is the correct, honest output, not a bug. See the seed
-- template at the bottom of this file.
-- =============================================================================


-- ── Analyses ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS costbeat_analyses (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_name            TEXT,
    address                  TEXT,
    unit_count               INT,
    building_type            TEXT,
    market                   TEXT,
    uploaded_filename        TEXT,
    total_budget             NUMERIC,
    total_target             NUMERIC,
    total_savings            NUMERIC,
    savings_pct              NUMERIC,
    one_time_fee             NUMERIC,
    mgmt_fee_uplift_monthly  NUMERIC,
    mgmt_fee_uplift_annual   NUMERIC,
    recommended_fee_model    TEXT,
    line_items               JSONB,
    not_addressed            JSONB,
    notes                    TEXT,
    status                   TEXT DEFAULT 'draft',
    created_by               TEXT,
    created_at               TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_costbeat_analyses_created_at
    ON costbeat_analyses (created_at DESC);


-- ── Portfolio comparables ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS portfolio_benchmarks (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    building_name  TEXT,
    address        TEXT,
    unit_count     INT,
    building_type  TEXT,
    market         TEXT,
    category       TEXT,
    annual_cost    NUMERIC,
    monthly_cost   NUMERIC,
    source         TEXT,
    as_of_date     DATE,
    notes          TEXT,
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- Comps are always looked up by category within a unit-count band.
CREATE INDEX IF NOT EXISTS idx_portfolio_benchmarks_category_units
    ON portfolio_benchmarks (category, unit_count);


-- ── Row Level Security ──────────────────────────────────────────────────────
-- Both tables are written and read only by the bot using the service-role key,
-- which bypasses RLS. RLS is enabled so that no anon-key client can reach the
-- data if the project's anon key is ever exposed.

ALTER TABLE costbeat_analyses ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_benchmarks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role full access" ON costbeat_analyses;
CREATE POLICY "Service role full access" ON costbeat_analyses
    FOR ALL USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "Service role full access" ON portfolio_benchmarks;
CREATE POLICY "Service role full access" ON portfolio_benchmarks
    FOR ALL USING (auth.role() = 'service_role');


-- =============================================================================
-- Seeding portfolio_benchmarks
-- =============================================================================
-- One row per building per expense category. `category` MUST be one of the
-- sixteen taxonomy keys below, spelled exactly — anything else is ignored by
-- the lookup:
--
--   payroll_and_cleaning, insurance, hvac_mechanical, electricity, water_sewer,
--   gas, phone_internet_cable, intercom_security, elevator_maintenance,
--   sprinkler_fire_alarm, exterminator, compactor_waste, misc_repairs,
--   admin_fees, legal_accounting_management, taxes_bank_fees
--
-- Set `annual_cost`, or `monthly_cost` alone (it is multiplied by 12). Rows
-- with no cost or no unit_count are skipped — unit_count is required because
-- every comparison is made per unit.
--
-- `as_of_date` and `source` are printed on the client-facing report as the
-- citation for the figure, so fill them in.
--
-- INSERT INTO portfolio_benchmarks
--     (building_name, address, unit_count, building_type, market,
--      category, annual_cost, source, as_of_date)
-- VALUES
--     ('The Warwick', '120 West 78th Street', 32, 'co-op', 'Manhattan',
--      'insurance', 41200, 'MDS monthly management report', '2026-06-30'),
--     ('The Warwick', '120 West 78th Street', 32, 'co-op', 'Manhattan',
--      'water_sewer', 18450, 'MDS monthly management report', '2026-06-30');
-- =============================================================================
