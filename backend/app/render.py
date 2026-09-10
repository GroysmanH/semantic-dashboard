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
from .semantic.compile import CompiledQuery, compile_query
from .semantic.query import ChartHint, SemanticQuery
from .semantic.restate import restate
from .semantic.result import ResultContractError, verify_result_envelope
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
    # Set only for presentation rows: a pie's explicitly disclosed collapsed
    # tail, or non-aggregating shared-tooltip lookup fields. `rows` below stays
    # the untouched result set, so SQL and row-count evidence remain true.
    chart_rows: list[dict[str, Any]] | None = None
    restatement: str = ""
    compiled_sql: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    # The query had more to give and the limit stopped it. Surfaced rather
    # than silently absorbed: "by company" on a real warehouse returned a
    # hundred of a hundred and twenty-five, which was 71% of the total, and
    # said nothing about it.
    truncated: bool = False
    data_max_ts: str | None = None
    fetched_at: str | None = None
    from_cache: bool = False
    error: str | None = None
    error_reason: str | None = None
    output_units: dict[str, str | None] = Field(default_factory=dict)
    quality_warnings: list[str] = Field(default_factory=list)
    cache: dict[str, Any] | None = Field(default=None, exclude=True)


def apply_row_limit(
    compiled: CompiledQuery, rows: list[Any]
) -> tuple[list[Any], bool]:
    """Drop the compiler's one-row truncation probe and disclose its presence."""
    return rows[:compiled.row_limit], len(rows) > compiled.row_limit


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

    cache_input = not force and cache is not None and cache != {}
    from_cache = cache_input
    try:
        try:
            fresh = cache_input and cache_mod.is_fresh(
                cache, ttl_seconds, key
            )
        except Exception as exc:
            raise ResultContractError("cache envelope is invalid") from exc

        # A stale cache is still persisted input. Validate matching results
        # before freshness fallback so a refresh cannot hide malformed rows.
        if cache_input and cache["key"] == key:
            verify_result_envelope(compiled, cache)

        if fresh:
            envelope = cache
        else:
            from_cache = False
            rows = execute.run(compiled)
            # One row over the limit was fetched on purpose; its existence is
            # the whole signal, and it is dropped before anything sees it.
            rows, truncated = apply_row_limit(compiled, rows)
            envelope = {
                "key": key,
                "result": rows,
                "row_count": len(rows),
                "truncated": truncated,
            }

        rows = verify_result_envelope(compiled, envelope)
    except ResultContractError:
        return Render(
            state="broken", semantic_query=q, chart_hint=chart_hint,
            error="I couldn’t verify this result safely. Nothing was saved.",
            error_reason="result_verification", from_cache=from_cache,
        )

    if not from_cache:
        envelope = cache_mod.envelope(
            key, rows, compiled.sql, execute.data_max_ts(compiled),
            truncated=envelope["truncated"],
        )

    warnings = list(dict.fromkeys(
        f"{compiled.entity.measure(ref.name).label.capitalize()}: "
        f"{compiled.quality_warnings[ref.output_name]}"
        for ref in q.measures
        if ref.output_name in compiled.quality_warnings
    ))

    # The builder now sees the rows. Three rules cannot be decided without
    # them -- whether a pie has too many slices or a negative value, whether
    # a third dimension is sparse enough to face, whether two measures span
    # enough categories to want a scatter. Encoding *types* still come from
    # the compiler, never from the values.
    chart = build_spec(compiled, envelope["result"], chart_hint, title=title)

    if chart.chart_type == "unplottable":
        # The query is fine and the numbers are real; there is simply no
        # honest picture of them. Breaking the card names the dimension,
        # where silently dropping it would leave a chart that disagrees
        # with the header above it.
        return Render(state="broken", semantic_query=q, chart_hint=chart_hint,
                      hint_rejected=chart.hint_rejected,
                      error=chart.error, error_reason="unplottable",
                      restatement=restate(q, compiled.entity,
                                          truncated=envelope.get("truncated", False),
                                          limit=compiled.row_limit),
                      compiled_sql=compiled.sql_readable, rows=envelope["result"],
                      row_count=envelope["row_count"],
                      truncated=envelope.get("truncated", False),
                      data_max_ts=envelope["data_max_ts"],
                      fetched_at=envelope["fetched_at"], from_cache=from_cache,
                      output_units=compiled.output_units, quality_warnings=warnings,
                      cache=envelope)

    return Render(
        state="ready",
        semantic_query=q,
        chart_hint=chart_hint,
        vega_spec=chart.spec,
        chart_type=chart.chart_type,
        hint_rejected=chart.hint_rejected,
        chart_rows=chart.chart_rows,
        # The sentence states meaning; the row count and freshness ride in
        # their own fields so the card header does not print both twice.
        # A collapsed tail changes the meaning, so it rides in the sentence.
        restatement=restate(q, compiled.entity, note=chart.note,
                            truncated=envelope.get("truncated", False),
                            limit=compiled.row_limit),
        compiled_sql=compiled.sql_readable,
        rows=envelope["result"],
        row_count=envelope["row_count"],
        truncated=envelope.get("truncated", False),
        data_max_ts=envelope["data_max_ts"],
        fetched_at=envelope["fetched_at"],
        from_cache=from_cache,
        output_units=compiled.output_units,
        quality_warnings=warnings,
        cache=envelope,
    )


def to_payload(r: Render) -> dict[str, Any]:
    """The cache envelope is server-side bookkeeping and is excluded."""
    return r.model_dump(mode="json")


def is_persistable(r: Render) -> bool:
    """Whether a render contains a valid query result worth saving.

    An unplottable result is visually broken on purpose, but its query,
    rows, SQL and cache are all valid. Treating it like a layer failure
    loses the user's refinement and reruns the warehouse on every reload.
    """
    return r.state == "ready" or r.error_reason == "unplottable"
