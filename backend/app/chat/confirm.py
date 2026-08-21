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
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ..llm.client import LLMClient, LLMError
from ..store import chat as chat_store
from . import plan as planner
from .plan import Applied, PlanRefused


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
        return Confirmation(applied=applied, board_id=applied.board_id,
                            action_id=None)

    board_id, placed = planner.create_placeholders(resolved)
    action = chat_store.create_action(
        stored_plan,
        provider=getattr(client, "provider", ""),
        model=getattr(client, "model", ""),
        effects={"created_board_id":
                 str(board_id) if resolved["kind"] == "new_dashboard"
                 else None,
                 "created_card_ids": [str(c["card_id"]) for c in placed]},
    )
    for ordinal, card in enumerate(placed):
        chat_store.append_action_item(
            action["id"], ordinal=ordinal,
            request={"request_id": card["request_id"],
                     "title": card["title"],
                     "question": card["question"],
                     "chart_hint": card.get("chart_hint")},
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
    stopped = False
    try:
        for item in chat_store.list_action_items(action_id):
            if item["status"] != "queued":
                continue
            current = chat_store.get_action(action_id)
            if current is not None and current["cancel_requested"]:
                stopped = True
                break

            request = item["request"]
            chat_store.update_action_item(item["id"], status="running")
            chat_store.append_event(action_id, "item_started", {
                "request_id": request["request_id"],
                "title": request["title"]})

            try:
                error = planner.build_card(
                    item["card_id"], request["question"],
                    chart_hint=request.get("chart_hint"), client=client)
            except LLMError as exc:
                # A provider that is down is a fact about the provider, not
                # about this question. It still fails this card, because
                # there is nobody watching it to retry.
                error = str(exc)

            if error is None:
                completed += 1
                chat_store.update_action_item(item["id"], status="succeeded")
                chat_store.append_event(action_id, "card", {
                    "request_id": request["request_id"],
                    "card_id": str(item["card_id"]),
                    "board_id": str(action["board_id"] or "")})
            else:
                failed += 1
                chat_store.update_action_item(item["id"], status="failed",
                                              error=error)
                chat_store.append_event(action_id, "item_failed", {
                    "request_id": request["request_id"],
                    "reason": error,
                    # A refusal is a legitimate outcome, not a crash: the
                    # placeholder says why rather than disappearing.
                    "refused": True})
    except Exception as exc:                        # noqa: BLE001
        chat_store.append_event(action_id, "done",
                                {"completed": completed, "failed": failed})
        chat_store.transition_action(action_id, expected="running",
                                     status="failed")
        raise exc

    if stopped:
        remaining = sum(1 for i in chat_store.list_action_items(action_id)
                        if i["status"] == "queued")
        for item in chat_store.list_action_items(action_id):
            if item["status"] == "queued":
                chat_store.update_action_item(item["id"], status="cancelled")
        chat_store.append_event(action_id, "stopped", {
            "completed": completed, "remaining": remaining})
        chat_store.transition_action(action_id, expected="running",
                                     status="stopped")
        return

    chat_store.append_event(action_id, "done",
                            {"completed": completed, "failed": failed})
    chat_store.transition_action(
        action_id, expected="running",
        status="completed_with_errors" if failed else "completed")
