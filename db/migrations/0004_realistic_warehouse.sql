-- Add the opt-in operational warehouse profile without replacing any landed
-- data. This SQL is intentionally idempotent in addition to being ledgered:
-- operators can inspect/replay it safely while converging an older warehouse.

CREATE SCHEMA IF NOT EXISTS ddh;
CREATE SCHEMA IF NOT EXISTS stg;

CREATE TABLE IF NOT EXISTS ddh.dim_wells (
    well_id integer PRIMARY KEY,
    well_name text NOT NULL,
    region_name text NOT NULL,
    field_name text NOT NULL,
    spud_date date,
    well_type text,
    latitude numeric(9, 6),
    longitude numeric(9, 6),
    asset_name text,
    operator_name text,
    basin_name text,
    operating_status text,
    lift_method text
);
ALTER TABLE ddh.dim_wells ADD COLUMN IF NOT EXISTS asset_name text;
ALTER TABLE ddh.dim_wells ADD COLUMN IF NOT EXISTS operator_name text;
ALTER TABLE ddh.dim_wells ADD COLUMN IF NOT EXISTS basin_name text;
ALTER TABLE ddh.dim_wells ADD COLUMN IF NOT EXISTS operating_status text;
ALTER TABLE ddh.dim_wells ADD COLUMN IF NOT EXISTS lift_method text;

CREATE TABLE IF NOT EXISTS ddh.fct_well_interventions (
    intervention_id integer PRIMARY KEY,
    well_id integer NOT NULL,
    intervention_date date NOT NULL,
    intervention_type text NOT NULL,
    status text NOT NULL,
    net_gain_bbl numeric(12, 2),
    cost_usd numeric(14, 2),
    contractor text
);

CREATE TABLE IF NOT EXISTS ddh.fct_production_daily (
    well_id integer NOT NULL,
    reading_date date NOT NULL,
    oil_bbl numeric(12, 2),
    gas_mcf numeric(12, 2),
    water_bbl numeric(12, 2),
    downtime_hours numeric(6, 2),
    PRIMARY KEY (well_id, reading_date)
);

CREATE TABLE IF NOT EXISTS ddh.fct_downtime_events (
    event_id integer PRIMARY KEY,
    well_id integer NOT NULL,
    event_start timestamp without time zone NOT NULL,
    event_end timestamp without time zone NOT NULL,
    duration_hours numeric(8, 2) NOT NULL,
    downtime_category text NOT NULL,
    planning_status text NOT NULL
);

CREATE TABLE IF NOT EXISTS ddh.fct_field_targets_monthly (
    field_name text NOT NULL,
    target_month date NOT NULL,
    asset_name text NOT NULL,
    region_name text NOT NULL,
    operator_name text NOT NULL,
    basin_name text NOT NULL,
    target_oil_bbl numeric(16, 2) NOT NULL,
    PRIMARY KEY (field_name, target_month)
);

CREATE TABLE IF NOT EXISTS ddh.mart_production_performance_monthly (
    field_name text NOT NULL,
    performance_month date NOT NULL,
    asset_name text NOT NULL,
    region_name text NOT NULL,
    operator_name text NOT NULL,
    basin_name text NOT NULL,
    actual_oil_bbl numeric(16, 2) NOT NULL,
    target_oil_bbl numeric(16, 2) NOT NULL,
    variance_oil_bbl numeric(16, 2) NOT NULL,
    attainment_pct numeric(9, 4) NOT NULL,
    PRIMARY KEY (field_name, performance_month)
);

CREATE TABLE IF NOT EXISTS stg.wells_raw (
    well_id text,
    wellname text,
    region text,
    fieldname text,
    spud_dt text,
    welltype text,
    asset text,
    operator_name text,
    basin text,
    op_status text,
    lift text
);
ALTER TABLE stg.wells_raw ADD COLUMN IF NOT EXISTS asset text;
ALTER TABLE stg.wells_raw ADD COLUMN IF NOT EXISTS operator_name text;
ALTER TABLE stg.wells_raw ADD COLUMN IF NOT EXISTS basin text;
ALTER TABLE stg.wells_raw ADD COLUMN IF NOT EXISTS op_status text;
ALTER TABLE stg.wells_raw ADD COLUMN IF NOT EXISTS lift text;

CREATE TABLE IF NOT EXISTS stg.interventions_raw (
    job_id text,
    well_id text,
    job_dt text,
    job_type text,
    stat text,
    gain text,
    cost text,
    contractor text
);

CREATE TABLE IF NOT EXISTS stg.production_daily_raw (
    well_id text,
    reading_dt text,
    oil_vol text,
    gas_vol text,
    water_vol text,
    down_hrs text
);

CREATE TABLE IF NOT EXISTS stg.downtime_events_raw (
    event_id text,
    well_id text,
    start_ts text,
    end_ts text,
    duration_hrs text,
    reason text,
    plan_stat text
);

CREATE TABLE IF NOT EXISTS stg.field_targets_monthly_raw (
    fieldname text,
    target_dt text,
    asset text,
    region text,
    operator text,
    basin text,
    oil_target text
);

-- The source contract has now been reviewed. Keep the source codes in stg,
-- and expose only the manager-facing values in the curated fact.
UPDATE ddh.fct_well_interventions
   SET status = CASE status
       WHEN '1' THEN 'COMPLETED'
       WHEN '2' THEN 'CANCELLED'
       WHEN '3' THEN 'IN_PROGRESS'
       ELSE upper(status)
   END
 WHERE status IN ('1', '2', '3') OR status <> upper(status);

CREATE INDEX IF NOT EXISTS dim_wells_field_name_idx
    ON ddh.dim_wells (field_name);
CREATE INDEX IF NOT EXISTS dim_wells_asset_name_idx
    ON ddh.dim_wells (asset_name);
CREATE INDEX IF NOT EXISTS dim_wells_operator_name_idx
    ON ddh.dim_wells (operator_name);
CREATE INDEX IF NOT EXISTS dim_wells_status_type_idx
    ON ddh.dim_wells (operating_status, well_type);
CREATE INDEX IF NOT EXISTS fct_well_interventions_intervention_date_idx
    ON ddh.fct_well_interventions (intervention_date);
CREATE INDEX IF NOT EXISTS fct_well_interventions_well_id_idx
    ON ddh.fct_well_interventions (well_id);
CREATE INDEX IF NOT EXISTS fct_well_interventions_well_date_idx
    ON ddh.fct_well_interventions (well_id, intervention_date);
CREATE INDEX IF NOT EXISTS fct_production_daily_reading_date_idx
    ON ddh.fct_production_daily (reading_date);
CREATE INDEX IF NOT EXISTS fct_downtime_events_event_start_idx
    ON ddh.fct_downtime_events (event_start);
CREATE INDEX IF NOT EXISTS fct_downtime_events_well_start_idx
    ON ddh.fct_downtime_events (well_id, event_start);
CREATE INDEX IF NOT EXISTS fct_field_targets_monthly_target_month_idx
    ON ddh.fct_field_targets_monthly (target_month);
CREATE INDEX IF NOT EXISTS fct_field_targets_monthly_asset_month_idx
    ON ddh.fct_field_targets_monthly (asset_name, target_month);
CREATE INDEX IF NOT EXISTS mart_production_performance_monthly_month_idx
    ON ddh.mart_production_performance_monthly (performance_month);
CREATE INDEX IF NOT EXISTS mart_production_performance_monthly_asset_month_idx
    ON ddh.mart_production_performance_monthly (asset_name, performance_month);

GRANT USAGE ON SCHEMA stg, ddh TO warehouse_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA stg, ddh TO warehouse_ro;
