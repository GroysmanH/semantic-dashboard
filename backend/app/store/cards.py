"""Persistence for dashboard boards and cards.

The public read helpers expose only live objects.  Explicit soft, restore,
and hard-delete operations are intentionally named so callers cannot choose
the wrong deletion semantics by accident.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from collections.abc import Iterable
from typing import Any, Iterator

from psycopg.rows import dict_row

from ..db import app_pool


BOARD_COLUMNS = (
    "id, title, position, revision, deleted_at, created_at, updated_at"
)
CARD_COLUMNS = (
    "id, board_id, title, semantic_query, chart_hint, vega_spec, prompt, "
    "state, layout, cache, ttl_seconds, previous, pending_clarification, "
    "deleted_at, created_at, updated_at"
)


class LastVisibleBoardError(RuntimeError):
    """Raised when a deletion would leave the application with no board."""


class BoardOrderError(RuntimeError):
    """Raised when a reorder is not the exact set of visible boards."""


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
        return None


# -- boards --------------------------------------------------------------


def create_board(title: str, *, conn=None) -> dict[str, Any]:
    return _q(
        f"INSERT INTO app.board (id, title, position) "
        f"VALUES (%s, %s, (SELECT coalesce(max(position) + 1, 0) "
        f"FROM app.board)) RETURNING {BOARD_COLUMNS}",
        (uuid.uuid4(), title),
        fetch="one",
        conn=conn,
    )


def list_boards(*, conn=None) -> list[dict[str, Any]]:
    return _q(
        f"SELECT {BOARD_COLUMNS} FROM app.board WHERE deleted_at IS NULL "
        "ORDER BY position, created_at",
        fetch="all",
        conn=conn,
    )


def get_board(board_id: uuid.UUID, *, conn=None) -> dict[str, Any] | None:
    return _q(
        f"SELECT {BOARD_COLUMNS} FROM app.board "
        "WHERE id = %s AND deleted_at IS NULL",
        (board_id,),
        fetch="one",
        conn=conn,
    )


def update_board(
    board_id: uuid.UUID, *, conn=None, **fields: Any
) -> dict[str, Any] | None:
    allowed = {"title", "position"}
    sets: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"not a settable board column: {key}")
        sets.append(f"{key} = %s")
        params.append(value)

    if not sets:
        return get_board(board_id, conn=conn)

    sets.extend(("revision = revision + 1", "updated_at = now()"))
    params.append(board_id)
    return _q(
        f"UPDATE app.board SET {', '.join(sets)} "
        f"WHERE id = %s AND deleted_at IS NULL RETURNING {BOARD_COLUMNS}",
        tuple(params),
        fetch="one",
        conn=conn,
    )


def reorder_boards(order: list[uuid.UUID], *, conn=None) -> None:
    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        cur.execute("LOCK TABLE app.board IN SHARE ROW EXCLUSIVE MODE")
        cur.execute(
            "SELECT id FROM app.board WHERE deleted_at IS NULL "
            "ORDER BY position, created_at FOR UPDATE"
        )
        visible = [row["id"] for row in cur.fetchall()]
        if len(order) != len(set(order)) or set(order) != set(visible):
            raise BoardOrderError(
                "order must contain every visible board exactly once"
            )
        for position, board_id in enumerate(order):
            cur.execute(
                "UPDATE app.board SET position = %s, revision = revision + 1, "
                "updated_at = now() WHERE id = %s AND deleted_at IS NULL",
                (position, board_id),
            )


def _lock_board_for_deletion(cur, board_id: uuid.UUID) -> dict | None:
    # Locking the table makes the visible-count check and deletion one atomic
    # decision even when two clients try to remove different boards at once.
    cur.execute("LOCK TABLE app.board IN SHARE ROW EXCLUSIVE MODE")
    cur.execute(
        "SELECT id, deleted_at FROM app.board WHERE id = %s FOR UPDATE",
        (board_id,),
    )
    target = cur.fetchone()
    if target is None or target["deleted_at"] is not None:
        return target
    cur.execute("SELECT count(*) FROM app.board WHERE deleted_at IS NULL")
    if cur.fetchone()["count"] <= 1:
        raise LastVisibleBoardError("at least one visible board is required")
    return target


def hard_delete_board(board_id: uuid.UUID, *, conn=None) -> None:
    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        target = _lock_board_for_deletion(cur, board_id)
        if target is not None:
            cur.execute("DELETE FROM app.board WHERE id = %s", (board_id,))


def soft_delete_board(
    board_id: uuid.UUID, *, conn=None
) -> dict[str, Any] | None:
    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        target = _lock_board_for_deletion(cur, board_id)
        if target is None or target.get("deleted_at") is not None:
            return None
        cur.execute(
            "UPDATE app.board SET deleted_at = now(), revision = revision + 1, "
            f"updated_at = now() WHERE id = %s RETURNING {BOARD_COLUMNS}",
            (board_id,),
        )
        return cur.fetchone()


def restore_board(board_id: uuid.UUID, *, conn=None) -> dict[str, Any] | None:
    return _q(
        "UPDATE app.board SET deleted_at = NULL, revision = revision + 1, "
        f"updated_at = now() WHERE id = %s AND deleted_at IS NOT NULL "
        f"RETURNING {BOARD_COLUMNS}",
        (board_id,),
        fetch="one",
        conn=conn,
    )


def board_basis(
    board_ids: Iterable[uuid.UUID], *, conn=None
) -> dict[str, int]:
    ids = list(board_ids)
    if not ids:
        return {}
    rows = _q(
        "SELECT id, revision FROM app.board "
        "WHERE id = ANY(%s) AND deleted_at IS NULL",
        (ids,),
        fetch="all",
        conn=conn,
    )
    return {str(row["id"]): row["revision"] for row in rows}


# -- cards ---------------------------------------------------------------


CARD_W, CARD_H, COLS = 6, 10, 12


def next_slot(board_id: uuid.UUID, *, conn=None) -> dict[str, int]:
    n = len(list_cards(board_id, conn=conn))
    per_row = COLS // CARD_W
    return {
        "x": (n % per_row) * CARD_W,
        "y": (n // per_row) * CARD_H,
        "w": CARD_W,
        "h": CARD_H,
    }


def create_card(
    board_id: uuid.UUID, layout: dict | None = None, *, conn=None
) -> dict[str, Any] | None:
    with _connection(conn) as active, active.cursor() as cur:
        # A row lock serializes slot calculation for one board while leaving
        # card creation on other boards independent.
        cur.execute("LOCK TABLE app.board IN ROW EXCLUSIVE MODE")
        cur.execute(
            "SELECT id FROM app.board "
            "WHERE id = %s AND deleted_at IS NULL FOR UPDATE",
            (board_id,),
        )
        if cur.fetchone() is None:
            return None
        card = _q(
            f"INSERT INTO app.card (id, board_id, layout) "
            f"VALUES (%s, %s, %s) RETURNING {CARD_COLUMNS}",
            (
                uuid.uuid4(),
                board_id,
                json.dumps(layout or next_slot(board_id, conn=active)),
            ),
            fetch="one",
            conn=active,
        )
        if card is not None:
            _q(
                "UPDATE app.board SET revision = revision + 1, "
                "updated_at = now() WHERE id = %s",
                (board_id,),
                conn=active,
            )
        return card


def list_cards(
    board_id: uuid.UUID, *, conn=None
) -> list[dict[str, Any]]:
    return _q(
        f"SELECT {CARD_COLUMNS} FROM app.card c "
        "WHERE c.board_id = %s AND c.deleted_at IS NULL "
        "AND EXISTS (SELECT 1 FROM app.board b WHERE b.id = c.board_id "
        "AND b.deleted_at IS NULL) ORDER BY c.created_at",
        (board_id,),
        fetch="all",
        conn=conn,
    )


def get_card(
    card_id: uuid.UUID, *, conn=None, for_update: bool = False
) -> dict[str, Any] | None:
    lock = " FOR UPDATE OF c" if for_update else ""
    sql = (
        f"SELECT {CARD_COLUMNS} FROM app.card c "
        "WHERE c.id = %s AND c.deleted_at IS NULL "
        "AND EXISTS (SELECT 1 FROM app.board b WHERE b.id = c.board_id "
        f"AND b.deleted_at IS NULL){lock}"
    )
    if not for_update:
        return _q(sql, (card_id,), fetch="one", conn=conn)

    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        # Board deletion locks this table first, so transactionally saving a
        # card follows the same table-before-row order and cannot deadlock it.
        cur.execute("LOCK TABLE app.board IN ROW EXCLUSIVE MODE")
        cur.execute(sql, (card_id,))
        return cur.fetchone()


def _change_card_visibility(
    card_id: uuid.UUID, *, deleted: bool, conn=None
) -> dict[str, Any] | None:
    predicate = "deleted_at IS NULL" if deleted else "deleted_at IS NOT NULL"
    value = "now()" if deleted else "NULL"
    with _connection(conn) as active:
        card = _q(
            f"UPDATE app.card AS c SET deleted_at = {value}, updated_at = now() "
            f"WHERE c.id = %s AND {predicate} AND EXISTS (SELECT 1 "
            f"FROM app.board b WHERE b.id = c.board_id "
            f"AND b.deleted_at IS NULL) RETURNING {CARD_COLUMNS}",
            (card_id,),
            fetch="one",
            conn=active,
        )
        if card is not None:
            _q(
                "UPDATE app.board SET revision = revision + 1, "
                "updated_at = now() WHERE id = %s",
                (card["board_id"],),
                conn=active,
            )
        return card


def soft_delete_card(card_id: uuid.UUID, *, conn=None) -> dict[str, Any] | None:
    return _change_card_visibility(card_id, deleted=True, conn=conn)


def restore_card(card_id: uuid.UUID, *, conn=None) -> dict[str, Any] | None:
    return _change_card_visibility(card_id, deleted=False, conn=conn)


def hard_delete_card(card_id: uuid.UUID, *, conn=None) -> None:
    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "DELETE FROM app.card WHERE id = %s RETURNING board_id",
            (card_id,),
        )
        card = cur.fetchone()
        if card is not None:
            cur.execute(
                "UPDATE app.board SET revision = revision + 1, "
                "updated_at = now() WHERE id = %s",
                (card["board_id"],),
            )


def update_card(
    card_id: uuid.UUID, *, conn=None, **fields: Any
) -> dict[str, Any] | None:
    allowed = {
        "title", "semantic_query", "chart_hint", "vega_spec", "prompt",
        "state", "layout", "cache", "ttl_seconds", "previous",
        "pending_clarification",
    }
    json_cols = {"semantic_query", "vega_spec", "layout", "cache", "previous",
                 "pending_clarification"}
    non_substantive = {"cache", "state", "vega_spec"}

    sets: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"not a settable card column: {key}")
        sets.append(f"{key} = %s")
        params.append(json.dumps(value) if key in json_cols and value is not None else value)

    if not sets:
        return get_card(card_id, conn=conn)

    with _connection(conn) as active:
        sets.append("updated_at = now()")
        params.append(card_id)
        card = _q(
            f"UPDATE app.card AS c SET {', '.join(sets)} "
            f"WHERE c.id = %s AND c.deleted_at IS NULL AND EXISTS (SELECT 1 "
            f"FROM app.board b WHERE b.id = c.board_id "
            f"AND b.deleted_at IS NULL) RETURNING {CARD_COLUMNS}",
            tuple(params),
            fetch="one",
            conn=active,
        )
        if card is not None and any(key not in non_substantive for key in fields):
            _q(
                "UPDATE app.board SET revision = revision + 1, "
                "updated_at = now() WHERE id = %s AND deleted_at IS NULL",
                (card["board_id"],),
                conn=active,
            )
        return card


def save_layouts(
    board_id: uuid.UUID, layouts: dict[str, dict], *, conn=None
) -> None:
    with _connection(conn) as active, active.cursor() as cur:
        changed = False
        for card_id, layout in layouts.items():
            cur.execute(
                "UPDATE app.card AS c SET layout = %s, updated_at = now() "
                "WHERE c.id = %s AND c.board_id = %s AND c.deleted_at IS NULL "
                "AND EXISTS (SELECT 1 FROM app.board b WHERE b.id = %s "
                "AND b.deleted_at IS NULL)",
                (json.dumps(layout), card_id, board_id, board_id),
            )
            changed = changed or cur.rowcount > 0
        if changed:
            cur.execute(
                "UPDATE app.board SET revision = revision + 1, "
                "updated_at = now() WHERE id = %s AND deleted_at IS NULL",
                (board_id,),
            )
