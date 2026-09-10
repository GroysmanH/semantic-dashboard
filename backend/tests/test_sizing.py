from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import layout
from app.chat import plan as planner
from app.llm.query_step import AskResponse
from app.main import app
from app.render import Render
from app.semantic.query import SemanticQuery
from app.store import cards as store
from app.store import chat as chat_store


SCALAR = {"entity": "production", "measures": ["oil"]}
BY_WELL = {
    "entity": "production",
    "measures": ["oil"],
    "dimensions": [{"field": "well_name"}],
    "limit": 20,
}


@pytest.fixture
def client():
    return TestClient(app)


def _rows(field: str, count: int) -> list[dict]:
    return [{field: f"value-{index}", "value": index} for index in range(count)]


def _axis_spec(field: str, kind: str, *, mark: str = "line") -> dict:
    return {
        "mark": {"type": mark},
        "encoding": {
            "x": {"field": field, "type": kind},
            "y": {"field": "value", "type": "quantitative"},
        },
    }


def _series_spec() -> dict:
    spec = _axis_spec("date", "temporal")
    spec["encoding"]["color"] = {"field": "series", "type": "nominal"}
    return spec


def _ready_result(query=SCALAR) -> Render:
    rows = [{"oil": 42.0}]
    return Render(
        state="ready",
        semantic_query=SemanticQuery.model_validate(query),
        vega_spec={
            "data": {"name": "table"},
            "usermeta": {"presentation": "kpi"},
            "mark": "text",
        },
        chart_type="big_number",
        rows=rows,
        row_count=1,
        cache={"key": "ready", "result": rows, "row_count": 1},
    )


def test_scalar_kpi_uses_the_compact_profile():
    spec = {"usermeta": {"presentation": "kpi"}}

    assert layout.visualization_size("big_number", spec, [{"value": 42}]) == {
        "w": 4,
        "h": 6,
    }


def test_an_ordinary_chart_uses_the_standard_profile():
    assert layout.visualization_size(
        "line", _axis_spec("date", "temporal"), _rows("date", 36)
    ) == {"w": 6, "h": 10}


@pytest.mark.parametrize("count", [9, 16])
def test_nine_to_sixteen_bar_categories_use_the_roomy_profile(count):
    assert layout.visualization_size(
        "bar", _axis_spec("category", "nominal", mark="bar"),
        _rows("category", count),
    ) == {"w": 8, "h": 12}


@pytest.mark.parametrize(
    ("count", "expected_height"),
    [(17, 15), (22, 16)],
)
def test_long_bar_rankings_grow_by_category_with_a_height_cap(
    count, expected_height
):
    assert layout.visualization_size(
        "bar", _axis_spec("category", "nominal", mark="bar"),
        _rows("category", count),
    ) == {"w": 8, "h": expected_height}


def test_more_than_thirty_six_temporal_points_use_the_roomy_profile():
    assert layout.visualization_size(
        "line", _axis_spec("date", "temporal"), _rows("date", 37)
    ) == {"w": 8, "h": 12}


@pytest.mark.parametrize("series_count", [4, 8])
def test_four_to_eight_visible_series_use_the_roomy_profile(series_count):
    rows = [
        {"date": f"2026-01-{index + 1:02d}", "series": f"s-{series}"}
        for index in range(3)
        for series in range(series_count)
    ]

    assert layout.visualization_size("line", _series_spec(), rows) == {
        "w": 8,
        "h": 12,
    }


def test_more_than_eight_visible_series_use_the_wide_profile():
    rows = [
        {"date": "2026-01-01", "series": f"s-{series}"}
        for series in range(9)
    ]

    assert layout.visualization_size("line", _series_spec(), rows) == {
        "w": 12,
        "h": 14,
    }


def test_a_moderately_dense_scatter_uses_the_roomy_profile():
    rows = [{"oil": index, "gas": index * 2} for index in range(30)]
    spec = {
        "mark": {"type": "point"},
        "encoding": {
            "x": {"field": "oil", "type": "quantitative"},
            "y": {"field": "gas", "type": "quantitative"},
        },
    }

    assert layout.visualization_size("scatter", spec, rows) == {
        "w": 8,
        "h": 12,
    }


@pytest.mark.parametrize("chart_type", ["map", "heatmap", "faceted_bar"])
def test_map_heatmap_and_facets_use_the_roomy_profile(chart_type):
    spec = (
        {"facet": {"field": "panel", "type": "nominal"}, "spec": {}}
        if chart_type == "faceted_bar"
        else {}
    )

    assert layout.visualization_size(chart_type, spec, _rows("panel", 4)) == {
        "w": 8,
        "h": 12,
    }


def test_more_than_four_facets_use_the_wide_profile():
    spec = {
        "facet": {"field": "panel", "type": "nominal"},
        "spec": _axis_spec("category", "nominal", mark="bar"),
    }

    assert layout.visualization_size(
        "faceted_bar", spec, _rows("panel", 5)
    ) == {"w": 12, "h": 14}


def test_store_created_cards_do_not_opt_in_to_future_auto_sizing_by_default():
    board = store.create_board("Existing-card exclusion")

    card = store.create_card(board["id"])

    assert card["auto_size_pending"] is False


def test_direct_first_success_sizes_once_and_refresh_or_edit_does_not_resize(client):
    board = client.post("/boards", json={"title": "Direct sizing"}).json()
    card = client.post(f"/boards/{board['id']}/cards").json()

    first = client.post("/query", json={"semantic_query": SCALAR,
                                        "card_id": card["id"]})
    refreshed = client.post(f"/cards/{card['id']}/refresh")
    edited = client.post("/query", json={"semantic_query": BY_WELL,
                                         "card_id": card["id"]})

    saved = store.get_card(uuid.UUID(card["id"]))
    assert first.status_code == 200
    assert refreshed.status_code == 200
    assert edited.status_code == 200
    assert saved["layout"] == {"x": 0, "y": 0, "w": 4, "h": 6}
    assert saved["auto_size_pending"] is False


def test_manual_resize_of_a_blank_card_cancels_later_first_success_sizing(client):
    board = client.post("/boards", json={"title": "Manual sizing"}).json()
    card = client.post(f"/boards/{board['id']}/cards").json()
    manual = {"x": 1, "y": 3, "w": 5, "h": 7}
    revision = client.get(f"/boards/{board['id']}").json()["revision"]
    client.patch(f"/boards/{board['id']}/layout", json={
        "layouts": {card["id"]: manual},
        "manually_resized": [card["id"]],
        "expected_revision": revision,
    })

    response = client.post("/query", json={"semantic_query": SCALAR,
                                           "card_id": card["id"]})

    saved = store.get_card(uuid.UUID(card["id"]))
    assert response.status_code == 200
    assert saved["layout"] == manual
    assert saved["auto_size_pending"] is False


def test_growth_keeps_the_anchor_pushes_its_collision_chain_only(client):
    board = client.post("/boards", json={"title": "Growth collision"}).json()
    first, collision, unaffected = [
        client.post(f"/boards/{board['id']}/cards").json() for _ in range(3)
    ]
    initial = {
        first["id"]: {"x": 0, "y": 0, "w": 6, "h": 10},
        collision["id"]: {"x": 6, "y": 0, "w": 6, "h": 10},
        unaffected["id"]: {"x": 0, "y": 20, "w": 4, "h": 4},
    }
    revision = client.get(f"/boards/{board['id']}").json()["revision"]
    client.patch(f"/boards/{board['id']}/layout", json={
        "layouts": initial,
        "expected_revision": revision,
    })

    response = client.post("/query", json={"semantic_query": BY_WELL,
                                           "card_id": first["id"]})

    cards = {str(card["id"]): card for card in store.list_cards(
        uuid.UUID(board["id"]))}
    assert response.status_code == 200
    assert cards[first["id"]]["layout"] == {"x": 0, "y": 0, "w": 8, "h": 16}
    assert cards[collision["id"]]["layout"] == {
        "x": 6, "y": 16, "w": 6, "h": 10,
    }
    assert cards[unaffected["id"]]["layout"] == initial[unaffected["id"]]


def test_a_wide_profile_at_the_right_edge_stays_inside_the_grid(client):
    board = client.post("/boards", json={"title": "Right edge"}).json()
    left, right = [
        client.post(f"/boards/{board['id']}/cards").json() for _ in range(2)
    ]

    response = client.post("/query", json={"semantic_query": BY_WELL,
                                           "card_id": right["id"]})

    cards = {str(card["id"]): card for card in store.list_cards(
        uuid.UUID(board["id"]))}
    assert response.status_code == 200
    assert cards[right["id"]]["layout"] == {
        "x": 4, "y": 0, "w": 8, "h": 16,
    }
    assert cards[left["id"]]["layout"] == {
        "x": 0, "y": 16, "w": 6, "h": 10,
    }


class _FakeAsk:
    provider = "gemini"
    model = "fake-1"

    def ask(self, system, user, schema):
        return AskResponse.model_validate({"semantic_query": SCALAR,
                                           "title": "Oil total"})


def test_direct_and_chat_created_cards_make_the_same_sizing_decision(client):
    direct_board = client.post("/boards", json={"title": "Direct"}).json()
    direct = client.post(f"/boards/{direct_board['id']}/cards").json()
    client.post("/query", json={"semantic_query": SCALAR,
                                "card_id": direct["id"]})

    chat_board = store.create_board("Chat")
    resolved = {
        "kind": "new_cards",
        "board_id": str(chat_board["id"]),
        "cards": [{
            "question": "total oil",
            "title": "Oil total",
            "chart_hint": None,
            "layout": {"x": 0, "y": 0, "w": 6, "h": 10},
        }],
    }
    _, placed = planner.create_placeholders(resolved)
    chat_id = placed[0]["card_id"]
    reason = planner.build_card(
        chat_id, "total oil", chart_hint=None, client=_FakeAsk()
    )

    direct_saved = store.get_card(uuid.UUID(direct["id"]))
    chat_saved = store.get_card(chat_id)
    assert reason is None
    assert direct_saved["layout"] == chat_saved["layout"] == {
        "x": 0, "y": 0, "w": 4, "h": 6,
    }
    assert direct_saved["auto_size_pending"] is False
    assert chat_saved["auto_size_pending"] is False


def test_a_failed_chat_build_leaves_its_placeholder_size_untouched():
    board = store.create_board("Failed chat")
    resolved = {
        "kind": "new_cards",
        "board_id": str(board["id"]),
        "cards": [{
            "question": "ambiguous oil",
            "title": "Oil",
            "chart_hint": None,
            "layout": {"x": 0, "y": 0, "w": 6, "h": 10},
        }],
    }
    _, placed = planner.create_placeholders(resolved)
    card_id = placed[0]["card_id"]

    class ClarifyingAsk(_FakeAsk):
        def ask(self, system, user, schema):
            return AskResponse.model_validate({
                "semantic_query": SCALAR,
                "ambiguity": {
                    "term": "oil",
                    "candidates": ["oil", "oil equivalent"],
                    "question": "Oil or oil equivalent?",
                },
            })

    reason = planner.build_card(
        card_id, "ambiguous oil", chart_hint=None, client=ClarifyingAsk()
    )

    saved = store.get_card(card_id)
    assert reason == "Oil or oil equivalent?"
    assert saved["layout"] == {"x": 0, "y": 0, "w": 6, "h": 10}
    assert saved["auto_size_pending"] is True


def test_unplottable_first_result_sizes_on_a_later_ready_refresh(client):
    board = client.post("/boards", json={"title": "Refresh sizing"}).json()
    card = client.post(f"/boards/{board['id']}/cards").json()
    query = {"entity": "production", "measures": ["oil"]}
    store.update_card(
        uuid.UUID(card["id"]),
        semantic_query=query,
        state="broken",
        cache=None,
    )

    with patch("app.routes.cards.render", return_value=_ready_result(query)):
        response = client.post(f"/cards/{card['id']}/refresh")

    saved = store.get_card(uuid.UUID(card["id"]))
    assert response.status_code == 200
    assert response.json()["layout"] == {"x": 0, "y": 0, "w": 4, "h": 6}
    assert saved["layout"] == {"x": 0, "y": 0, "w": 4, "h": 6}
    assert saved["auto_size_pending"] is False


def test_kept_transient_kpi_consumes_its_first_successful_size(
    client, monkeypatch,
):
    from app.config import settings

    monkeypatch.setattr(settings, "chat_enabled", True)
    thread = client.post("/chat/threads").json()
    board = client.post("/boards", json={"title": "Keep sizing"}).json()
    stored = chat_store.save_transient(
        uuid.UUID(thread["id"]),
        query=SCALAR,
        chart_hint=None,
        title="Oil total",
        cache={},
        ttl_seconds=900,
    )

    with patch("app.routes.chat.render", return_value=_ready_result()):
        response = client.post(
            f"/chat/transient/{stored['id']}/keep",
            json={"board_id": board["id"]},
        )

    saved = store.get_card(uuid.UUID(response.json()["id"]))
    assert response.status_code == 200
    assert saved["layout"] == {"x": 0, "y": 0, "w": 4, "h": 6}
    assert saved["auto_size_pending"] is False


def test_pending_chat_edit_consumes_sizing_on_its_successful_result(client):
    board = client.post("/boards", json={"title": "Chat edit sizing"}).json()
    card = client.post(f"/boards/{board['id']}/cards").json()
    store.update_card(
        uuid.UUID(card["id"]),
        semantic_query=BY_WELL,
        state="broken",
    )
    resolved = {
        "kind": "edit_card",
        "board_id": board["id"],
        "board_title": "Chat edit sizing",
        "card_id": card["id"],
        "card_title": "Oil",
        "instruction": "show total oil",
        "semantic_query": SCALAR,
        "chart_hint": None,
    }

    with patch("app.chat.plan.render", return_value=_ready_result()):
        planner.apply_immediate(resolved)

    saved = store.get_card(uuid.UUID(card["id"]))
    assert saved["layout"] == {"x": 0, "y": 0, "w": 4, "h": 6}
    assert saved["auto_size_pending"] is False
