"""Confirmation, over HTTP.

The rule this file exists to hold: a change the chat proposes reaches the
database only through an explicit second request. Everything else here is
about what happens when that second request arrives late, twice, or against
a dashboard that has moved on.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.chat import plan as planner
from app.config import settings
from app.chat.confirm import run_action
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


def final_sizes(monkeypatch, *sizes):
    remaining = iter(sizes)
    monkeypatch.setattr(
        "app.chat.plan.visualization_size",
        lambda chart_type, spec, rows: next(remaining),
    )


def queued_card_action(client, thread, board, fake, monkeypatch):
    """Confirm one create action without letting the background worker run."""
    monkeypatch.setattr(
        "app.routes.chat.BackgroundTasks.add_task", lambda *a, **k: None
    )
    model = fake(
        task("new_cards"),
        {"action": "new_cards", "say": "", "cards": [{
            "request_id": "r1", "question": "oil by region",
            "title": "Queued",
        }]},
    )
    plan = turn(client, thread, board).json()["pending_plan"]
    action_id = uuid.UUID(confirm(client, plan["id"]).json()["action"]["id"])
    return action_id, model


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
         {"action": "delete_card", "say": "",
          "card_ids": [str(card["id"])]})
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
    # The board did not exist when the plan was written, so the action has
    # to be told about it or the browser follows the build on nothing.
    assert out["action"]["board_id"] == str(created["id"])


def test_new_dashboard_reflows_from_final_sizes_and_reloads_server_geometry(
    client, thread, board, fake, monkeypatch,
):
    final_sizes(
        monkeypatch,
        {"w": 4, "h": 6},
        {"w": 4, "h": 6},
        {"w": 8, "h": 12},
    )
    detail = {
        "action": "new_dashboard", "say": "", "title": "Compact",
        "cards": [
            {"request_id": f"r{index}", "question": "oil by region",
             "title": title}
            for index, title in enumerate(("One", "Two", "Wide"), start=1)
        ],
    }
    fake(
        task("new_dashboard"), detail,
        *({"semantic_query": BY_REGION, "title": title}
          for title in ("One", "Two", "Wide")),
    )
    plan = turn(client, thread, board).json()["pending_plan"]

    out = confirm(client, plan["id"]).json()
    board_id = out["board_id"]
    reloaded = client.get(f"/boards/{board_id}").json()

    assert [card["layout"] for card in reloaded["cards"]] == [
        {"x": 0, "y": 0, "w": 4, "h": 6},
        {"x": 4, "y": 0, "w": 4, "h": 6},
        {"x": 0, "y": 6, "w": 8, "h": 12},
    ]
    assert all(card["auto_size_pending"] is False
               for card in reloaded["cards"])


def test_free_mode_reflows_only_the_new_batch_around_existing_cards(
    client, thread, board, fake, monkeypatch,
):
    top = a_card(board, "Fixed top")
    lower = a_card(board, "Fixed lower")
    fixed = {
        str(top["id"]): {"x": 0, "y": 0, "w": 12, "h": 7},
        str(lower["id"]): {"x": 8, "y": 12, "w": 4, "h": 9},
    }
    store.save_layouts(board["id"], fixed, require_complete=True)
    final_sizes(monkeypatch, {"w": 4, "h": 6}, {"w": 8, "h": 12})
    fake(
        task("new_cards"),
        {"action": "new_cards", "say": "", "cards": [
            {"request_id": "r1", "question": "oil by region",
             "title": "First"},
            {"request_id": "r2", "question": "oil by region",
             "title": "Wide"},
        ]},
        {"semantic_query": BY_REGION, "title": "First"},
        {"semantic_query": BY_REGION, "title": "Wide"},
    )
    plan = turn(client, thread, store.get_board(board["id"])).json()[
        "pending_plan"
    ]

    confirm(client, plan["id"])
    cards = store.list_cards(board["id"])
    by_title = {card["title"]: card["layout"] for card in cards}

    assert by_title["Fixed top"] == fixed[str(top["id"])]
    assert by_title["Fixed lower"] == fixed[str(lower["id"])]
    assert by_title["First"] == {"x": 0, "y": 10, "w": 4, "h": 6}
    assert by_title["Wide"] == {"x": 0, "y": 16, "w": 8, "h": 12}


def test_auto_pack_runs_after_final_size_batch_placement(
    client, thread, board, fake, monkeypatch,
):
    existing = a_card(board, "Existing")
    store.update_card(
        existing["id"], layout={"x": 4, "y": 0, "w": 8, "h": 8}
    )
    store.update_board(board["id"], layout_mode="auto_pack")
    final_sizes(monkeypatch, {"w": 4, "h": 6})
    fake(
        task("new_cards"),
        {"action": "new_cards", "say": "", "cards": [
            {"request_id": "r1", "question": "oil by region",
             "title": "One"},
        ]},
        {"semantic_query": BY_REGION, "title": "One"},
    )
    plan = turn(client, thread, store.get_board(board["id"])).json()[
        "pending_plan"
    ]

    confirm(client, plan["id"])
    by_title = {
        card["title"]: card["layout"] for card in store.list_cards(board["id"])
    }

    assert by_title == {
        "Existing": {"x": 4, "y": 0, "w": 8, "h": 8},
        "One": {"x": 0, "y": 0, "w": 4, "h": 6},
    }


def test_failed_placeholder_keeps_its_size_while_the_batch_reflows(
    client, thread, board, fake, monkeypatch,
):
    final_sizes(monkeypatch, {"w": 4, "h": 6})
    fake(
        task("new_cards"),
        {"action": "new_cards", "say": "", "cards": [
            {"request_id": "r1", "question": "oil by region",
             "title": "Ready"},
            {"request_id": "r2", "question": "ambiguous oil",
             "title": "Failed"},
        ]},
        {"semantic_query": BY_REGION, "title": "Ready"},
        {"semantic_query": BY_REGION, "ambiguity": {
            "term": "oil", "candidates": ["oil", "oil_equivalent"],
            "question": "Which oil?",
        }},
    )
    plan = turn(client, thread, board).json()["pending_plan"]

    confirm(client, plan["id"])
    by_title = {
        card["title"]: card for card in store.list_cards(board["id"])
    }

    assert by_title["Ready"]["layout"] == {
        "x": 0, "y": 0, "w": 4, "h": 6,
    }
    assert by_title["Failed"]["layout"] == {
        "x": 4, "y": 0, "w": 6, "h": 10,
    }
    assert by_title["Failed"]["auto_size_pending"] is True


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("app.chat.confirm.cards_store.get_board", "board lookup failed"),
        ("app.chat.confirm.chat_store.list_action_items", "item lookup failed"),
    ],
)
def test_post_cas_store_initialization_failures_terminalize_the_action(
    client, thread, board, fake, monkeypatch, target, message,
):
    action_id, model = queued_card_action(
        client, thread, board, fake, monkeypatch
    )
    monkeypatch.setattr(
        target,
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(message)),
    )

    with pytest.raises(RuntimeError, match=message):
        run_action(action_id, client=model)

    assert chat_store.get_action(action_id)["status"] == "failed"


def test_malformed_persisted_batch_insertion_row_terminalizes_the_action(
    client, thread, board, fake, monkeypatch, app_conn,
):
    action_id, model = queued_card_action(
        client, thread, board, fake, monkeypatch
    )
    app_conn.execute(
        "UPDATE app.chat_action SET effects = effects || %s::jsonb WHERE id = %s",
        (json.dumps({"batch_insertion_row": "not-a-row"}), action_id),
    )
    app_conn.commit()

    with pytest.raises(ValueError, match="invalid literal"):
        run_action(action_id, client=model)

    assert chat_store.get_action(action_id)["status"] == "failed"


def test_completed_render_and_layout_roll_back_when_item_outcome_cannot_commit(
    client, thread, board, fake, monkeypatch,
):
    monkeypatch.setattr(
        "app.routes.chat.BackgroundTasks.add_task", lambda *a, **k: None
    )
    model = fake(
        task("new_cards"),
        {"action": "new_cards", "say": "", "cards": [{
            "request_id": "r1", "question": "oil by region",
            "title": "Atomic",
        }]},
        {"semantic_query": BY_REGION, "title": "Atomic"},
    )
    plan = turn(client, thread, board).json()["pending_plan"]
    action_id = uuid.UUID(confirm(client, plan["id"]).json()["action"]["id"])
    original = chat_store.transition_action_item

    def reject_success(item_id, *, expected, status, error=None, conn=None):
        if status == "succeeded":
            raise RuntimeError("forced outcome write failure")
        return original(
            item_id, expected=expected, status=status, error=error, conn=conn
        )

    monkeypatch.setattr(chat_store, "transition_action_item", reject_success)

    with pytest.raises(RuntimeError, match="forced outcome write failure"):
        run_action(action_id, client=model)

    item = chat_store.list_action_items(action_id)[0]
    card = store.get_card(item["card_id"])
    assert card["state"] == "empty"
    assert card["layout"] == {"x": 0, "y": 0, "w": 6, "h": 10}
    assert card["auto_size_pending"] is True
    assert item["status"] == "failed"
    assert chat_store.get_action(action_id)["status"] == "failed"


def test_disappearing_card_is_counted_failed_instead_of_succeeded(
    client, thread, board, fake, monkeypatch,
):
    monkeypatch.setattr(
        "app.routes.chat.BackgroundTasks.add_task", lambda *a, **k: None
    )
    model = fake(
        task("new_cards"),
        {"action": "new_cards", "say": "", "cards": [{
            "request_id": "r1", "question": "oil by region",
            "title": "Gone",
        }]},
        {"semantic_query": BY_REGION, "title": "Gone"},
    )
    plan = turn(client, thread, board).json()["pending_plan"]
    action_id = uuid.UUID(confirm(client, plan["id"]).json()["action"]["id"])
    monkeypatch.setattr(store, "save_rendered_batch_card", lambda *a, **k: None)

    run_action(action_id, client=model)

    item = chat_store.list_action_items(action_id)[0]
    assert item["status"] == "failed"
    assert "removed" in item["error"]
    assert chat_store.get_action(action_id)["status"] == "completed_with_errors"


def test_late_success_cannot_overwrite_item_cancellation_or_commit_render(
    client, thread, board, fake, monkeypatch,
):
    monkeypatch.setattr(
        "app.routes.chat.BackgroundTasks.add_task", lambda *a, **k: None
    )
    model = fake(
        task("new_cards"),
        {"action": "new_cards", "say": "", "cards": [{
            "request_id": "r1", "question": "oil by region",
            "title": "Cancelled",
        }]},
        {"semantic_query": BY_REGION, "title": "Cancelled"},
    )
    plan = turn(client, thread, board).json()["pending_plan"]
    action_id = uuid.UUID(confirm(client, plan["id"]).json()["action"]["id"])
    item_id = chat_store.list_action_items(action_id)[0]["id"]
    real_build = planner.build_card

    def cancel_after_render(*args, **kwargs):
        result = real_build(*args, **kwargs)
        chat_store.transition_action_item(
            item_id, expected="running", status="cancelled"
        )
        return result

    monkeypatch.setattr("app.chat.confirm.planner.build_card", cancel_after_render)

    with pytest.raises(chat_store.ActionItemTransitionError):
        run_action(action_id, client=model)

    item = chat_store.list_action_items(action_id)[0]
    card = store.get_card(item["card_id"])
    assert item["status"] == "cancelled"
    assert card["state"] == "empty"
    assert card["auto_size_pending"] is True
    assert chat_store.get_action(action_id)["status"] == "failed"


def test_stop_reflow_exception_still_terminalizes_the_action(
    client, thread, board, fake, monkeypatch,
):
    monkeypatch.setattr(
        "app.routes.chat.BackgroundTasks.add_task", lambda *a, **k: None
    )
    model = fake(
        task("new_cards"),
        {"action": "new_cards", "say": "", "cards": [{
            "request_id": "r1", "question": "oil by region",
            "title": "Stopped",
        }]},
    )
    plan = turn(client, thread, board).json()["pending_plan"]
    action_id = uuid.UUID(confirm(client, plan["id"]).json()["action"]["id"])
    chat_store.request_cancel(action_id)
    monkeypatch.setattr(
        "app.chat.confirm.cards_store.reflow_card_batch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop reflow failed")),
    )

    with pytest.raises(RuntimeError, match="stop reflow failed"):
        run_action(action_id, client=model)

    assert chat_store.get_action(action_id)["status"] == "stopped"


def test_error_reflow_exception_still_fails_the_item_and_action(
    client, thread, board, fake, monkeypatch,
):
    monkeypatch.setattr(
        "app.routes.chat.BackgroundTasks.add_task", lambda *a, **k: None
    )
    model = fake(
        task("new_cards"),
        {"action": "new_cards", "say": "", "cards": [{
            "request_id": "r1", "question": "ambiguous oil",
            "title": "Failed",
        }]},
        {"semantic_query": BY_REGION, "ambiguity": {
            "term": "oil", "candidates": ["oil", "oil_equivalent"],
            "question": "Which oil?",
        }},
    )
    plan = turn(client, thread, board).json()["pending_plan"]
    action_id = uuid.UUID(confirm(client, plan["id"]).json()["action"]["id"])
    monkeypatch.setattr(
        "app.chat.confirm.cards_store.reflow_card_batch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("error reflow failed")),
    )

    with pytest.raises(RuntimeError, match="error reflow failed"):
        run_action(action_id, client=model)

    assert chat_store.list_action_items(action_id)[0]["status"] == "failed"
    assert chat_store.get_action(action_id)["status"] == "failed"


def test_stopping_leaves_the_cards_already_built(client, thread, board, fake,
                                                 monkeypatch):
    """Stop means "after this one", not "in the middle of one". A card half
    written to the database is worse than one extra card."""
    final_sizes(monkeypatch, {"w": 4, "h": 6})
    monkeypatch.setattr(
        "app.routes.chat.BackgroundTasks.add_task", lambda *a, **k: None
    )
    model = fake(
        task("new_cards"),
        {"action": "new_cards", "say": "", "cards": [
            {"request_id": "r1", "question": "oil by region", "title": "One"},
            {"request_id": "r2", "question": "oil by region",
             "title": "Two"},
        ]},
        {"semantic_query": BY_REGION, "title": "One"},
    )
    plan = turn(client, thread, board).json()["pending_plan"]
    action = confirm(client, plan["id"]).json()["action"]
    action_id = uuid.UUID(action["id"])

    real_build = planner.build_card

    def stop_after_first(*args, **kwargs):
        result = real_build(*args, **kwargs)
        chat_store.request_cancel(action_id)
        return result

    monkeypatch.setattr("app.chat.confirm.planner.build_card", stop_after_first)
    run_action(action_id, client=model)

    progress = client.get(f"/chat/actions/{action['id']}").json()
    assert progress["status"] == "stopped"
    cards = store.list_cards(board["id"])
    titles = {c["title"] for c in cards}
    assert {"One", "Two"} <= titles, "the placeholders stay either way"
    by_title = {card["title"]: card for card in cards}
    assert by_title["One"]["layout"] == {
        "x": 0, "y": 0, "w": 4, "h": 6,
    }
    assert by_title["Two"]["layout"] == {
        "x": 4, "y": 0, "w": 6, "h": 10,
    }
    assert by_title["Two"]["auto_size_pending"] is True


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


# -- undo ----------------------------------------------------------------
#
# A turn undoes as one thing. The card's own one-step undo still serves the
# edit box and is not touched by any of this.

def apply_a(client, thread, board, fake, task_kind, detail, *extra):
    fake(task(task_kind), detail, *extra)
    plan = turn(client, thread, board).json()["pending_plan"]
    return confirm(client, plan["id"]).json()


def test_a_rename_undoes(client, thread, board, fake):
    out = apply_a(client, thread, board, fake, "rename_dashboard",
                  {"action": "rename_dashboard", "say": "",
                   "board_id": str(board["id"]), "title": "Wells"})
    assert store.get_board(board["id"])["title"] == "Wells"

    undone = client.post(f"/chat/actions/{out['message']['action_id']}/undo")

    assert undone.status_code == 200
    assert store.get_board(board["id"])["title"] == "Operations"
    assert undone.json()["action"] == "undone"


def test_undoing_twice_is_refused(client, thread, board, fake):
    out = apply_a(client, thread, board, fake, "rename_dashboard",
                  {"action": "rename_dashboard", "say": "",
                   "board_id": str(board["id"]), "title": "Wells"})
    action_id = out["message"]["action_id"]
    client.post(f"/chat/actions/{action_id}/undo")

    again = client.post(f"/chat/actions/{action_id}/undo")

    assert again.status_code == 409
    assert store.get_board(board["id"])["title"] == "Operations"


def test_undo_will_not_discard_a_later_change(client, thread, board, fake):
    """Undo restores a remembered state. It is not a licence to overwrite
    whatever is there now with an older version of it."""
    out = apply_a(client, thread, board, fake, "rename_dashboard",
                  {"action": "rename_dashboard", "say": "",
                   "board_id": str(board["id"]), "title": "Wells"})
    store.update_board(board["id"], title="Renamed by someone else")

    undone = client.post(f"/chat/actions/{out['message']['action_id']}/undo")

    assert undone.status_code == 409
    assert "newer change" in undone.json()["detail"]
    assert store.get_board(board["id"])["title"] == "Renamed by someone else"


def test_a_removed_card_comes_back(client, thread, board, fake):
    card = a_card(board)
    out = apply_a(client, thread, board, fake, "delete_card",
                  {"action": "delete_card", "say": "",
                   "card_ids": [str(card["id"])]})
    assert store.get_card(card["id"]) is None

    client.post(f"/chat/actions/{out['message']['action_id']}/undo")

    assert store.get_card(card["id"]) is not None


def test_a_card_edit_undoes_to_the_earlier_query(client, thread, board, fake):
    card = a_card(board)
    out = apply_a(client, thread, board, fake, "edit_card",
                  # EditCardIntent carries no query: the replacement is
                  # written by the query step, which is the next payload.
                  {"card_id": str(card["id"]), "instruction": "by month"},
                  {"semantic_query": BY_MONTH, "title": "Oil"})
    assert store.get_card(card["id"])["semantic_query"][
        "dimensions"][0]["field"] == "reading_date"

    client.post(f"/chat/actions/{out['message']['action_id']}/undo")

    assert store.get_card(card["id"])["semantic_query"][
        "dimensions"][0]["field"] == "region"


def test_a_moved_card_goes_back(client, thread, board, fake):
    card = a_card(board)
    before = store.get_card(card["id"])["layout"]
    out = apply_a(client, thread, board, fake, "layout",
                  {"action": "layout", "say": "", "changes": [{
                      "card_id": str(card["id"]),
                      "layout": {"x": 0, "y": 0, "w": 12, "h": 10}}]})
    assert store.get_card(card["id"])["layout"]["w"] == 12

    client.post(f"/chat/actions/{out['message']['action_id']}/undo")

    assert store.get_card(card["id"])["layout"] == before


def test_a_whole_generated_dashboard_undoes_in_one_action(client, thread,
                                                          board, fake):
    """A six-card dashboard reverses as one thing rather than six. That is
    the entire reason a turn has an undo on top of the card's."""
    out = apply_a(client, thread, board, fake, "new_dashboard",
                  {"action": "new_dashboard", "say": "", "title": "Wells",
                   "cards": [
                       {"request_id": "r1", "question": "oil by region",
                        "title": "One"},
                       {"request_id": "r2", "question": "oil by region",
                        "title": "Two"}]},
                  {"semantic_query": BY_REGION, "title": "One"},
                  {"semantic_query": BY_REGION, "title": "Two"})
    created = uuid.UUID(out["board_id"])
    assert len(store.list_cards(created)) == 2

    undone = client.post(f"/chat/actions/{out['action']['id']}/undo")

    assert undone.status_code == 200
    assert store.get_board(created) is None
    # Soft, so nothing a person spent a minute on is actually destroyed.
    assert store.restore_board(created) is not None


def test_cards_added_to_an_existing_dashboard_undo_without_it(client, thread,
                                                              board, fake):
    out = apply_a(client, thread, board, fake, "new_cards",
                  {"action": "new_cards", "say": "", "cards": [
                      {"request_id": "r1", "question": "oil by region",
                       "title": "Added"}]},
                  {"semantic_query": BY_REGION, "title": "Added"})
    assert any(c["title"] == "Added" for c in store.list_cards(board["id"]))

    client.post(f"/chat/actions/{out['action']['id']}/undo")

    assert not any(c["title"] == "Added"
                   for c in store.list_cards(board["id"]))
    assert store.get_board(board["id"]) is not None, (
        "undoing added cards must not take the dashboard with them")


def test_a_build_still_running_says_to_stop_it_first(client, thread, board,
                                                     fake, monkeypatch):
    monkeypatch.setattr("app.routes.chat.BackgroundTasks.add_task",
                        lambda *a, **k: None)
    out = apply_a(client, thread, board, fake, "new_cards",
                  {"action": "new_cards", "say": "", "cards": [
                      {"request_id": "r1", "question": "oil by region",
                       "title": "Pending"}]})

    refused = client.post(f"/chat/actions/{out['action']['id']}/undo")

    assert refused.status_code == 409
    assert "Stop it first" in refused.json()["detail"]


def test_the_undo_route_is_absent_while_chat_is_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "chat_enabled", False)
    assert client.post(
        f"/chat/actions/{uuid.uuid4()}/undo").status_code == 404


def test_clearing_a_dashboard_removes_every_card_in_one_change(client, thread,
                                                               board, fake):
    """"Delete all the cards" is an ordinary request, and a one-card
    contract cannot express it: the turn removes one, reports success, and
    the person has to ask three more times."""
    cards = [a_card(board, title=f"Card {n}") for n in range(3)]
    out = apply_a(client, thread, board, fake, "delete_card",
                  {"action": "delete_card", "say": "",
                   "card_ids": [str(c["id"]) for c in cards]})

    assert store.list_cards(board["id"]) == []
    assert out["message"]["say"] == "Removed 3 cards."

    client.post(f"/chat/actions/{out['message']['action_id']}/undo")

    assert len(store.list_cards(board["id"])) == 3


def test_clearing_a_dashboard_keeps_the_dashboard(client, thread, board, fake):
    a_card(board)
    out = apply_a(client, thread, board, fake, "delete_card",
                  {"action": "delete_card", "say": "",
                   "card_ids": [str(c["id"])
                                for c in store.list_cards(board["id"])]})

    assert out["message"]["action"] == "applied"
    assert store.get_board(board["id"]) is not None


def test_a_card_that_went_first_does_not_fail_the_rest(client, thread, board,
                                                       fake):
    """The preview describes what will actually happen, and nothing will
    happen to a card that is not there any more."""
    alive = a_card(board, title="Alive")
    gone = a_card(board, title="Gone")
    store.soft_delete_card(gone["id"])

    out = apply_a(client, thread, board, fake, "delete_card",
                  {"action": "delete_card", "say": "",
                   "card_ids": [str(gone["id"]), str(alive["id"])]})

    assert len(out["message"]["say"]) > 0
    assert store.get_card(alive["id"]) is None


def test_every_card_gets_its_own_preview_line(client, thread, board, fake):
    cards = [a_card(board, title=f"Card {n}") for n in range(3)]
    fake(task("delete_card"),
         {"action": "delete_card", "say": "",
          "card_ids": [str(c["id"]) for c in cards]})

    plan = turn(client, thread, board).json()["pending_plan"]

    assert len(plan["operations"]) == 3
    assert all("Card" in o["summary"] for o in plan["operations"])
