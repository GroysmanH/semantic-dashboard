-- ---------------------------------------------------------------- ddh
CREATE TABLE ddh.dim_wells (
    well_id      integer PRIMARY KEY,
    well_name    text        NOT NULL,
    region_name  text        NOT NULL,
    field_name   text        NOT NULL,
    spud_date    date,
    well_type    text,
    -- Synthetic, jittered around real oblast centroids. Enough spatial
    -- structure for a map to show clustering, no claim to be survey data.
    latitude     numeric(9, 6),
    longitude    numeric(9, 6)
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

CREATE INDEX ON ddh.fct_well_interventions (intervention_date);
CREATE INDEX ON ddh.fct_well_interventions (well_id);
CREATE INDEX ON ddh.fct_production_daily (reading_date);

-- No FK from the fact tables to dim_wells on purpose: the seed plants a
-- few orphan well_ids, which is the kind of mess the deferred profiler
-- exists to surface.

-- ---------------------------------------------------------------- stg
-- Raw landed shapes: everything text, source-system naming.
CREATE TABLE stg.wells_raw (
    well_id   text,
    wellname  text,
    region    text,
    fieldname text,
    spud_dt   text,
    welltype  text
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
