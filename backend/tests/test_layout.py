from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import cards as store


@pytest.fixture
def client():
    return TestClient(app)


def make_board(client, title="Layout board"):
    return client.post("/boards", json={"title": title}).json()


def layouts_for(client, board_id):
    cards = client.get(f"/boards/{board_id}").json()["cards"]
    return {card["id"]: card["layout"] for card in cards}


def layout_body(client, board_id, layouts, *, manually_resized=None):
    board = client.get(f"/boards/{board_id}").json()
    return {
        "layouts": layouts,
        "manually_resized": manually_resized or [],
        "expected_revision": board["revision"],
    }


def test_vertical_packer_removes_gaps_and_preserves_horizontal_geometry():
    from app.layout import pack_vertical

    packed = pack_vertical({
        "later": {"x": 0, "y": 20, "w": 6, "h": 4},
        "right": {"x": 6, "y": 8, "w": 6, "h": 7},
        "first": {"x": 0, "y": 5, "w": 6, "h": 3},
    })

    assert packed == {
        "first": {"x": 0, "y": 0, "w": 6, "h": 3},
        "right": {"x": 6, "y": 0, "w": 6, "h": 7},
        "later": {"x": 0, "y": 3, "w": 6, "h": 4},
    }


def test_vertical_packer_uses_stable_id_to_break_equal_anchor_ties():
    from app.layout import pack_vertical

    packed = pack_vertical({
        "beta": {"x": 2, "y": 4, "w": 4, "h": 2},
        "alpha": {"x": 2, "y": 4, "w": 4, "h": 3},
    })

    assert packed["alpha"]["y"] == 0
    assert packed["beta"]["y"] == 3


def test_vertical_packer_skips_only_actual_horizontal_collisions():
    from app.layout import pack_vertical

    packed = pack_vertical({
        "wide": {"x": 2, "y": 0, "w": 6, "h": 4},
        "left": {"x": 0, "y": 1, "w": 2, "h": 9},
        "overlap": {"x": 7, "y": 2, "w": 3, "h": 2},
    })

    assert packed["wide"]["y"] == 0
    assert packed["left"]["y"] == 0
    assert packed["overlap"]["y"] == 4


def test_final_size_batch_placement_compacts_the_six_card_regression_in_order():
    from app.layout import place_final_size_batch

    placed = place_final_size_batch(
        {},
        [
            ("kpi-one", {"x": 0, "y": 0, "w": 4, "h": 6}),
            ("kpi-two", {"x": 6, "y": 0, "w": 4, "h": 6}),
            ("bar", {"x": 0, "y": 10, "w": 6, "h": 10}),
            ("dense-one", {"x": 6, "y": 10, "w": 8, "h": 16}),
            ("dense-two", {"x": 0, "y": 20, "w": 8, "h": 16}),
            ("dense-three", {"x": 6, "y": 20, "w": 8, "h": 16}),
        ],
        insertion_row=0,
    )

    assert placed == {
        "kpi-one": {"x": 0, "y": 0, "w": 4, "h": 6},
        "kpi-two": {"x": 4, "y": 0, "w": 4, "h": 6},
        "bar": {"x": 0, "y": 6, "w": 6, "h": 10},
        "dense-one": {"x": 0, "y": 16, "w": 8, "h": 16},
        "dense-two": {"x": 0, "y": 32, "w": 8, "h": 16},
        "dense-three": {"x": 0, "y": 48, "w": 8, "h": 16},
    }


def test_final_size_batch_placement_keeps_existing_cards_fixed_and_starts_at_insertion_row():
    from app.layout import place_final_size_batch

    existing = {
        "fixed-top": {"x": 0, "y": 0, "w": 12, "h": 7},
        "fixed-lower": {"x": 8, "y": 12, "w": 4, "h": 9},
    }

    placed = place_final_size_batch(
        existing,
        [
            ("first", {"x": 6, "y": 10, "w": 4, "h": 6}),
            ("wide", {"x": 0, "y": 20, "w": 8, "h": 12}),
        ],
        insertion_row=10,
    )

    assert placed == {
        **existing,
        "first": {"x": 0, "y": 10, "w": 4, "h": 6},
        "wide": {"x": 0, "y": 16, "w": 8, "h": 12},
    }


def test_batch_render_and_layout_reflow_roll_back_together(client, monkeypatch):
    board = make_board(client)
    cards = [
        store.create_card(uuid.UUID(board["id"]), auto_size_pending=True)
        for _ in range(2)
    ]
    before = {
        str(card["id"]): card["layout"]
        for card in store.list_cards(uuid.UUID(board["id"]))
    }

    def fail_placement(*args, **kwargs):
        raise RuntimeError("forced batch placement failure")

    monkeypatch.setattr(
        store, "place_final_size_batch", fail_placement, raising=False
    )

    with pytest.raises(RuntimeError, match="forced batch placement failure"):
        store.save_rendered_batch_card(
            cards[0]["id"],
            batch_card_ids=[card["id"] for card in cards],
            insertion_row=0,
            size={"w": 4, "h": 6},
            state="ready",
        )

    saved = store.get_card(cards[0]["id"])
    assert saved["state"] == "empty"
    assert saved["auto_size_pending"] is True
    assert {
        str(card["id"]): card["layout"]
        for card in store.list_cards(uuid.UUID(board["id"]))
    } == before


def test_new_blank_cards_explicitly_wait_for_their_first_successful_size(client):
    board = make_board(client)

    card = client.post(f"/boards/{board['id']}/cards").json()

    assert board["layout_mode"] == "free"
    assert card["auto_size_pending"] is True


@pytest.mark.parametrize(
    "layout",
    [
        {"x": -1, "y": 0, "w": 1, "h": 1},
        {"x": 0, "y": -1, "w": 1, "h": 1},
        {"x": 0, "y": 0, "w": 0, "h": 1},
        {"x": 0, "y": 0, "w": 1, "h": 0},
        {"x": 11, "y": 0, "w": 2, "h": 1},
        {"x": 0.5, "y": 0, "w": 1, "h": 1},
    ],
)
def test_layout_route_rejects_geometry_outside_the_integer_grid(client, layout):
    board = make_board(client)
    card = client.post(f"/boards/{board['id']}/cards").json()

    response = client.patch(
        f"/boards/{board['id']}/layout",
        json=layout_body(client, board["id"], {card["id"]: layout}),
    )

    assert response.status_code == 422


def test_layout_route_requires_the_complete_owned_card_set(client):
    board = make_board(client, "owner")
    first = client.post(f"/boards/{board['id']}/cards").json()
    second = client.post(f"/boards/{board['id']}/cards").json()
    other = make_board(client, "other")
    outsider = client.post(f"/boards/{other['id']}/cards").json()
    before = layouts_for(client, board["id"])

    missing = client.patch(
        f"/boards/{board['id']}/layout",
        json=layout_body(client, board["id"], {first["id"]: first["layout"]}),
    )
    foreign = client.patch(
        f"/boards/{board['id']}/layout",
        json=layout_body(client, board["id"], {
            first["id"]: first["layout"],
            second["id"]: second["layout"],
            outsider["id"]: outsider["layout"],
        }),
    )

    assert missing.status_code == 409
    assert foreign.status_code == 409
    assert layouts_for(client, board["id"]) == before


def test_layout_route_returns_canonical_collision_free_layout_and_revision(client):
    board = make_board(client)
    cards = [client.post(f"/boards/{board['id']}/cards").json()
             for _ in range(2)]
    ordered = sorted(card["id"] for card in cards)
    proposed = {
        card_id: {"x": 2, "y": 5, "w": 4, "h": 3}
        for card_id in ordered
    }

    response = client.patch(
        f"/boards/{board['id']}/layout",
        json=layout_body(client, board["id"], proposed),
    )

    assert response.status_code == 200
    assert response.json()["layouts"] == {
        ordered[0]: {"x": 2, "y": 5, "w": 4, "h": 3},
        ordered[1]: {"x": 2, "y": 8, "w": 4, "h": 3},
    }
    assert response.json()["revision"] > board["revision"]
    assert layouts_for(client, board["id"]) == response.json()["layouts"]


def test_manual_resize_cancels_pending_auto_size_only_for_named_cards(client):
    board = make_board(client)
    first = client.post(f"/boards/{board['id']}/cards").json()
    second = client.post(f"/boards/{board['id']}/cards").json()
    proposed = {first["id"]: first["layout"], second["id"]: second["layout"]}

    client.patch(
        f"/boards/{board['id']}/layout",
        json=layout_body(
            client, board["id"], proposed, manually_resized=[first["id"]]
        ),
    )

    assert store.get_card(uuid.UUID(first["id"]))["auto_size_pending"] is False
    assert store.get_card(uuid.UUID(second["id"]))["auto_size_pending"] is True


def test_enabling_auto_pack_immediately_removes_vertical_gaps(client):
    board = make_board(client)
    cards = [client.post(f"/boards/{board['id']}/cards").json()
             for _ in range(2)]
    proposed = {
        cards[0]["id"]: {"x": 0, "y": 8, "w": 6, "h": 4},
        cards[1]["id"]: {"x": 6, "y": 15, "w": 6, "h": 5},
    }
    client.patch(
        f"/boards/{board['id']}/layout",
        json=layout_body(client, board["id"], proposed),
    )

    enabled = client.patch(
        f"/boards/{board['id']}", json={"layout_mode": "auto_pack"}
    )

    assert enabled.status_code == 200
    assert enabled.json()["layout_mode"] == "auto_pack"
    assert layouts_for(client, board["id"]) == {
        cards[0]["id"]: {"x": 0, "y": 0, "w": 6, "h": 4},
        cards[1]["id"]: {"x": 6, "y": 0, "w": 6, "h": 5},
    }


def test_auto_pack_reflows_up_after_card_deletion(client):
    board = make_board(client)
    cards = [client.post(f"/boards/{board['id']}/cards").json()
             for _ in range(3)]
    proposed = {
        card["id"]: {"x": 0, "y": index * 4, "w": 6, "h": 4}
        for index, card in enumerate(cards)
    }
    client.patch(
        f"/boards/{board['id']}/layout",
        json=layout_body(client, board["id"], proposed),
    )
    client.patch(f"/boards/{board['id']}", json={"layout_mode": "auto_pack"})

    assert client.delete(f"/cards/{cards[0]['id']}").status_code == 204

    assert layouts_for(client, board["id"]) == {
        cards[1]["id"]: {"x": 0, "y": 0, "w": 6, "h": 4},
        cards[2]["id"]: {"x": 0, "y": 4, "w": 6, "h": 4},
    }


def test_disabling_auto_pack_freezes_subsequent_free_layout(client):
    board = make_board(client)
    card = client.post(f"/boards/{board['id']}/cards").json()
    client.patch(f"/boards/{board['id']}", json={"layout_mode": "auto_pack"})
    client.patch(f"/boards/{board['id']}", json={"layout_mode": "free"})

    response = client.patch(
        f"/boards/{board['id']}/layout",
        json=layout_body(client, board["id"], {
            card["id"]: {"x": 3, "y": 19, "w": 5, "h": 7}
        }),
    )

    assert response.json()["layouts"][card["id"]] == {
        "x": 3, "y": 19, "w": 5, "h": 7,
    }


def test_card_duplicate_copies_finished_fields_but_not_unfinished_business(client):
    board = make_board(client)
    source = client.post(f"/boards/{board['id']}/cards").json()
    source = store.update_card(
        uuid.UUID(source["id"]),
        title="Oil by region",
        semantic_query={"entity": "production", "measures": ["oil"]},
        chart_hint="bar",
        vega_spec={"mark": "bar"},
        prompt="show oil by region",
        state="ready",
        cache={"result": [{"region": "West", "oil": 12}]},
        ttl_seconds=321,
        previous={"semantic_query": {"entity": "production"}},
        pending_clarification={"question": "Which region?"},
        layout={"x": 0, "y": 0, "w": 4, "h": 7},
    )

    response = client.post(f"/cards/{source['id']}/duplicate")
    copied = response.json()["card"]

    assert response.status_code == 200
    assert copied["id"] != source["id"]
    assert copied["board_id"] == str(source["board_id"])
    assert copied["title"] == "Copy of Oil by region"
    for field in (
        "semantic_query", "chart_hint", "vega_spec", "prompt", "state",
        "cache", "ttl_seconds",
    ):
        assert copied[field] == source[field]
    assert copied["layout"]["w"] == source["layout"]["w"]
    assert copied["layout"]["h"] == source["layout"]["h"]
    assert copied["previous"] is None
    assert copied["pending_clarification"] is None
    assert copied["auto_size_pending"] is False
    assert response.json()["layouts"] == layouts_for(client, board["id"])


def test_card_duplicate_prefers_the_open_space_to_the_right(client):
    board = make_board(client)
    source = client.post(f"/boards/{board['id']}/cards").json()
    store.update_card(
        uuid.UUID(source["id"]), layout={"x": 1, "y": 5, "w": 4, "h": 3}
    )

    copied = client.post(f"/cards/{source['id']}/duplicate").json()["card"]

    assert copied["layout"] == {"x": 5, "y": 5, "w": 4, "h": 3}


def test_card_duplicate_uses_below_when_right_is_outside_the_grid(client):
    board = make_board(client)
    source = client.post(f"/boards/{board['id']}/cards").json()
    store.update_card(
        uuid.UUID(source["id"]), layout={"x": 8, "y": 2, "w": 4, "h": 5}
    )

    copied = client.post(f"/cards/{source['id']}/duplicate").json()["card"]

    assert copied["layout"] == {"x": 8, "y": 7, "w": 4, "h": 5}


def test_card_duplicate_uses_the_nearest_stable_anchor_when_priority_slots_are_busy(
    client,
):
    board = make_board(client)
    cards = [client.post(f"/boards/{board['id']}/cards").json()
             for _ in range(3)]
    client.patch(f"/boards/{board['id']}/layout", json=layout_body(
        client, board["id"], {
        cards[0]["id"]: {"x": 0, "y": 0, "w": 4, "h": 4},
        cards[1]["id"]: {"x": 4, "y": 0, "w": 4, "h": 4},
        cards[2]["id"]: {"x": 0, "y": 4, "w": 4, "h": 4},
    }))

    copied = client.post(f"/cards/{cards[0]['id']}/duplicate").json()["card"]

    assert copied["layout"] == {"x": 8, "y": 0, "w": 4, "h": 4}


def test_card_duplicate_is_independent_of_its_source(client):
    board = make_board(client)
    source = client.post(f"/boards/{board['id']}/cards").json()
    store.update_card(uuid.UUID(source["id"]), title="Source")
    copied = client.post(f"/cards/{source['id']}/duplicate").json()["card"]

    store.update_card(uuid.UUID(copied["id"]), title="Changed copy")

    assert store.get_card(uuid.UUID(source["id"]))["title"] == "Source"


def test_card_duplicate_rolls_back_if_canonical_layout_resolution_fails(
    client, monkeypatch,
):
    board = make_board(client)
    source = client.post(f"/boards/{board['id']}/cards").json()
    before = len(store.list_cards(uuid.UUID(board["id"])))

    def fail_layout(*args, **kwargs):
        raise RuntimeError("forced canonicalization failure")

    monkeypatch.setattr(store, "canonical_layouts", fail_layout)
    quiet_client = TestClient(app, raise_server_exceptions=False)
    response = quiet_client.post(f"/cards/{source['id']}/duplicate")

    assert response.status_code == 500
    assert len(store.list_cards(uuid.UUID(board["id"]))) == before


def test_card_duplicate_of_missing_card_is_not_found(client):
    assert client.post(f"/cards/{uuid.uuid4()}/duplicate").status_code == 404


def test_stale_complete_layout_write_is_rejected_with_current_canonical_state(
    client,
):
    board = make_board(client, "two clients")
    first = client.post(f"/boards/{board['id']}/cards").json()
    second = client.post(f"/boards/{board['id']}/cards").json()
    snapshot = client.get(f"/boards/{board['id']}").json()
    initial = {card["id"]: card["layout"] for card in snapshot["cards"]}

    client_a = {
        **initial,
        first["id"]: {"x": 0, "y": 4, "w": 6, "h": 10},
    }
    accepted = client.patch(f"/boards/{board['id']}/layout", json={
        "layouts": client_a,
        "manually_resized": [],
        "expected_revision": snapshot["revision"],
    })
    assert accepted.status_code == 200

    client_b = {
        **initial,
        second["id"]: {"x": 6, "y": 30, "w": 6, "h": 10},
    }
    stale = client.patch(f"/boards/{board['id']}/layout", json={
        "layouts": client_b,
        "manually_resized": [],
        "expected_revision": snapshot["revision"],
    })

    assert stale.status_code == 409
    assert stale.json() == {
        "code": "layout_conflict",
        "message": "This dashboard changed while you were arranging it.",
        "layouts": accepted.json()["layouts"],
        "revision": accepted.json()["revision"],
    }
    assert layouts_for(client, board["id"]) == accepted.json()["layouts"]
