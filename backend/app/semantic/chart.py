"""Compiled query -> Vega-Lite spec, by rule.

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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .compile import CompiledQuery
from .query import ChartHint

SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"

# Which hints are meaningful for which shape. A hint outside its row is
# rejected rather than silently disagreeing with the chart.
HINT_REQUIREMENTS: dict[str, str] = {
    "line": "temporal",
    "area": "temporal",
    "bar": "any",
    "point": "any",
    "heatmap": "two_nominal",
    "big_number": "no_dimensions",
}


@dataclass
class ChartResult:
    spec: dict[str, Any]
    chart_type: str
    hint_rejected: bool = False


def _kinds(compiled: CompiledQuery) -> tuple[list[str], list[str], list[str]]:
    temporal = [c for c in compiled.columns if compiled.column_kinds[c] == "temporal"]
    nominal = [c for c in compiled.columns if compiled.column_kinds[c] == "nominal"]
    quantitative = [c for c in compiled.columns
                    if compiled.column_kinds[c] == "quantitative"]
    return temporal, nominal, quantitative


def _enc(field: str, kind: str, title: str | None = None,
         **extra: Any) -> dict[str, Any]:
    """One encoding channel. Deliberately has no `aggregate` parameter --
    there is no code path that can add one."""
    out: dict[str, Any] = {"field": field, "type": kind}
    if title:
        out["title"] = title
    out.update(extra)
    return out


def _hint_fits(hint: ChartHint, temporal: list[str], nominal: list[str]) -> bool:
    need = HINT_REQUIREMENTS.get(hint)
    n_dims = len(temporal) + len(nominal)
    if need == "temporal":
        return bool(temporal)
    if need == "two_nominal":
        return len(nominal) == 2
    if need == "no_dimensions":
        return n_dims == 0
    return True


def build_spec(compiled: CompiledQuery, chart_hint: ChartHint | None = None,
               title: str = "") -> ChartResult:
    temporal, nominal, quantitative = _kinds(compiled)
    labels = {
        **{k: d.label for k, d in compiled.entity.dimensions.items()},
        **{k: m.label for k, m in compiled.entity.measures.items()},
    }

    hint_rejected = False
    if chart_hint is not None and not _hint_fits(chart_hint, temporal, nominal):
        chart_hint, hint_rejected = None, True

    n_dims = len(temporal) + len(nominal)
    n_meas = len(quantitative)

    spec: dict[str, Any] = {"$schema": SCHEMA, "data": {"name": "table"}}
    if title:
        spec["title"] = title

    # --- 0 dimensions: a single number ---------------------------------
    if n_dims == 0:
        measure = quantitative[0]
        spec.update({
            "mark": {"type": "text", "fontSize": 48, "fontWeight": 600},
            "encoding": {"text": _enc(measure, "quantitative",
                                      labels.get(measure), format=",.0f")},
        })
        return ChartResult(spec, "big_number", hint_rejected)

    # --- 1 dimension ----------------------------------------------------
    if n_dims == 1:
        dim = (temporal or nominal)[0]
        dim_kind = "temporal" if temporal else "nominal"
        default = "line" if temporal else "bar"
        mark = chart_hint or default

        if n_meas == 1:
            measure = quantitative[0]
            x = _enc(dim, dim_kind, labels.get(dim))
            if dim_kind == "nominal":
                x["sort"] = _sort_for(compiled, measure)
            spec.update({
                "mark": {"type": _mark(mark), "tooltip": True,
                         **({"point": True} if mark == "line" else {})},
                "encoding": {"x": x,
                             "y": _enc(measure, "quantitative", labels.get(measure))},
            })
            return ChartResult(spec, mark, hint_rejected)

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
        spec.update({"mark": {"type": _mark(mark), "tooltip": True}, "encoding": encoding})
        return ChartResult(spec, mark, hint_rejected)

    # --- 2 dimensions ---------------------------------------------------
    if temporal and nominal:
        mark = chart_hint or "line"
        measure = quantitative[0]
        encoding = {
            "x": _enc(temporal[0], "temporal", labels.get(temporal[0])),
            "y": _enc(measure, "quantitative", labels.get(measure)),
            "color": _enc(nominal[0], "nominal", labels.get(nominal[0])),
        }
        spec.update({"mark": {"type": _mark(mark), "tooltip": True}, "encoding": encoding})
        if n_meas > 1:
            # Never silently drop a requested measure: give each its own row.
            spec["transform"] = [{"fold": quantitative, "as": ["measure", "value"]}]
            encoding["y"] = _enc("value", "quantitative", "value")
            encoding["row"] = _enc("measure", "nominal", "measure")
        return ChartResult(spec, mark, hint_rejected)

    # two nominals
    measure = quantitative[0]
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
        return ChartResult(spec, "heatmap", hint_rejected)

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
    return ChartResult(spec, mark, hint_rejected)


def _mark(hint: str) -> str:
    return {"heatmap": "rect", "big_number": "text"}.get(hint, hint)


def _sort_for(compiled: CompiledQuery, measure: str) -> Any:
    """Honour the query's own ordering rather than inventing one."""
    for ob in compiled.query.order_by:
        if ob.field == measure:
            return "-y" if ob.dir == "desc" else "y"
    return "-y"
