"""Read-only chat routes.

The whole surface is behind a server flag. When chat is off these are 404,
not 403: an endpoint that says "forbidden" tells you the feature exists.

The browser sends a consent flag, and it is only ever the second of two
gates. `share_rows = settings.chat_sees_data and body.share_visible_data`
— a client can withhold consent it was given, never grant consent the
server withheld.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..chat.schema import (
    ChatMessageOut,
    ChatThreadView,
    ChatTurnResponse,
    TransientResultView,
)
from ..chat.turn import TurnRequest, _message_out, run_turn
from ..config import Provider, settings
from ..deps import LAYER
from ..llm.client import LLMRateLimited, make_client
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
    return ChatThreadView(
        id=thread["id"],
        # Reloading a transcript re-reads it. It never re-runs a query:
        # opening yesterday's conversation must not touch the warehouse.
        messages=[_message_out(m, m["body"]) for m in messages],
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
