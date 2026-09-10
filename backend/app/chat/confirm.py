"""Confirmation, and the generation that follows it.

A pending plan becomes an effect here and nowhere else. Two shapes of
effect exist, and the difference is whether a model still has to be asked
anything:

*Immediate.* Renames, reorders, moves, removals and a card edit are pure
database work -- the query for an edit was written while the plan was being
resolved, so by now there is nothing left to decide. They apply inside the
request and the answer is final.

*Generated.* New cards carry questions, not queries. The dashboard and its
empty cards are created straight away so the grid is settled, and then each
question is answered one model call at a time in the background. Each card
succeeds or fails on its own: a question the layer cannot answer costs that
card and nothing else.

The plan row moves `pending -> confirmed` before any of this, and that
transition is a compare-and-set. Two browsers confirming the same plan is a
real sequence of events, not a hypothetical one, and the loser has to be
told rather than served a second copy of the effect.

Both shapes get an action row, including the ones where nothing runs. It is
what Undo attaches to, and "this change cannot be undone because it was too
quick to need a progress bar" is not a rule anybody could be told with a
straight face. A six-card dashboard then reverses as one thing, which is
the reason a turn has an undo of its own on top of the card's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ..config import settings
from ..db import app_pool
from ..llm.client import LLMClient, LLMError
from ..store import cards as cards_store
from ..store import chat as chat_store
from . import plan as planner
from .plan import Applied, PlanRefused, UndoRefused


@dataclass(frozen=True)
class Confirmation:
    applied: Applied
    board_id: uuid.UUID | None
    action_id: uuid.UUID | None


def confirm(stored_plan: dict, *, client: LLMClient) -> Confirmation:
    """Apply a plan the person has just authorised.

    Reads `resolved`, never the action the chat proposed. Whatever the
    preview said is what happens.
    """
    resolved = stored_plan["resolved"] or {}

    if planner.is_stale(stored_plan.get("basis") or {}):
        raise PlanRefused(
            "That dashboard changed after I wrote this plan, so I have not "
            "applied it. Ask again and I will plan against what is there "
            "now.")

    # Claim the plan before doing anything. A second confirmation then
    # fails here rather than applying the change twice.
    chat_store.transition_plan(stored_plan["id"], expected="pending",
                               status="confirmed")

    if resolved.get("kind") not in ("new_cards", "new_dashboard"):
        applied = planner.apply_immediate(resolved)
        # An action row even though nothing runs: it is what Undo attaches
        # to, and a change that cannot be undone because it was too quick to
        # need a progress bar would be a strange rule to explain.
        action = chat_store.create_action(
            stored_plan,
            provider=getattr(client, "provider", ""),
            model=getattr(client, "model", ""),
            effects={"kind": resolved["kind"], "undo": applied.undo,
                     "summary": applied.summary},
        )
        chat_store.transition_action(action["id"], expected="queued",
                                     status="completed")
        return Confirmation(
            applied=Applied(summary=applied.summary,
                            board_id=applied.board_id,
                            action_id=action["id"], undo=applied.undo),
            board_id=applied.board_id, action_id=action["id"])

    board_id, placed = planner.create_placeholders(resolved)
    # A new dashboard has no id until now, and the action row derives its
    # board from the plan. Handing on the resolved document with the id
    # filled in is what lets the browser follow the build on the tab it
    # just landed on rather than on nothing.
    action = chat_store.create_action(
        {**stored_plan, "resolved": {**resolved, "board_id": str(board_id)}},
        provider=getattr(client, "provider", ""),
        model=getattr(client, "model", ""),
        effects={
            "kind": resolved["kind"],
            "created_board_id": (str(board_id)
                                 if resolved["kind"] == "new_dashboard"
                                 else None),
            "created_card_ids": [str(c["card_id"]) for c in placed],
            "batch_insertion_row": min(
                (int(c["layout"]["y"]) for c in placed), default=0
            ),
            # A six-card dashboard reverses as one thing, which is the whole
            # reason a turn has an undo of its own on top of the card's.
            "undo": {
                "kind": "created",
                "board_id": (str(board_id)
                             if resolved["kind"] == "new_dashboard" else None),
                "card_ids": [str(c["card_id"]) for c in placed],
            },
        },
    )
    for ordinal, card in enumerate(placed):
        chat_store.append_action_item(
            action["id"], ordinal=ordinal,
            request={"request_id": card["request_id"],
                     "title": card["title"],
                     "question": card["question"],
                     "chart_hint": card.get("chart_hint"),
                     "layout": card["layout"]},
            card_id=card["card_id"],
        )
    chat_store.append_event(action["id"], "plan", {
        "board_id": str(board_id),
        "board_title": resolved.get("board_title", ""),
        "cards": [{"request_id": c["request_id"], "title": c["title"],
                   "question": c["question"], "layout": c["layout"]}
                  for c in placed],
    })

    count = len(placed)
    return Confirmation(
        applied=Applied(
            summary=f"Building {count} card{'' if count == 1 else 's'}.",
            board_id=board_id,
            created_card_ids=[c["card_id"] for c in placed],
            action_id=action["id"],
        ),
        board_id=board_id,
        action_id=action["id"],
    )


def _terminalize_action(action_id: uuid.UUID, status: str) -> None:
    """Best-effort terminal state after richer cleanup rolled back."""
    try:
        current = chat_store.get_action(action_id)
        if current is not None and current["status"] == "running":
            chat_store.transition_action(
                action_id, expected="running", status=status
            )
    except Exception:  # noqa: BLE001 - preserve the original worker failure
        return


def _fail_action(
    action: dict,
    *,
    current_item_id: uuid.UUID | None,
    batch_card_ids: list[uuid.UUID],
    insertion_row: int,
    completed: int,
    failed: int,
    error: Exception,
) -> None:
    """Record the fullest failure possible, then always terminalize."""
    try:
        with app_pool.connection() as conn:
            if current_item_id is not None:
                chat_store.transition_action_item(
                    current_item_id,
                    expected="running",
                    status="failed",
                    error=str(error),
                    conn=conn,
                )
            cards_store.reflow_card_batch(
                action["board_id"], batch_card_ids,
                insertion_row=insertion_row, conn=conn,
            )
            chat_store.append_event(
                action["id"], "done",
                {"completed": completed,
                 "failed": failed + (current_item_id is not None)},
                conn=conn,
            )
            chat_store.transition_action(
                action["id"], expected="running", status="failed", conn=conn
            )
        return
    except Exception:  # noqa: BLE001 - fallback deliberately omits cleanup
        pass

    if current_item_id is not None:
        try:
            chat_store.transition_action_item(
                current_item_id,
                expected="running",
                status="failed",
                error=str(error),
            )
        except Exception:  # noqa: BLE001 - cancelled/stale items stay terminal
            pass
    _terminalize_action(action["id"], "failed")


def _stop_action(
    action: dict,
    *,
    batch_card_ids: list[uuid.UUID],
    insertion_row: int,
    completed: int,
) -> None:
    """Cancel queued items, reflow and stop as one transaction."""
    try:
        with app_pool.connection() as conn:
            items = chat_store.list_action_items(action["id"], conn=conn)
            queued = [item for item in items if item["status"] == "queued"]
            for item in queued:
                chat_store.transition_action_item(
                    item["id"], expected="queued", status="cancelled", conn=conn
                )
            cards_store.reflow_card_batch(
                action["board_id"], batch_card_ids,
                insertion_row=insertion_row, conn=conn,
            )
            chat_store.append_event(
                action["id"], "stopped",
                {"completed": completed, "remaining": len(queued)},
                conn=conn,
            )
            chat_store.transition_action(
                action["id"], expected="running", status="stopped", conn=conn
            )
    except Exception:
        _terminalize_action(action["id"], "stopped")
        raise


def _finish_action(action_id: uuid.UUID, *, completed: int, failed: int) -> None:
    status = "completed_with_errors" if failed else "completed"
    try:
        with app_pool.connection() as conn:
            chat_store.append_event(
                action_id, "done",
                {"completed": completed, "failed": failed}, conn=conn,
            )
            chat_store.transition_action(
                action_id, expected="running", status=status, conn=conn
            )
    except Exception:
        _terminalize_action(action_id, status)
        raise


def run_action(action_id: uuid.UUID, *, client: LLMClient) -> None:
    """Answer each queued question, one at a time.

    Deliberately sequential. The point of showing a dashboard filling in is
    that a person can see where it got to; six concurrent calls would
    finish sooner and arrive as one indistinguishable batch, and they would
    hit a free tier's rate limit as a burst rather than a trickle.

    Every exit path leaves the action in a terminal state. An action stuck
    on `running` is indistinguishable from one still working, and the
    browser would poll it forever.
    """
    action = chat_store.get_action(action_id)
    if action is None or action["status"] != "queued":
        return
    chat_store.transition_action(action_id, expected="queued",
                                 status="running")

    completed = failed = 0
    current_item_id: uuid.UUID | None = None
    batch_card_ids: list[uuid.UUID] = []
    insertion_row = 0
    try:
        # Every operation after queued -> running belongs inside this
        # boundary. Persisted JSON and store reads are inputs too; neither
        # may strand an action in a state the browser will poll forever.
        board = (cards_store.get_board(action["board_id"])
                 if action["board_id"] else None)
        scope = (board or {}).get("schema_name") or settings.default_schema

        items = chat_store.list_action_items(action_id)
        batch_card_ids = [
            item["card_id"] for item in items if item["card_id"] is not None
        ]
        stored_insertion_row = (action.get("effects") or {}).get(
            "batch_insertion_row"
        )
        if stored_insertion_row is None:
            stored_insertion_row = min(
                (
                    int(item["request"]["layout"]["y"])
                    for item in items
                    if isinstance(item["request"].get("layout"), dict)
                ),
                default=0,
            )
        insertion_row = int(stored_insertion_row)

        for item in items:
            if item["status"] != "queued":
                continue
            current = chat_store.get_action(action_id)
            if current is None or current["status"] != "running":
                return
            if current["cancel_requested"]:
                _stop_action(
                    action,
                    batch_card_ids=batch_card_ids,
                    insertion_row=insertion_row,
                    completed=completed,
                )
                return

            request = item["request"]
            current_item_id = item["id"]
            with app_pool.connection() as conn:
                chat_store.transition_action_item(
                    item["id"], expected="queued", status="running", conn=conn
                )
                chat_store.append_event(action_id, "item_started", {
                    "request_id": request["request_id"],
                    "title": request["title"]}, conn=conn)

            with app_pool.connection() as conn:
                try:
                    error = planner.build_card(
                        item["card_id"], request["question"],
                        chart_hint=request.get("chart_hint"), client=client,
                        scope=scope, batch_card_ids=batch_card_ids,
                        insertion_row=insertion_row, conn=conn)
                except LLMError as exc:
                    # A provider outage is an item failure, not a reason to
                    # leave the whole action running indefinitely.
                    error = str(exc)

                if error is None:
                    chat_store.transition_action_item(
                        item["id"], expected="running", status="succeeded",
                        conn=conn,
                    )
                    chat_store.append_event(action_id, "card", {
                        "request_id": request["request_id"],
                        "card_id": str(item["card_id"]),
                        "board_id": str(action["board_id"] or "")}, conn=conn)
                else:
                    cards_store.reflow_card_batch(
                        action["board_id"], batch_card_ids,
                        insertion_row=insertion_row, conn=conn,
                    )
                    chat_store.transition_action_item(
                        item["id"], expected="running", status="failed",
                        error=error, conn=conn,
                    )
                    chat_store.append_event(action_id, "item_failed", {
                        "request_id": request["request_id"],
                        "reason": error,
                        # A refusal is a legitimate outcome, not a crash: the
                        # placeholder says why rather than disappearing.
                        "refused": True}, conn=conn)

            if error is None:
                completed += 1
            else:
                failed += 1
            current_item_id = None

        current = chat_store.get_action(action_id)
        if current is not None and current["status"] == "running" \
                and current["cancel_requested"]:
            _stop_action(
                action,
                batch_card_ids=batch_card_ids,
                insertion_row=insertion_row,
                completed=completed,
            )
            return
        _finish_action(action_id, completed=completed, failed=failed)
    except Exception as exc:                        # noqa: BLE001
        _fail_action(
            action,
            current_item_id=current_item_id,
            batch_card_ids=batch_card_ids,
            insertion_row=insertion_row,
            completed=completed,
            failed=failed,
            error=exc,
        )
        raise exc


# States an action can be undone from. `running` is deliberately absent:
# reversing a generation while it is still writing cards would race the
# worker, and "stop it first" is a sentence a person can act on.
UNDOABLE = {"completed", "completed_with_errors", "stopped", "failed"}


def undo(action_id: uuid.UUID) -> str:
    """Reverse one confirmed change, whatever shape it was."""
    action = chat_store.get_action(action_id)
    if action is None:
        raise UndoRefused("There is no record of that change.")
    if action["status"] == "undone":
        raise UndoRefused("That has already been undone.")
    if action["status"] not in UNDOABLE:
        raise UndoRefused("That is still being built. Stop it first, then "
                          "undo what it managed.")

    recorded = (action["effects"] or {}).get("undo") or {}
    # Reversed before the status moves, so a refusal leaves the action
    # undoable rather than marked done and unreversed.
    summary = planner.reverse(recorded)
    chat_store.transition_action(action_id, expected=action["status"],
                                 status="undone")
    return summary
