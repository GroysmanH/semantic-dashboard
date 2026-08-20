"""One-step undo, per design section 9.

Refinement occasionally makes a card worse. Without this the only recourse
is rebuilding it, which is the moment someone stops trusting the edit box.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import cards as store

BY_MONTH = {"entity": "production", "measures": ["oil"],
            "dimensions": [{"field": "reading_date", "grain": "month"}]}
BY_MONTH_AND_REGION = {**BY_MONTH,
                       "dimensions": [{"field": "reading_date", "grain": "month"},
                                      {"field": "region"}]}


@pytest.fixture
def client():
    # Not a `with` block: entering TestClient's context runs the FastAPI
    # lifespan, whose shutdown closes the pools the rest of the suite shares.
    return TestClient(app)


@pytest.fixture
def card(client):
    board = client.post("/boards", json={"title": "undo"}).json()
    return client.post(f"/boards/{board['id']}/cards").json()


def refine(client, card_id, query):
    return client.post("/query", json={"semantic_query": query,
                                       "card_id": card_id, "title": "t"}).json()


def test_a_fresh_card_has_nothing_to_undo(client, card):
    assert client.get(f"/cards/{card['id']}").json()["can_undo"] is False
    r = client.post(f"/cards/{card['id']}/undo")
    assert r.status_code == 409
    assert "nothing to undo" in r.json()["detail"]


def test_a_first_query_is_not_an_edit(client, card):
    """Nothing was replaced, so there is nothing behind it."""
    refine(client, card["id"], BY_MONTH)
    assert client.get(f"/cards/{card['id']}").json()["can_undo"] is False


def test_undo_restores_the_previous_query(client, card):
    refine(client, card["id"], BY_MONTH)
    refine(client, card["id"], BY_MONTH_AND_REGION)

    assert client.get(f"/cards/{card['id']}").json()["can_undo"] is True

    restored = client.post(f"/cards/{card['id']}/undo").json()
    dims = [d["field"] for d in restored["semantic_query"]["dimensions"]]
    assert dims == ["reading_date"]
    assert restored["render"]["state"] == "ready"


def test_undo_is_one_step_and_says_so(client, card):
    """The button has to disappear. An undo that silently restores
    something older is worse than no undo at all."""
    refine(client, card["id"], BY_MONTH)
    refine(client, card["id"], BY_MONTH_AND_REGION)
    restored = client.post(f"/cards/{card['id']}/undo").json()

    assert restored["can_undo"] is False
    assert client.post(f"/cards/{card['id']}/undo").status_code == 409


def test_undo_survives_a_reload(client, card):
    refine(client, card["id"], BY_MONTH)
    refine(client, card["id"], BY_MONTH_AND_REGION)
    client.post(f"/cards/{card['id']}/undo")

    fetched = client.get(f"/cards/{card['id']}").json()
    dims = [d["field"] for d in fetched["semantic_query"]["dimensions"]]
    assert dims == ["reading_date"]
    assert fetched["can_undo"] is False


def test_undo_on_a_card_that_does_not_exist_is_a_404(client):
    assert client.post(f"/cards/{uuid.uuid4()}/undo").status_code == 404


def test_an_edit_keeps_the_card_name(client, card):
    """"Break this down by region" is an instruction, not a title. Letting
    it become one renames the card to the last thing anybody typed at it."""
    client.patch(f"/cards/{card['id']}", json={"title": "Oil production"})
    refine(client, card["id"], BY_MONTH)
    client.patch(f"/cards/{card['id']}", json={"title": "Oil production"})

    from unittest.mock import patch

    from app.llm.query_step import AskOutcome
    from app.semantic.query import SemanticQuery

    outcome = AskOutcome(
        query=SemanticQuery.model_validate(BY_MONTH_AND_REGION),
        title="break this down by region", attempts=1)
    with patch("app.routes.ask.ask_model", return_value=outcome):
        r = client.post("/ask", json={"question": "break this down by region",
                                      "card_id": str(card["id"])}).json()

    assert r["state"] == "ready"
    assert client.get(f"/cards/{card['id']}").json()["title"] == "Oil production"
