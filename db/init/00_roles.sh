#!/bin/bash
# Creates the two least-privilege login roles. Passwords come from the
# environment so they are never committed. Grants live in 04_grants.sql,
# which must run after the tables exist.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	CREATE ROLE warehouse_ro LOGIN PASSWORD '${WAREHOUSE_PASSWORD}';
	CREATE ROLE app_rw       LOGIN PASSWORD '${APP_PASSWORD}';
SQL
