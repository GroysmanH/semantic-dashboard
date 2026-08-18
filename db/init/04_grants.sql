-- The security claim of this project, expressed as grants rather than
-- intentions. Tested in backend/tests/test_grants.py.

-- warehouse_ro: SELECT on the warehouse, nothing anywhere else.
GRANT USAGE ON SCHEMA stg, ddh TO warehouse_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA stg, ddh TO warehouse_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA stg, ddh
    GRANT SELECT ON TABLES TO warehouse_ro;
-- No USAGE on app: the read path cannot see boards or cards at all.

-- app_rw: full control of app, nothing on the warehouse.
GRANT USAGE, CREATE ON SCHEMA app TO app_rw;
GRANT ALL ON ALL TABLES IN SCHEMA app TO app_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT ALL ON TABLES TO app_rw;

-- PUBLIC gets CREATE on the public schema by default; revoke it so neither
-- role can plant objects.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
