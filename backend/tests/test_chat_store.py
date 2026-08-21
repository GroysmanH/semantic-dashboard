from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import app_pool
from app.store import chat


@pytest.fixture(autouse=True)
def isolate_chat_rows():
    """Remove every row this test created, including retained tombstones."""
    with app_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM app.chat_event")
        before_events = {row[0] for row in cur.fetchall()}
        cur.execute("SELECT id FROM app.chat_action_item")
        before_items = {row[0] for row in cur.fetchall()}
        cur.execute("SELECT id FROM app.chat_action")
        before_actions = {row[0] for row in cur.fetchall()}
        cur.execute("SELECT id FROM app.chat_thread")
        before_threads = {row[0] for row in cur.fetchall()}

    yield

    with app_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM app.chat_event")
        events = [row[0] for row in cur.fetchall() if row[0] not in before_events]
        if events:
            cur.execute("DELETE FROM app.chat_event WHERE id = ANY(%s)", (events,))
        cur.execute("SELECT id FROM app.chat_action_item")
        items = [row[0] for row in cur.fetchall() if row[0] not in before_items]
        if items:
            cur.execute(
                "DELETE FROM app.chat_action_item WHERE id = ANY(%s)",
                (items,),
            )
        cur.execute("SELECT id FROM app.chat_action")
        actions = [
            row[0] for row in cur.fetchall() if row[0] not in before_actions
        ]
        if actions:
            cur.execute("DELETE FROM app.chat_action WHERE id = ANY(%s)", (actions,))
        cur.execute("SELECT id FROM app.chat_thread")
        threads = [
            row[0] for row in cur.fetchall() if row[0] not in before_threads
        ]
        if threads:
            cur.execute("DELETE FROM app.chat_thread WHERE id = ANY(%s)", (threads,))


@pytest.fixture
def thread():
    return chat.create_thread()


def pending(thread_id, suffix="one"):
    return chat.save_pending_plan(
        thread_id,
        action={"action": "rename_dashboard", "title": suffix},
        resolved={"operations": [{"kind": "rename", "title": suffix}]},
        basis={"boards": {str(uuid.uuid4()): 3}},
    )


def test_thread_messages_are_global_ordered_and_privacy_filterable(thread):
    first_board = uuid.uuid4()
    second_board = uuid.uuid4()
    first = chat.append_message(
        thread["id"], role="user", body={"say": "first"},
        active_board_id=first_board, active_board_title="Operations",
        data_exposed=False,
    )
    exposed = chat.append_message(
        thread["id"], role="assistant", body={"say": "private answer"},
        active_board_id=second_board, active_board_title="Finance",
        data_exposed=True,
    )
    last = chat.append_message(
        thread["id"], role="user", body={"say": "last"},
        active_board_id=first_board, active_board_title="Operations",
        data_exposed=False,
    )

    assert [message["id"] for message in chat.list_messages(thread["id"], limit=2)] == [
        exposed["id"], last["id"],
    ]
    filtered = chat.list_messages(
        thread["id"], limit=10, include_data_exposed=False,
    )
    assert [message["id"] for message in filtered] == [first["id"], last["id"]]
    assert {message["active_board_title"] for message in chat.list_messages(thread["id"])} == {
        "Operations", "Finance",
    }


def test_only_one_pending_plan_exists_and_transitions_are_compare_and_set(thread):
    plan = pending(thread["id"])
    assert chat.get_pending_plan(thread["id"])["id"] == plan["id"]
    with pytest.raises(chat.PendingPlanExistsError):
        pending(thread["id"], "two")

    confirmed = chat.transition_plan(plan["id"], expected="pending", status="confirmed")
    assert confirmed["status"] == "confirmed"
    assert chat.get_pending_plan(thread["id"]) is None
    with pytest.raises(chat.PlanTransitionError):
        chat.transition_plan(plan["id"], expected="pending", status="cancelled")
    with pytest.raises(ValueError):
        chat.transition_plan(plan["id"], expected="confirmed", status="cancelled")


def test_actions_effects_ordered_items_and_events_round_trip(thread):
    plan = pending(thread["id"])
    chat.transition_plan(plan["id"], expected="pending", status="confirmed")
    action = chat.create_action(
        plan, provider="gemini", model="gemini-test",
        effects={"before": {"title": "old"}, "after": {"title": "new"}},
    )
    assert chat.get_action(action["id"])["effects"]["before"]["title"] == "old"

    second = chat.append_action_item(action["id"], ordinal=1, request={"title": "second"})
    first = chat.append_action_item(action["id"], ordinal=0, request={"title": "first"})
    assert [item["id"] for item in chat.list_action_items(action["id"])] == [
        first["id"], second["id"],
    ]

    one = chat.append_event(action["id"], "plan", {"step": 1})
    two = chat.append_event(action["id"], "item_started", {"step": 2})
    assert two["id"] > one["id"]
    assert [event["id"] for event in chat.list_events(action["id"], after_id=one["id"])] == [
        two["id"],
    ]
    with pytest.raises(ValueError):
        chat.transition_action(action["id"], expected="queued", status="pending")


def test_action_transition_graph_supports_retry_and_has_absorbing_states(thread):
    cancelled_plan = pending(thread["id"], "cancelled")
    chat.transition_plan(cancelled_plan["id"], expected="pending", status="confirmed")
    cancelled = chat.create_action(
        cancelled_plan, provider="gemini", model="test", effects={},
    )
    chat.transition_action(cancelled["id"], expected="queued", status="cancelled")
    with pytest.raises(ValueError):
        chat.transition_action(cancelled["id"], expected="cancelled", status="running")

    completed_plan = pending(thread["id"], "completed")
    chat.transition_plan(completed_plan["id"], expected="pending", status="confirmed")
    completed = chat.create_action(
        completed_plan, provider="gemini", model="test", effects={},
    )
    chat.transition_action(completed["id"], expected="queued", status="running")
    chat.transition_action(completed["id"], expected="running", status="completed")
    undone = chat.transition_action(
        completed["id"], expected="completed", status="undone",
    )
    assert undone["status"] == "undone"
    with pytest.raises(ValueError):
        chat.transition_action(completed["id"], expected="undone", status="running")

    retry_plan = pending(thread["id"], "retry")
    chat.transition_plan(retry_plan["id"], expected="pending", status="confirmed")
    retry = chat.create_action(
        retry_plan, provider="gemini", model="test", effects={},
    )
    chat.transition_action(retry["id"], expected="queued", status="failed")
    resumed = chat.transition_action(
        retry["id"], expected="failed", status="running",
    )
    assert resumed["status"] == "running"

    with pytest.raises(ValueError):
        chat.transition_action(retry["id"], expected="running", status="running")

    undoable_plan = pending(thread["id"], "partial undo")
    chat.transition_plan(undoable_plan["id"], expected="pending", status="confirmed")
    undoable = chat.create_action(
        undoable_plan, provider="gemini", model="test", effects={},
    )
    chat.transition_action(undoable["id"], expected="queued", status="running")
    chat.transition_action(
        undoable["id"], expected="running", status="completed_with_errors",
    )
    result = chat.transition_action(
        undoable["id"], expected="completed_with_errors", status="undone",
    )
    assert result["status"] == "undone"


def test_transients_expire_and_are_purged(thread):
    fresh = chat.save_transient(
        thread["id"], query={"entity": "production", "measures": ["oil"]},
        chart_hint="bar", title="Oil", cache={"rows": [{"oil": 1}]},
        ttl_seconds=60,
    )
    expired = chat.save_transient(
        thread["id"], query={"entity": "production", "measures": ["gas"]},
        chart_hint=None, title="Gas", cache={"rows": [{"gas": 2}]},
        ttl_seconds=-1,
    )
    assert chat.get_transient(fresh["id"])["cache"]["rows"] == [{"oil": 1}]
    assert chat.get_transient(expired["id"]) is None
    assert chat.purge_expired_transients() >= 1


def test_clear_thread_cancels_active_work_and_detaches_completed_actions(thread):
    chat.append_message(
        thread["id"], role="user", body={"say": "clear me"},
        active_board_id=None, active_board_title="Operations", data_exposed=False,
    )
    completed_plan = pending(thread["id"], "completed")
    chat.transition_plan(completed_plan["id"], expected="pending", status="confirmed")
    completed = chat.create_action(
        completed_plan, provider="gemini", model="test", effects={"done": True},
    )
    # An action cannot complete without having run: the transition graph
    # forbids the shortcut, and so does reality.
    chat.transition_action(completed["id"], expected="queued", status="running")
    chat.transition_action(completed["id"], expected="running", status="completed")
    completed_item = chat.append_action_item(
        completed["id"], ordinal=0, request={"title": "kept"}, status="succeeded",
    )
    completed_event = chat.append_event(completed["id"], "done", {"kept": True})

    active_plan = pending(thread["id"], "active")
    chat.transition_plan(active_plan["id"], expected="pending", status="confirmed")
    active = chat.create_action(
        active_plan, provider="gemini", model="test", effects={"done": False},
    )
    chat.transition_action(active["id"], expected="queued", status="running")
    queued_item = chat.append_action_item(
        active["id"], ordinal=0, request={"title": "queued"}, status="queued",
    )
    running_item = chat.append_action_item(
        active["id"], ordinal=1, request={"title": "running"}, status="running",
    )
    waiting_plan = pending(thread["id"], "waiting")
    transient = chat.save_transient(
        thread["id"], query={"entity": "production", "measures": ["oil"]},
        chart_hint=None, title=None, cache={"rows": []}, ttl_seconds=60,
    )

    before_clear = datetime.now(timezone.utc)
    chat.clear_thread(thread["id"], tombstone_days=2)
    after_clear = datetime.now(timezone.utc)

    assert chat.get_thread(thread["id"]) is None
    assert chat.list_messages(thread["id"]) == []
    assert chat.get_pending_plan(thread["id"]) is None
    with app_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM app.chat_plan WHERE id = %s", (waiting_plan["id"],))
        assert cur.fetchone()[0] == 0
    assert chat.get_transient(transient["id"]) is None
    detached = chat.get_action(completed["id"])
    assert detached["thread_id"] is None
    assert before_clear + timedelta(days=2) <= detached["purge_after"]
    assert detached["purge_after"] <= after_clear + timedelta(days=2)
    assert [item["id"] for item in chat.list_action_items(completed["id"])] == [
        completed_item["id"],
    ]
    assert [event["id"] for event in chat.list_events(completed["id"])] == [
        completed_event["id"],
    ]
    cancelled = chat.get_action(active["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    assert cancelled["thread_id"] is None
    active_items = {item["id"]: item for item in chat.list_action_items(active["id"])}
    assert active_items[queued_item["id"]]["status"] == "cancelled"
    assert active_items[running_item["id"]]["status"] == "cancelled"


def test_expired_action_purge_is_independent_and_cascades_children(thread):
    plan = pending(thread["id"])
    chat.transition_plan(plan["id"], expected="pending", status="confirmed")
    action = chat.create_action(plan, provider="gemini", model="test", effects={})
    item = chat.append_action_item(
        action["id"], ordinal=0, request={"title": "cascade"},
    )
    event = chat.append_event(action["id"], "plan", {"cascade": True})
    chat.transition_action(action["id"], expected="queued", status="running")
    chat.transition_action(action["id"], expected="running", status="completed")
    chat.clear_thread(thread["id"], tombstone_days=1)
    assert chat.get_action(action["id"]) is not None
    assert chat.list_action_items(action["id"])[0]["id"] == item["id"]
    assert chat.list_events(action["id"])[0]["id"] == event["id"]

    with app_pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE app.chat_action SET purge_after = now() - interval '1 second' WHERE id = %s",
            (action["id"],),
        )
        cur.execute("SELECT count(*) FROM app.chat_action WHERE id = %s", (action["id"],))
        assert cur.fetchone()[0] == 1

    assert chat.get_action(action["id"]) is None
    assert chat.purge_expired_actions() == 1
    assert chat.get_action(action["id"]) is None
    assert chat.list_action_items(action["id"]) == []
    assert chat.list_events(action["id"]) == []
