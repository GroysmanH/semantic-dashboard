"""Boards as tabs: ordering, renaming, and the scoping the layout route
never had.

There were no route tests for boards at all before this. The layout case is
the reason there are now: `PATCH /boards/{id}/layout` accepted a board id and
then ignored it, so a request naming one board could move another board's
cards.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    # Not a `with` block: entering TestClient's context runs the FastAPI
    # lifespan, whose shutdown closes the pools the rest of the suite shares.
    return TestClient(app)


def make_board(client, title):
    return client.post("/boards", json={"title": title}).json()


def ids(boards):
    return [b["id"] for b in boards]


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
    client.post("/boards/reorder", json={"order": [b["id"], a["id"]]})
    listed = ids(client.get("/boards").json())
    assert listed.index(b["id"]) < listed.index(a["id"])


def test_reorder_is_not_captured_as_a_board_id(client):
    # /boards/reorder must not be parsed as /boards/{board_id}; if the route
    # order is wrong this 422s on uuid parsing instead of reordering.
    assert client.post("/boards/reorder", json={"order": []}).status_code == 204


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
    board = make_board(client, "doomed")
    card = client.post(f"/boards/{board['id']}/cards").json()
    client.delete(f"/boards/{board['id']}")
    assert client.get(f"/cards/{card['id']}").status_code == 404
