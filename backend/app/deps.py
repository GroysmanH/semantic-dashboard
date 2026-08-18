"""Shared singletons. The layer is loaded once at import: a malformed
layer should be a startup failure, not a surprise at query time."""

from __future__ import annotations

from .config import settings
from .layer.loader import load_layer, synonym_index

LAYER = load_layer(settings.layer_dir)
SYNONYMS = synonym_index(LAYER)


def example_questions(limit: int = 6) -> list[str]:
    """Drawn from the layer, so the empty card doubles as the discoverability
    mechanism for what vocabulary actually exists."""
    out: list[str] = []
    for entity in LAYER.values():
        # An entity behind the confidence gate refuses every query, so
        # offering it as an example teaches the manager that the box does
        # not work.
        if entity.has_low_confidence:
            continue
        measures = list(entity.measures)
        dates = [k for k, d in entity.dimensions.items() if d.type == "date"]
        nominals = [k for k, d in entity.dimensions.items() if d.type != "date"]
        if measures and dates:
            out.append(f"{entity.measures[measures[0]].label} by month")
        if measures and nominals:
            out.append(f"{entity.measures[measures[0]].label} by "
                       f"{entity.dimensions[nominals[0]].label}")
        if len(measures) > 1 and nominals:
            out.append(f"top {entity.dimensions[nominals[0]].label} by "
                       f"{entity.measures[measures[1]].label}")
    return out[:limit]
