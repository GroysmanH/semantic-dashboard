"""Two connection pools, because the design needs two privilege levels.

warehouse: SELECT-only role, and a read-only transaction default as a
second layer behind the grant.
app: owns the app schema, and cannot reach the warehouse at all.
"""

from __future__ import annotations

from psycopg_pool import ConnectionPool

from .config import settings

warehouse_pool = ConnectionPool(
    settings.warehouse_url,
    min_size=1,
    max_size=8,
    open=False,
    kwargs={"options": "-c default_transaction_read_only=on"},
)

app_pool = ConnectionPool(settings.app_url, min_size=1, max_size=8, open=False)


def open_pools() -> None:
    warehouse_pool.open()
    app_pool.open()
    warehouse_pool.wait(timeout=30)
    app_pool.wait(timeout=30)


def close_pools() -> None:
    warehouse_pool.close()
    app_pool.close()
