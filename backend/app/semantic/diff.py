"""What a refinement changed, in words.

Design section 9 asks for the diff to be computed and displayed. It is the
trust surface for an edit, in the same spirit as the restatement: the
sentence says what the card means now, and this says what moved to get
there. Both are generated here rather than by the model, for the same
reason -- a second thing that can lie is not a safeguard.

Deliberately shallow. This describes the change, it does not justify it,
and it never guesses intent.
"""

from __future__ import annotations

from ..layer.models import Entity
from .query import SemanticQuery


def _label(entity: Entity, name: str) -> str:
    """A field's human name, falling back to its key. A refinement that
    renamed a field out of existence still gets a readable diff."""
    dim = entity.dimensions.get(name)
    if dim is not None:
        return dim.label
    measure = entity.measure(name)
    return measure.label if measure is not None else name


def _measures(q: SemanticQuery) -> dict[str, str]:
    return {m.output_name: m.name for m in q.measures}


def _filters(q: SemanticQuery) -> dict[str, str]:
    return {f.field: f"{f.op} {f.value}" for f in q.filters}


def diff_queries(before: SemanticQuery, after: SemanticQuery,
                 entity: Entity) -> list[str]:
    """One short phrase per change, or an empty list when only the chart
    moved -- which is itself worth knowing, because that path re-renders
    from cache without touching the warehouse."""
    out: list[str] = []

    if before.entity != after.entity:
        # Everything downstream is named against a different entity, so
        # comparing further would produce nonsense phrases.
        return [f"switched from {before.entity} to {after.entity}"]

    was, now = _measures(before), _measures(after)
    for key in now.keys() - was.keys():
        out.append(f"added {_label(entity, now[key])}")
    for key in was.keys() - now.keys():
        out.append(f"removed {_label(entity, was[key])}")

    was_dims = {d.field: d.grain for d in before.dimensions}
    now_dims = {d.field: d.grain for d in after.dimensions}
    for field in now_dims.keys() - was_dims.keys():
        out.append(f"broke down by {_label(entity, field)}")
    for field in was_dims.keys() - now_dims.keys():
        out.append(f"stopped breaking down by {_label(entity, field)}")
    for field in was_dims.keys() & now_dims.keys():
        if was_dims[field] != now_dims[field]:
            out.append(f"{_label(entity, field)} {was_dims[field]} "
                       f"→ {now_dims[field]}")

    was_f, now_f = _filters(before), _filters(after)
    for field in now_f.keys() - was_f.keys():
        out.append(f"filtered to {_label(entity, field)} {now_f[field]}")
    for field in was_f.keys() - now_f.keys():
        out.append(f"dropped the {_label(entity, field)} filter")
    for field in was_f.keys() & now_f.keys():
        if was_f[field] != now_f[field]:
            out.append(f"{_label(entity, field)} filter "
                       f"{was_f[field]} → {now_f[field]}")

    was_order = [(o.field, o.dir) for o in before.order_by]
    now_order = [(o.field, o.dir) for o in after.order_by]
    if was_order != now_order:
        out.append(f"ordered by {', '.join(f'{_label(entity, f)} {d}' for f, d in now_order)}"
                   if now_order else "removed the ordering")

    if before.limit != after.limit:
        out.append(f"limit {before.limit} → {after.limit}")

    return out
