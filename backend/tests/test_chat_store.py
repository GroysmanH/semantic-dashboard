from __future__ import annotations

import uuid

import pytest

from app.db import app_pool
from app.store import chat


@pytest.fixture
def thread():
    created = chat.create_thread()
    yield created
    if chat.get_thread(created["id"]):
        chat.clear_thread(created["id"], tombstone_days=1)


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
    chat.transition_action(completed["id"], expected="queued", status="completed")

    active_plan = pending(thread["id"], "active")
    chat.transition_plan(active_plan["id"], expected="pending", status="confirmed")
    active = chat.create_action(
        active_plan, provider="gemini", model="test", effects={"done": False},
    )
    transient = chat.save_transient(
        thread["id"], query={"entity": "production", "measures": ["oil"]},
        chart_hint=None, title=None, cache={"rows": []}, ttl_seconds=60,
    )

    chat.clear_thread(thread["id"], tombstone_days=2)

    assert chat.get_thread(thread["id"]) is None
    assert chat.list_messages(thread["id"]) == []
    assert chat.get_transient(transient["id"]) is None
    detached = chat.get_action(completed["id"])
    assert detached["thread_id"] is None
    assert detached["purge_after"] is not None
    cancelled = chat.get_action(active["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    assert cancelled["thread_id"] is None


def test_clear_thread_purges_detached_actions_only_after_retention(thread):
    plan = pending(thread["id"])
    chat.transition_plan(plan["id"], expected="pending", status="confirmed")
    action = chat.create_action(plan, provider="gemini", model="test", effects={})
    chat.transition_action(action["id"], expected="queued", status="completed")
    chat.clear_thread(thread["id"], tombstone_days=1)
    assert chat.get_action(action["id"]) is not None

    with app_pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE app.chat_action SET purge_after = now() - interval '1 second' WHERE id = %s",
            (action["id"],),
        )
    another = chat.create_thread()
    chat.clear_thread(another["id"], tombstone_days=1)
    assert chat.get_action(action["id"]) is None
