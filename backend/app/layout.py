"""Pure, deterministic rules for canonical dashboard geometry."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Iterable, Literal, TypeAlias, TypedDict

from pydantic import BaseModel, Field, model_validator


class Layout(TypedDict):
    x: int
    y: int
    w: int
    h: int


Layouts: TypeAlias = dict[str, Layout]


class VisualizationSize(TypedDict):
    w: int
    h: int


class LayoutInput(BaseModel):
    """One complete card rectangle on the persisted 12-column grid."""

    x: int = Field(strict=True, ge=0, lt=12)
    y: int = Field(strict=True, ge=0)
    w: int = Field(strict=True, gt=0, le=12)
    h: int = Field(strict=True, gt=0)

    @model_validator(mode="after")
    def fits_grid(self):
        if self.x + self.w > 12:
            raise ValueError("x + w must not exceed 12")
        return self


class LayoutResult(BaseModel):
    layouts: dict[str, LayoutInput]
    revision: int


class LayoutConflict(BaseModel):
    code: Literal["layout_conflict"] = "layout_conflict"
    message: str = "This dashboard changed while you were arranging it."
    layouts: dict[str, LayoutInput]
    revision: int


def overlaps(a: Layout, b: Layout) -> bool:
    return not (
        a["x"] + a["w"] <= b["x"]
        or b["x"] + b["w"] <= a["x"]
        or a["y"] + a["h"] <= b["y"]
        or b["y"] + b["h"] <= a["y"]
    )


def pack_vertical(layouts: Mapping[str, Layout]) -> Layouts:
    """Remove vertical gaps while preserving every card's x/w/h.

    Desired position orders the cards; the stable string ID makes ties
    independent of request or database iteration order.
    """
    placed: list[Layout] = []
    packed: Layouts = {}
    for card_id, desired in sorted(
        layouts.items(), key=lambda item: (item[1]["y"], item[1]["x"], item[0])
    ):
        candidate: Layout = {**desired, "y": 0}
        while True:
            collisions = [other for other in placed if overlaps(candidate, other)]
            if not collisions:
                break
            candidate = {
                **candidate,
                "y": max(other["y"] + other["h"] for other in collisions),
            }
        packed[card_id] = candidate
        placed.append(candidate)
    return packed


def resolve_collisions(
    layouts: Mapping[str, Layout], *, preferred: Iterable[str] = ()
) -> Layouts:
    """Keep desired free-placement anchors, moving only collisions down."""
    priority = set(preferred)
    placed: list[Layout] = []
    resolved: Layouts = {}
    for card_id, desired in sorted(
        layouts.items(),
        key=lambda item: (
            0 if item[0] in priority else 1,
            item[1]["y"],
            item[1]["x"],
            item[0],
        ),
    ):
        candidate: Layout = dict(desired)  # type: ignore[assignment]
        while True:
            collisions = [other for other in placed if overlaps(candidate, other)]
            if not collisions:
                break
            candidate = {
                **candidate,
                "y": max(other["y"] + other["h"] for other in collisions),
            }
        resolved[card_id] = candidate
        placed.append(candidate)
    return resolved


def canonical_layouts(
    layouts: Mapping[str, Layout], *, auto_pack: bool,
    preferred: Iterable[str] = (),
) -> Layouts:
    priority = tuple(preferred)
    if not priority:
        return pack_vertical(layouts) if auto_pack else resolve_collisions(layouts)
    anchored = resolve_collisions(layouts, preferred=priority)
    return pack_vertical(anchored) if auto_pack else anchored


def place_final_size_batch(
    existing: Mapping[str, Layout],
    batch: Iterable[tuple[str, Layout]],
    *,
    insertion_row: int,
) -> Layouts:
    """Place one ordered chat batch without moving existing cards.

    Each batch rectangle already carries the size it will keep: a completed
    visualization's one-time size or a failed/queued placeholder's current
    size. Candidate anchors are inspected by row and then column, so the
    result depends only on the fixed obstacles, action order and final sizes.
    """
    placed: Layouts = {
        card_id: dict(layout)  # type: ignore[assignment]
        for card_id, layout in existing.items()
    }
    occupied = list(placed.values())
    first_row = max(0, insertion_row)

    for card_id, layout in batch:
        width = layout["w"]
        height = layout["h"]
        y = first_row
        while True:
            candidate = next(
                (
                    {"x": x, "y": y, "w": width, "h": height}
                    for x in range(12 - width + 1)
                    if not any(
                        overlaps(
                            {"x": x, "y": y, "w": width, "h": height},
                            other,
                        )
                        for other in occupied
                    )
                ),
                None,
            )
            if candidate is not None:
                placed[card_id] = candidate
                occupied.append(candidate)
                break
            y += 1

    return placed


def _nodes(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def _channel_definitions(spec: Mapping[str, Any], channel: str):
    for node in _nodes(spec):
        encoding = node.get("encoding")
        if not isinstance(encoding, Mapping):
            continue
        definition = encoding.get(channel)
        if isinstance(definition, Mapping):
            yield definition


def _distinct(rows: list[dict[str, Any]], field: str) -> int:
    # Rendered values are JSON-compatible. repr also keeps the helper total
    # if a caller hands it a list/dict value from a custom spec.
    return len({repr(row[field]) for row in rows if row.get(field) is not None})


def _transformed_cardinality(spec: Mapping[str, Any], field: str) -> int:
    """Cardinality of fields Vega creates with a fold transform."""
    counts: list[int] = []
    for node in _nodes(spec):
        transforms = node.get("transform")
        if not isinstance(transforms, list):
            continue
        for transform in transforms:
            if not isinstance(transform, Mapping):
                continue
            folded = transform.get("fold")
            names = transform.get("as", ["key", "value"])
            if (isinstance(folded, list) and isinstance(names, list)
                    and names and names[0] == field):
                counts.append(len(folded))
    return max(counts, default=0)


def _field_cardinality(
    spec: Mapping[str, Any], rows: list[dict[str, Any]], field: str
) -> int:
    return max(_distinct(rows, field), _transformed_cardinality(spec, field))


def _facet_count(spec: Mapping[str, Any], rows: list[dict[str, Any]]) -> int:
    fields: list[str] = []
    for node in _nodes(spec):
        facet = node.get("facet")
        if not isinstance(facet, Mapping):
            continue
        if isinstance(facet.get("field"), str):
            fields.append(facet["field"])
        for channel in ("row", "column"):
            definition = facet.get(channel)
            if isinstance(definition, Mapping) and isinstance(
                definition.get("field"), str
            ):
                fields.append(definition["field"])
    count = 1
    for field in dict.fromkeys(fields):
        count *= max(1, _field_cardinality(spec, rows, field))
    return count if fields else 0


def _visible_series(spec: Mapping[str, Any], rows: list[dict[str, Any]]) -> int:
    counts = []
    for definition in _channel_definitions(spec, "color"):
        field = definition.get("field")
        if definition.get("type") == "nominal" and isinstance(field, str):
            counts.append(_field_cardinality(spec, rows, field))
    return max(counts, default=0)


def _temporal_points(spec: Mapping[str, Any], rows: list[dict[str, Any]]) -> int:
    fields = {
        definition["field"]
        for channel in ("x", "y")
        for definition in _channel_definitions(spec, channel)
        if definition.get("type") == "temporal"
        and isinstance(definition.get("field"), str)
    }
    return max(
        (sum(row.get(field) is not None for row in rows) for field in fields),
        default=0,
    )


def _bar_categories(spec: Mapping[str, Any], rows: list[dict[str, Any]]) -> int:
    counts = []
    for channel in ("x", "y"):
        for definition in _channel_definitions(spec, channel):
            field = definition.get("field")
            if definition.get("type") == "nominal" and isinstance(field, str):
                counts.append(_field_cardinality(spec, rows, field))
    return max(counts, default=0)


def visualization_size(
    chart_type: str | None,
    spec: Mapping[str, Any] | None,
    rows: list[dict[str, Any]],
) -> VisualizationSize:
    """Choose initial grid geometry from the finished visualization only.

    This is deliberately independent of cards, boards and browser geometry:
    identical rendered output always receives identical initial dimensions.
    """
    spec = spec or {}
    presentation = (spec.get("usermeta") or {}).get("presentation") \
        if isinstance(spec.get("usermeta"), Mapping) else None
    if chart_type == "big_number" or presentation == "kpi":
        return {"w": 4, "h": 6}

    facets = _facet_count(spec, rows)
    series = _visible_series(spec, rows)
    if facets > 4 or series > 8:
        return {"w": 12, "h": 14}

    is_faceted = bool(facets) or (chart_type or "").startswith("faceted_") \
        or presentation == "facets"
    if chart_type in {"map", "heatmap", "faceted_heatmap"} or is_faceted:
        return {"w": 8, "h": 12}

    is_bar = chart_type in {"bar", "stacked_bar", "normalised_bar"}
    if is_bar:
        categories = _bar_categories(spec, rows)
        if categories > 16:
            return {"w": 8, "h": min(16, 9 + math.ceil(categories / 3))}
        if categories >= 9:
            return {"w": 8, "h": 12}

    if (_temporal_points(spec, rows) > 36 or 4 <= series <= 8
            or chart_type in {"scatter", "bubble"} and len(rows) >= 30):
        return {"w": 8, "h": 12}

    return {"w": 6, "h": 10}
