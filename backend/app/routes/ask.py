"""Query endpoints.

/query takes an explicit semantic query and renders it. /ask takes natural
language and is wired to the model in the LLM phase.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import Provider, settings
from ..db import app_pool
from ..deps import LAYER, SYNONYMS, example_questions
from ..layer.scope import elsewhere, layer_for, schemas
from ..llm.client import (
    LLMError,
    LLMRateLimited,
    configured_providers,
    make_client,
)
from ..llm.query_step import ask as ask_model
from ..layout import visualization_size
from ..render import is_persistable, render, to_payload
from ..semantic.diff import diff_queries
from ..semantic.query import ChartHint, SemanticQuery
from ..store import cards as store

router = APIRouter(tags=["query"])


class AskIn(BaseModel):
    question: str
    card_id: uuid.UUID | None = None
    # The asker says the question is hard; the server decides what that
    # costs. Exposing a model id here would let any caller spend the
    # expensive one, and would ask a manager to reason about model names.
    hard: bool = False
    # Which API answers. Unlike a model id this is not an escalator -- it
    # chooses which account pays, and one of the two is free -- so it is
    # safe to expose. `hard` remains the only way to spend more.
    provider: Provider | None = None
    # Is this an answer to what the card last said, or a new request?
    #
    # True replies to the card's unfinished exchange whatever it was, False
    # abandons it, and None -- for callers that predate the question -- keeps
    # the older rule: a clarifying question is answered by whatever is typed
    # next, a refusal is not.
    reply: bool | None = None


class QueryIn(BaseModel):
    semantic_query: SemanticQuery
    chart_hint: ChartHint | None = None
    title: str = ""
    card_id: uuid.UUID | None = None      # when set, the result is saved


@router.get("/layer")
def get_layer(schema: str | None = None):
    """What the manager is allowed to ask about. Also what the empty card
    shows as examples.

    Scoped when a schema is named, because an empty card on a planning
    dashboard suggesting "oil production by month" teaches the wrong
    vocabulary and then refuses it.
    """
    scoped = layer_for(LAYER, schema) if schema else LAYER
    return {
        "entities": [
            {
                "name": e.name,
                "label": e.label,
                "description": e.description,
                "unverified": e.low_confidence_fields(),
                "dimensions": [
                    {"name": k, "label": d.label, "type": d.type,
                     "grains": d.grains, "values": d.values}
                    for k, d in e.dimensions.items()
                ],
                "measures": [
                    {"name": k, "label": m.label, "agg": m.agg,
                     "description": m.description}
                    for k, m in e.measures.items()
                ],
            }
            for e in scoped.values()
        ],
        "examples": example_questions(scoped),
        # Allowlisted, like everywhere else. A name offered here that the
        # catalogue will not show is a schema somebody can be told about
        # and then cannot reach.
        "schemas": [s for s in schemas(LAYER)
                    if not settings.schema_allowlist
                    or s in settings.schema_allowlist],
        "providers": {
            "default": settings.llm_provider,
            "available": configured_providers(),
            # strong_available is false when a provider's two tiers are the
            # same model id, as NVIDIA's deliberately are. Offering "think
            # harder" there would promise an escalation that cannot happen.
            "capabilities": {
                provider: {
                    "default_model": settings.models(provider)[0],
                    "strong_model": settings.models(provider)[1],
                    "strong_available":
                        settings.models(provider)[0] != settings.models(provider)[1],
                }
                for provider in configured_providers()
            },
        },
        # Both server gates, stated separately. The consent control has to
        # be able to say which one is closed.
        "chat": {
            "enabled": settings.chat_enabled,
            "data_sharing_permitted": settings.chat_sees_data,
        },
    }


@router.post("/query")
def run_query(body: QueryIn):
    r = render(body.semantic_query, LAYER, chart_hint=body.chart_hint,
               title=body.title)

    if body.card_id is not None and is_persistable(r):
        _save(body, r)

    return to_payload(r)


def _save(body: QueryIn, r, *, prompt: str | None = None) -> None:
    with app_pool.connection() as conn:
        existing = store.get_card(body.card_id, conn=conn, for_update=True)
        if existing is None:
            raise HTTPException(404, "no such card")
        previous = (
            {"semantic_query": existing["semantic_query"],
             "chart_hint": existing["chart_hint"],
             "vega_spec": existing["vega_spec"]}
            if existing.get("semantic_query") else None
        )
        fields = {
            "semantic_query": r.semantic_query.model_dump(mode="json"),
            "chart_hint": r.chart_hint,
            "vega_spec": r.vega_spec,
            "title": body.title or existing["title"],
            "state": r.state,
            "cache": r.cache,
            "previous": previous,
        }
        if prompt is not None:
            fields["prompt"] = prompt
        size = (
            visualization_size(
                r.chart_type,
                r.vega_spec,
                r.chart_rows if r.chart_rows is not None else r.rows,
            )
            if r.state == "ready"
            else None
        )
        saved = store.save_rendered_card(
            body.card_id, conn=conn, size=size, **fields
        )
        if saved is None:
            raise HTTPException(404, "no such card")


def _board_schema(card: dict | None) -> str:
    """The schema the card's dashboard asks of, or "" when there is no board.

    A question asked with no card behind it -- the eval does this -- has no
    dashboard and therefore no scope, and gets the whole layer.
    """
    if not card:
        return ""
    board = store.get_board(card["board_id"])
    return (board or {}).get("schema_name") or settings.default_schema


def _is_a_reply(reply: bool | None, pending: dict | None) -> bool:
    """Whether the card's last word is context for what was just typed."""
    if not pending:
        return False
    if reply is not None:
        return reply
    # No opinion from the caller. A question was asked and is owed an
    # answer; a refusal ended the exchange and is owed nothing.
    return pending.get("kind", "clarify") == "clarify"


@router.post("/ask")
def ask(body: AskIn):
    """Natural language in, a card out -- or one clarifying question, or a
    refusal naming what is undefined. Never a confidently wrong chart."""
    card = store.get_card(body.card_id) if body.card_id else None
    current = (SemanticQuery.model_validate(card["semantic_query"])
               if card and card.get("semantic_query") else None)
    # The card's unfinished exchange, so that a reply to it has something to
    # attach to. Without this the card asks "oil or gas?", the person types
    # "oil", and the next request is a single word with no subject.
    stored = (card.get("pending_clarification") if card else None) or None
    clarifying = stored if _is_a_reply(body.reply, stored) else None

    # What this dashboard is allowed to ask about. Writing a query is
    # scoped; rendering one is not -- a card built before the dashboard was
    # switched still draws, through the whole layer, further down.
    scope = _board_schema(card)
    writable = layer_for(LAYER, scope) if scope else LAYER

    if scope:
        # Before the client exists, so an out-of-scope question costs
        # nothing. A narrowed menu is exactly the condition under which a
        # model stops refusing and starts force-fitting, so the question of
        # whether the subject is even here is settled deterministically
        # rather than delegated to the thing that would guess.
        other = elsewhere(body.question, LAYER, SYNONYMS, scope)
        if other is not None:
            message = (f"That belongs to the {other} schema, and this "
                       f"dashboard is set to {scope}. Switch the dashboard's "
                       f"schema to ask about it.")
            if body.card_id:
                store.update_card(body.card_id, pending_clarification={
                    "kind": "refused", "question": message,
                    "asked": (clarifying or {}).get("asked", body.question),
                })
            return {"state": "refused", "message": message,
                    "provider": "", "model": ""}

    try:
        client = make_client(body.provider, hard=body.hard)
    except LLMError as exc:
        raise HTTPException(400, str(exc)) from exc
    who = {"provider": client.provider, "model": client.model}

    try:
        outcome = ask_model(body.question, writable, client,
                            synonyms=SYNONYMS, current=current,
                            clarifying=clarifying)
    except LLMRateLimited as exc:
        # A person waiting on a card wants to be told, not held. The eval
        # makes the other choice and waits.
        return {
            "state": "refused",
            "message": str(exc),
            "replyable": False,
            **who,
        }

    # Both of these leave the card with something to say and nothing to
    # show, and in both cases the useful next move is to reply to it. A
    # refusal is stored for the same reason a question is: "the layer has no
    # per-region ranking" is a sentence somebody answers with "then rank
    # them overall", and that answer means nothing on its own.
    said = outcome.refusal or outcome.clarify
    if said:
        replyable = bool(outcome.clarify) or outcome.replyable
        if body.card_id and replyable:
            store.update_card(body.card_id, pending_clarification={
                "kind": "refused" if outcome.refusal else "clarify",
                "question": said,
                # The request the exchange is about, not the sentence that
                # interrupted it: that is what has to be rebuilt once the
                # obstacle is out of the way. It carries over only from an
                # exchange this question is a reply to -- somebody who
                # abandoned the last one and asked something else is not
                # still asking for the thing they walked away from.
                "asked": (clarifying or {}).get("asked", body.question),
            })
        elif body.card_id and stored and not _is_a_reply(body.reply, stored):
            # The person abandoned the old exchange with a new request.
            # An operational failure in that new request must not resurrect
            # the unrelated exchange they left.
            store.update_card(body.card_id, pending_clarification=None)
        return {"state": "refused" if outcome.refusal else "clarify",
                "message": said, "replyable": replyable, **who}

    # Answered: the exchange has done its job and must not colour the next
    # unrelated question asked of this card.
    if stored and body.card_id:
        store.update_card(body.card_id, pending_clarification=None)

    r = render(outcome.query, LAYER, chart_hint=outcome.chart_hint,
               title=(card["title"] if current is not None and card.get("title")
                      else outcome.title))

    # What moved, stated deterministically. An edit that silently changes
    # more than was asked for is the same failure as a chart that silently
    # means something else.
    changed = (diff_queries(current, outcome.query, LAYER[outcome.query.entity])
               if current is not None and outcome.query.entity in LAYER else [])

    if body.card_id is not None and is_persistable(r):
        # An edit keeps the card's name. "Break this down by well type" is
        # an instruction, not a title, and letting it become one renames the
        # card to the last thing anybody typed at it. The restatement
        # already carries the full meaning.
        title = card["title"] if current is not None and card.get("title") \
            else outcome.title
        _save(
            QueryIn(
                semantic_query=outcome.query,
                chart_hint=outcome.chart_hint,
                title=title,
                card_id=body.card_id,
            ),
            r,
            prompt=body.question,
        )

    return {"state": r.state, **who, "changed": changed, **to_payload(r)}
