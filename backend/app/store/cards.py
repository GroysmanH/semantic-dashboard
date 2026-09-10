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

from ..config import settings
from ..db import app_pool
from ..layout import (
    Layout,
    Layouts,
    VisualizationSize,
    canonical_layouts,
    overlaps,
    place_final_size_batch,
)


BOARD_COLUMNS = (
    "id, title, position, layout_mode, schema_name, revision, deleted_at, "
    "created_at, updated_at"
)
CARD_COLUMNS = (
    "id, board_id, title, semantic_query, chart_hint, vega_spec, prompt, "
    "state, layout, auto_size_pending, cache, ttl_seconds, previous, "
    "pending_clarification, "
    "deleted_at, created_at, updated_at"
)


class LastVisibleBoardError(RuntimeError):
    """Raised when a deletion would leave the application with no board."""


class BoardOrderError(RuntimeError):
    """Raised when a reorder is not the exact set of visible boards."""


class BoardLayoutError(RuntimeError):
    """Raised when a layout is not the exact owned card set requested."""


class BoardLayoutConflict(RuntimeError):
    """Raised when a complete-board write was based on an older revision."""

    def __init__(self, *, layouts: Layouts, revision: int) -> None:
        super().__init__("layout revision conflict")
        self.layouts = layouts
        self.revision = revision


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


def _lock_live_board_for_card(cur, card_id: uuid.UUID) -> uuid.UUID | None:
    """Lock table then board before any mutation can lock the card row."""
    cur.execute("LOCK TABLE app.board IN ROW EXCLUSIVE MODE")
    cur.execute("SELECT board_id FROM app.card WHERE id = %s", (card_id,))
    card = cur.fetchone()
    if card is None:
        return None
    cur.execute(
        "SELECT id FROM app.board WHERE id = %s AND deleted_at IS NULL "
        "FOR UPDATE",
        (card["board_id"],),
    )
    return card["board_id"] if cur.fetchone() is not None else None


# -- boards --------------------------------------------------------------


def create_board(
    title: str, *, layout_mode: str = "free", schema_name: str | None = None,
    conn=None,
) -> dict[str, Any]:
    """A board always has a schema.

    The caller supplies one -- routes inherit it from the board the person
    was looking at, so a second Finance dashboard takes no extra step. The
    fallback is configuration rather than a guess, because a board with no
    schema would be a board that can ask nothing.
    """
    return _q(
        f"INSERT INTO app.board (id, title, position, layout_mode, schema_name) "
        f"VALUES (%s, %s, (SELECT coalesce(max(position) + 1, 0) "
        f"FROM app.board), %s, %s) RETURNING {BOARD_COLUMNS}",
        (uuid.uuid4(), title, layout_mode,
         schema_name or settings.default_schema),
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
    allowed = {"title", "position", "layout_mode", "schema_name"}
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
    with _connection(conn) as active, active.cursor() as cur:
        cur.execute("LOCK TABLE app.board IN ROW EXCLUSIVE MODE")
        board = _q(
            f"UPDATE app.board SET {', '.join(sets)} "
            f"WHERE id = %s AND deleted_at IS NULL RETURNING {BOARD_COLUMNS}",
            tuple(params),
            fetch="one",
            conn=active,
        )
        if board is not None and fields.get("layout_mode") == "auto_pack":
            save_layouts(board_id, {}, conn=active)
            board = get_board(board_id, conn=active)
        return board


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


# Fields a copy inherits. Deliberately not `previous` or
# `pending_clarification`: those are one card's unfinished business with
# one person, and a duplicate that arrives mid-clarification, or that can
# undo a step it never took, is confusing rather than faithful.
COPIED_CARD_FIELDS = ("title", "semantic_query", "chart_hint", "vega_spec",
                      "prompt", "state", "layout", "cache", "ttl_seconds")

COPIED_SINGLE_CARD_FIELDS = (
    "semantic_query", "chart_hint", "vega_spec", "prompt", "state",
    "cache", "ttl_seconds",
)


def duplicate_board(
    board_id: uuid.UUID, title: str, *, conn=None
) -> dict[str, Any] | None:
    """A copy of a dashboard, cards and layout and all.

    The cached results come with it, so a duplicate opens showing the same
    numbers rather than re-running every query against the warehouse the
    moment it appears. Each card keeps its own TTL, so they refresh on
    their own schedule exactly as the originals do.

    Done in one transaction: a half-copied dashboard is worse than none,
    because it looks like a real one.
    """
    with _connection(conn) as active:
        source = get_board(board_id, conn=active)
        if source is None:
            return None

        # Build under free placement so an early source card is not packed
        # in isolation. Applying the source policy after every row exists
        # makes one canonical decision from the complete copied layout.
        # A copy asks the same questions of the same schema; anything
        # else would silently orphan every card it just copied.
        copy = create_board(title, layout_mode="free",
                            schema_name=source["schema_name"], conn=active)
        for card in list_cards(board_id, conn=active):
            fields = {k: card[k] for k in COPIED_CARD_FIELDS}
            made = create_card(
                copy["id"], layout=fields.pop("layout"),
                auto_size_pending=False, conn=active,
            )
            if made is None:                    # pragma: no cover
                return None
            update_card(made["id"], conn=active, **fields)
        if source["layout_mode"] == "auto_pack":
            copy = update_board(
                copy["id"], layout_mode="auto_pack", conn=active
            )
        return get_board(copy["id"], conn=active)


def _duplicate_anchor(source: Layout, occupied: list[Layout]) -> Layout:
    def free(candidate: Layout) -> bool:
        return not any(overlaps(candidate, other) for other in occupied)

    right: Layout = {
        **source,
        "x": source["x"] + source["w"],
    }
    if right["x"] + right["w"] <= COLS and free(right):
        return right

    below: Layout = {
        **source,
        "y": source["y"] + source["h"],
    }
    if free(below):
        return below

    max_y = max(other["y"] + other["h"] for other in occupied)
    candidates = (
        {**source, "x": x, "y": y}
        for y in range(max_y + source["h"] + 1)
        for x in range(COLS - source["w"] + 1)
    )
    return min(
        (candidate for candidate in candidates if free(candidate)),
        key=lambda candidate: (
            abs(candidate["x"] - source["x"])
            + abs(candidate["y"] - source["y"]),
            candidate["y"],
            candidate["x"],
        ),
    )


def duplicate_card(card_id: uuid.UUID, *, conn=None) -> dict[str, Any] | None:
    """Copy one finished card and return the board's canonical geometry."""
    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        if _lock_live_board_for_card(cur, card_id) is None:
            return None
        source = get_card(card_id, conn=active, for_update=True)
        if source is None:
            return None
        existing = list_cards(source["board_id"], conn=active)
        anchor = _duplicate_anchor(
            source["layout"], [card["layout"] for card in existing]
        )
        copied = create_card(
            source["board_id"], layout=anchor, auto_size_pending=False,
            conn=active,
        )
        if copied is None:  # pragma: no cover - board row is locked above
            return None
        fields = {field: source[field] for field in COPIED_SINGLE_CARD_FIELDS}
        copied = update_card(
            copied["id"], title=f"Copy of {source['title']}", conn=active,
            **fields,
        )
        result = save_layouts(source["board_id"], {}, conn=active)
        if result is None:  # pragma: no cover - board row is locked above
            return None
        return {"card": copied, **result}


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
    board_id: uuid.UUID,
    layout: dict | None = None,
    *,
    card_id: uuid.UUID | None = None,
    auto_size_pending: bool = False,
    conn=None,
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
            f"INSERT INTO app.card (id, board_id, layout, auto_size_pending) "
            f"VALUES (%s, %s, %s, %s) RETURNING {CARD_COLUMNS}",
            (
                card_id or uuid.uuid4(),
                board_id,
                json.dumps(layout or next_slot(board_id, conn=active)),
                auto_size_pending,
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
            save_layouts(board_id, {}, conn=active)
            card = get_card(card["id"], conn=active)
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
        cur.execute(
            "SELECT c.board_id FROM app.card c "
            "WHERE c.id = %s AND c.deleted_at IS NULL AND EXISTS "
            "(SELECT 1 FROM app.board b WHERE b.id = c.board_id "
            "AND b.deleted_at IS NULL)",
            (card_id,),
        )
        owner = cur.fetchone()
        if owner is None:
            return None
        cur.execute(
            "SELECT id FROM app.board WHERE id = %s AND deleted_at IS NULL "
            "FOR UPDATE",
            (owner["board_id"],),
        )
        if cur.fetchone() is None:
            return None
        cur.execute(sql, (card_id,))
        return cur.fetchone()


def _change_card_visibility(
    card_id: uuid.UUID, *, deleted: bool, conn=None
) -> dict[str, Any] | None:
    predicate = "deleted_at IS NULL" if deleted else "deleted_at IS NOT NULL"
    value = "now()" if deleted else "NULL"
    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        if _lock_live_board_for_card(cur, card_id) is None:
            return None
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
            save_layouts(card["board_id"], {}, conn=active)
        return card


def soft_delete_card(card_id: uuid.UUID, *, conn=None) -> dict[str, Any] | None:
    return _change_card_visibility(card_id, deleted=True, conn=conn)


def restore_card(card_id: uuid.UUID, *, conn=None) -> dict[str, Any] | None:
    return _change_card_visibility(card_id, deleted=False, conn=conn)


def hard_delete_card(card_id: uuid.UUID, *, conn=None) -> None:
    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        if _lock_live_board_for_card(cur, card_id) is None:
            return
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
            save_layouts(card["board_id"], {}, conn=active)


def update_card(
    card_id: uuid.UUID, *, conn=None, **fields: Any
) -> dict[str, Any] | None:
    allowed = {
        "title", "semantic_query", "chart_hint", "vega_spec", "prompt",
        "state", "layout", "cache", "ttl_seconds", "previous",
        "pending_clarification", "auto_size_pending",
    }
    json_cols = {"semantic_query", "vega_spec", "layout", "cache", "previous",
                 "pending_clarification"}
    # Render artifacts and a person's unfinished card exchange do not change
    # what the dashboard means, so they must not stale a pending chat plan.
    non_substantive = {
        "cache", "state", "vega_spec", "pending_clarification",
    }

    sets: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"not a settable card column: {key}")
        sets.append(f"{key} = %s")
        params.append(json.dumps(value) if key in json_cols and value is not None else value)

    if not sets:
        return get_card(card_id, conn=conn)

    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        if _lock_live_board_for_card(cur, card_id) is None:
            return None
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
        if card is not None and "layout" in fields:
            save_layouts(card["board_id"], {}, conn=active)
            card = get_card(card_id, conn=active)
        return card


def save_rendered_card(
    card_id: uuid.UUID,
    *,
    size: VisualizationSize | None,
    conn=None,
    **fields: Any,
) -> dict[str, Any] | None:
    """Persist one render and consume its one-time sizing flag atomically."""
    with _connection(conn) as active:
        card = get_card(card_id, conn=active, for_update=True)
        if card is None:
            return None
        saved = update_card(card_id, conn=active, **fields)
        if saved is None or size is None or not card["auto_size_pending"]:
            return saved

        target: Layout = {
            **card["layout"],
            "x": min(card["layout"]["x"], COLS - size["w"]),
            "w": size["w"],
            "h": size["h"],
        }
        result = save_layouts(
            card["board_id"],
            {str(card_id): target},
            auto_sized=[card_id],
            conn=active,
        )
        if result is None:  # pragma: no cover - locked live owner above
            return None
        return get_card(card_id, conn=active)


def _reflow_card_batch(
    board_id: uuid.UUID,
    batch_card_ids: Iterable[uuid.UUID],
    *,
    insertion_row: int,
    resized_card_id: uuid.UUID | None = None,
    size: VisualizationSize | None = None,
    conn=None,
) -> dict[str, Any] | None:
    """Persist one action's ordered batch layout from current/final sizes."""
    ordered_ids = list(dict.fromkeys(batch_card_ids))
    cards = list_cards(board_id, conn=conn)
    current: Layouts = {
        str(card["id"]): card["layout"] for card in cards
    }
    owned = set(current)
    batch_ids = [card_id for card_id in ordered_ids if str(card_id) in owned]
    batch_set = {str(card_id) for card_id in batch_ids}
    fixed = {
        card_id: layout for card_id, layout in current.items()
        if card_id not in batch_set
    }
    batch: list[tuple[str, Layout]] = []
    auto_sized: list[uuid.UUID] = []
    pending = {
        card["id"]: card["auto_size_pending"] for card in cards
    }
    for batch_id in batch_ids:
        layout: Layout = dict(current[str(batch_id)])  # type: ignore[assignment]
        if (
            batch_id == resized_card_id
            and size is not None
            and pending[batch_id]
        ):
            layout = {
                **layout,
                "w": size["w"],
                "h": size["h"],
            }
            auto_sized.append(batch_id)
        batch.append((str(batch_id), layout))

    layouts = place_final_size_batch(
        fixed, batch, insertion_row=insertion_row
    )
    return save_layouts(
        board_id,
        layouts,
        auto_sized=auto_sized,
        require_complete=True,
        conn=conn,
    )


def reflow_card_batch(
    board_id: uuid.UUID,
    batch_card_ids: Iterable[uuid.UUID],
    *,
    insertion_row: int,
    conn=None,
) -> dict[str, Any] | None:
    """Reflow failed or stopped placeholders without changing their sizes."""
    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        cur.execute("LOCK TABLE app.board IN ROW EXCLUSIVE MODE")
        cur.execute(
            "SELECT id FROM app.board WHERE id = %s AND deleted_at IS NULL "
            "FOR UPDATE",
            (board_id,),
        )
        if cur.fetchone() is None:
            return None
        return _reflow_card_batch(
            board_id,
            batch_card_ids,
            insertion_row=insertion_row,
            conn=active,
        )


def save_rendered_batch_card(
    card_id: uuid.UUID,
    *,
    batch_card_ids: Iterable[uuid.UUID],
    insertion_row: int,
    size: VisualizationSize | None,
    conn=None,
    **fields: Any,
) -> dict[str, Any] | None:
    """Atomically save one render and re-place its complete chat batch."""
    with _connection(conn) as active:
        card = get_card(card_id, conn=active, for_update=True)
        if card is None:
            return None
        saved = update_card(card_id, conn=active, **fields)
        if saved is None:
            return None
        result = _reflow_card_batch(
            card["board_id"],
            batch_card_ids,
            insertion_row=insertion_row,
            resized_card_id=card_id,
            size=size,
            conn=active,
        )
        if result is None:  # pragma: no cover - locked live owner above
            return None
        return get_card(card_id, conn=active)


def save_layouts(
    board_id: uuid.UUID,
    layouts: dict[str, dict],
    *,
    manually_resized: Iterable[uuid.UUID] = (),
    auto_sized: Iterable[uuid.UUID] = (),
    require_complete: bool = False,
    expected_revision: int | None = None,
    conn=None,
) -> dict[str, Any] | None:
    """Resolve and persist one canonical board layout in one transaction."""
    resized_ids = list(manually_resized)
    auto_sized_ids = list(auto_sized)
    resized = {str(card_id) for card_id in resized_ids}
    completed = {str(card_id) for card_id in auto_sized_ids}
    cleared_ids = [*resized_ids, *auto_sized_ids]
    with _connection(conn) as active, active.cursor(row_factory=dict_row) as cur:
        cur.execute("LOCK TABLE app.board IN ROW EXCLUSIVE MODE")
        cur.execute(
            "SELECT layout_mode, revision FROM app.board "
            "WHERE id = %s AND deleted_at IS NULL FOR UPDATE",
            (board_id,),
        )
        board = cur.fetchone()
        if board is None:
            return None
        cur.execute(
            "SELECT id, layout, auto_size_pending FROM app.card "
            "WHERE board_id = %s AND deleted_at IS NULL FOR UPDATE",
            (board_id,),
        )
        cards = cur.fetchall()
        current: Layouts = {str(card["id"]): card["layout"] for card in cards}
        if expected_revision is not None and board["revision"] != expected_revision:
            raise BoardLayoutConflict(
                layouts=canonical_layouts(
                    current, auto_pack=board["layout_mode"] == "auto_pack"
                ),
                revision=board["revision"],
            )
        submitted = set(layouts)
        owned = set(current)
        if require_complete and submitted != owned:
            raise BoardLayoutError(
                "layouts must contain every card on this board exactly once"
            )
        if not submitted <= owned or not resized <= owned or not completed <= owned:
            raise BoardLayoutError("layout contains a card outside this board")

        desired: Layouts = {**current, **layouts}  # type: ignore[assignment]
        canonical = canonical_layouts(
            desired,
            auto_pack=board["layout_mode"] == "auto_pack",
            preferred=completed,
        )
        changed = False
        pending = {str(card["id"]): card["auto_size_pending"] for card in cards}
        for card_id, layout in canonical.items():
            clear_pending = card_id in resized | completed and pending[card_id]
            if layout == current[card_id] and not clear_pending:
                continue
            cur.execute(
                "UPDATE app.card SET layout = %s, "
                "auto_size_pending = CASE WHEN id = ANY(%s) THEN false "
                "ELSE auto_size_pending END, updated_at = now() WHERE id = %s",
                (json.dumps(layout), cleared_ids, card_id),
            )
            changed = True
        if changed:
            cur.execute(
                "UPDATE app.board SET revision = revision + 1, updated_at = now() "
                "WHERE id = %s RETURNING revision",
                (board_id,),
            )
            revision = cur.fetchone()["revision"]
        else:
            revision = board["revision"]
        return {"layouts": canonical, "revision": revision}
