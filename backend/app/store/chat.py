"""Small, typed persistence seams for the browser-global chat thread.

Only transcript metadata and structured model results enter this module. Raw
dashboard rows and assembled prompt context have no persistence API here.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from ..db import app_pool


THREAD_COLUMNS = "id, created_at, updated_at"
MESSAGE_COLUMNS = (
    "id, thread_id, role, body, active_board_id, active_board_title, "
    "data_exposed, created_at"
)
PLAN_COLUMNS = (
    "id, thread_id, action, resolved, basis, status, created_at, updated_at"
)
ACTION_COLUMNS = (
    "id, thread_id, plan_id, board_id, provider, model, effects, status, "
    "cancel_requested, created_at, updated_at, purge_after"
)
ITEM_COLUMNS = (
    "id, action_id, ordinal, request, card_id, status, error, created_at, "
    "updated_at"
)
EVENT_COLUMNS = "id, action_id, kind, payload, created_at"
TRANSIENT_COLUMNS = (
    "id, thread_id, query, chart_hint, title, cache, expires_at, created_at"
)
PLAN_STATUSES = {"pending", "confirmed", "cancelled"}
ACTION_STATUSES = {
    "queued", "running", "completed", "completed_with_errors", "stopped",
    "failed", "cancelled", "undone",
}


class PendingPlanExistsError(RuntimeError):
    """A thread may have only one mutation awaiting confirmation."""


class PlanTransitionError(RuntimeError):
    """The plan no longer has the state the caller expected."""


class ActionTransitionError(RuntimeError):
    """The action no longer has the state the caller expected."""


@contextmanager
def _connection(conn=None) -> Iterator[Any]:
    if conn is not None:
        yield conn
        return
    with app_pool.connection() as managed:
        yield managed


def _q(
    sql: str,
    params: tuple = (),
    *,
    fetch: str | None = None,
    conn=None,
):
    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return cur.rowcount


def create_thread(*, conn=None) -> dict[str, Any]:
    with _connection(conn) as active:
        purge_expired_actions(conn=active)
        return _q(
            f"INSERT INTO app.chat_thread (id) VALUES (%s) "
            f"RETURNING {THREAD_COLUMNS}",
            (uuid.uuid4(),),
            fetch="one",
            conn=active,
        )


def get_thread(thread_id: uuid.UUID, *, conn=None) -> dict[str, Any] | None:
    return _q(
        f"SELECT {THREAD_COLUMNS} FROM app.chat_thread WHERE id = %s",
        (thread_id,),
        fetch="one",
        conn=conn,
    )


def list_messages(
    thread_id: uuid.UUID,
    *,
    limit: int | None = None,
    include_data_exposed: bool = True,
    conn=None,
) -> list[dict[str, Any]]:
    privacy = "" if include_data_exposed else " AND data_exposed = false"
    if limit is None:
        return _q(
            f"SELECT {MESSAGE_COLUMNS} FROM app.chat_message "
            f"WHERE thread_id = %s{privacy} ORDER BY created_at, id",
            (thread_id,),
            fetch="all",
            conn=conn,
        )
    if limit < 0:
        raise ValueError("limit must be non-negative")
    return _q(
        f"SELECT * FROM (SELECT {MESSAGE_COLUMNS} FROM app.chat_message "
        f"WHERE thread_id = %s{privacy} ORDER BY created_at DESC, id DESC "
        f"LIMIT %s) recent ORDER BY created_at, id",
        (thread_id, limit),
        fetch="all",
        conn=conn,
    )


def append_message(
    thread_id: uuid.UUID,
    *,
    role: str,
    body: dict,
    active_board_id: uuid.UUID | None,
    active_board_title: str,
    data_exposed: bool,
    conn=None,
) -> dict[str, Any]:
    with _connection(conn) as active:
        message = _q(
            f"INSERT INTO app.chat_message "
            f"(id, thread_id, role, body, active_board_id, "
            f"active_board_title, data_exposed, created_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, clock_timestamp()) "
            f"RETURNING {MESSAGE_COLUMNS}",
            (
                uuid.uuid4(), thread_id, role, json.dumps(body),
                active_board_id, active_board_title, data_exposed,
            ),
            fetch="one",
            conn=active,
        )
        _q(
            "UPDATE app.chat_thread SET updated_at = now() WHERE id = %s",
            (thread_id,),
            conn=active,
        )
        return message


def save_pending_plan(
    thread_id: uuid.UUID,
    *,
    action: dict,
    resolved: dict,
    basis: dict,
    conn=None,
) -> dict[str, Any]:
    try:
        return _q(
            f"INSERT INTO app.chat_plan "
            f"(id, thread_id, action, resolved, basis) "
            f"VALUES (%s, %s, %s, %s, %s) RETURNING {PLAN_COLUMNS}",
            (
                uuid.uuid4(), thread_id, json.dumps(action),
                json.dumps(resolved), json.dumps(basis),
            ),
            fetch="one",
            conn=conn,
        )
    except UniqueViolation as exc:
        raise PendingPlanExistsError(
            "the thread already has a pending plan"
        ) from exc


def get_pending_plan(
    thread_id: uuid.UUID, *, conn=None
) -> dict[str, Any] | None:
    return _q(
        f"SELECT {PLAN_COLUMNS} FROM app.chat_plan "
        "WHERE thread_id = %s AND status = 'pending' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (thread_id,),
        fetch="one",
        conn=conn,
    )


def transition_plan(
    plan_id: uuid.UUID, *, expected: str, status: str, conn=None
) -> dict[str, Any]:
    if expected not in PLAN_STATUSES or status not in PLAN_STATUSES:
        raise ValueError("unknown plan status")
    if expected != "pending" or status not in {"confirmed", "cancelled"}:
        raise ValueError("plans may transition only from pending to a terminal state")
    plan = _q(
        "UPDATE app.chat_plan SET status = %s, updated_at = now() "
        f"WHERE id = %s AND status = %s RETURNING {PLAN_COLUMNS}",
        (status, plan_id, expected),
        fetch="one",
        conn=conn,
    )
    if plan is None:
        raise PlanTransitionError("plan status changed before transition")
    return plan


def _plan_board_id(plan: dict) -> uuid.UUID | None:
    for source in (plan.get("resolved") or {}, plan.get("action") or {}):
        candidate = source.get("board_id") if isinstance(source, dict) else None
        if candidate:
            return uuid.UUID(str(candidate))
    return None


def create_action(
    plan: dict,
    *,
    provider: str,
    model: str,
    effects: dict,
    conn=None,
) -> dict[str, Any]:
    return _q(
        f"INSERT INTO app.chat_action "
        f"(id, thread_id, plan_id, board_id, provider, model, effects, status) "
        f"VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued') "
        f"RETURNING {ACTION_COLUMNS}",
        (
            uuid.uuid4(), plan["thread_id"], plan["id"], _plan_board_id(plan),
            provider, model, json.dumps(effects),
        ),
        fetch="one",
        conn=conn,
    )


def get_action(action_id: uuid.UUID, *, conn=None) -> dict[str, Any] | None:
    return _q(
        f"SELECT {ACTION_COLUMNS} FROM app.chat_action WHERE id = %s "
        "AND NOT (thread_id IS NULL AND purge_after <= now())",
        (action_id,),
        fetch="one",
        conn=conn,
    )


def transition_action(
    action_id: uuid.UUID, *, expected: str, status: str, conn=None
) -> dict[str, Any]:
    """Compare-and-set between known states.

    The graph stays deliberately flexible because generation retries can
    resume or terminate work from more than one non-terminal state.
    """
    if expected not in ACTION_STATUSES or status not in ACTION_STATUSES:
        raise ValueError("unknown action status")
    action = _q(
        "UPDATE app.chat_action SET status = %s, updated_at = now() "
        f"WHERE id = %s AND status = %s RETURNING {ACTION_COLUMNS}",
        (status, action_id, expected),
        fetch="one",
        conn=conn,
    )
    if action is None:
        raise ActionTransitionError("action status changed before transition")
    return action


def append_action_item(
    action_id: uuid.UUID,
    *,
    ordinal: int,
    request: dict,
    card_id: uuid.UUID | None = None,
    status: str = "queued",
    error: str | None = None,
    conn=None,
) -> dict[str, Any]:
    return _q(
        f"INSERT INTO app.chat_action_item "
        f"(id, action_id, ordinal, request, card_id, status, error) "
        f"VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {ITEM_COLUMNS}",
        (
            uuid.uuid4(), action_id, ordinal, json.dumps(request), card_id,
            status, error,
        ),
        fetch="one",
        conn=conn,
    )


def list_action_items(
    action_id: uuid.UUID, *, conn=None
) -> list[dict[str, Any]]:
    return _q(
        f"SELECT {ITEM_COLUMNS} FROM app.chat_action_item "
        "WHERE action_id = %s ORDER BY ordinal, id",
        (action_id,),
        fetch="all",
        conn=conn,
    )


def append_event(
    action_id: uuid.UUID, kind: str, payload: dict, *, conn=None
) -> dict[str, Any]:
    return _q(
        f"INSERT INTO app.chat_event (action_id, kind, payload) "
        f"VALUES (%s, %s, %s) RETURNING {EVENT_COLUMNS}",
        (action_id, kind, json.dumps(payload)),
        fetch="one",
        conn=conn,
    )


def list_events(
    action_id: uuid.UUID, *, after_id: int = 0, conn=None
) -> list[dict[str, Any]]:
    return _q(
        f"SELECT {EVENT_COLUMNS} FROM app.chat_event "
        "WHERE action_id = %s AND id > %s ORDER BY id",
        (action_id, after_id),
        fetch="all",
        conn=conn,
    )


def save_transient(
    thread_id: uuid.UUID,
    *,
    query: dict,
    chart_hint: str | None,
    title: str | None,
    cache: dict,
    ttl_seconds: int,
    conn=None,
) -> dict[str, Any]:
    return _q(
        f"INSERT INTO app.chat_transient_result "
        f"(id, thread_id, query, chart_hint, title, cache, expires_at) "
        f"VALUES (%s, %s, %s, %s, %s, %s, "
        f"now() + make_interval(secs => %s)) RETURNING {TRANSIENT_COLUMNS}",
        (
            uuid.uuid4(), thread_id, json.dumps(query), chart_hint, title,
            json.dumps(cache), ttl_seconds,
        ),
        fetch="one",
        conn=conn,
    )


def get_transient(
    result_id: uuid.UUID, *, conn=None
) -> dict[str, Any] | None:
    return _q(
        f"SELECT {TRANSIENT_COLUMNS} FROM app.chat_transient_result "
        "WHERE id = %s AND expires_at > now()",
        (result_id,),
        fetch="one",
        conn=conn,
    )


def purge_expired_transients(*, conn=None) -> int:
    return _q(
        "DELETE FROM app.chat_transient_result WHERE expires_at <= now()",
        conn=conn,
    )


def purge_expired_actions(*, conn=None) -> int:
    return _q(
        "DELETE FROM app.chat_action "
        "WHERE thread_id IS NULL AND purge_after <= now()",
        conn=conn,
    )


def clear_thread(
    thread_id: uuid.UUID, *, tombstone_days: int, conn=None
) -> None:
    if not 1 <= tombstone_days <= 30:
        raise ValueError("tombstone_days must be between 1 and 30")

    with _connection(conn) as active, active.cursor() as cur:
        # Opportunistic retention cleanup is intentionally global but can
        # touch only already-detached actions whose deadline has passed.
        purge_expired_actions(conn=active)
        cur.execute(
            "UPDATE app.chat_action_item SET status = 'cancelled', "
            "updated_at = now() WHERE status IN ('queued', 'running') "
            "AND action_id IN (SELECT id FROM app.chat_action "
            "WHERE thread_id = %s)",
            (thread_id,),
        )
        cur.execute(
            "UPDATE app.chat_action SET status = 'cancelled', "
            "cancel_requested = true, updated_at = now() "
            "WHERE thread_id = %s AND status IN ('queued', 'running')",
            (thread_id,),
        )
        cur.execute(
            "UPDATE app.chat_plan SET status = 'cancelled', updated_at = now() "
            "WHERE thread_id = %s AND status = 'pending'",
            (thread_id,),
        )
        cur.execute("DELETE FROM app.chat_message WHERE thread_id = %s", (thread_id,))
        cur.execute("DELETE FROM app.chat_transient_result WHERE thread_id = %s", (thread_id,))
        cur.execute("DELETE FROM app.chat_plan WHERE thread_id = %s", (thread_id,))
        cur.execute(
            "UPDATE app.chat_action SET thread_id = NULL, updated_at = now(), "
            "purge_after = now() + make_interval(days => %s) "
            "WHERE thread_id = %s",
            (tombstone_days, thread_id),
        )
        cur.execute("DELETE FROM app.chat_thread WHERE id = %s", (thread_id,))
