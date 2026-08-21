"""Boards as tabs: ordering, renaming, and the scoping the layout route
never had.

There were no route tests for boards at all before this. The layout case is
the reason there are now: `PATCH /boards/{id}/layout` accepted a board id and
then ignored it, so a request naming one board could move another board's
cards.
"""

import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.db import app_pool
from app.main import app
from app.store import cards as store


@pytest.fixture
def client():
    # Not a `with` block: entering TestClient's context runs the FastAPI
    # lifespan, whose shutdown closes the pools the rest of the suite shares.
    return TestClient(app)


def make_board(client, title):
    return client.post("/boards", json={"title": title}).json()


def ids(boards):
    return [b["id"] for b in boards]


def raw_revision(board_id):
    with app_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT revision FROM app.board WHERE id = %s", (board_id,))
        return cur.fetchone()[0]


@contextmanager
def only_visible(board_id):
    """Temporarily isolate the last-board invariant without depending on
    what another test left in its fixture baseline."""
    with app_pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE app.board SET deleted_at = now() "
            "WHERE id <> %s AND deleted_at IS NULL RETURNING id",
            (board_id,),
        )
        hidden = [row[0] for row in cur.fetchall()]
    try:
        yield
    finally:
        if hidden:
            with app_pool.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE app.board SET deleted_at = NULL WHERE id = ANY(%s)",
                    (hidden,),
                )


# -- ordering ------------------------------------------------------------

def test_new_boards_land_at_the_end(client):
    a = make_board(client, "first")
    b = make_board(client, "second")
    listed = ids(client.get("/boards").json())
    assert listed.index(a["id"]) < listed.index(b["id"])


def test_a_new_board_gets_a_position_past_every_existing_one(client):
    make_board(client, "existing")
    fresh = make_board(client, "fresh")
    others = [b["position"] for b in client.get("/boards").json()
              if b["id"] != fresh["id"]]
    assert fresh["position"] > max(others)


def test_listing_is_ordered_by_position_not_creation(client):
    a = make_board(client, "a")
    b = make_board(client, "b")
    order = ids(client.get("/boards").json())
    order.remove(a["id"])
    order.remove(b["id"])
    order.extend((b["id"], a["id"]))
    client.post("/boards/reorder", json={"order": order})
    listed = ids(client.get("/boards").json())
    assert listed.index(b["id"]) < listed.index(a["id"])


def test_reorder_is_not_captured_as_a_board_id(client):
    # /boards/reorder must not be parsed as /boards/{board_id}; if the route
    # order is wrong this 422s on uuid parsing instead of reordering.
    order = ids(client.get("/boards").json())
    assert client.post("/boards/reorder", json={"order": order}).status_code == 204


@pytest.mark.parametrize("invalid", ["duplicate", "omission", "unknown"])
def test_reorder_requires_an_exact_visible_board_permutation(client, invalid):
    make_board(client, "order a")
    make_board(client, "order b")
    order = ids(client.get("/boards").json())
    before = store.board_basis(uuid.UUID(board_id) for board_id in order)

    if invalid == "duplicate":
        proposed = [*order, order[0]]
    elif invalid == "omission":
        proposed = order[:-1]
    else:
        proposed = [*order[:-1], str(uuid.uuid4())]

    response = client.post("/boards/reorder", json={"order": proposed})

    assert response.status_code == 409
    assert store.board_basis(uuid.UUID(board_id) for board_id in order) == before


# -- renaming ------------------------------------------------------------

def test_a_board_can_be_renamed(client):
    board = make_board(client, "before")
    renamed = client.patch(f"/boards/{board['id']}", json={"title": "after"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "after"
    assert client.get(f"/boards/{board['id']}").json()["title"] == "after"


def test_renaming_leaves_position_alone(client):
    board = make_board(client, "keep my slot")
    before = board["position"]
    after = client.patch(f"/boards/{board['id']}", json={"title": "new"}).json()
    assert after["position"] == before


def test_patching_an_unknown_board_is_404(client):
    missing = client.patch(f"/boards/{uuid.uuid4()}", json={"title": "x"})
    assert missing.status_code == 404


def test_an_empty_patch_changes_nothing(client):
    board = make_board(client, "untouched")
    after = client.patch(f"/boards/{board['id']}", json={}).json()
    assert after["title"] == "untouched"


# -- layout scoping ------------------------------------------------------

def test_layout_is_scoped_to_the_board_that_owns_the_card(client):
    """The bug this file exists for. A layout request naming board A must
    not move a card that belongs to board B, however the card id was
    obtained."""
    a = make_board(client, "board a")
    b = make_board(client, "board b")
    victim = client.post(f"/boards/{b['id']}/cards").json()
    before = victim["layout"]

    moved = client.patch(f"/boards/{a['id']}/layout", json={
        "layouts": {victim["id"]: {"x": 11, "y": 99, "w": 1, "h": 1}}})
    assert moved.status_code == 204

    after = client.get(f"/cards/{victim['id']}").json()["layout"]
    assert after == before, "a card was moved by another board's request"


def test_layout_still_saves_for_the_owning_board(client):
    board = make_board(client, "owner")
    card = client.post(f"/boards/{board['id']}/cards").json()
    target = {"x": 6, "y": 3, "w": 4, "h": 7}

    client.patch(f"/boards/{board['id']}/layout",
                 json={"layouts": {card["id"]: target}})

    assert client.get(f"/cards/{card['id']}").json()["layout"] == target


# -- deletion ------------------------------------------------------------

def test_deleting_a_board_takes_its_cards_with_it(client):
    make_board(client, "survivor")
    board = make_board(client, "doomed")
    card = client.post(f"/boards/{board['id']}/cards").json()
    client.delete(f"/boards/{board['id']}")
    assert client.get(f"/cards/{card['id']}").status_code == 404


def test_direct_delete_refuses_to_remove_the_last_visible_board(client):
    board = make_board(client, "last visible")
    with only_visible(board["id"]):
        response = client.delete(f"/boards/{board['id']}")
        assert response.status_code == 409
        assert client.get(f"/boards/{board['id']}").status_code == 200


def test_soft_deleted_cards_and_boards_are_hidden_until_restored(client):
    make_board(client, "visible survivor")
    board = make_board(client, "recoverable")
    card = client.post(f"/boards/{board['id']}/cards").json()
    board_id = uuid.UUID(board["id"])
    card_id = uuid.UUID(card["id"])
    before_card_delete = store.get_board(board_id)["revision"]

    deleted_card = store.soft_delete_card(card_id)
    assert deleted_card["deleted_at"] is not None
    after_card_delete = store.get_board(board_id)["revision"]
    assert after_card_delete > before_card_delete
    assert client.get(f"/cards/{card['id']}").status_code == 404
    store.restore_card(card_id)
    after_card_restore = store.get_board(board_id)["revision"]
    assert after_card_restore > after_card_delete
    assert client.get(f"/cards/{card['id']}").status_code == 200

    before_board_delete = store.get_board(board_id)["revision"]
    store.soft_delete_board(board_id)
    after_board_delete = raw_revision(board_id)
    assert after_board_delete > before_board_delete
    assert board["id"] not in ids(client.get("/boards").json())
    assert store.board_basis([board_id, uuid.uuid4()]) == {}
    assert client.get(f"/boards/{board['id']}").status_code == 404
    assert client.get(f"/cards/{card['id']}").status_code == 404
    restored = store.restore_board(board_id)
    assert restored["revision"] > after_board_delete
    assert store.board_basis([board_id, uuid.uuid4()]) == {
        board["id"]: restored["revision"],
    }
    assert client.get(f"/boards/{board['id']}").status_code == 200
    assert client.get(f"/cards/{card['id']}").status_code == 200


def test_soft_delete_cannot_hide_the_last_visible_board(client):
    board = make_board(client, "last recoverable")
    with only_visible(board["id"]):
        with pytest.raises(store.LastVisibleBoardError):
            store.soft_delete_board(uuid.UUID(board["id"]))


def test_revisions_track_substantive_changes_but_not_render_cache(client):
    board = make_board(client, "revision owner")
    board_id = uuid.UUID(board["id"])
    initial = store.get_board(board_id)["revision"]

    card = client.post(f"/boards/{board['id']}/cards").json()
    after_membership = store.get_board(board_id)["revision"]
    assert after_membership > initial

    store.update_card(
        uuid.UUID(card["id"]),
        cache={"key": "cache-only"},
        state="ready",
        vega_spec={"mark": "bar"},
    )
    assert store.get_board(board_id)["revision"] == after_membership

    store.update_card(
        uuid.UUID(card["id"]),
        semantic_query={"entity": "production", "measures": ["oil"]},
    )
    after_semantic = store.get_board(board_id)["revision"]
    assert after_semantic > after_membership

    client.patch(
        f"/boards/{board['id']}/layout",
        json={"layouts": {card["id"]: {"x": 6, "y": 2, "w": 6, "h": 10}}},
    )
    after_layout = store.get_board(board_id)["revision"]
    assert after_layout > after_semantic

    client.patch(f"/boards/{board['id']}", json={"title": "renamed revision owner"})
    assert store.get_board(board_id)["revision"] > after_layout


def test_reorder_increments_every_affected_board_revision(client):
    a = make_board(client, "revision a")
    b = make_board(client, "revision b")
    order = ids(client.get("/boards").json())
    before = store.board_basis(uuid.UUID(board_id) for board_id in order)
    order.remove(a["id"])
    order.remove(b["id"])
    order.extend((b["id"], a["id"]))

    client.post("/boards/reorder", json={"order": order})

    after = store.board_basis([uuid.UUID(a["id"]), uuid.UUID(b["id"])])
    assert after[a["id"]] > before[a["id"]]
    assert after[b["id"]] > before[b["id"]]


def test_two_cards_created_for_one_board_receive_distinct_slots(client):
    board = make_board(client, "serialized slots")
    first = client.post(f"/boards/{board['id']}/cards").json()
    second = client.post(f"/boards/{board['id']}/cards").json()

    assert (first["layout"]["x"], first["layout"]["y"]) != (
        second["layout"]["x"], second["layout"]["y"],
    )


def test_creating_a_card_for_an_unknown_board_is_404(client):
    response = client.post(f"/boards/{uuid.uuid4()}/cards")
    assert response.status_code == 404


def test_hard_card_delete_changes_membership_revision(client):
    board = make_board(client, "hard card owner")
    card = client.post(f"/boards/{board['id']}/cards").json()
    before = store.get_board(uuid.UUID(board["id"]))["revision"]

    assert client.delete(f"/cards/{card['id']}").status_code == 204
    assert client.get(f"/cards/{card['id']}").status_code == 404
    assert store.get_board(uuid.UUID(board["id"]))["revision"] > before


def test_deletion_store_has_no_ambiguous_aliases():
    assert not hasattr(store, "delete_board")
    assert not hasattr(store, "delete_card")
