"""Output-unit semantics for compiled measure references."""

from __future__ import annotations

from ..layer.models import Entity
from .query import MeasureRef


_PERCENT = {"percent_of_total", "period_change_pct"}
_UNITLESS = {"rank", "ratio"}


def output_unit(entity: Entity, ref: MeasureRef) -> str | None:
    """The unit of a compiled output, not merely its source measure.

    Transforms such as rank and ratio change a quantity's dimension. Other
    transforms (for example cumulative and previous-period) retain it.
    """
    if ref.transform in _PERCENT:
        return "percent"
    if ref.transform in _UNITLESS:
        return None
    target = entity.measure(ref.name)
    return target.unit if target is not None else None
