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
SCATTER_THRESHOLD = 30         # below this a grouped bar still reads
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
    return {}


def _is_signed(compiled: CompiledQuery, column: str) -> bool:
    ref = _ref_for(compiled, column)
    return ref is not None and ref.transform in _SIGNED


def _distinct(rows: list[dict[str, Any]], column: str) -> int:
    return len({r.get(column) for r in rows})


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
        return n_dims == 2
    if need == "two_measures":
        return len(quantitative) == 2 and n_dims == 1
    if need == "three_measures":
        return len(quantitative) >= 3 and n_dims == 1
    if need == "geo":
        geo = compiled.entity.geo
        return geo is not None and nominal == [geo.of]
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
        },
    }
    return ChartResult(spec, "donut" if donut else "pie",
                       chart_rows=chart_rows if note else None, note=note)


def build_spec(compiled: CompiledQuery, rows: list[dict[str, Any]] | None = None,
               chart_hint: ChartHint | None = None,
               title: str = "") -> ChartResult:
    rows = rows or []
    temporal, nominal, quantitative = _kinds(compiled)
    labels = _labels(compiled)

    hint_rejected = False
    if chart_hint is not None and not _hint_fits(chart_hint, temporal, nominal,
                                                 quantitative, compiled):
        chart_hint, hint_rejected = None, True

    n_dims = len(temporal) + len(nominal)
    n_meas = len(quantitative)

    spec: dict[str, Any] = {"$schema": SCHEMA, "data": {"name": ROWS}}
    if title:
        spec["title"] = title

    def done(result: ChartResult) -> ChartResult:
        if title:
            result.spec.setdefault("title", title)
        result.hint_rejected = result.hint_rejected or hint_rejected
        return result

    # --- 3 dimensions: the third becomes a facet ------------------------
    if n_dims == 3:
        return done(_faceted(compiled, rows, temporal, nominal, quantitative,
                             labels))

    # --- 0 dimensions: a single number ---------------------------------
    if n_dims == 0:
        measure = quantitative[0]
        spec.update({
            "mark": {"type": "text", "fontSize": 48, "fontWeight": 600},
            "encoding": {"text": _enc(measure, "quantitative",
                                      labels.get(measure), format=",.0f")},
        })
        return done(ChartResult(spec, "big_number"))

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
        if n_meas == 2 and dim_kind == "nominal" and (chart_hint == "scatter"
                                                      or (many and chart_hint is None)):
            return done(_scatter(compiled, dim, quantitative, labels))
        if n_meas >= 3 and chart_hint == "bubble":
            return done(_scatter(compiled, dim, quantitative, labels, bubble=True))

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
            x = _enc(dim, dim_kind, labels.get(dim))
            if dim_kind == "nominal":
                x["sort"] = _sort_for(compiled, measure)
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
            spec.update({
                "mark": {"type": _mark(mark), "tooltip": True,
                         **({"point": True} if mark == "line" else {})},
                "encoding": encoding,
            })
            return done(ChartResult(spec, mark))

        # Several measures over one dimension: fold them into a series.
        spec["transform"] = [{"fold": quantitative, "as": ["measure", "value"]}]
        x = _enc(dim, dim_kind, labels.get(dim))
        encoding = {
            "x": x,
            "y": _enc("value", "quantitative", "value"),
            "color": _enc("measure", "nominal", "measure"),
        }
        if dim_kind == "nominal" and mark == "bar":
            encoding["xOffset"] = _enc("measure", "nominal")
        spec.update({"mark": {"type": _mark(mark), "tooltip": True},
                     "encoding": encoding})
        return done(ChartResult(spec, mark))

    # --- 2 dimensions ---------------------------------------------------
    return done(_two_dimensions(compiled, temporal, nominal, quantitative,
                                labels, chart_hint, spec))


def _scatter(compiled: CompiledQuery, dim: str, quantitative: list[str],
             labels: dict[str, str], bubble: bool = False) -> ChartResult:
    """Two measures against each other, the dimension carried in the
    tooltip rather than on an axis. A third measure becomes size."""
    x, y = quantitative[0], quantitative[1]
    encoding = {
        "x": _enc(x, "quantitative", labels.get(x), **_number_format(compiled, x)),
        "y": _enc(y, "quantitative", labels.get(y), **_number_format(compiled, y)),
        "tooltip": [_enc(dim, "nominal", labels.get(dim)),
                    _enc(x, "quantitative", labels.get(x)),
                    _enc(y, "quantitative", labels.get(y))],
    }
    if bubble:
        size = quantitative[2]
        encoding["size"] = _enc(size, "quantitative", labels.get(size))
        encoding["tooltip"].append(_enc(size, "quantitative", labels.get(size)))
    return ChartResult(
        {"$schema": SCHEMA, "data": {"name": ROWS},
         "mark": {"type": "point", "tooltip": True, "filled": not bubble,
                  "opacity": 0.7},
         "encoding": encoding},
        "bubble" if bubble else "scatter")


def _map(compiled: CompiledQuery, dim: str, measure: str,
         labels: dict[str, str]) -> ChartResult:
    """Points over an outline.

    Coordinates come from the entity's geo block, which the model cannot
    see, so a map is always a rendering of a per-`of` grouping rather than
    something the grammar can be talked into. The outline is a second
    dataset, which is why the frontend must stop replacing `data` wholesale.
    """
    geo = compiled.entity.geo
    lat = geo.lat.split(".")[-1]
    lon = geo.lon.split(".")[-1]
    return ChartResult(
        {"$schema": SCHEMA,
         "layer": [
             {"data": {"name": "outline",
                       "format": {"type": "json", "property": "features"}},
              "mark": {"type": "geoshape", "fill": "#e3e9e3", "stroke": "#cdd6ce"}},
             {"data": {"name": ROWS},
              "mark": {"type": "circle", "tooltip": True, "opacity": 0.75},
              "encoding": {
                  "longitude": _enc(lon, "quantitative"),
                  "latitude": _enc(lat, "quantitative"),
                  "size": _enc(measure, "quantitative", labels.get(measure)),
                  "color": _enc(measure, "quantitative", labels.get(measure),
                                scale={"scheme": "teals"}),
                  "tooltip": [_enc(dim, "nominal", labels.get(dim)),
                              _enc(measure, "quantitative", labels.get(measure))],
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

    return ChartResult(
        {"$schema": SCHEMA, "data": {"name": ROWS},
         "facet": {"field": facet, "type": "nominal",
                   "title": labels.get(facet), "columns": 3},
         "spec": {"mark": {"type": mark, "tooltip": True}, "encoding": encoding}},
        f"faceted_{mark}")


def _two_dimensions(compiled: CompiledQuery, temporal: list[str],
                    nominal: list[str], quantitative: list[str],
                    labels: dict[str, str], chart_hint: ChartHint | None,
                    spec: dict[str, Any]) -> ChartResult:
    measure = quantitative[0]
    n_meas = len(quantitative)

    if chart_hint in ("stacked_bar", "normalised_bar"):
        x = temporal[0] if temporal else nominal[0]
        series = nominal[-1] if temporal else nominal[1]
        y = _enc(measure, "quantitative", labels.get(measure),
                 stack="normalize" if chart_hint == "normalised_bar" else "zero")
        if chart_hint == "normalised_bar":
            y["axis"] = {"format": ".0%"}
        spec.update({
            "mark": {"type": "bar", "tooltip": True},
            "encoding": {
                "x": _enc(x, "temporal" if temporal else "nominal", labels.get(x)),
                "y": y,
                "color": _enc(series, "nominal", labels.get(series)),
            },
        })
        return ChartResult(spec, chart_hint)

    if temporal and nominal:
        mark = chart_hint or "line"
        encoding = {
            "x": _enc(temporal[0], "temporal", labels.get(temporal[0])),
            "y": _enc(measure, "quantitative", labels.get(measure),
                      **_number_format(compiled, measure)),
            "color": _enc(nominal[0], "nominal", labels.get(nominal[0])),
        }
        spec.update({"mark": {"type": _mark(mark), "tooltip": True},
                     "encoding": encoding})
        if n_meas > 1:
            # Never silently drop a requested measure: give each its own row.
            spec["transform"] = [{"fold": quantitative, "as": ["measure", "value"]}]
            encoding["y"] = _enc("value", "quantitative", "value")
            encoding["row"] = _enc("measure", "nominal", "measure")
        return ChartResult(spec, mark)

    # two nominals
    mark = chart_hint or "heatmap"
    if mark == "heatmap":
        spec.update({
            "mark": {"type": "rect", "tooltip": True},
            "encoding": {
                "x": _enc(nominal[0], "nominal", labels.get(nominal[0])),
                "y": _enc(nominal[1], "nominal", labels.get(nominal[1])),
                "color": _enc(measure, "quantitative", labels.get(measure)),
            },
        })
        return ChartResult(spec, "heatmap")

    spec.update({
        "mark": {"type": _mark(mark), "tooltip": True},
        "encoding": {
            "x": _enc(nominal[0], "nominal", labels.get(nominal[0]),
                      sort=_sort_for(compiled, measure)),
            "y": _enc(measure, "quantitative", labels.get(measure)),
            "color": _enc(nominal[1], "nominal", labels.get(nominal[1])),
            "xOffset": _enc(nominal[1], "nominal"),
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
