"""Compiled query -> plain English, deterministically.

The manager cannot read SQL by construction, so this sentence is the
trust surface. It is generated from the query object and the layer's
labels -- never by the model, because a second thing that can lie is not
a safeguard.
"""

from __future__ import annotations

from datetime import date, datetime


def dt_parse(v: str) -> datetime:
    return datetime.fromisoformat(v)

from ..layer.models import Entity
from .query import Filter, SemanticQuery

_AGG_PHRASE = {
    "sum": "Sum of {}",
    "avg": "Average {}",
    "count": "Count of {}",
    "min": "Minimum {}",
    "max": "Maximum {}",
}

_GRAIN_PHRASE = {
    "day": "day",
    "month": "calendar month",
    "quarter": "calendar quarter",
    "year": "year",
}


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _fmt_value(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (date, datetime)):
        return v.strftime("%-d %b %Y")
    return str(v)


def _fmt_ts(v) -> str:
    """The freshness tail reaches here as an ISO string once it has been
    through JSON, so parse before formatting rather than echoing 2026-07-31
    at a manager."""
    if isinstance(v, str):
        try:
            v = dt_parse(v)
        except ValueError:
            return v
    return v.strftime("%-d %b %Y")


def _filter_phrase(entity: Entity, f: Filter) -> str:
    target = entity.dimensions.get(f.field) or entity.measures.get(f.field)
    label = target.label if target else f.field

    if f.op == "in_year":
        return str(f.value)
    if f.op == "last_n_days":
        return f"the last {f.value} days"
    if f.op == "between":
        return f"{label} between {_fmt_value(f.value[0])} and {_fmt_value(f.value[1])}"
    if f.op == "in":
        return f"{label} one of {_join([_fmt_value(v) for v in f.value])}"
    if f.op == "!=":
        return f"{label} not {_fmt_value(f.value)}"
    return f"{label} {_fmt_value(f.value)}"


def restate(q: SemanticQuery, entity: Entity, row_count: int | None = None,
            data_max_ts: date | datetime | str | None = None) -> str:
    measures = [
        _AGG_PHRASE[entity.measures[m].agg].format(entity.measures[m].label)
        for m in q.measures
    ]
    sentence = _join(measures)

    if q.dimensions:
        parts = []
        for ref in q.dimensions:
            dim = entity.dimensions[ref.field]
            if ref.grain:
                parts.append(_GRAIN_PHRASE[ref.grain])
            else:
                parts.append(dim.label)
        sentence += ", by " + _join(parts)

    if q.filters:
        sentence += ", filtered to " + _join(
            [_filter_phrase(entity, f) for f in q.filters]
        )

    sentence += f", from {entity.label}"

    tail = []
    if row_count is not None:
        tail.append(f"{row_count:,} row{'' if row_count == 1 else 's'}")
    if data_max_ts is not None:
        tail.append(f"data through {_fmt_ts(data_max_ts)}")
    if tail:
        sentence += " — " + ", ".join(tail)

    return sentence + "."
