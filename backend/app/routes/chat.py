"""Chat routes.

The whole surface is behind a server flag. When chat is off these are 404,
not 403: an endpoint that says "forbidden" tells you the feature exists.

A change the chat proposes is never applied by the turn that proposed it.
The turn writes a pending plan; a second, explicit request from the browser
confirms it. So the button the person presses is the only thing in the
system that authorises a change, and it authorises exactly the document
they were shown.

The browser sends a consent flag, and it is only ever the second of two
gates. `share_rows = settings.chat_sees_data and body.share_visible_data`
— a client can withhold consent it was given, never grant consent the
server withheld.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from ..chat import confirm as confirm_plan
from ..chat.plan import PlanRefused, UndoRefused
from ..chat.schema import (
    ActionProgressView,
    ChatEventEnvelope,
    ChatMessageOut,
    ChatThreadView,
    ChatTurnResponse,
    PlanConfirmedView,
    TransientResultView,
)
from ..chat.turn import TurnRequest, _message_out, plan_view, run_turn
from ..config import Provider, settings
from ..deps import LAYER
from ..llm.client import LLMError, LLMRateLimited, make_client
from ..render import render
from ..store import cards as store
from ..store import chat as chat_store

def _guard() -> None:
    """Declared as a router dependency, not called inside each handler.

    Dependencies resolve before the request body is validated. Called from
    inside a handler, a disabled endpoint answers a malformed body with 422
    — which tells a prober both that the route exists and what shape it
    wants, the exact thing answering 404 was meant to avoid.
    """
    if not settings.chat_enabled:
        raise HTTPException(404, "not found")


router = APIRouter(prefix="/chat", tags=["chat"],
                   dependencies=[Depends(_guard)])


class TurnIn(BaseModel):
    active_board_id: uuid.UUID
    question: str
    provider: Provider | None = None
    hard: bool = False
    share_visible_data: bool = False
    selected_card_id: uuid.UUID | None = None


class ConfirmIn(BaseModel):
    """Which account pays for the cards this plan still has to build.

    Asked again at confirmation rather than remembered from the turn: the
    plan may have been sitting there while the person changed the setting,
    and the one they can see is the one that should apply.
    """

    provider: Provider | None = None
    hard: bool = False


def _progress(action: dict) -> ActionProgressView:
    items = chat_store.list_action_items(action["id"])
    return ActionProgressView(
        id=action["id"], action="new_cards", status=_status(action["status"]),
        board_id=action["board_id"], total=len(items),
        completed=sum(1 for i in items if i["status"] == "succeeded"),
        failed=sum(1 for i in items if i["status"] == "failed"),
    )


def _status(stored: str) -> str:
    """Storage keeps eight states; the browser needs six.

    `completed_with_errors` is not a different thing to wait for -- the
    per-card failures are already on the cards -- and `cancelled` and
    `undone` both mean the same to someone watching a progress line.
    """
    return {
        "queued": "pending",
        "completed_with_errors": "done",
        "completed": "done",
        "cancelled": "stopped",
        "undone": "stopped",
    }.get(stored, stored)


def _action_or_404(action_id: uuid.UUID) -> dict:
    action = chat_store.get_action(action_id)
    if action is None:
        raise HTTPException(404, "no such action")
    return action


def _thread_or_404(thread_id: uuid.UUID) -> dict:
    thread = chat_store.get_thread(thread_id)
    if thread is None:
        raise HTTPException(404, "no such conversation")
    return thread


@router.post("/threads")
def create_thread():
    return chat_store.create_thread()


@router.get("/threads/{thread_id}")
def get_thread(thread_id: uuid.UUID) -> ChatThreadView:
    thread = _thread_or_404(thread_id)
    messages = chat_store.list_messages(thread_id)
    pending = chat_store.get_pending_plan(thread_id)
    running = chat_store.list_actions(thread_id,
                                      statuses=("queued", "running"))
    return ChatThreadView(
        id=thread["id"],
        # Reloading a transcript re-reads it. It never re-runs a query:
        # opening yesterday's conversation must not touch the warehouse.
        messages=[_message_out(m, m["body"]) for m in messages],
        # A plan outlives the tab that proposed it. Someone who reloads
        # mid-confirmation must find the same document, not a change that
        # quietly disappeared.
        pending_plan=plan_view(pending) if pending else None,
        active_actions=[_progress(a) for a in running],
    )


@router.delete("/threads/{thread_id}")
def clear_thread(thread_id: uuid.UUID):
    _thread_or_404(thread_id)
    chat_store.clear_thread(thread_id,
                            tombstone_days=settings.chat_tombstone_days)
    # A new server-issued id, so the browser cannot keep writing into a
    # conversation the person believes they cleared.
    return chat_store.create_thread()


@router.post("/threads/{thread_id}/turns")
def take_turn(thread_id: uuid.UUID, body: TurnIn) -> ChatTurnResponse:
    _thread_or_404(thread_id)

    if store.get_board(body.active_board_id) is None:
        raise HTTPException(404, "no such dashboard")

    client = make_client(body.provider, hard=body.hard)
    request = TurnRequest(
        thread_id=thread_id, active_board_id=body.active_board_id,
        question=body.question, provider=client.provider, hard=body.hard,
        share_visible_data=body.share_visible_data,
        selected_card_id=body.selected_card_id,
    )
    try:
        return run_turn(request, client=client)
    except LLMRateLimited as exc:
        # The one error the turn loop deliberately does not swallow. A
        # person can try again in a moment; a refusal would imply the
        # question was the problem.
        raise HTTPException(429, str(exc)) from exc


@router.post("/transient/{result_id}/rerun")
def rerun_transient(result_id: uuid.UUID) -> TransientResultView:
    """Explicit only. An expired result shows as expired and waits to be
    asked again rather than silently re-querying the warehouse."""
    stored = chat_store.get_transient(result_id)
    if stored is None:
        raise HTTPException(404, "that result is no longer available")

    from ..semantic.query import SemanticQuery

    query = SemanticQuery.model_validate(stored["query"])
    r = render(query, LAYER, chart_hint=stored["chart_hint"], force=True,
               ttl_seconds=settings.chat_transient_ttl_seconds)
    if r.state != "ready":
        raise HTTPException(409, r.error or "That query could not be run.")

    return TransientResultView(
        id=result_id, restatement=r.restatement or "", semantic_query=query,
        chart_hint=stored["chart_hint"], vega_spec=r.vega_spec,
        rows=r.rows or [], row_count=r.row_count or 0,
        compiled_sql=r.compiled_sql or "",
        data_max_ts=str(r.data_max_ts or "") or None,
        fetched_at=str(r.fetched_at or "") or None,
    )


@router.post("/plans/{plan_id}/confirm")
def confirm(plan_id: uuid.UUID, body: ConfirmIn,
            background: BackgroundTasks) -> PlanConfirmedView:
    """Authorise a plan and carry it out.

    No model is asked what the change is; that was settled when the plan
    was written. A model is asked only the questions the plan already
    contains, and only for cards, and only after this returns.
    """
    plan = chat_store.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, "no such plan")
    if plan["status"] != "pending":
        # Not a 404: the plan exists and the person can see it. Saying so
        # is what stops a double-tap reading as a lost change.
        raise HTTPException(409, f"that plan was already {plan['status']}")

    try:
        client = make_client(body.provider, hard=body.hard)
    except LLMError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        outcome = confirm_plan.confirm(plan, client=client)
    except PlanRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    except chat_store.PlanTransitionError as exc:
        raise HTTPException(409, "that plan was confirmed elsewhere") from exc

    board = store.get_board(outcome.board_id) if outcome.board_id else None
    stored = chat_store.append_message(
        plan["thread_id"], role="assistant",
        body={"action": "applied", "say": outcome.applied.summary,
              "plan_id": str(plan_id),
              "board_id": str(outcome.board_id) if outcome.board_id else None,
              # Read back by the transcript so Undo names this change rather
              # than "the last one", which stops meaning anything the moment
              # a second tab is open.
              "action_id": (str(outcome.action_id) if outcome.action_id
                            else None)},
        active_board_id=outcome.board_id,
        active_board_title=(board or {}).get("title", ""),
        data_exposed=False,
    )

    action = None
    if outcome.action_id is not None:
        action = _progress(_action_or_404(outcome.action_id))
        # After the response, not during it. The person watches the cards
        # arrive rather than watching a spinner on the request that asked
        # for them.
        background.add_task(confirm_plan.run_action, outcome.action_id,
                            client=client)

    return PlanConfirmedView(message=_message_out(stored, stored["body"]),
                             board_id=outcome.board_id, action=action)


@router.post("/plans/{plan_id}/cancel")
def cancel(plan_id: uuid.UUID) -> ChatMessageOut:
    plan = chat_store.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, "no such plan")
    if plan["status"] != "pending":
        raise HTTPException(409, f"that plan was already {plan['status']}")

    chat_store.transition_plan(plan_id, expected="pending",
                               status="cancelled")
    stored = chat_store.append_message(
        plan["thread_id"], role="assistant",
        body={"action": "cancelled", "say": "Discarded, nothing changed.",
              "plan_id": str(plan_id)},
        active_board_id=None, active_board_title="", data_exposed=False,
    )
    return _message_out(stored, stored["body"])


@router.get("/actions/{action_id}")
def action_progress(action_id: uuid.UUID) -> ActionProgressView:
    return _progress(_action_or_404(action_id))


@router.get("/actions/{action_id}/events")
def action_events(action_id: uuid.UUID, after: int = 0
                  ) -> list[ChatEventEnvelope]:
    """Everything that has happened to this action since event `after`.

    A log rather than a stream, and the browser asks for the tail it has
    not seen. Same events, same order, same payloads a stream would carry;
    what it costs is a poll, and what it buys is that a reconnect is just
    another request with a different number in it.
    """
    action = _action_or_404(action_id)
    return [
        ChatEventEnvelope(event={
            "kind": event["kind"], "id": event["id"],
            "action_id": action["id"], "payload": event["payload"],
        })
        for event in chat_store.list_events(action_id, after_id=after)
    ]


@router.post("/actions/{action_id}/undo")
def undo_action(action_id: uuid.UUID) -> ChatMessageOut:
    """Reverse one confirmed change as a single thing.

    Whatever its shape: a rename, a move, a removal, or a six-card
    dashboard. The card's own one-step undo is untouched and still serves
    the edit box.
    """
    action = _action_or_404(action_id)
    try:
        summary = confirm_plan.undo(action_id)
    except UndoRefused as exc:
        raise HTTPException(409, str(exc)) from exc

    board = (store.get_board(action["board_id"]) if action["board_id"]
             else None)
    stored = chat_store.append_message(
        action["thread_id"], role="assistant",
        body={"action": "undone", "say": summary,
              "board_id": str(action["board_id"]) if action["board_id"]
              else None},
        active_board_id=(board or {}).get("id"),
        active_board_title=(board or {}).get("title", ""),
        data_exposed=False,
    )
    return _message_out(stored, stored["body"])


@router.post("/actions/{action_id}/stop")
def stop_action(action_id: uuid.UUID) -> ActionProgressView:
    """Stop after the card being built, not during it.

    A model call already in flight is paid for either way, and a card
    half-written to the database is worse than one extra card.
    """
    _action_or_404(action_id)
    chat_store.request_cancel(action_id)
    return _progress(_action_or_404(action_id))
