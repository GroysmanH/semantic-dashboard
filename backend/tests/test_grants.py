"""The security claim, expressed as tests rather than intentions.

'Destructive operations impossible by construction' means the grant
refuses them, not that the code never tries.
"""

import psycopg
import pytest


def test_warehouse_role_can_read_the_warehouse(warehouse_conn):
    with warehouse_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ddh.fct_well_interventions")
        assert cur.fetchone()[0] > 0


def test_warehouse_role_cannot_insert(warehouse_conn):
    with warehouse_conn.cursor() as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute(
            "INSERT INTO ddh.dim_wells (well_id, well_name, region_name, field_name) "
            "VALUES (999999, 'x', 'x', 'x')"
        )


def test_warehouse_role_cannot_update(warehouse_conn):
    with warehouse_conn.cursor() as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("UPDATE ddh.fct_well_interventions SET net_gain_bbl = 0")


def test_warehouse_role_cannot_delete(warehouse_conn):
    with warehouse_conn.cursor() as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("DELETE FROM ddh.fct_well_interventions")


def test_warehouse_role_cannot_drop(warehouse_conn):
    with warehouse_conn.cursor() as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("DROP TABLE ddh.dim_wells")


def test_warehouse_role_cannot_read_app_schema(warehouse_conn):
    """The read path cannot see boards or cards at all."""
    with warehouse_conn.cursor() as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("SELECT * FROM app.card")


def test_app_role_cannot_read_warehouse(app_conn):
    """And the write path cannot reach the data."""
    with app_conn.cursor() as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("SELECT * FROM ddh.dim_wells")


def test_app_role_can_write_its_own_schema(app_conn):
    with app_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app.board (id, title) VALUES (gen_random_uuid(), 'grant test') "
            "RETURNING id"
        )
        assert cur.fetchone()[0] is not None
    app_conn.rollback()
