"""Compiled query + rows -> Vega-Lite spec, by rule.

The design doc makes the plain-English restatement deterministic because
"a second thing that can lie is not a safeguard". The same argument
applies here and is why the model does not author specs.

Vega-Lite carries its own aggregation layer: a model-authored spec can
emit {"field": "net_gain", "aggregate": "mean"} over rows the compiler
already SUM-ed and grouped. Every field in such a spec exists in the SQL
output, so a field-membership cross-check passes clean while the header
says "Sum of net gain" and the chart shows means. So no encoding here
ever carries an `aggregate` key, and channels are assigned by rule from
the compiler's own column kinds -- never inferred from the data.

The model's only input is a nullable chart_hint, applied only where it
fits the shape.

-- Two invariants deliberately bent, and why ---------------------------

1. This used to be a pure function of the *query*. It now also takes the
   *rows*, because three rules cannot be decided without them: whether a
   pie has too many slices or a negative value, whether a third dimension
   has few enough distinct values to face, and whether two measures span
   enough categories to want a scatter. Each is a fact about the data that
   no amount of layer authoring can supply. Encoding *types* still come
   from column_kinds and are never sniffed from values -- that part holds.

2. A chart may now show a row the SQL never returned. Above eight
   categories a pie keeps the top seven and collapses the rest into
   "Other", which is a standard idiom and still a divergence: the SQL
   panel lists twenty regions, the chart shows eight. That is why the
   collapse returns a `note` the restatement is obliged to print, and why
   `chart_rows` is separate from the untouched result set.

Neither should be bent further without a fresh decision.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from ..layer.models import Derived
from .compile import CompiledQuery
from .query import ChartHint

SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"

ROWS = "table"                 # the named dataset the frontend fills

MAX_SLICES = 8                 # seven real categories plus Other
MAX_FACETS = 8                 # above this a small multiple is a wall
MAX_SERIES = 8                 # above this a colour legend is a lookup table
MAX_TREND_SERIES = 12          # 9–12 use nearest-series hover; beyond is spaghetti
SCATTER_THRESHOLD = 30         # below this a grouped bar still reads
MAX_CATEGORIES = 24            # beyond this labels become an index, not a chart
MAX_MATRIX_CELLS = 144         # a larger heatmap becomes an unlabeled pixel wall
MIN_RELATIONSHIP_POINTS = 3    # fewer points cannot communicate a relationship
OTHER = "Other"

# Which hints are meaningful for which shape. A hint outside its row is
# rejected rather than silently disagreeing with the chart.
HINT_REQUIREMENTS: dict[str, str] = {
    "line": "temporal",
    "area": "temporal",
    "bar": "any",
    "point": "any",
    "heatmap": "two_nominal",
    "big_number": "no_dimensions",
    "pie": "one_nominal",
    "donut": "one_nominal",
    "stacked_bar": "two_dimensions",
    "normalised_bar": "two_dimensions",
    "scatter": "two_measures",
    "bubble": "three_measures",
    "map": "geo",
}

# Transforms whose output is a proportion, and should be drawn as one.
_SHARE = {"percent_of_total", "period_change_pct"}
# Transforms whose output is signed and centred on zero.
_SIGNED = {"period_change", "period_change_pct"}


@dataclass
class ChartResult:
    spec: dict[str, Any]
    chart_type: str
    hint_rejected: bool = False
    # Populated only when the builder changed the data the chart draws.
    # The payload's own `rows` stays untouched so the SQL panel and the row
    # count remain literally true.
    chart_rows: list[dict[str, Any]] | None = None
    note: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)


def _kinds(compiled: CompiledQuery) -> tuple[list[str], list[str], list[str]]:
    temporal = [c for c in compiled.columns if compiled.column_kinds[c] == "temporal"]
    nominal = [c for c in compiled.columns if compiled.column_kinds[c] == "nominal"]
    quantitative = [c for c in compiled.columns
                    if compiled.column_kinds[c] == "quantitative"]
    return temporal, nominal, quantitative


def _enc(field_: str, kind: str, title: str | None = None,
         **extra: Any) -> dict[str, Any]:
    """One encoding channel. Deliberately has no `aggregate` parameter --
    there is no code path that can add one."""
    out: dict[str, Any] = {"field": field_, "type": kind}
    if title:
        out["title"] = title
    out.update(extra)
    return out


def _labels(compiled: CompiledQuery) -> dict[str, str]:
    """Output column name -> human label, transforms included."""
    entity = compiled.entity
    out = {k: d.label for k, d in entity.dimensions.items()}
    for ref in compiled.query.measures:
        target = entity.measure(ref.name)
        base = target.label if target else ref.name
        if ref.transform == "ratio":
            other = entity.measure(ref.per)
            out[ref.output_name] = f"{base} per {other.label if other else ref.per}"
        elif ref.transform == "moving_average":
            out[ref.output_name] = f"{base}, {ref.window}-period average"
        elif ref.transform:
            out[ref.output_name] = f"{base} ({ref.transform.replace('_', ' ')})"
        else:
            out[ref.output_name] = base
    return out


def _ref_for(compiled: CompiledQuery, column: str):
    for ref in compiled.query.measures:
        if ref.output_name == column:
            return ref
    return None


def _number_format(compiled: CompiledQuery, column: str) -> dict[str, Any]:
    """A proportion is not a volume. Drawing a share on a raw axis is
    technically correct and reads as noise."""
    ref = _ref_for(compiled, column)
    if ref is not None and ref.transform in _SHARE:
        return {"axis": {"format": ".1%"}}
    return {"axis": {"format": "~s"}}


def _display_format(compiled: CompiledQuery, column: str) -> str:
    ref = _ref_for(compiled, column)
    return ".1%" if ref is not None and ref.transform in _SHARE else "~s"


def _tooltip_enc(field_: str, kind: str, title: str | None = None,
                 *, number_format: str | None = None) -> dict[str, Any]:
    """Explicit, stable tooltip formatting rather than Vega's raw datum."""
    extra: dict[str, Any] = {}
    if kind == "quantitative":
        extra["format"] = number_format or ",.2f"
    elif kind == "temporal":
        extra["format"] = "%b %d, %Y"
    return _enc(field_, kind, title, **extra)


def _tooltip_from_encoding(encoding: dict[str, Any]) -> dict[str, Any]:
    axis = encoding.get("axis")
    axis_format = axis.get("format") if isinstance(axis, dict) else None
    number_format = ".1%" if axis_format == ".1%" else None
    return _tooltip_enc(
        encoding["field"], encoding.get("type", "nominal"), encoding.get("title"),
        number_format=number_format)


def _measure_tooltip(compiled: CompiledQuery, field_: str,
                     title: str | None = None) -> dict[str, Any]:
    return _tooltip_enc(
        field_, "quantitative", title,
        number_format=(".1%" if _display_format(compiled, field_) == ".1%"
                       else ",.2f"),
    )


def _series_values(rows: list[dict[str, Any]], field_: str) -> list[str]:
    values = {str(row[field_]) for row in rows if row.get(field_) is not None}
    return sorted(values)


def _bar_size(rows: list[dict[str, Any]], dimension: str) -> int:
    count = max(1, _distinct(rows, dimension)) if rows else 10
    return max(8, min(22, 180 // count))


def _sort_for_horizontal(compiled: CompiledQuery, measure: str) -> str:
    for ob in compiled.query.order_by:
        if ob.field == measure:
            return "-x" if ob.dir == "desc" else "x"
    return "-x"


def _measure_header(measures: list[str], labels: dict[str, str]) -> dict[str, Any]:
    expression = " : ".join(
        f"datum.label === {json.dumps(measure)} ? {json.dumps(labels.get(measure, measure))}"
        for measure in measures
    ) + " : datum.label"
    return {"title": None, "labelExpr": expression, "labelFontSize": 11,
            "labelFontWeight": 600, "labelPadding": 8}


def _kpi_unit(
    compiled: CompiledQuery,
    measure: str,
    dimensions: list[str],
    labels: dict[str, str],
) -> dict[str, Any]:
    tooltip = [
        _tooltip_enc(dimension, compiled.column_kinds[dimension], labels.get(dimension))
        for dimension in dimensions
    ]
    tooltip.append(_measure_tooltip(compiled, measure, labels.get(measure)))
    layers: list[dict[str, Any]] = [{
        "mark": {"type": "text", "fontSize": 42, "fontWeight": 650,
                 "dy": -8 if dimensions else 0},
        "encoding": {
            "text": _enc(measure, "quantitative", labels.get(measure),
                         format=_display_format(compiled, measure)),
            "tooltip": tooltip,
        },
    }]
    for index, dimension in enumerate(dimensions):
        kind = compiled.column_kinds[dimension]
        layers.append({
            "mark": {"type": "text", "fontSize": 11, "fontWeight": 500,
                     "dy": 26 + index * 16},
            "encoding": {
                "text": _enc(
                    dimension,
                    kind,
                    labels.get(dimension),
                    **({"format": "%b %d, %Y"} if kind == "temporal" else {}),
                ),
            },
        })
    return {"layer": layers}


def _kpi(
    compiled: CompiledQuery,
    dimensions: list[str],
    measures: list[str],
    labels: dict[str, str],
) -> ChartResult:
    spec: dict[str, Any] = {
        "$schema": SCHEMA,
        "data": {"name": ROWS},
        "usermeta": {"presentation": "kpi"},
    }
    units = [_kpi_unit(compiled, measure, dimensions, labels) for measure in measures]
    if len(units) == 1:
        spec.update(units[0])
    else:
        spec["hconcat"] = [
            {
                **unit,
                "width": 150,
                "height": 90,
                "title": {"text": labels.get(measure, measure), "anchor": "middle",
                          "fontSize": 11, "fontWeight": 600},
            }
            for measure, unit in zip(measures, units)
        ]
        spec["spacing"] = 20
    return ChartResult(spec, "big_number")


def _horizontal_bar(
    compiled: CompiledQuery,
    rows: list[dict[str, Any]],
    dimension: str,
    measure: str,
    labels: dict[str, str],
    *,
    mark: str = "bar",
) -> dict[str, Any]:
    encoding = {
        "x": _enc(measure, "quantitative", labels.get(measure),
                  **_number_format(compiled, measure)),
        "y": _enc(dimension, "nominal", labels.get(dimension),
                  sort=_sort_for_horizontal(compiled, measure),
                  scale={"paddingInner": 0.38, "paddingOuter": 0.18}),
        "tooltip": [
            _tooltip_enc(dimension, "nominal", labels.get(dimension)),
            _measure_tooltip(compiled, measure, labels.get(measure)),
        ],
    }
    if _is_signed(compiled, measure):
        encoding["x"]["scale"] = {"zero": True}
        encoding["color"] = {
            "condition": {"test": f"datum['{measure}'] < 0", "value": "#b4611a"},
            "value": "#1f6f63",
        }
    return {
        "usermeta": {
            "presentation": "ranked",
            "idealHeight": min(420, max(108, (_distinct(rows, dimension) or 1) * 42)),
        },
        "mark": {
            "type": _mark(mark),
            "tooltip": True,
            **({"cornerRadiusEnd": 2, "size": _bar_size(rows, dimension)}
               if _mark(mark) == "bar" else {"size": 70}),
        },
        "encoding": encoding,
    }


def _trend_mark(mark: str) -> dict[str, Any]:
    if mark == "area":
        return {"type": "area", "opacity": 0.18,
                "line": {"strokeWidth": 2.2}}
    return {"type": "line", "strokeWidth": 2.2}


def _shared_tooltip_rows(
    rows: list[dict[str, Any]],
    *,
    x_field: str,
    series_field: str,
    series_values: list[str],
    value_fields: list[str],
    group_fields: list[str],
) -> tuple[list[dict[str, Any]] | None, list[list[str]]]:
    """Prepare wide hover values without asking Vega to aggregate.

    SQL grouping should make each group/x/series tuple unique. If it does
    not, a shared tooltip would need an aggregation policy the query never
    requested, so the caller falls back to nearest-series hover instead.
    """
    lookup: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (*[row.get(field) for field in group_fields],
               row.get(x_field), str(row.get(series_field)))
        if key in lookup:
            return None, []
        lookup[key] = row

    generated = [
        [f"__tooltip_{measure_index}_{series_index}"
         for series_index in range(len(series_values))]
        for measure_index in range(len(value_fields))
    ]
    enriched: list[dict[str, Any]] = []
    for row in rows:
        group = (*[row.get(field) for field in group_fields], row.get(x_field))
        item = dict(row)
        for measure_index, value_field in enumerate(value_fields):
            for series_index, series in enumerate(series_values):
                source = lookup.get((*group, series))
                item[generated[measure_index][series_index]] = (
                    source.get(value_field) if source is not None else None
                )
        enriched.append(item)
    return enriched, generated


def _interactive_temporal(
    *,
    mark: str,
    x: dict[str, Any],
    y: dict[str, Any],
    rows: list[dict[str, Any]],
    color: dict[str, Any] | None = None,
    series_field: str | None = None,
    series_values: list[str] | None = None,
    series_titles: dict[str, str] | None = None,
    tooltip_value_fields: list[str] | None = None,
    fold_field: str | None = None,
    group_fields: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    """A temporal unit navigable across its full vertical plotting area.

    At eight series or fewer the rule owns a shared tooltip prepared from
    unique server-side rows. Vega never pivots or re-aggregates the SQL
    result. Dense charts keep the rule but show only the nearest series.
    """
    values = series_values if series_values is not None else (
        _series_values(rows, series_field) if series_field else [])
    shared = series_field is None or len(values) <= MAX_SERIES
    chart_rows: list[dict[str, Any]] | None = None
    generated: list[list[str]] = []
    if shared and series_field is not None:
        value_fields = tooltip_value_fields or [str(y["field"])]
        chart_rows, generated = _shared_tooltip_rows(
            rows,
            x_field=str(x["field"]),
            series_field=series_field,
            series_values=values,
            value_fields=value_fields,
            group_fields=group_fields or [],
        )
        if chart_rows is None:
            shared = False
    base_encoding: dict[str, Any] = {"y": y}
    if color is not None:
        base_encoding["color"] = color

    selected_point = {
        "transform": [{"filter": {"param": "hover", "empty": False}}],
        "mark": {"type": "point", "filled": True, "size": 54},
        "encoding": base_encoding,
    }
    layers: list[dict[str, Any]] = [
        {"mark": _trend_mark(mark), "encoding": base_encoding},
    ]

    if shared:
        layers.append(selected_point)
        tooltip = [_tooltip_from_encoding(x)]
        transforms: list[dict[str, Any]] = []
        if series_field is None:
            tooltip.append(_tooltip_from_encoding(y))
        else:
            titles = series_titles or {}
            if fold_field and len(generated) > 1:
                for series_index, value in enumerate(values):
                    output = f"__tooltip_value_{series_index}"
                    expression = " : ".join(
                        f"datum[{json.dumps(fold_field)}] === {json.dumps(field)} "
                        f"? datum[{json.dumps(generated[index][series_index])}]"
                        for index, field in enumerate(tooltip_value_fields or [])
                    ) + " : null"
                    transforms.append({"calculate": expression, "as": output})
                    tooltip.append(_tooltip_enc(
                        output, "quantitative", titles.get(value, value),
                        number_format=(
                            ".1%" if isinstance(y.get("axis"), dict)
                            and y["axis"].get("format") == ".1%" else ",.2f"
                        ),
                    ))
            else:
                tooltip.extend(
                    _tooltip_enc(
                        generated[0][index], "quantitative",
                        titles.get(value, value),
                        number_format=(
                            ".1%" if isinstance(y.get("axis"), dict)
                            and y["axis"].get("format") == ".1%" else ",.2f"
                        ),
                    )
                    for index, value in enumerate(values)
                )

        rule: dict[str, Any] = {
            "mark": {"type": "rule"},
            "params": [{
                "name": "hover",
                "select": {
                    "type": "point",
                    "fields": [x["field"]],
                    "nearest": True,
                    "on": "pointerover",
                    "clear": "pointerout",
                },
            }],
            "encoding": {
                "opacity": {
                    "condition": {
                        "param": "hover",
                        "empty": False,
                        "value": 0.62,
                    },
                    "value": 0,
                },
                "tooltip": tooltip,
            },
        }
        if transforms:
            rule["transform"] = transforms
        layers.append(rule)
    else:
        tooltip = [_tooltip_from_encoding(x)]
        if color is not None:
            tooltip.append(_tooltip_from_encoding(color))
        tooltip.append(_tooltip_from_encoding(y))
        layers.extend([
            {
                "mark": {"type": "point", "opacity": 0, "size": 140},
                "params": [{
                    "name": "hover",
                    "select": {
                        "type": "point",
                        "fields": [x["field"], series_field],
                        "nearest": True,
                        "on": "pointerover",
                        "clear": "pointerout",
                    },
                }],
                "encoding": {**base_encoding, "tooltip": tooltip},
            },
            {
                "transform": [{"filter": {"param": "hover", "empty": False}}],
                "mark": {"type": "rule", "opacity": 0.62},
            },
            selected_point,
        ])

    return {"encoding": {"x": x}, "layer": layers}, chart_rows


def _measure_facets(
    compiled: CompiledQuery,
    rows: list[dict[str, Any]],
    dimension: str,
    dimension_kind: str,
    measures: list[str],
    labels: dict[str, str],
    mark: str,
) -> ChartResult:
    common_format = _display_format(compiled, measures[0])
    tooltip_format = ".1%" if common_format == ".1%" else ",.2f"
    spec: dict[str, Any] = {
        "$schema": SCHEMA,
        "data": {"name": ROWS},
        "transform": [{"fold": measures, "as": ["measure", "value"]}],
        "facet": {
            "row": _enc(
                "measure", "nominal", "Measure",
                header=_measure_header(measures, labels),
            ),
        },
        "resolve": {"scale": {}},
        "usermeta": {"presentation": "facets"},
    }
    if dimension_kind == "temporal":
        x = _enc(dimension, "temporal", labels.get(dimension))
        y = _enc("value", "quantitative", "Value", axis={"format": common_format})
        if mark in ("line", "area"):
            inner, _ = _interactive_temporal(mark=mark, x=x, y=y, rows=rows)
        else:
            inner = {
                "mark": {"type": _mark(mark), "tooltip": True,
                         **({"cornerRadiusEnd": 2} if _mark(mark) == "bar" else {})},
                "encoding": {
                    "x": x,
                    "y": y,
                    "tooltip": [
                        _tooltip_enc(dimension, "temporal", labels.get(dimension)),
                        _tooltip_enc(
                            "value", "quantitative", "Value",
                            number_format=tooltip_format,
                        ),
                    ],
                },
            }
        spec["resolve"]["scale"] = {"y": "independent", "x": "shared"}
        chart_type = f"faceted_{mark}"
    else:
        inner = _horizontal_bar(
            compiled, rows, dimension, "value", {**labels, "value": "Value"}, mark=mark)
        inner.pop("usermeta", None)
        inner["encoding"]["x"]["axis"] = {"format": common_format}
        inner["encoding"]["tooltip"][-1]["format"] = tooltip_format
        spec["resolve"]["scale"] = {"x": "independent", "y": "shared"}
        chart_type = f"faceted_{_mark(mark)}"
    spec["spec"] = inner
    return ChartResult(spec, chart_type)


def _is_signed(compiled: CompiledQuery, column: str) -> bool:
    ref = _ref_for(compiled, column)
    return ref is not None and ref.transform in _SIGNED


def _distinct(rows: list[dict[str, Any]], column: str) -> int:
    return len({r.get(column) for r in rows})


def _relationship_point_count(rows: list[dict[str, Any]],
                              measures: list[str]) -> int:
    """How many distinct points Vega can actually draw.

    Row count overstates the evidence when measures are null/non-finite or
    multiple categories land on the same coordinates.
    """
    points: set[tuple[float, ...]] = set()
    for row in rows:
        values: list[float] = []
        for measure in measures:
            value = row.get(measure)
            if not isinstance(value, (int, float)):
                break
            numeric = float(value)
            if not math.isfinite(numeric):
                break
            values.append(numeric)
        else:
            # Bubble size must be finite to draw, but it does not create a
            # new spatial observation. Several sizes at one x/y position
            # overlap into one usable mark and hover target.
            points.add(tuple(values[:2]))
    return len(points)


def _hint_fits(hint: ChartHint, temporal: list[str], nominal: list[str],
               quantitative: list[str], compiled: CompiledQuery) -> bool:
    need = HINT_REQUIREMENTS.get(hint)
    n_dims = len(temporal) + len(nominal)
    if need == "temporal":
        return bool(temporal)
    if need == "two_nominal":
        return len(nominal) == 2
    if need == "no_dimensions":
        return n_dims == 0
    if need == "one_nominal":
        return len(nominal) == 1 and not temporal and len(quantitative) == 1
    if need == "two_dimensions":
        return n_dims == 2 and len(quantitative) == 1
    if need == "two_measures":
        return len(quantitative) == 2 and n_dims == 1
    if need == "three_measures":
        return len(quantitative) == 3 and n_dims == 1
    if need == "geo":
        # Not "the entity has coordinates" but "this query selected them".
        # The looser test is how a map spec came to reference columns the
        # SQL never emitted, plotting every well at one default position.
        return (len(compiled.geo_columns) == 2 and len(nominal) == 1
                and len(quantitative) == 1)
    return True


# -- pie: the type most able to make this app worse ----------------------

def _pie_refusal(compiled: CompiledQuery, rows: list[dict[str, Any]],
                 dim: str, measure: str) -> str | None:
    """Why this data must not be drawn as a pie, or None.

    Three mechanical failure modes, in the order they are worth naming.
    """
    target = compiled.entity.measure(
        (_ref_for(compiled, measure) or type("", (), {"name": measure})).name)
    if target is not None and not isinstance(target, Derived) \
            and getattr(target, "agg", None) == "avg":
        # Means do not sum to a whole, so the angles are arithmetic
        # nonsense. This is the guard a model will trip, because
        # "average daily oil per well by region as a pie" is a sentence
        # someone will type and it compiles perfectly.
        return (f"{target.label} is an average, and averages do not add up "
                f"to a whole — so a pie of them would be meaningless.")

    values = [r.get(measure) for r in rows if r.get(measure) is not None]
    if any(float(v) < 0 for v in values):
        return "some values are negative, and a negative slice has no angle."

    if len(rows) >= compiled.query.limit:
        # The result stopped at the limit, so "Other" would stand for an
        # unfetched remainder. A pie is a claim about a whole; without the
        # whole, the honest move is not to draw the shape that asserts one.
        return (f"the result stopped at {compiled.query.limit} rows, so a pie "
                f"would claim to show a whole it does not have.")
    return None


def _collapse_tail(rows: list[dict[str, Any]], dim: str,
                   measure: str) -> tuple[list[dict[str, Any]], str]:
    """Top slices by value, with the rest summed into one.

    Returns the rows the chart draws and a sentence the restatement must
    print. A chart showing eight of twenty categories means something
    different from one showing all twenty, and the difference has to be
    said out loud rather than left for the reader to notice.
    """
    if len(rows) <= MAX_SLICES:
        return rows, ""

    ordered = sorted(rows, key=lambda r: float(r.get(measure) or 0), reverse=True)
    head, tail = ordered[:MAX_SLICES - 1], ordered[MAX_SLICES - 1:]
    total = sum(float(r.get(measure) or 0) for r in tail)
    collapsed = [*head, {dim: OTHER, measure: total}]
    note = (f"top {MAX_SLICES - 1} shown, {len(tail)} more grouped as {OTHER}")
    return collapsed, note


def _arc(compiled: CompiledQuery, rows: list[dict[str, Any]], dim: str,
         measure: str, labels: dict[str, str], donut: bool) -> ChartResult:
    chart_rows, note = _collapse_tail(rows, dim, measure)
    spec: dict[str, Any] = {
        "$schema": SCHEMA,
        "data": {"name": ROWS},
        "mark": {"type": "arc", "tooltip": True,
                 **({"innerRadius": 50} if donut else {})},
        "encoding": {
            "theta": _enc(measure, "quantitative", labels.get(measure),
                          stack=True),
            "color": _enc(dim, "nominal", labels.get(dim),
                          sort={"field": measure, "order": "descending"}),
            "tooltip": [_tooltip_enc(dim, "nominal", labels.get(dim)),
                        _measure_tooltip(compiled, measure, labels.get(measure))],
        },
    }
    return ChartResult(spec, "donut" if donut else "pie",
                       chart_rows=chart_rows if note else None, note=note)


def build_spec(compiled: CompiledQuery, rows: list[dict[str, Any]] | None = None,
               chart_hint: ChartHint | None = None,
               title: str = "") -> ChartResult:
    # The card header is the sole title. Keep the argument for compatibility
    # with the render boundary, but never spend plot area repeating it.
    _ = title
    rows = rows or []
    temporal, nominal, quantitative = _kinds(compiled)
    labels = _labels(compiled)

    source_dimensions = [*temporal, *nominal]
    if len(rows) == 1:
        if chart_hint == "map" and _hint_fits(
                chart_hint, temporal, nominal, quantitative, compiled):
            return _map(compiled, nominal[0], quantitative[0], labels)
        result = _kpi(compiled, source_dimensions, quantitative, labels)
        result.hint_rejected = chart_hint not in (None, "big_number")
        return result

    # A grouped column that has only one returned value is context, not a
    # visual channel. Keeping it creates one-slice pies, 100% stacks, and
    # heatmaps with a decorative axis. The restatement still names it.
    if rows:
        temporal = [column for column in temporal if _distinct(rows, column) > 1]
        nominal = [column for column in nominal if _distinct(rows, column) > 1]

    hint_rejected = False
    if chart_hint in ("scatter", "bubble") and rows:
        required = 3 if chart_hint == "bubble" else 2
        if _relationship_point_count(rows, quantitative[:required]) \
                < MIN_RELATIONSHIP_POINTS:
            chart_hint, hint_rejected = None, True
    if chart_hint is not None and not _hint_fits(
            chart_hint, temporal, nominal, quantitative, compiled):
        chart_hint, hint_rejected = None, True

    n_dims = len(temporal) + len(nominal)
    n_meas = len(quantitative)

    spec: dict[str, Any] = {"$schema": SCHEMA, "data": {"name": ROWS}}
    def done(result: ChartResult) -> ChartResult:
        result.hint_rejected = result.hint_rejected or hint_rejected
        return result

    if n_meas > 1 and len({_display_format(compiled, measure)
                           for measure in quantitative}) > 1:
        return done(ChartResult(
            {}, "unplottable",
            error=("These measures use incompatible units (percentage and "
                   "numeric). Choose measures with matching units, or place "
                   "them on separate cards."),
        ))

    # --- 3 dimensions: the third becomes a facet ------------------------
    if n_dims == 3:
        if n_meas > 1:
            return done(ChartResult(
                {}, "unplottable",
                error=("A readable chart cannot show multiple measures across "
                       "three dimensions without dropping information. Choose "
                       "one measure, or remove a dimension."),
            ))
        return done(_faceted(compiled, rows, temporal, nominal, quantitative,
                             labels))

    # --- 0 dimensions: a single number ---------------------------------
    if n_dims == 0:
        return done(_kpi(compiled, source_dimensions, quantitative, labels))

    # --- 1 dimension ----------------------------------------------------
    if n_dims == 1:
        dim = (temporal or nominal)[0]
        dim_kind = "temporal" if temporal else "nominal"

        if chart_hint in ("pie", "donut"):
            why = _pie_refusal(compiled, rows, dim, quantitative[0])
            if why:
                chart_hint, hint_rejected = None, True
                # fall through to the bar; the reason rides along
                spec["description"] = f"A pie was asked for, but {why}"
            else:
                return done(_arc(compiled, rows, dim, quantitative[0], labels,
                                 donut=chart_hint == "donut"))

        if chart_hint == "map":
            return done(_map(compiled, dim, quantitative[0], labels))

        # Two measures over many categories: a grouped bar here is two bars
        # per category and unreadable past a few dozen, while a scatter of
        # one against the other is exactly the chart the question wants.
        many = _distinct(rows, dim) > SCATTER_THRESHOLD if rows else False
        can_scatter = _relationship_point_count(rows, quantitative[:2]) \
            >= MIN_RELATIONSHIP_POINTS
        if n_meas == 2 and dim_kind == "nominal" and can_scatter \
                and (chart_hint == "scatter" or (many and chart_hint is None)):
            return done(_scatter(compiled, dim, quantitative, labels))
        if n_meas == 3 and chart_hint == "bubble":
            return done(_scatter(compiled, dim, quantitative, labels, bubble=True))

        if dim_kind == "nominal" and rows \
                and _distinct(rows, dim) > MAX_CATEGORIES:
            count = _distinct(rows, dim)
            return done(ChartResult(
                {}, "unplottable",
                error=(f"{labels.get(dim, dim)} has {count} categories. "
                       f"Show a top {MAX_CATEGORIES}, or filter this result "
                       "before charting it."),
            ))

        default = "line" if temporal else "bar"
        ref_transform = _ref_for(compiled, quantitative[0])
        if temporal and ref_transform is not None:
            if ref_transform.transform == "cumulative":
                default = "area"        # a running total is a filling quantity
            elif ref_transform.transform in _SIGNED:
                default = "bar"         # discrete signed deltas, not a trend
        mark = chart_hint or default

        if n_meas == 1:
            measure = quantitative[0]
            if dim_kind == "nominal":
                spec.update(_horizontal_bar(
                    compiled, rows, dim, measure, labels, mark=mark))
                return done(ChartResult(spec, _mark(mark)))

            x = _enc(dim, dim_kind, labels.get(dim))
            y = _enc(measure, "quantitative", labels.get(measure),
                     **_number_format(compiled, measure))
            encoding = {"x": x, "y": y}
            if _is_signed(compiled, measure):
                # Negatives must read as negative at a glance, or the chart
                # is correct and backwards. The zero baseline does that on
                # any mark; the diverging colour only works on discrete
                # ones, since a line is a single path and cannot be two
                # colours at once.
                y["scale"] = {"zero": True}
                if _mark(mark) in ("bar", "point"):
                    encoding["color"] = {
                        "condition": {"test": f"datum['{measure}'] < 0",
                                      "value": "#b4611a"},
                        "value": "#1f6f63",
                    }
            if dim_kind == "temporal" and mark in ("line", "area"):
                interaction, _ = _interactive_temporal(
                    mark=mark, x=x, y=y, rows=rows)
                spec.update(interaction)
            else:
                encoding["tooltip"] = [
                    _tooltip_enc(dim, dim_kind, labels.get(dim)),
                    _measure_tooltip(compiled, measure, labels.get(measure)),
                ]
                spec.update({
                    "mark": {"type": _mark(mark), "tooltip": True,
                             **({"cornerRadiusEnd": 2} if _mark(mark) == "bar" else {})},
                    "encoding": encoding,
                })
            return done(ChartResult(spec, mark))

        return done(_measure_facets(
            compiled, rows, dim, dim_kind, quantitative, labels, mark))

    # --- 2 dimensions ---------------------------------------------------
    return done(_two_dimensions(compiled, rows, temporal, nominal, quantitative,
                                labels, chart_hint, spec))


def _scatter(compiled: CompiledQuery, dim: str, quantitative: list[str],
             labels: dict[str, str], bubble: bool = False) -> ChartResult:
    """Two measures against each other, the dimension carried in the
    tooltip rather than on an axis. A third measure becomes size."""
    x, y = quantitative[0], quantitative[1]
    encoding = {
        "x": _enc(x, "quantitative", labels.get(x), **_number_format(compiled, x)),
        "y": _enc(y, "quantitative", labels.get(y), **_number_format(compiled, y)),
        "tooltip": [_tooltip_enc(dim, "nominal", labels.get(dim)),
                    _measure_tooltip(compiled, x, labels.get(x)),
                    _measure_tooltip(compiled, y, labels.get(y))],
    }
    if bubble:
        size = quantitative[2]
        encoding["size"] = _enc(size, "quantitative", labels.get(size))
        encoding["tooltip"].append(_measure_tooltip(compiled, size, labels.get(size)))
    return ChartResult(
        {"$schema": SCHEMA, "data": {"name": ROWS},
         "layer": [
             {"mark": {"type": "point", "filled": True, "opacity": 0.72},
              "encoding": encoding},
             {"mark": {"type": "point", "opacity": 0, "size": 180},
              "params": [{
                  "name": "hover",
                  "select": {"type": "point", "fields": [dim],
                             "nearest": True, "on": "pointerover",
                             "clear": "pointerout"},
              }],
              "encoding": encoding},
             {"transform": [{"filter": {"param": "hover", "empty": False}}],
              "mark": {"type": "point", "filled": True, "opacity": 1,
                       "stroke": "white", "strokeWidth": 1.5},
              "encoding": encoding},
         ]},
        "bubble" if bubble else "scatter")


def _map(compiled: CompiledQuery, dim: str, measure: str,
         labels: dict[str, str]) -> ChartResult:
    """Points over an outline.

    Coordinates come from the entity's geo block, which the model cannot
    see, so a map is always a rendering of a per-`of` grouping rather than
    something the grammar can be talked into. The outline is a second
    dataset, which is why the frontend must stop replacing `data` wholesale.
    """
    lat, lon = compiled.geo_columns
    return ChartResult(
        {"$schema": SCHEMA,
         "layer": [
             {"data": {"name": "outline",
                       "format": {"type": "json", "property": "features"}},
              "mark": {"type": "geoshape"}},
             {"data": {"name": ROWS},
              "mark": {"type": "circle", "opacity": 0.68,
                       "color": "#1f6f63", "stroke": "white",
                       "strokeWidth": 0.7},
              "encoding": {
                  "longitude": _enc(lon, "quantitative"),
                  "latitude": _enc(lat, "quantitative"),
                  "size": _enc(measure, "quantitative", labels.get(measure),
                               legend={"orient": "right", "direction": "vertical"}),
                  "tooltip": [_tooltip_enc(dim, "nominal", labels.get(dim)),
                              _measure_tooltip(compiled, measure, labels.get(measure))],
              }},
             {"data": {"name": ROWS},
              "mark": {"type": "circle", "opacity": 0, "size": 180},
              "params": [{
                  "name": "hover",
                  "select": {"type": "point", "fields": [dim],
                             "nearest": True, "on": "pointerover",
                             "clear": "pointerout"},
              }],
              "encoding": {
                  "longitude": _enc(lon, "quantitative"),
                  "latitude": _enc(lat, "quantitative"),
                  "tooltip": [_tooltip_enc(dim, "nominal", labels.get(dim)),
                              _measure_tooltip(compiled, measure, labels.get(measure))],
              }},
             {"data": {"name": ROWS},
              "transform": [{"filter": {"param": "hover", "empty": False}}],
              "mark": {"type": "circle", "opacity": 1, "stroke": "white",
                       "strokeWidth": 1.5, "color": "#1f6f63"},
              "encoding": {
                  "longitude": _enc(lon, "quantitative"),
                  "latitude": _enc(lat, "quantitative"),
                  "size": _enc(measure, "quantitative", labels.get(measure),
                               legend=None),
              }},
         ],
         "projection": {"type": "mercator"}},
        "map")


def _faceted(compiled: CompiledQuery, rows: list[dict[str, Any]],
             temporal: list[str], nominal: list[str], quantitative: list[str],
             labels: dict[str, str]) -> ChartResult:
    """Two dimensions in the plot, the third as small multiples.

    Which dimension goes where is a question about cardinality, and
    cardinality is a fact about the returned rows -- not something the
    layer should hand-declare and then keep in sync.

    Only the x axis tolerates many values. Colour and facet do not: a
    legend of two hundred entries is a lookup table, and two hundred panels
    is not a small multiple. So both of those channels are checked, not
    just the facet -- picking the sparsest dimension to face and letting
    the dense one land on colour would move the problem rather than catch
    it. Over the limit the card breaks and names the dimension, because
    silently dropping one leaves a chart that disagrees with its own header.
    """
    measure = quantitative[0]
    counts = {c: _distinct(rows, c) for c in nominal} if rows else \
        {c: 0 for c in nominal}
    ranked = sorted(nominal, key=lambda c: counts[c])

    if temporal:
        # The time axis takes x; both nominals must fit a small channel.
        x, x_kind, mark = temporal[0], "temporal", "line"
        facet, colour = ranked[0], ranked[1]
    else:
        # The densest nominal takes x, where many categories are survivable.
        x, x_kind, mark = ranked[-1], "nominal", "bar"
        facet, colour = ranked[0], ranked[1]

    if x_kind == "nominal" and rows and counts[x] > MAX_CATEGORIES:
        return ChartResult(
            {}, "unplottable",
            error=(f"{labels.get(x, x)} has {counts[x]} categories. "
                   f"Filter it to at most {MAX_CATEGORIES} before charting."),
        )

    for channel, column, cap in (("panels", facet, MAX_FACETS),
                                 ("colours", colour, MAX_SERIES)):
        if rows and counts[column] > cap:
            return ChartResult(
                {}, "unplottable",
                error=(f"{labels.get(column, column)} has {counts[column]} "
                       f"distinct values, and a chart cannot show "
                       f"{counts[column]} {channel}. Filter it, or ask for "
                       f"two dimensions instead of three."))

    encoding = {
        "x": _enc(x, x_kind, labels.get(x)),
        "y": _enc(measure, "quantitative", labels.get(measure),
                  **_number_format(compiled, measure)),
        "color": _enc(colour, "nominal", labels.get(colour)),
    }

    if mark == "line":
        inner, chart_rows = _interactive_temporal(
            mark="line",
            x=encoding["x"],
            y=encoding["y"],
            color=encoding["color"],
            rows=rows,
            series_field=colour,
            series_titles={value: value for value in _series_values(rows, colour)},
            group_fields=[facet],
        )
    else:
        chart_rows = None
        encoding["tooltip"] = [
            _tooltip_enc(x, x_kind, labels.get(x)),
            _tooltip_enc(colour, "nominal", labels.get(colour)),
            _measure_tooltip(compiled, measure, labels.get(measure)),
        ]
        inner = {"mark": {"type": "bar", "tooltip": True,
                          "cornerRadiusEnd": 2}, "encoding": encoding}

    return ChartResult(
        {"$schema": SCHEMA, "data": {"name": ROWS},
         "facet": {"field": facet, "type": "nominal",
                   "title": labels.get(facet), "columns": 3},
         "spec": inner},
        f"faceted_{mark}", chart_rows=chart_rows)


def _two_dimensions(compiled: CompiledQuery, rows: list[dict[str, Any]],
                    temporal: list[str],
                    nominal: list[str], quantitative: list[str],
                    labels: dict[str, str], chart_hint: ChartHint | None,
                    spec: dict[str, Any]) -> ChartResult:
    measure = quantitative[0]
    n_meas = len(quantitative)

    if chart_hint in ("stacked_bar", "normalised_bar"):
        x = temporal[0] if temporal else nominal[0]
        series = nominal[-1] if temporal else nominal[1]
        if rows and _distinct(rows, x) > MAX_CATEGORIES:
            count = _distinct(rows, x)
            return ChartResult(
                {}, "unplottable",
                error=(f"{labels.get(x, x)} has {count} categories. "
                       f"Filter it to at most {MAX_CATEGORIES} before charting."),
            )
        if rows and _distinct(rows, series) > MAX_SERIES:
            count = _distinct(rows, series)
            return ChartResult(
                {}, "unplottable",
                error=(f"{labels.get(series, series)} has {count} series. "
                       f"Filter it to at most {MAX_SERIES} before charting."),
            )
        y = _enc(measure, "quantitative", labels.get(measure),
                 stack="normalize" if chart_hint == "normalised_bar" else "zero")
        if chart_hint == "normalised_bar":
            y["axis"] = {"format": ".0%"}
        spec.update({
            "mark": {"type": "bar", "tooltip": True, "cornerRadiusEnd": 2},
            "encoding": {
                "x": _enc(x, "temporal" if temporal else "nominal", labels.get(x)),
                "y": y,
                "color": _enc(series, "nominal", labels.get(series)),
                "tooltip": [
                    _tooltip_enc(x, "temporal" if temporal else "nominal", labels.get(x)),
                    _tooltip_enc(series, "nominal", labels.get(series)),
                    _measure_tooltip(compiled, measure, labels.get(measure)),
                ],
            },
        })
        return ChartResult(spec, chart_hint)

    if temporal and nominal:
        if rows and _distinct(rows, nominal[0]) > MAX_TREND_SERIES:
            count = _distinct(rows, nominal[0])
            return ChartResult(
                {}, "unplottable",
                error=(f"{labels.get(nominal[0], nominal[0])} has {count} "
                       f"series. Filter it to at most {MAX_TREND_SERIES} before "
                       "charting."),
            )
        mark = chart_hint or "line"
        encoding = {
            "x": _enc(temporal[0], "temporal", labels.get(temporal[0])),
            "y": _enc(measure, "quantitative", labels.get(measure),
                      **_number_format(compiled, measure)),
            "color": _enc(nominal[0], "nominal", labels.get(nominal[0])),
        }
        if n_meas > 1:
            common_format = _display_format(compiled, quantitative[0])
            tooltip_format = ".1%" if common_format == ".1%" else ",.2f"
            spec["transform"] = [{"fold": quantitative, "as": ["measure", "value"]}]
            spec["facet"] = {
                "row": _enc(
                    "measure", "nominal", "Measure",
                    header=_measure_header(quantitative, labels),
                ),
            }
            folded_encoding = {
                **encoding,
                "y": _enc("value", "quantitative", "Value",
                          axis={"format": common_format}),
            }
            if mark in ("line", "area"):
                spec["spec"], chart_rows = _interactive_temporal(
                    mark=mark,
                    x=folded_encoding["x"],
                    y=folded_encoding["y"],
                    color=folded_encoding["color"],
                    rows=rows,
                    series_field=nominal[0],
                    series_titles={
                        value: value for value in _series_values(rows, nominal[0])},
                    tooltip_value_fields=quantitative,
                    fold_field="measure",
                )
            else:
                chart_rows = None
                folded_encoding["tooltip"] = [
                    _tooltip_enc(temporal[0], "temporal", labels.get(temporal[0])),
                    _tooltip_enc(nominal[0], "nominal", labels.get(nominal[0])),
                    _tooltip_enc(
                        "value", "quantitative", "Value",
                        number_format=tooltip_format,
                    ),
                ]
                spec["spec"] = {
                    "mark": {"type": _mark(mark), "tooltip": True,
                             **({"cornerRadiusEnd": 2}
                                if _mark(mark) == "bar" else {})},
                    "encoding": folded_encoding,
                }
            spec["resolve"] = {"scale": {"y": "independent", "x": "shared"}}
            spec["usermeta"] = {"presentation": "facets"}
            return ChartResult(
                spec, f"faceted_{_mark(mark)}", chart_rows=chart_rows)

        if mark in ("line", "area"):
            interaction, chart_rows = _interactive_temporal(
                mark=mark,
                x=encoding["x"],
                y=encoding["y"],
                color=encoding["color"],
                rows=rows,
                series_field=nominal[0],
                series_titles={value: value for value in _series_values(rows, nominal[0])},
            )
            spec.update(interaction)
        else:
            chart_rows = None
            encoding["tooltip"] = [
                _tooltip_enc(temporal[0], "temporal", labels.get(temporal[0])),
                _tooltip_enc(nominal[0], "nominal", labels.get(nominal[0])),
                _measure_tooltip(compiled, measure, labels.get(measure)),
            ]
            spec.update({"mark": {"type": _mark(mark), "tooltip": True,
                                  **({"cornerRadiusEnd": 2} if _mark(mark) == "bar" else {})},
                         "encoding": encoding})
        return ChartResult(spec, mark, chart_rows=chart_rows)

    # two nominals
    mark = chart_hint or "heatmap"
    if mark != "heatmap" and rows:
        category_count = _distinct(rows, nominal[0])
        if category_count > MAX_CATEGORIES:
            return ChartResult(
                {}, "unplottable",
                error=(f"{labels.get(nominal[0], nominal[0])} has "
                       f"{category_count} categories. Filter it to at most "
                       f"{MAX_CATEGORIES} before charting."),
            )
        series_count = _distinct(rows, nominal[1])
        if series_count > MAX_SERIES:
            return ChartResult(
                {}, "unplottable",
                error=(f"{labels.get(nominal[1], nominal[1])} has "
                       f"{series_count} series. Filter it to at most "
                       f"{MAX_SERIES} before charting."),
            )
    if mark == "heatmap" and rows:
        cells = _distinct(rows, nominal[0]) * _distinct(rows, nominal[1])
        if cells > MAX_MATRIX_CELLS:
            return ChartResult(
                {}, "unplottable",
                error=(f"This matrix has {cells} cells. Filter one dimension "
                       f"until it has at most {MAX_MATRIX_CELLS} cells."),
            )

    if n_meas > 1:
        common_format = _display_format(compiled, quantitative[0])
        tooltip_format = ".1%" if common_format == ".1%" else ",.2f"
        spec["transform"] = [{"fold": quantitative, "as": ["measure", "value"]}]
        spec["facet"] = {
            "row": _enc(
                "measure", "nominal", "Measure",
                header=_measure_header(quantitative, labels),
            ),
        }
        if mark == "heatmap":
            inner = {
                "mark": {"type": "rect", "tooltip": True},
                "encoding": {
                    "x": _enc(nominal[0], "nominal", labels.get(nominal[0])),
                    "y": _enc(nominal[1], "nominal", labels.get(nominal[1])),
                    "color": _enc(
                        "value", "quantitative", "Value",
                        legend={"format": common_format},
                    ),
                    "tooltip": [
                        _tooltip_enc(nominal[0], "nominal", labels.get(nominal[0])),
                        _tooltip_enc(nominal[1], "nominal", labels.get(nominal[1])),
                        _tooltip_enc(
                            "value", "quantitative", "Value",
                            number_format=tooltip_format,
                        ),
                    ],
                },
            }
            spec["resolve"] = {"scale": {"color": "independent"}}
            chart_type = "faceted_heatmap"
        else:
            inner = {
                "mark": {"type": _mark(mark), "tooltip": True,
                         **({"cornerRadiusEnd": 2,
                            "size": _bar_size(rows, nominal[0])}
                            if _mark(mark) == "bar" else {})},
                "encoding": {
                    "x": _enc("value", "quantitative", "Value",
                              axis={"format": common_format}),
                    "y": _enc(nominal[0], "nominal", labels.get(nominal[0]),
                              sort="-x"),
                    "color": _enc(nominal[1], "nominal", labels.get(nominal[1])),
                    "yOffset": _enc(nominal[1], "nominal"),
                    "tooltip": [
                        _tooltip_enc(nominal[0], "nominal", labels.get(nominal[0])),
                        _tooltip_enc(nominal[1], "nominal", labels.get(nominal[1])),
                        _tooltip_enc(
                            "value", "quantitative", "Value",
                            number_format=tooltip_format,
                        ),
                    ],
                },
            }
            spec["resolve"] = {"scale": {"x": "independent", "y": "shared"}}
            chart_type = f"faceted_{_mark(mark)}"
        spec["spec"] = inner
        spec["usermeta"] = {"presentation": "facets"}
        return ChartResult(spec, chart_type)

    if mark == "heatmap":
        spec.update({
            "mark": {"type": "rect", "tooltip": True},
            "encoding": {
                "x": _enc(nominal[0], "nominal", labels.get(nominal[0])),
                "y": _enc(nominal[1], "nominal", labels.get(nominal[1])),
                "color": _enc(measure, "quantitative", labels.get(measure)),
                "tooltip": [
                    _tooltip_enc(nominal[0], "nominal", labels.get(nominal[0])),
                    _tooltip_enc(nominal[1], "nominal", labels.get(nominal[1])),
                    _measure_tooltip(compiled, measure, labels.get(measure)),
                ],
            },
        })
        return ChartResult(spec, "heatmap")

    spec.update({
        "mark": {"type": _mark(mark), "tooltip": True,
                 **({"cornerRadiusEnd": 2,
                    "size": _bar_size(rows, nominal[0])}
                    if _mark(mark) == "bar" else {})},
        "encoding": {
            "x": _enc(measure, "quantitative", labels.get(measure),
                      **_number_format(compiled, measure)),
            "y": _enc(nominal[0], "nominal", labels.get(nominal[0]),
                      sort=_sort_for_horizontal(compiled, measure)),
            "color": _enc(nominal[1], "nominal", labels.get(nominal[1])),
            "yOffset": _enc(nominal[1], "nominal"),
            "tooltip": [
                _tooltip_enc(nominal[0], "nominal", labels.get(nominal[0])),
                _tooltip_enc(nominal[1], "nominal", labels.get(nominal[1])),
                _measure_tooltip(compiled, measure, labels.get(measure)),
            ],
        },
    })
    return ChartResult(spec, mark)


def _mark(hint: str) -> str:
    return {"heatmap": "rect", "big_number": "text", "scatter": "point",
            "bubble": "point", "stacked_bar": "bar",
            "normalised_bar": "bar"}.get(hint, hint)


def _sort_for(compiled: CompiledQuery, measure: str) -> Any:
    """Honour the query's own ordering rather than inventing one."""
    for ob in compiled.query.order_by:
        if ob.field == measure:
            return "-y" if ob.dir == "desc" else "y"
    return "-y"
