-- Two additions that arrive together because they only make sense together:
-- a second business schema to scope to, and the per-dashboard scope itself.
--
-- Idempotent, like every migration here, so an operator can replay it while
-- converging an older database.

-- 1. The planning mart -------------------------------------------------
--
-- A view, not a copy. The numbers stay where they are landed; the mart is
-- the schema a planning question is answered from, and answering one
-- touches nothing operational.

CREATE SCHEMA IF NOT EXISTS dm_planning;

CREATE OR REPLACE VIEW dm_planning.field_targets_monthly AS
SELECT field_name,
       target_month,
       asset_name,
       region_name,
       operator_name,
       basin_name,
       target_oil_bbl
FROM ddh.fct_field_targets_monthly;

GRANT USAGE ON SCHEMA dm_planning TO warehouse_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA dm_planning TO warehouse_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA dm_planning
    GRANT SELECT ON TABLES TO warehouse_ro;

-- 2. The scope ---------------------------------------------------------
--
-- Nullable on purpose, and only for the length of this migration. The
-- runner is raw SQL and cannot read the semantic layer, so it cannot know
-- which schema an existing board's cards belong to. `app.backfill` fills
-- the nulls at startup, where the layer is loaded, and every board has a
-- schema from then on.

ALTER TABLE app.board
    ADD COLUMN IF NOT EXISTS schema_name text;

COMMENT ON COLUMN app.board.schema_name IS
    'Warehouse schema this dashboard asks questions of. Backfilled from the '
    'board''s own cards on first start after this migration.';
