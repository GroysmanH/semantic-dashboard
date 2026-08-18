"""Validate a semantic query against the layer.

Runs before any database contact. Rules are ordered cheapest and most
informative first, so a refusal names the single most useful thing.
"""

from __future__ import annotations

from ..layer.models import Dimension, Entity, Layer, Measure
from .query import Filter, SemanticQuery

# Ops that only make sense on ordered types.
ORDERED_OPS = {"between", "in_year", "last_n_days"}
DATE_ONLY_OPS = {"in_year", "last_n_days"}


class QueryValidationError(ValueError):
    """Raised when a semantic query does not fit the layer.

    `reason` is a stable machine code; `detail` is the sentence shown to
    the manager. Refusals always name what is undefined -- a confident
    wrong chart is the one failure the design cannot recover from.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _field(entity: Entity, name: str) -> Dimension | Measure | None:
    return entity.dimensions.get(name) or entity.measures.get(name)


def validate_query(q: SemanticQuery, layer: Layer) -> Entity:
    """Return the resolved entity, or raise QueryValidationError."""

    # 1. The entity exists. There is no free-text table slot anywhere in
    #    the grammar, so this is the only way a table name enters the SQL.
    entity = layer.get(q.entity)
    if entity is None:
        raise QueryValidationError(
            "unknown_entity",
            f"There is no {q.entity!r} in the semantic layer. "
            f"Available: {', '.join(sorted(layer))}.",
        )

    # 2. Confidence gate. Entity-level by design: one unverified field
    #    refuses every query on the entity until a human signs it off.
    if entity.has_low_confidence:
        fields = ", ".join(entity.low_confidence_fields())
        raise QueryValidationError(
            "unverified_layer",
            f"{entity.label} has unverified fields and cannot be queried "
            f"until they are reviewed: {fields}.",
        )

    # 3. Measures.
    for name in q.measures:
        if name not in entity.measures:
            raise QueryValidationError(
                "unknown_measure",
                f"{entity.label} has no measure {name!r}. "
                f"Available: {', '.join(sorted(entity.measures))}.",
            )

    # 4. Dimensions and grains.
    for ref in q.dimensions:
        dim = entity.dimensions.get(ref.field)
        if dim is None:
            raise QueryValidationError(
                "unknown_dimension",
                f"{entity.label} has no dimension {ref.field!r}. "
                f"Available: {', '.join(sorted(entity.dimensions))}.",
            )
        if ref.grain is not None:
            if dim.type != "date":
                raise QueryValidationError(
                    "grain_on_non_date",
                    f"{ref.field!r} is a {dim.type} dimension and has no time grain.",
                )
            if ref.grain not in dim.grains:
                raise QueryValidationError(
                    "unsupported_grain",
                    f"{ref.field!r} does not support the {ref.grain!r} grain. "
                    f"Available: {', '.join(dim.grains)}.",
                )

    seen = [r.field for r in q.dimensions]
    if len(seen) != len(set(seen)):
        raise QueryValidationError(
            "duplicate_dimension", "The same dimension is requested twice."
        )

    # 5-6. Filters: field exists, op fits the type, value shape fits the op,
    #      and any declared value set is respected.
    for f in q.filters:
        _validate_filter(entity, f)

    # 7. Ordering may only reference something actually selected.
    selectable = set(q.measures) | {r.field for r in q.dimensions}
    for ob in q.order_by:
        if ob.field not in selectable:
            raise QueryValidationError(
                "order_by_unselected",
                f"Cannot order by {ob.field!r} because it is not selected. "
                f"Selected: {', '.join(sorted(selectable))}.",
            )

    return entity


def _validate_filter(entity: Entity, f: Filter) -> None:
    target = _field(entity, f.field)
    if target is None:
        raise QueryValidationError(
            "unknown_filter_field",
            f"{entity.label} has no field {f.field!r} to filter on.",
        )

    ftype = target.type if isinstance(target, Dimension) else "number"

    if f.op in DATE_ONLY_OPS and ftype != "date":
        raise QueryValidationError(
            "op_type_mismatch",
            f"{f.op!r} only applies to dates; {f.field!r} is a {ftype}.",
        )
    if f.op in ORDERED_OPS and ftype == "string":
        raise QueryValidationError(
            "op_type_mismatch",
            f"{f.op!r} does not apply to the text field {f.field!r}.",
        )

    # Value shape.
    if f.op == "in":
        if not isinstance(f.value, list) or not f.value:
            raise QueryValidationError(
                "bad_filter_value", f"{f.op!r} on {f.field!r} needs a non-empty list."
            )
    elif f.op == "between":
        if not isinstance(f.value, list) or len(f.value) != 2:
            raise QueryValidationError(
                "bad_filter_value", f"'between' on {f.field!r} needs exactly two values."
            )
    elif f.op == "in_year":
        if not isinstance(f.value, int) or isinstance(f.value, bool):
            raise QueryValidationError(
                "bad_filter_value", f"'in_year' on {f.field!r} needs a whole year, e.g. 2026."
            )
    elif f.op == "last_n_days":
        if not isinstance(f.value, int) or isinstance(f.value, bool) or f.value <= 0:
            raise QueryValidationError(
                "bad_filter_value", f"'last_n_days' on {f.field!r} needs a positive whole number."
            )
    else:  # = and !=
        if isinstance(f.value, list) or f.value is None:
            raise QueryValidationError(
                "bad_filter_value", f"{f.op!r} on {f.field!r} needs a single value."
            )

    # Declared value sets. Catching this here is the difference between a
    # refusal that names the mistake and a chart that is silently empty.
    if isinstance(target, Dimension) and target.values is not None:
        supplied = f.value if isinstance(f.value, list) else [f.value]
        unknown = [v for v in supplied if v not in target.values]
        if unknown:
            raise QueryValidationError(
                "value_not_in_domain",
                f"{f.field!r} has no value {unknown[0]!r}. "
                f"Available: {', '.join(target.values)}.",
            )
