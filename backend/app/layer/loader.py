"""Load and structurally validate the semantic layer at import time.

Failing fast here means a malformed layer is a startup error, not a
mystery at query time.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import Entity, Layer, LayerError


def _check_yaml_boolean_keys(path: Path, raw: dict) -> None:
    """YAML 1.1 turns bare `on:`, `off:`, `yes:` and `no:` into booleans.
    Layer files are hand-edited, so name the trap instead of failing with a
    confusing 'field required' several lines away."""
    for alias, join in (raw.get("joins") or {}).items():
        if isinstance(join, dict) and any(isinstance(k, bool) for k in join):
            raise LayerError(
                f"{path.name}: join {alias!r} has a boolean key -- YAML read a "
                f"bare `on:` as true. Write `condition: a.id = b.id` instead."
            )


def load_entity(path: Path) -> Entity:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise LayerError(f"{path.name}: expected a YAML mapping")
    _check_yaml_boolean_keys(path, raw)
    try:
        return Entity.model_validate(raw)
    except ValidationError as exc:
        raise LayerError(f"{path.name}: {exc}") from exc


def load_layer(directory: Path) -> Layer:
    directory = Path(directory)
    if not directory.is_dir():
        raise LayerError(f"layer directory does not exist: {directory}")

    layer: Layer = {}
    for path in sorted(directory.glob("*.yaml")):
        entity = load_entity(path)
        if entity.name in layer:
            raise LayerError(f"duplicate entity name {entity.name!r} in {path.name}")
        layer[entity.name] = entity

    if not layer:
        raise LayerError(f"no entity definitions found in {directory}")
    return layer


def synonym_index(layer: Layer) -> dict[str, dict[str, list[str]]]:
    """term -> entity -> [field names it could mean].

    Feeds the deterministic half of ambiguity detection: a term that maps
    to two or more measures on one entity forces a clarifying question
    regardless of how confident the model sounded.
    """
    index: dict[str, dict[str, list[str]]] = {}
    for name, entity in layer.items():
        for field, terms in entity.synonyms.items():
            for term in [*terms, field]:
                index.setdefault(term.lower(), {}).setdefault(name, [])
                if field not in index[term.lower()][name]:
                    index[term.lower()][name].append(field)
    return index
