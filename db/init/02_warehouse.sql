-- ---------------------------------------------------------------- ddh
CREATE TABLE ddh.dim_wells (
    well_id          integer PRIMARY KEY,
    well_name        text        NOT NULL,
    region_name      text        NOT NULL,
    field_name       text        NOT NULL,
    spud_date        date,
    well_type        text,
    -- Synthetic, jittered around real oblast centroids. Enough spatial
    -- structure for a map to show clustering, no claim to be survey data.
    latitude         numeric(9, 6),
    longitude        numeric(9, 6),
    asset_name       text,
    operator_name    text,
    basin_name       text,
    operating_status text,
    lift_method      text
);

CREATE TABLE ddh.fct_well_interventions (
    intervention_id   integer PRIMARY KEY,
    well_id           integer NOT NULL,
    intervention_date date    NOT NULL,
    intervention_type text    NOT NULL,
    status            text    NOT NULL,
    net_gain_bbl      numeric(12, 2),
    cost_usd          numeric(14, 2),
    contractor        text
);

CREATE TABLE ddh.fct_production_daily (
    well_id        integer NOT NULL,
    reading_date   date    NOT NULL,
    oil_bbl        numeric(12, 2),
    gas_mcf        numeric(12, 2),
    water_bbl      numeric(12, 2),
    downtime_hours numeric(6, 2),
    PRIMARY KEY (well_id, reading_date)
);

CREATE TABLE ddh.fct_downtime_events (
    event_id          integer PRIMARY KEY,
    well_id           integer                     NOT NULL,
    event_start       timestamp without time zone NOT NULL,
    event_end         timestamp without time zone NOT NULL,
    duration_hours    numeric(8, 2)                NOT NULL,
    downtime_category text                         NOT NULL,
    planning_status   text                         NOT NULL
);

CREATE TABLE ddh.fct_field_targets_monthly (
    field_name     text           NOT NULL,
    target_month   date           NOT NULL,
    asset_name     text           NOT NULL,
    region_name    text           NOT NULL,
    operator_name  text           NOT NULL,
    basin_name     text           NOT NULL,
    target_oil_bbl numeric(16, 2) NOT NULL,
    PRIMARY KEY (field_name, target_month)
);

CREATE TABLE ddh.mart_production_performance_monthly (
    field_name        text           NOT NULL,
    performance_month date           NOT NULL,
    asset_name        text           NOT NULL,
    region_name       text           NOT NULL,
    operator_name     text           NOT NULL,
    basin_name        text           NOT NULL,
    actual_oil_bbl    numeric(16, 2) NOT NULL,
    target_oil_bbl    numeric(16, 2) NOT NULL,
    variance_oil_bbl  numeric(16, 2) NOT NULL,
    attainment_pct    numeric(9, 4)  NOT NULL,
    PRIMARY KEY (field_name, performance_month)
);

CREATE INDEX dim_wells_field_name_idx
    ON ddh.dim_wells (field_name);
CREATE INDEX dim_wells_asset_name_idx
    ON ddh.dim_wells (asset_name);
CREATE INDEX dim_wells_operator_name_idx
    ON ddh.dim_wells (operator_name);
CREATE INDEX dim_wells_status_type_idx
    ON ddh.dim_wells (operating_status, well_type);
CREATE INDEX fct_well_interventions_intervention_date_idx
    ON ddh.fct_well_interventions (intervention_date);
CREATE INDEX fct_well_interventions_well_id_idx
    ON ddh.fct_well_interventions (well_id);
CREATE INDEX fct_well_interventions_well_date_idx
    ON ddh.fct_well_interventions (well_id, intervention_date);
CREATE INDEX fct_production_daily_reading_date_idx
    ON ddh.fct_production_daily (reading_date);
CREATE INDEX fct_downtime_events_event_start_idx
    ON ddh.fct_downtime_events (event_start);
CREATE INDEX fct_downtime_events_well_start_idx
    ON ddh.fct_downtime_events (well_id, event_start);
CREATE INDEX fct_field_targets_monthly_target_month_idx
    ON ddh.fct_field_targets_monthly (target_month);
CREATE INDEX fct_field_targets_monthly_asset_month_idx
    ON ddh.fct_field_targets_monthly (asset_name, target_month);
CREATE INDEX mart_production_performance_monthly_month_idx
    ON ddh.mart_production_performance_monthly (performance_month);
CREATE INDEX mart_production_performance_monthly_asset_month_idx
    ON ddh.mart_production_performance_monthly (asset_name, performance_month);

-- No FK from the fact tables to dim_wells on purpose: the seed plants a
-- few orphan well_ids, which is the kind of mess the profiler should surface.

-- ---------------------------------------------------------------- stg
-- Raw landed shapes: everything text, source-system naming. Reviewed
-- transformations (including intervention status codes) happen only in ddh.
-- The planning mart. A view rather than a copy: one set of numbers, read
-- through the schema whose subject they belong to. A query against it
-- touches no operational table, which is the property a dashboard scoped
-- to dm_planning relies on.
CREATE VIEW dm_planning.field_targets_monthly AS
SELECT field_name,
       target_month,
       asset_name,
       region_name,
       operator_name,
       basin_name,
       target_oil_bbl
FROM ddh.fct_field_targets_monthly;

CREATE TABLE stg.wells_raw (
    well_id       text,
    wellname      text,
    region        text,
    fieldname     text,
    spud_dt       text,
    welltype      text,
    asset         text,
    operator_name text,
    basin         text,
    op_status     text,
    lift          text
);

CREATE TABLE stg.interventions_raw (
    job_id     text,
    well_id    text,
    job_dt     text,
    job_type   text,
    stat       text,
    gain       text,
    cost       text,
    contractor text
);

CREATE TABLE stg.production_daily_raw (
    well_id    text,
    reading_dt text,
    oil_vol    text,
    gas_vol    text,
    water_vol  text,
    down_hrs   text
);

CREATE TABLE stg.downtime_events_raw (
    event_id     text,
    well_id      text,
    start_ts     text,
    end_ts       text,
    duration_hrs text,
    reason       text,
    plan_stat    text
);

CREATE TABLE stg.field_targets_monthly_raw (
    fieldname  text,
    target_dt  text,
    asset      text,
    region     text,
    operator   text,
    basin      text,
    oil_target text
);
