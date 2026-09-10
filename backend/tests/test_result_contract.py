"""Runtime contracts for rendered warehouse results.

These tests deliberately use only semantic definitions and deterministic
rows; no test in this module opens a warehouse connection.
"""

import copy

import pytest

from app import execute
from app.cache import cache_key, envelope
from app.routes import cards
from app.render import render
import app.render as render_module
from app.semantic.chart import build_spec
from app.semantic.compile import compile_query
from app.semantic.query import DimensionRef, OrderBy, SemanticQuery
from app.semantic.restate import restate


def test_outputs_keep_declared_units_and_the_downtime_warning(layer):
    """Removing a declared unit or the documented-unit warning must be visible
    to every downstream renderer, rather than becoming a chart-only detail."""
    compiled = compile_query(
        SemanticQuery(
            entity="oil_mined",
            measures=["actual", "attainment", "well_downtime"],
        ),
        layer,
    )

    assert compiled.output_units == {
        "actual": "tonnes",
        "attainment": "percent",
        "well_downtime": None,
    }
    assert compiled.quality_warnings == {
        "well_downtime": "The source unit for well downtime is not documented."
    }


@pytest.mark.parametrize("rows", [
    [{"unexpected": 1}],
    [{"oil": float("inf")}],
    [{"reading_date": "not-a-date", "oil": 1}],
    [{"region": "North", "oil": 2}, {"region": "North", "oil": 1}],
    [{"region": "North", "oil": 1}, {"region": "South", "oil": 2}],
])
def test_unverified_fresh_results_are_not_rendered_or_cached(layer, monkeypatch, rows):
    """A missing column, invalid value, duplicate key, or wrong ranking
    must stop before chart generation; otherwise a plausible visual can
    launder an invalid result into a saved card."""
    query = SemanticQuery(
        entity="production",
        measures=["oil"],
        dimensions=[DimensionRef(field="region")],
    )
    monkeypatch.setattr(execute, "run", lambda compiled: rows)
    monkeypatch.setattr(execute, "data_max_ts", lambda compiled: None)

    result = render(query, layer)

    assert result.state == "broken"
    assert result.error == "I couldn’t verify this result safely. Nothing was saved."
    assert result.cache is None


def test_an_invalid_cached_result_is_rejected_without_charting(layer):
    """Cache hydration is an input boundary too: accepting an old malformed
    envelope would bypass the fresh-result checks on every reload."""
    query = SemanticQuery(entity="production", measures=["oil"])
    compiled = compile_query(query, layer)
    cached = envelope(
        cache_key(compiled.sql, compiled.params),
        [{"unexpected": 1}],
        compiled.sql,
        None,
    )

    result = render(query, layer, cache=cached)

    assert result.state == "broken"
    assert result.from_cache
    assert result.error == "I couldn’t verify this result safely. Nothing was saved."
    assert result.cache is None


def test_a_cached_result_keeps_its_unit_warning(layer):
    """The warning is semantic metadata, so a card reload must not lose it
    merely because its rows came from a fresh cache envelope."""
    query = SemanticQuery(entity="oil_mined", measures=["well_downtime"])
    compiled = compile_query(query, layer)
    cached = envelope(
        cache_key(compiled.sql, compiled.params), [{"well_downtime": 3.0}],
        compiled.sql, None,
    )

    result = render(query, layer, cache=cached)

    assert result.from_cache
    assert result.quality_warnings == [
        "Well downtime: The source unit for well downtime is not documented."
    ]


def test_invalid_temporal_values_are_not_accepted_as_grouped_dates(layer, monkeypatch):
    query = SemanticQuery(
        entity="production", measures=["oil"],
        dimensions=[DimensionRef(field="reading_date", grain="month")],
    )
    monkeypatch.setattr(execute, "run", lambda compiled: [{"reading_date": "not-a-date", "oil": 1.0}])
    monkeypatch.setattr(execute, "data_max_ts", lambda compiled: None)

    result = render(query, layer)

    assert result.state == "broken"
    assert result.error_reason == "result_verification"


@pytest.mark.parametrize(
    ("source", "column", "value"),
    [
        ("fresh", "latitude", float("nan")),
        ("fresh", "longitude", float("inf")),
        ("cached", "latitude", float("-inf")),
        ("cached", "longitude", float("nan")),
    ],
)
def test_every_non_null_geo_output_must_be_finite(
    layer, monkeypatch, source, column, value,
):
    """Coordinates bypass chart measure counts, but not result safety."""
    query = SemanticQuery(
        entity="production",
        measures=["oil"],
        dimensions=[DimensionRef(field="well_name")],
    )
    compiled = compile_query(query, layer)
    row = {
        "well_name": "Well 1",
        "oil": 100.0,
        "latitude": 47.1,
        "longitude": 51.9,
        column: value,
    }

    if source == "cached":
        cached = envelope(
            cache_key(compiled.sql, compiled.params), [row], compiled.sql, None,
        )
        result = render(query, layer, cache=cached)
    else:
        monkeypatch.setattr(execute, "run", lambda _compiled: [row])
        monkeypatch.setattr(execute, "data_max_ts", lambda _compiled: None)
        result = render(query, layer)

    assert result.state == "broken"
    assert result.error_reason == "result_verification"
    assert result.error == "I couldn’t verify this result safely. Nothing was saved."
    assert result.cache is None


def test_units_and_warnings_reach_restatement_and_kpi(layer, monkeypatch):
    """Changing a unit must change both the prose and the visible chart
    contract, including the exact hover title a manager reads."""
    query = SemanticQuery(entity="oil_mined", measures=["actual", "attainment", "well_downtime"])
    monkeypatch.setattr(execute, "run", lambda compiled: [{
        "actual": 1250.0, "attainment": 97.5, "well_downtime": 3.0,
    }])
    monkeypatch.setattr(execute, "data_max_ts", lambda compiled: None)

    result = render(query, layer)

    assert result.state == "ready"
    assert "tonnes" in result.restatement
    assert "%" in result.restatement
    assert result.output_units == {
        "actual": "tonnes", "attainment": "percent", "well_downtime": None,
    }
    assert result.quality_warnings == [
        "Well downtime: The source unit for well downtime is not documented."
    ]
    actual = result.vega_spec["hconcat"][0]["layer"][0]["encoding"]
    attainment = result.vega_spec["hconcat"][1]["layer"][0]["encoding"]
    assert actual["text"]["title"] == "actual production (tonnes)"
    assert attainment["text"]["title"] == "plan attainment (%)"
    assert attainment["tooltip"][-1]["title"] == "plan attainment (%)"


def test_repeated_measure_warnings_are_deduplicated_in_query_order(
    layer, monkeypatch,
):
    query = SemanticQuery(
        entity="oil_mined",
        measures=[
            "well_downtime",
            {"name": "well_downtime", "transform": "rank"},
        ],
        dimensions=[DimensionRef(field="company")],
    )
    monkeypatch.setattr(execute, "run", lambda _compiled: [{
        "company": "Company A",
        "well_downtime": 3.0,
        "well_downtime_rank": 1,
    }])
    monkeypatch.setattr(execute, "data_max_ts", lambda _compiled: None)

    result = render(query, layer)

    assert result.state == "ready"
    assert result.quality_warnings == [
        "Well downtime: The source unit for well downtime is not documented."
    ]


def test_cached_truncation_must_match_the_saved_rows(layer):
    """A partial-result flag with fewer than the requested rows cannot have
    come from the one-extra-row protocol and must never be trusted."""
    query = SemanticQuery(entity="production", measures=["oil"], limit=2)
    compiled = compile_query(query, layer)
    cached = envelope(
        cache_key(compiled.sql, compiled.params), [{"oil": 1}], compiled.sql, None,
        truncated=True,
    )
    cached["row_count"] = 1

    result = render(query, layer, cache=copy.deepcopy(cached))

    assert result.state == "broken"
    assert result.cache is None


def test_rankings_put_nulls_last_for_requested_and_default_order(layer):
    """A null measure must not become the apparent leader on a descending
    ranking, and the default top-N order must have the same guarantee."""
    default = compile_query(
        SemanticQuery(
            entity="production", measures=["oil"],
            dimensions=[DimensionRef(field="region")],
        ),
        layer,
    )
    requested = compile_query(
        SemanticQuery(
            entity="production", measures=["oil"],
            dimensions=[DimensionRef(field="region")],
            order_by=[OrderBy(field="oil", dir="asc")],
        ),
        layer,
    )

    assert (
        'ORDER BY "oil" DESC NULLS LAST, '
        '"region" COLLATE "C" ASC NULLS LAST'
    ) in default.sql
    assert (
        'ORDER BY "oil" ASC NULLS LAST, '
        '"region" COLLATE "C" ASC NULLS LAST'
    ) in requested.sql


def test_invalid_rows_never_reach_chart_generation(layer, monkeypatch):
    """A chart builder is a presentation boundary, never a validator that
    may see malformed fresh or hydrated warehouse results."""
    query = SemanticQuery(entity="production", measures=["oil"])
    compiled = compile_query(query, layer)
    invalid_cache = envelope(
        cache_key(compiled.sql, compiled.params), [{"unexpected": 1}], compiled.sql, None,
    )
    monkeypatch.setattr(execute, "run", lambda compiled: [{"unexpected": 1}])
    monkeypatch.setattr(execute, "data_max_ts", lambda compiled: None)
    monkeypatch.setattr(
        render_module, "build_spec",
        lambda *args, **kwargs: pytest.fail("build_spec received an invalid result"),
    )

    fresh = render(query, layer)
    cached = render(query, layer, cache=invalid_cache)

    assert fresh.error_reason == "result_verification"
    assert cached.error_reason == "result_verification"


def test_invalid_cached_card_result_is_cleared_from_persistence(layer, monkeypatch):
    """Leaving the rejected envelope on the card turns every reload into the
    same invalid cache hydration instead of quarantining it once."""
    query = SemanticQuery(entity="production", measures=["oil"])
    compiled = compile_query(query, layer)
    card = {
        "id": "card-1",
        "semantic_query": query.model_dump(mode="json"),
        "chart_hint": None,
        "title": "Oil",
        "cache": envelope(
            cache_key(compiled.sql, compiled.params), [{"unexpected": 1}], compiled.sql, None,
        ),
        "ttl_seconds": 900,
        "state": "ready",
        "auto_size_pending": False,
        "previous": None,
    }
    monkeypatch.setattr(cards, "LAYER", layer)
    monkeypatch.setattr(cards.store, "update_card", lambda card_id, **fields: {**card, **fields})

    response = cards._render_card(card)

    assert response["cache"] is None
    assert response["render"]["error_reason"] == "result_verification"


def test_stale_same_query_invalid_cache_is_rejected_before_refresh(
    layer, monkeypatch,
):
    """A stale matching cache is still untrusted input and must be
    quarantined before a warehouse refresh can hide its invalid contents."""
    query = SemanticQuery(entity="production", measures=["oil"])
    compiled = compile_query(query, layer)
    cached = envelope(
        cache_key(compiled.sql, compiled.params),
        [{"oil": 10 ** 400}],
        compiled.sql,
        None,
    )
    cached["fetched_at"] = "2000-01-01T00:00:00+00:00"
    card = {
        "id": "card-1",
        "semantic_query": query.model_dump(mode="json"),
        "chart_hint": None,
        "title": "Oil",
        "cache": cached,
        "ttl_seconds": 900,
        "state": "ready",
        "auto_size_pending": False,
        "previous": None,
    }
    updates = []
    monkeypatch.setattr(cards, "LAYER", layer)
    monkeypatch.setattr(
        execute,
        "run",
        lambda _compiled: pytest.fail("stale invalid cache reached execution"),
    )

    def update(card_id, **fields):
        updates.append(fields)
        return {**card, **fields}

    monkeypatch.setattr(cards.store, "update_card", update)

    response = cards._render_card(card)

    assert response["cache"] is None
    assert updates == [{"cache": None}]
    assert response["render"]["error_reason"] == "result_verification"
    assert response["render"]["error"] == (
        "I couldn’t verify this result safely. Nothing was saved."
    )


@pytest.mark.parametrize(
    "case",
    [
        "malformed_timestamp",
        "naive_timestamp",
        "malformed_timezone",
        "oversized_number",
        "string",
        "number",
        "boolean",
        "array",
        "missing_fields",
        "nested_timestamp",
        "result_not_array",
        "row_not_object",
    ],
)
def test_malformed_cache_json_is_rejected_totally_and_cleared(
    layer, monkeypatch, case,
):
    """Every JSON shape at the persisted cache boundary must collapse to
    the same safe response instead of raising or remaining on the card."""
    query = SemanticQuery(entity="production", measures=["oil"])
    compiled = compile_query(query, layer)
    valid = envelope(
        cache_key(compiled.sql, compiled.params), [{"oil": 1.0}],
        compiled.sql, None,
    )
    malformed = copy.deepcopy(valid)
    if case == "malformed_timestamp":
        malformed["fetched_at"] = "not-a-timestamp"
    elif case == "naive_timestamp":
        malformed["fetched_at"] = "2099-01-01T00:00:00"
    elif case == "malformed_timezone":
        malformed["fetched_at"] = "2099-01-01T00:00:00+99:99"
    elif case == "oversized_number":
        malformed["result"] = [{"oil": 10 ** 400}]
    elif case == "string":
        malformed = "cache-envelope"
    elif case == "number":
        malformed = 7
    elif case == "boolean":
        malformed = True
    elif case == "array":
        malformed = [valid]
    elif case == "missing_fields":
        malformed = {"fetched_at": valid["fetched_at"]}
    elif case == "nested_timestamp":
        malformed["fetched_at"] = {"when": valid["fetched_at"]}
    elif case == "result_not_array":
        malformed["result"] = {"oil": 1.0}
        malformed["row_count"] = 1
    elif case == "row_not_object":
        malformed["result"] = [["oil", 1.0]]

    card = {
        "id": "card-1",
        "semantic_query": query.model_dump(mode="json"),
        "chart_hint": None,
        "title": "Oil",
        "cache": malformed,
        "ttl_seconds": 900,
        "state": "ready",
        "auto_size_pending": False,
        "previous": None,
    }
    updates = []
    monkeypatch.setattr(cards, "LAYER", layer)
    monkeypatch.setattr(
        execute,
        "run",
        lambda _compiled: pytest.fail("invalid cache fell through to execution"),
    )

    def update(card_id, **fields):
        updates.append(fields)
        return {**card, **fields}

    monkeypatch.setattr(cards.store, "update_card", update)

    response = cards._render_card(card)

    assert response["cache"] is None
    assert updates == [{"cache": None}]
    assert response["render"]["error_reason"] == "result_verification"
    assert response["render"]["error"] == (
        "I couldn’t verify this result safely. Nothing was saved."
    )


def test_transform_units_change_only_when_the_math_changes_the_dimension(layer):
    """A rank and same-unit ratio are not tonnes, while cumulative tonnes
    remain tonnes. This catches accidental source-unit inheritance."""
    query = SemanticQuery(
        entity="oil_mined",
        measures=[
            {"name": "actual", "transform": "rank"},
            {"name": "actual", "transform": "ratio", "per": "plan"},
            {"name": "actual", "transform": "cumulative"},
        ],
        dimensions=[DimensionRef(field="report_date", grain="month")],
    )
    compiled = compile_query(query, layer)

    assert compiled.output_units == {
        "actual_rank": None,
        "actual_per_plan": None,
        "actual_cumulative": "tonnes",
    }
    statement = restate(query, compiled.entity)
    assert "actual production (tonnes) ranked" not in statement
    assert "actual production (tonnes) per planned production" not in statement
    assert "running total of actual production (tonnes)" in statement


def test_temporal_series_tooltips_keep_the_measure_unit_and_axis_format(layer):
    """A trend tooltip must say which unit its number is in, whether it has
    one series or a shared hover value for each company."""
    single = compile_query(
        SemanticQuery(
            entity="oil_mined", measures=["actual"],
            dimensions=[DimensionRef(field="report_date", grain="month")],
        ),
        layer,
    )
    single_chart = build_spec(single, [
        {"report_date": "2026-01-01", "actual": 1200.0},
        {"report_date": "2026-02-01", "actual": 1300.0},
    ])
    single_value = single_chart.spec["layer"][-1]["encoding"]["tooltip"][-1]
    assert single_value == {
        "field": "actual", "type": "quantitative",
        "title": "actual production (tonnes)", "format": ",.0f",
    }

    series = compile_query(
        SemanticQuery(
            entity="oil_mined", measures=["actual"],
            dimensions=[
                DimensionRef(field="report_date", grain="month"),
                DimensionRef(field="company"),
            ],
        ),
        layer,
    )
    series_chart = build_spec(series, [
        {"report_date": "2026-01-01", "company": "North", "actual": 1200.0},
        {"report_date": "2026-01-01", "company": "South", "actual": 1300.0},
        {"report_date": "2026-02-01", "company": "North", "actual": 1400.0},
        {"report_date": "2026-02-01", "company": "South", "actual": 1500.0},
    ])
    shared_values = series_chart.spec["layer"][-1]["encoding"]["tooltip"][1:]
    assert [item["title"] for item in shared_values] == [
        "actual production (tonnes) — North", "actual production (tonnes) — South",
    ]
    assert {item["format"] for item in shared_values} == {",.0f"}
