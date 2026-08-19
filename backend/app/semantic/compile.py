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

from ..layer.models import Derived, Dimension, Entity, Layer
from .query import MeasureRef, SemanticQuery
from .validate import validate_query

MAX_ROWS = 10_000

# `ratio` is arithmetic over two aggregates and resolves in the grouped
# SELECT. The rest need to see neighbouring rows, which means a window --
# and a window means an outer layer, for a concrete reason: a window's
# ORDER BY cannot use output ordinals, and `date_trunc($2, col)` is not
# expression-equal to the `date_trunc($1, col)` in the GROUP BY, so
# Postgres rejects it as ungrouped. Wrapping sidesteps that entirely and
# reads far better in the SQL panel besides.
_WINDOWED = {"percent_of_total", "previous_period", "period_change",
             "period_change_pct", "cumulative", "moving_average", "rank"}


def _needs_window(ref) -> bool:
    return ref.transform in _WINDOWED

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
    # Coordinates, when the query groups by the dimension they belong to.
    # Kept out of `columns` and `column_kinds` on purpose: they are not
    # measures, and counting them as such would change the measure count,
    # which is what picks the chart.
    geo_columns: list[str] = field(default_factory=list)


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


def _base_measure_expr(entity: Entity, name: str) -> sql.Composable:
    m = entity.measures[name]
    if m.column == "*":
        return sql.SQL("COUNT(*)")
    return sql.SQL("{}({}.{})").format(
        sql.SQL(_AGG[m.agg]),
        sql.Identifier(_base_alias(entity)),
        sql.Identifier(m.column),
    )


def _formula_sql(entity: Entity, formula: str) -> sql.Composable:
    """A declared formula, expanded so each operand becomes its own
    aggregate.

    This is where "a ratio of sums is not a sum of ratios" is enforced in
    code rather than in a comment: `water / (oil + water)` becomes
    `SUM(water_bbl) / NULLIF(SUM(oil_bbl) + SUM(water_bbl), 0)`, never
    `SUM(water_bbl / (oil_bbl + water_bbl))`. Every division gets a NULLIF
    so an empty denominator yields NULL rather than raising -- one bad
    grouping should blank a point, not fail the card.
    """
    import ast

    ops = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}

    def walk(node: ast.AST) -> sql.Composable:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Name):
            return _base_measure_expr(entity, node.id)
        if isinstance(node, ast.Constant):
            return sql.SQL("{}").format(sql.Literal(node.value))
        if isinstance(node, ast.UnaryOp):
            sign = "-" if isinstance(node.op, ast.USub) else "+"
            return sql.SQL("({}{})").format(sql.SQL(sign), walk(node.operand))
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Div):
                return sql.SQL("({} / NULLIF({}, 0))").format(
                    walk(node.left), walk(node.right))
            return sql.SQL("({} {} {})").format(
                walk(node.left), sql.SQL(ops[type(node.op)]), walk(node.right))
        raise ValueError(f"unhandled formula node {type(node).__name__}")

    return walk(ast.parse(formula, mode="eval"))


def _grouped_measure_expr(entity: Entity, ref: MeasureRef) -> sql.Composable:
    """The inner, pre-window expression for one measure reference.

    Derived measures and `ratio` are pure arithmetic over aggregates, so
    they resolve here. Everything else in the Transform enum needs to see
    neighbouring rows and is applied in the outer layer.
    """
    if ref.transform == "ratio":
        return sql.SQL("({} / NULLIF({}, 0))").format(
            _base_measure_expr(entity, ref.name),
            _base_measure_expr(entity, ref.per),
        )
    target = entity.measure(ref.name)
    if isinstance(target, Derived):
        return _formula_sql(entity, target.formula)
    return _base_measure_expr(entity, ref.name)


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


def _window_expr(ref: MeasureRef, temporal: list[str],
                 nominal: list[str]) -> tuple[sql.Composable, list[Any]]:
    """One transformed measure, expressed over the grouped result.

    Time-series transforms partition by every non-temporal dimension and
    order along the temporal one, so a per-region series steps through its
    own months rather than through the interleaved rows of all regions --
    the single most likely way this could be quietly wrong.
    """
    col = sql.Identifier(ref.name)
    part = sql.SQL("")
    if nominal:
        part = sql.SQL("PARTITION BY {} ").format(
            sql.SQL(", ").join(sql.Identifier(n) for n in nominal))
    order = sql.SQL("ORDER BY {}").format(
        sql.SQL(", ").join(sql.Identifier(t) for t in temporal)) if temporal else sql.SQL("")
    over = sql.SQL("OVER ({}{})").format(part, order)
    lag = sql.SQL("LAG({}) {}").format(col, over)

    if ref.transform == "percent_of_total":
        # Share within the period when there is one, otherwise across the
        # whole result. Per-period is nearly always what "share by region
        # over time" means.
        scope = (sql.SQL("PARTITION BY {}").format(
            sql.SQL(", ").join(sql.Identifier(t) for t in temporal))
            if temporal else sql.SQL(""))
        return sql.SQL("({} / NULLIF(SUM({}) OVER ({}), 0))").format(
            col, col, scope), []
    if ref.transform == "previous_period":
        return lag, []
    if ref.transform == "period_change":
        return sql.SQL("({} - {})").format(col, lag), []
    if ref.transform == "period_change_pct":
        return sql.SQL("(({} - {}) / NULLIF({}, 0))").format(col, lag, lag), []
    if ref.transform == "cumulative":
        return sql.SQL("SUM({}) {}").format(col, over), []
    if ref.transform == "moving_average":
        return sql.SQL(
            "AVG({}) OVER ({}{} ROWS BETWEEN %s PRECEDING AND CURRENT ROW)"
        ).format(col, part, order), [ref.window - 1]
    if ref.transform == "rank":
        scope = sql.SQL("PARTITION BY {} ").format(
            sql.SQL(", ").join(sql.Identifier(t) for t in temporal)) if temporal else sql.SQL("")
        return sql.SQL("RANK() OVER ({}ORDER BY {} DESC)").format(scope, col), []
    raise ValueError(f"unhandled transform {ref.transform!r}")   # pragma: no cover


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

    # Measures that need a window are selected untransformed here and wrapped
    # in the outer layer. A measure asked for both plainly and transformed
    # appears once inside and twice outside.
    windowed = [m for m in q.measures if _needs_window(m)]
    inner_names: list[str] = []
    for ref in q.measures:
        name = ref.name if _needs_window(ref) else ref.output_name
        if name in inner_names:
            continue
        inner_names.append(name)
        expr = (_base_measure_expr(entity, ref.name) if _needs_window(ref)
                else _grouped_measure_expr(entity, ref))
        select.append(sql.SQL("{} AS {}").format(expr, sql.Identifier(name)))

    for ref in q.measures:
        columns.append(ref.output_name)
        kinds[ref.output_name] = "quantitative"

    # Coordinates ride along whenever the query groups by the dimension
    # they describe. The chart builder cannot ask for them -- it runs after
    # compilation -- and the model must never name them, so the only place
    # this decision can live is here, keyed off the grouping.
    #
    # MIN rather than a bare column because the query is grouped: one well
    # has one location, so MIN(latitude) is that location, and Postgres
    # requires an aggregate either way.
    geo_columns: list[str] = []
    geo = entity.geo
    if geo is not None and any(r.field == geo.of for r in q.dimensions):
        for ref in (geo.lat, geo.lon):
            alias, column = (ref.split(".", 1) if "." in ref
                             else (base, ref))
            if alias != base and alias not in joins_needed:
                joins_needed.append(alias)
            select.append(sql.SQL("MIN({}.{}) AS {}").format(
                sql.Identifier(alias), sql.Identifier(column),
                sql.Identifier(column)))
            geo_columns.append(column)

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

    # A window has to see the whole grouped result to be right: a running
    # total over an arbitrary hundred rows is not a running total. So the
    # inner layer carries only the safety cap and the asker's limit moves
    # outside. Without transforms this is the same single statement it
    # always was, byte for byte.
    if windowed:
        stmt += sql.SQL(" LIMIT %s")
        params.append(MAX_ROWS)

        temporal = [c for c in columns if kinds.get(c) == "temporal"]
        nominal = [c for c in columns if kinds.get(c) == "nominal"]

        outer: list[sql.Composable] = [sql.Identifier(c) for c in columns
                                       if kinds.get(c) != "quantitative"]
        outer.extend(sql.Identifier(c) for c in geo_columns)
        outer_params: list[Any] = []
        for ref in q.measures:
            if _needs_window(ref):
                expr, extra = _window_expr(ref, temporal, nominal)
                outer_params.extend(extra)
            else:
                expr = sql.Identifier(ref.output_name)
            outer.append(sql.SQL("{} AS {}").format(expr,
                                                    sql.Identifier(ref.output_name)))

        stmt = (sql.SQL("SELECT ") + sql.SQL(", ").join(outer)
                + sql.SQL(" FROM (") + stmt + sql.SQL(") AS grouped"))
        # Placeholders bind by position, and the outer SELECT is written
        # before the subquery it wraps -- so its parameters lead. Appending
        # them would misbind only `moving_average`, the one transform that
        # carries a parameter, which is exactly the kind of bug that ships.
        params = outer_params + params

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
        geo_columns=geo_columns,
    )


def data_max_ts_sql(entity: Entity) -> tuple[str, list[Any]] | None:
    """Kept out of the main query so it cannot perturb the GROUP BY."""
    if not entity.time_column:
        return None
    stmt = sql.SQL("SELECT MAX({}) FROM {}").format(
        sql.Identifier(entity.time_column), _table_ref(entity.table)
    )
    return stmt.as_string(None), []
