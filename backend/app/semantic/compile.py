"""Semantic query -> SQL. Deterministic, ordinary Python, unit-tested.

A given semantic query compiles to exactly one SQL string. That is what
lets the compiler take ordinary golden-string tests, and lets the eval
compare two small dicts instead of judging SQL equivalence.

Every identifier is wrapped in psycopg's sql.Identifier and every value
is a bound placeholder. Nothing is string-interpolated into SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from psycopg import sql

from ..layer.models import Dimension, Entity, Layer
from .query import SemanticQuery
from .validate import validate_query

MAX_ROWS = 10_000

ColumnKind = Literal["temporal", "nominal", "quantitative"]

_AGG = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "min": "MIN", "max": "MAX"}
_GRAIN_LABEL = {"day": "day", "month": "month", "quarter": "quarter", "year": "year"}


@dataclass
class CompiledQuery:
    sql: str
    params: list[Any]
    columns: list[str]
    column_kinds: dict[str, ColumnKind]
    entity: Entity
    query: SemanticQuery
    joins_used: list[str] = field(default_factory=list)


def _base_alias(entity: Entity) -> str:
    """Unqualified table name, which is what join conditions are written
    against: `wells.well_id = fct_well_interventions.well_id`."""
    return entity.table.split(".")[-1]


def _table_ref(qualified: str) -> sql.Composed:
    parts = qualified.split(".")
    return sql.SQL(".").join(sql.Identifier(p) for p in parts)


def _dimension_expr(entity: Entity, name: str, dim: Dimension,
                    grain: str | None) -> sql.Composable:
    if dim.via:
        alias, column = dim.via.split(".", 1)
        col = sql.SQL("{}.{}").format(sql.Identifier(alias), sql.Identifier(column))
    else:
        col = sql.SQL("{}.{}").format(
            sql.Identifier(_base_alias(entity)), sql.Identifier(dim.column or name)
        )
    if grain:
        # The grain is a bound parameter, not a literal spliced into SQL.
        return sql.SQL("date_trunc(%s, {})").format(col)
    return col


def _measure_expr(entity: Entity, name: str) -> sql.Composable:
    m = entity.measures[name]
    if m.column == "*":
        return sql.SQL("COUNT(*)")
    return sql.SQL("{}({}.{})").format(
        sql.SQL(_AGG[m.agg]),
        sql.Identifier(_base_alias(entity)),
        sql.Identifier(m.column),
    )


def _filter_sql(entity: Entity, f) -> tuple[sql.Composable, list[Any]]:
    target = entity.dimensions.get(f.field) or entity.measures.get(f.field)
    if isinstance(target, Dimension) and target.via:
        alias, column = target.via.split(".", 1)
        col = sql.SQL("{}.{}").format(sql.Identifier(alias), sql.Identifier(column))
    else:
        column = getattr(target, "column", None) or f.field
        col = sql.SQL("{}.{}").format(
            sql.Identifier(_base_alias(entity)), sql.Identifier(column)
        )

    if f.op == "=":
        return sql.SQL("{} = %s").format(col), [f.value]
    if f.op == "!=":
        return sql.SQL("{} <> %s").format(col), [f.value]
    if f.op == "in":
        return sql.SQL("{} = ANY(%s)").format(col), [list(f.value)]
    if f.op == "between":
        return sql.SQL("{} BETWEEN %s AND %s").format(col), [f.value[0], f.value[1]]
    if f.op == "in_year":
        return sql.SQL("date_part('year', {}) = %s").format(col), [f.value]
    if f.op == "last_n_days":
        return (
            sql.SQL("{} >= CURRENT_DATE - make_interval(days => %s)").format(col),
            [f.value],
        )
    raise ValueError(f"unhandled filter op {f.op!r}")   # pragma: no cover


def compile_query(q: SemanticQuery, layer: Layer) -> CompiledQuery:
    entity = validate_query(q, layer)
    base = _base_alias(entity)

    select: list[sql.Composable] = []
    group_by: list[sql.Composable] = []   # ordinals; dimensions lead the SELECT
    params: list[Any] = []
    columns: list[str] = []
    kinds: dict[str, ColumnKind] = {}
    joins_needed: list[str] = []

    # Dimensions first, so the output column order is stable and readable.
    for ref in q.dimensions:
        dim = entity.dimensions[ref.field]
        if dim.via:
            alias = dim.via.split(".", 1)[0]
            if alias not in joins_needed:
                joins_needed.append(alias)
        expr = _dimension_expr(entity, ref.field, dim, ref.grain)
        if ref.grain:
            params.append(_GRAIN_LABEL[ref.grain])
        select.append(sql.SQL("{} AS {}").format(expr, sql.Identifier(ref.field)))
        # Grouped by ordinal, not by restating the expression: a date_trunc
        # whose grain is a bound parameter is not expression-equal to the
        # same call with a second placeholder, and Postgres rejects it.
        group_by.append(sql.SQL(str(len(group_by) + 1)))
        columns.append(ref.field)
        kinds[ref.field] = "temporal" if dim.type == "date" else "nominal"

    for name in q.measures:
        select.append(
            sql.SQL("{} AS {}").format(_measure_expr(entity, name), sql.Identifier(name))
        )
        columns.append(name)
        kinds[name] = "quantitative"

    # Filters may pull in a join no dimension needed.
    where: list[sql.Composable] = []
    where_params: list[Any] = []
    for f in q.filters:
        target = entity.dimensions.get(f.field)
        if target is not None and target.via:
            alias = target.via.split(".", 1)[0]
            if alias not in joins_needed:
                joins_needed.append(alias)
        clause, vals = _filter_sql(entity, f)
        where.append(clause)
        where_params.extend(vals)

    # Only the joins actually reached are emitted.
    join_sql: list[sql.Composable] = []
    for alias in joins_needed:
        j = entity.joins[alias]
        join_sql.append(
            sql.SQL(" LEFT JOIN {} AS {} ON ").format(_table_ref(j.to), sql.Identifier(alias))
            + sql.SQL(j.condition)          # layer-authored, never model-authored
        )

    stmt = sql.SQL("SELECT ") + sql.SQL(", ").join(select)
    stmt += sql.SQL(" FROM {} AS {}").format(_table_ref(entity.table), sql.Identifier(base))
    for js in join_sql:
        stmt += js

    if where:
        stmt += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where)
        params.extend(where_params)

    if group_by:
        stmt += sql.SQL(" GROUP BY ") + sql.SQL(", ").join(group_by)

    if q.order_by:
        parts = [
            sql.SQL("{} {}").format(
                sql.Identifier(ob.field),
                sql.SQL("DESC" if ob.dir == "desc" else "ASC"),
            )
            for ob in q.order_by
        ]
        stmt += sql.SQL(" ORDER BY ") + sql.SQL(", ").join(parts)

    stmt += sql.SQL(" LIMIT %s")
    params.append(min(q.limit, MAX_ROWS))

    return CompiledQuery(
        sql=stmt.as_string(None),
        params=params,
        columns=columns,
        column_kinds=kinds,
        entity=entity,
        query=q,
        joins_used=joins_needed,
    )


def data_max_ts_sql(entity: Entity) -> tuple[str, list[Any]] | None:
    """Kept out of the main query so it cannot perturb the GROUP BY."""
    if not entity.time_column:
        return None
    stmt = sql.SQL("SELECT MAX({}) FROM {}").format(
        sql.Identifier(entity.time_column), _table_ref(entity.table)
    )
    return stmt.as_string(None), []
