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
    kwargs={"options": (
        "-c default_transaction_read_only=on "
        f"-c statement_timeout={settings.warehouse_statement_timeout}"
    )},
)

app_pool = ConnectionPool(settings.app_url, min_size=1, max_size=8, open=False)


class WarehouseIsWritable(RuntimeError):
    """The warehouse connection could write, and must not be used."""


def _assert_warehouse_is_read_only() -> None:
    """Check the guarantee rather than trust the setting.

    `default_transaction_read_only` is passed in `options`, and options are
    easy to lose: a DSN that already carries its own `options`, a pooler
    that drops them, an edit to the kwargs above. The login this points at
    may well hold INSERT, UPDATE and DELETE -- warehouse accounts usually
    do -- in which case this one setting is the whole of the protection,
    and something that is the whole of the protection should be measured
    at boot rather than assumed.

    Refusing to start is the right failure. A dashboard that does not come
    up is a bad morning; a dashboard silently able to write to the
    warehouse it reads is a different kind of day.
    """
    with warehouse_pool.connection() as conn:
        state = conn.execute("SHOW transaction_read_only").fetchone()
        if not state or state[0] != "on":
            raise WarehouseIsWritable(
                "the warehouse connection is not read-only: "
                f"transaction_read_only={state[0] if state else 'unknown'}. "
                "Refusing to start.")


def open_pools() -> None:
    warehouse_pool.open()
    app_pool.open()
    warehouse_pool.wait(timeout=30)
    app_pool.wait(timeout=30)
    _assert_warehouse_is_read_only()


def close_pools() -> None:
    warehouse_pool.close()
    app_pool.close()
