"""Confirmation, over HTTP.

The rule this file exists to hold: a change the chat proposes reaches the
database only through an explicit second request. Everything else here is
about what happens when that second request arrives late, twice, or against
a dashboard that has moved on.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.llm.query_step import AskResponse
from app.main import app
from app.render import render
from app.semantic.query import SemanticQuery
from app.store import cards as store
from app.store import chat as chat_store

BY_REGION = {"entity": "production", "measures": ["oil"],
             "dimensions": [{"field": "region"}]}
BY_MONTH = {"entity": "production", "measures": ["oil"],
            "dimensions": [{"field": "reading_date", "grain": "month"}]}


class FakeClient:
    """One fake for both stages and for the query step, dispatching on the
    schema it is handed -- which is what the real seam does too."""

    provider = "gemini"
    model = "fake-1"

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.seen = []

    def ask(self, system, user, schema):
        self.seen.append(user)
        nxt = self.payloads.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if schema is AskResponse:
            return AskResponse.model_validate(nxt)
        if "turn" in schema.model_fields:
            return schema.model_validate({"turn": nxt})
        return schema.model_validate(nxt)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def chat_on(monkeypatch):
    monkeypatch.setattr(settings, "chat_enabled", True)


@pytest.fixture
def board():
    return store.create_board("Operations")


@pytest.fixture
def thread(client, chat_on):
    return client.post("/chat/threads").json()


@pytest.fixture
def fake(monkeypatch):
    """Installs one fake client for the whole request, the way a provider
    would be installed."""
    holder = {}

    def install(*payloads):
        holder["client"] = FakeClient(*payloads)
        monkeypatch.setattr("app.routes.chat.make_client",
                            lambda *a, **k: holder["client"])
        return holder["client"]

    return install


def task(kind, say="Here is what I will do."):
    return {"action": "task", "say": say, "kind": kind}


def turn(client, thread, board, question="do it"):
    return client.post(f"/chat/threads/{thread['id']}/turns", json={
        "active_board_id": str(board["id"]), "question": question})


def a_card(board, title="Oil by region"):
    card = store.create_card(board["id"])
    from app.deps import LAYER
    r = render(SemanticQuery.model_validate(BY_REGION), LAYER)
    store.update_card(card["id"], title=title, state=r.state,
                      semantic_query=r.semantic_query.model_dump(mode="json"),
                      chart_hint=r.chart_hint, vega_spec=r.vega_spec,
                      cache=r.cache)
    return store.get_card(card["id"])


# -- the proposal --------------------------------------------------------

def test_a_turn_returns_a_plan_and_changes_nothing(client, thread, board,
                                                   fake):
    fake(task("rename_dashboard"),
         {"action": "rename_dashboard", "say": "",
          "board_id": str(board["id"]), "title": "Wells"})

    body = turn(client, thread, board).json()

    assert body["pending_plan"]["action"] == "rename_dashboard"
    assert body["pending_plan"]["operations"][0]["before"] == "Operations"
    assert store.get_board(board["id"])["title"] == "Operations"


def test_the_plan_survives_a_reload(client, thread, board, fake):
    """A plan outlives the tab that proposed it. Someone who reloads
    mid-decision must find the same document."""
    fake(task("rename_dashboard"),
         {"action": "rename_dashboard", "say": "",
          "board_id": str(board["id"]), "title": "Wells"})
    proposed = turn(client, thread, board).json()["pending_plan"]

    reloaded = client.get(f"/chat/threads/{thread['id']}").json()

    assert reloaded["pending_plan"]["id"] == proposed["id"]


# -- confirming ----------------------------------------------------------

def confirm(client, plan_id):
    return client.post(f"/chat/plans/{plan_id}/confirm", json={})


def test_confirming_applies_exactly_what_was_previewed(client, thread, board,
                                                       fake):
    fake(task("rename_dashboard"),
         {"action": "rename_dashboard", "say": "",
          "board_id": str(board["id"]), "title": "Wells"})
    plan = turn(client, thread, board).json()["pending_plan"]

    out = confirm(client, plan["id"])

    assert out.status_code == 200
    assert store.get_board(board["id"])["title"] == "Wells"
    assert out.json()["message"]["action"] == "applied"


def test_confirming_twice_is_refused_rather_than_repeated(client, thread,
                                                          board, fake):
    fake(task("rename_dashboard"),
         {"action": "rename_dashboard", "say": "",
          "board_id": str(board["id"]), "title": "Wells"})
    plan = turn(client, thread, board).json()["pending_plan"]
    confirm(client, plan["id"])

    again = confirm(client, plan["id"])

    assert again.status_code == 409
    assert "confirmed" in again.json()["detail"]


def test_a_plan_whose_board_moved_is_not_applied(client, thread, board, fake):
    """Confirming means "do the thing I just read". If the dashboard is no
    longer the one described, that sentence is no longer true."""
    fake(task("rename_dashboard"),
         {"action": "rename_dashboard", "say": "",
          "board_id": str(board["id"]), "title": "Wells"})
    plan = turn(client, thread, board).json()["pending_plan"]
    store.create_card(board["id"])          # the board moves underneath

    out = confirm(client, plan["id"])

    assert out.status_code == 409
    assert store.get_board(board["id"])["title"] == "Operations"


def test_a_stale_plan_says_so_before_it_is_confirmed(client, thread, board,
                                                     fake):
    fake(task("rename_dashboard"),
         {"action": "rename_dashboard", "say": "",
          "board_id": str(board["id"]), "title": "Wells"})
    turn(client, thread, board)
    store.create_card(board["id"])

    reloaded = client.get(f"/chat/threads/{thread['id']}").json()

    assert reloaded["pending_plan"]["stale"] is True


def test_cancelling_leaves_everything_alone(client, thread, board, fake):
    fake(task("rename_dashboard"),
         {"action": "rename_dashboard", "say": "",
          "board_id": str(board["id"]), "title": "Wells"})
    plan = turn(client, thread, board).json()["pending_plan"]

    out = client.post(f"/chat/plans/{plan['id']}/cancel")

    assert out.status_code == 200
    assert store.get_board(board["id"])["title"] == "Operations"
    assert client.get(f"/chat/threads/{thread['id']}"
                      ).json()["pending_plan"] is None


def test_a_cancelled_plan_cannot_then_be_confirmed(client, thread, board,
                                                   fake):
    fake(task("rename_dashboard"),
         {"action": "rename_dashboard", "say": "",
          "board_id": str(board["id"]), "title": "Wells"})
    plan = turn(client, thread, board).json()["pending_plan"]
    client.post(f"/chat/plans/{plan['id']}/cancel")

    assert confirm(client, plan["id"]).status_code == 409
    assert store.get_board(board["id"])["title"] == "Operations"


def test_removing_a_card_goes_through_the_same_gate(client, thread, board,
                                                    fake):
    card = a_card(board)
    fake(task("delete_card"),
         {"action": "delete_card", "say": "", "card_id": str(card["id"])})
    plan = turn(client, thread, board).json()["pending_plan"]
    assert store.get_card(card["id"]) is not None

    confirm(client, plan["id"])

    assert store.get_card(card["id"]) is None
    # Soft, so it is still recoverable.
    assert store.restore_card(card["id"]) is not None


# -- generation ----------------------------------------------------------

def test_cards_are_created_and_then_filled_in(client, thread, board, fake):
    """The dashboard appears at once and the questions are answered after.
    TestClient runs background tasks before returning, so by here the whole
    sequence has played out."""
    fake(task("new_cards"),
         {"action": "new_cards", "say": "", "cards": [
             {"request_id": "r1", "question": "oil by region",
              "title": "Oil by region"}]},
         {"semantic_query": BY_REGION, "title": "Oil by region"})
    plan = turn(client, thread, board).json()["pending_plan"]

    out = confirm(client, plan["id"]).json()

    assert out["action"]["total"] == 1
    built = [c for c in store.list_cards(board["id"])
             if c["title"] == "Oil by region"]
    assert built and built[0]["state"] == "ready"


def test_one_refused_question_does_not_take_the_rest_down(client, thread,
                                                          board, fake):
    fake(task("new_cards"),
         {"action": "new_cards", "say": "", "cards": [
             {"request_id": "r1", "question": "oil by region",
              "title": "Good"},
             {"request_id": "r2", "question": "drilling cost",
              "title": "Bad"}]},
         {"semantic_query": BY_REGION, "title": "Good"},
         {"semantic_query": BY_REGION, "ambiguity": {
             "term": "cost", "candidates": ["a", "b"],
             "question": "Which cost?"}})
    plan = turn(client, thread, board).json()["pending_plan"]

    action = confirm(client, plan["id"]).json()["action"]
    progress = client.get(f"/chat/actions/{action['id']}").json()

    assert progress["completed"] == 1
    assert progress["failed"] == 1
    assert progress["status"] == "done"
    states = {c["title"]: c["state"] for c in store.list_cards(board["id"])}
    assert states["Good"] == "ready"
    # The placeholder stays and says what it was for, rather than vanishing.
    assert states["Bad"] == "empty"


def test_the_event_log_is_replayable_from_any_point(client, thread, board,
                                                    fake):
    fake(task("new_cards"),
         {"action": "new_cards", "say": "", "cards": [
             {"request_id": "r1", "question": "oil by region",
              "title": "Oil"}]},
         {"semantic_query": BY_REGION, "title": "Oil"})
    plan = turn(client, thread, board).json()["pending_plan"]
    action = confirm(client, plan["id"]).json()["action"]

    events = client.get(f"/chat/actions/{action['id']}/events").json()
    kinds = [e["event"]["kind"] for e in events]

    assert kinds == ["plan", "item_started", "card", "done"]
    tail = client.get(f"/chat/actions/{action['id']}/events",
                      params={"after": events[0]["event"]["id"]}).json()
    assert [e["event"]["kind"] for e in tail] == kinds[1:]


def test_a_new_dashboard_arrives_as_a_new_tab(client, thread, board, fake):
    fake(task("new_dashboard"),
         {"action": "new_dashboard", "say": "", "title": "Wells", "cards": [
             {"request_id": "r1", "question": "oil by region",
              "title": "Oil"}]},
         {"semantic_query": BY_REGION, "title": "Oil"})
    plan = turn(client, thread, board).json()["pending_plan"]
    assert plan["target_board_id"] is None

    out = confirm(client, plan["id"]).json()

    created = store.get_board(uuid.UUID(out["board_id"]))
    assert created["title"] == "Wells"
    assert [c["title"] for c in store.list_cards(created["id"])] == ["Oil"]


def test_stopping_leaves_the_cards_already_built(client, thread, board, fake,
                                                 monkeypatch):
    """Stop means "after this one", not "in the middle of one". A card half
    written to the database is worse than one extra card."""
    fake(task("new_cards"),
         {"action": "new_cards", "say": "", "cards": [
             {"request_id": "r1", "question": "oil by region", "title": "One"},
             {"request_id": "r2", "question": "oil by region",
              "title": "Two"}]},
         {"semantic_query": BY_REGION, "title": "One"})
    plan = turn(client, thread, board).json()["pending_plan"]

    real = chat_store.get_action

    def stop_after_first(action_id, **kw):
        action = real(action_id, **kw)
        if action is not None:
            chat_store.request_cancel(action_id)
        return action

    monkeypatch.setattr("app.chat.confirm.chat_store.get_action",
                        stop_after_first)
    action = confirm(client, plan["id"]).json()["action"]

    progress = client.get(f"/chat/actions/{action['id']}").json()
    assert progress["status"] == "stopped"
    titles = {c["title"] for c in store.list_cards(board["id"])}
    assert {"One", "Two"} <= titles, "the placeholders stay either way"


# -- the gate ------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("post", "/chat/plans/{id}/confirm"),
    ("post", "/chat/plans/{id}/cancel"),
    ("get", "/chat/actions/{id}"),
    ("get", "/chat/actions/{id}/events"),
    ("post", "/chat/actions/{id}/stop"),
])
def test_the_new_routes_are_absent_while_chat_is_disabled(client, method,
                                                          path, monkeypatch):
    monkeypatch.setattr(settings, "chat_enabled", False)
    call = getattr(client, method)
    kwargs = {"json": {}} if method == "post" else {}
    assert call(path.format(id=uuid.uuid4()), **kwargs).status_code == 404


def test_an_unknown_plan_is_not_found(client, chat_on):
    assert confirm(client, uuid.uuid4()).status_code == 404
