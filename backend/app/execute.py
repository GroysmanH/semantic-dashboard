"""Run a compiled query and coerce the result into JSON-safe rows."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from .db import warehouse_pool
from .semantic.compile import CompiledQuery, data_max_ts_sql


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def run(compiled: CompiledQuery) -> list[dict[str, Any]]:
    with warehouse_pool.connection() as conn, conn.cursor() as cur:
        cur.execute(compiled.sql, compiled.params)
        names = [d.name for d in cur.description]
        return [{n: _jsonable(v) for n, v in zip(names, row)} for row in cur.fetchall()]


def data_max_ts(compiled: CompiledQuery) -> str | None:
    stmt = data_max_ts_sql(compiled.entity)
    if stmt is None:
        return None
    sql_text, params = stmt
    with warehouse_pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql_text, params)
        row = cur.fetchone()
    return _jsonable(row[0]) if row and row[0] is not None else None
