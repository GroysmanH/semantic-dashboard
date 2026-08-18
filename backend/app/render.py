"""The render pipeline: semantic query -> everything a card needs.

Used by both the ask endpoint and card reads, so a card rendered from
cache and one rendered fresh go through exactly the same code.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from . import cache as cache_mod
from . import execute
from .layer.models import Layer
from .semantic.chart import build_spec
from .semantic.compile import compile_query
from .semantic.query import ChartHint, SemanticQuery
from .semantic.restate import restate
from .semantic.validate import QueryValidationError


class Render(BaseModel):
    """Everything a card needs to draw itself, including the honest version
    of where the numbers came from and how old they are."""

    state: Literal["empty", "ready", "broken"]
    semantic_query: SemanticQuery | None = None
    chart_hint: ChartHint | None = None
    vega_spec: dict[str, Any] | None = None
    chart_type: str | None = None
    hint_rejected: bool = False
    restatement: str = ""
    compiled_sql: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    data_max_ts: str | None = None
    fetched_at: str | None = None
    from_cache: bool = False
    error: str | None = None
    error_reason: str | None = None
    cache: dict[str, Any] | None = Field(default=None, exclude=True)


def render(q: SemanticQuery, layer: Layer, *, chart_hint: ChartHint | None = None,
           title: str = "", cache: dict[str, Any] | None = None,
           ttl_seconds: int = 900, force: bool = False) -> Render:
    """Compile, execute (or reuse cache), then build the spec and sentence.

    A card whose layer moved underneath it renders as a broken card naming
    the missing field -- never as stale-but-pretty cached numbers, and
    never repaired by a model behind the manager's back.
    """
    try:
        compiled = compile_query(q, layer)
    except QueryValidationError as exc:
        return Render(state="broken", semantic_query=q,
                      error=exc.detail, error_reason=exc.reason)

    key = cache_mod.cache_key(compiled.sql, compiled.params)

    if not force and cache_mod.is_fresh(cache, ttl_seconds, key):
        envelope, from_cache = cache, True
    else:
        rows = execute.run(compiled)
        envelope = cache_mod.envelope(key, rows, compiled.sql,
                                      execute.data_max_ts(compiled))
        from_cache = False

    chart = build_spec(compiled, chart_hint, title=title)

    return Render(
        state="ready",
        semantic_query=q,
        chart_hint=chart_hint,
        vega_spec=chart.spec,
        chart_type=chart.chart_type,
        hint_rejected=chart.hint_rejected,
        # The sentence states meaning; the row count and freshness ride in
        # their own fields so the card header does not print both twice.
        restatement=restate(q, compiled.entity),
        compiled_sql=compiled.sql,
        rows=envelope["result"],
        row_count=envelope["row_count"],
        data_max_ts=envelope["data_max_ts"],
        fetched_at=envelope["fetched_at"],
        from_cache=from_cache,
        cache=envelope,
    )


def to_payload(r: Render) -> dict[str, Any]:
    """The cache envelope is server-side bookkeeping and is excluded."""
    return r.model_dump(mode="json")
