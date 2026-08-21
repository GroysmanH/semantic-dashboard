"""One chat turn: build context, ask, validate, dispatch.

The error discipline mirrors query_step.ask() because the failure modes are
identical. One retry on an answer outside the grammar, with the reason
stated so the retry is informed rather than a second roll of the dice.
LLMRateLimited is re-raised: a card should say "try again in a moment" and
a batch should wait, and only the caller knows which it is.

The turn loop never applies a mutation. It produces intent; a plan resolver
freezes that into an exact preview and a person confirms it. A mutation
arriving here with changes switched off is refused, not quietly executed.

A turn is one call when the answer is prose and two when it is not. The
first picks the shape; the second fills it in against a schema holding only
that shape. The reason is a measured grammar ceiling -- see
schema.TaskAction -- but the arrangement pays for itself twice over: the
router cannot name a card or write a query, and `run_query` and `edit_card`
are resolved through query_step.ask(), which is the only place in this
application that turns English into a semantic query.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable
from uuid import UUID

from ..config import Provider, settings
from ..deps import LAYER, SYNONYMS
from ..llm.client import LLMClient, LLMError, LLMRateLimited, LLMSchemaError
from ..llm.query_step import ask as ask_model
from ..render import render
from ..semantic.query import SemanticQuery
from ..semantic.restate import restate
from ..store import cards as store
from ..store import chat as chat_store
from . import plan as planner
from .context import ContextLimits, build_context
from .prompt import build_chat_system_prompt, detail_instruction
from .schema import (
    DETAIL_SCHEMAS,
    ChatMessageOut,
    ChatRouterResponse,
    ChatTurnResponse,
    PendingPlanView,
    RunQueryAction,
    SourceRef,
    TransientResultView,
    VerifiedClaimView,
)
from .verify import verify_turn

SPOKEN = {"answer", "clarify", "refuse"}

NO_CHANGES = (
    "I can only read dashboards at the moment. Changing them is not "
    "switched on in this build."
)

ALREADY_PENDING = (
    "There is already a change waiting for you to confirm or discard. "
    "Deal with that one first and I will pick this up after."
)


@dataclass(frozen=True)
class TurnRequest:
    thread_id: UUID
    active_board_id: UUID
    question: str
    provider: Provider
    hard: bool
    share_visible_data: bool
    selected_card_id: UUID | None = None


def _rendered_cards(board_id: UUID) -> list[dict[str, Any]]:
    """Cards as the person currently sees them.

    Read from the stored render cache rather than re-executed: the chat
    must describe what is on screen, and a silent rerun could answer about
    numbers the person has never seen.

    The cache envelope holds the rows under `result` and carries no
    restatement — that is computed deterministically at render time. So it
    is recomputed here from the semantic query, the same way and by the
    same function the card header uses.
    """
    out = []
    for card in store.list_cards(board_id):
        cache = card.get("cache") or {}
        rows = cache.get("result") or []

        restatement = ""
        query = card.get("semantic_query")
        if query:
            try:
                parsed = SemanticQuery.model_validate(query)
                entity = LAYER.get(parsed.entity)
                if entity is not None:
                    restatement = restate(parsed, entity,
                                          row_count=cache.get("row_count"))
            except Exception:      # noqa: BLE001
                # A card whose layer moved underneath it has no honest
                # sentence. Better to say nothing than to invent one.
                restatement = ""

        out.append({
            "id": str(card["id"]),
            "board_id": str(card["board_id"]),
            "title": card.get("title") or "",
            "layout": card.get("layout") or {},
            "render": {
                "state": card.get("state"),
                "restatement": restatement,
                "chart_type": card.get("chart_hint") or "",
                "rows": rows,
                "row_count": cache.get("row_count") or len(rows),
                "data_max_ts": cache.get("data_max_ts") or "",
            },
        })
    return out


def _message_out(stored: dict, body: dict) -> ChatMessageOut:
    return ChatMessageOut(
        id=stored["id"],
        role=stored["role"],
        action=body.get("action", "answer"),
        say=body.get("say", ""),
        claims=[VerifiedClaimView(**c) for c in body.get("claims", [])],
        clarify=body.get("clarify"),
        refusal=body.get("refusal"),
        missing_metric=body.get("missing_metric"),
        request_text=body.get("request_text"),
        active_board_id=stored.get("active_board_id"),
        active_board_title=stored.get("active_board_title") or "",
        data_exposed=stored.get("data_exposed", False),
        created_at=str(stored["created_at"]),
    )


def run_turn(request: TurnRequest, *, client: LLMClient,
             allow_changes: bool = True,
             today: date | None = None) -> ChatTurnResponse:
    board = store.get_board(request.active_board_id)
    if board is None:
        raise ValueError("no such dashboard")

    boards = store.list_boards()
    # Taken once and reused for the whole turn. Claims address rows by
    # position, so re-reading the cards between prompt and verification
    # could shift the numbers under the index the model was given.
    cards = _rendered_cards(request.active_board_id)

    # Two gates, and the browser's is only ever the second of them. A
    # client flag alone must never open the data path.
    share_rows = bool(settings.chat_sees_data and request.share_visible_data)

    history = chat_store.list_messages(request.thread_id)
    built = build_context(
        boards=boards, active_board=board, rendered_cards=cards,
        messages=history, question=request.question,
        selected_card_id=(str(request.selected_card_id)
                          if request.selected_card_id else None),
        share_rows=share_rows, limits=ContextLimits(
            max_rows=settings.chat_max_rows,
            max_chars=settings.chat_max_context_chars,
            history_turns=settings.chat_history_turns,
        ),
    )

    chat_store.append_message(
        request.thread_id, role="user",
        body={"action": "ask", "say": request.question},
        active_board_id=board["id"],
        active_board_title=board["title"],
        data_exposed=built.data_exposed,
    )

    system = build_chat_system_prompt(LAYER)
    today = today or date.today()
    base = (f"Today is {today:%d %B %Y}.\n\n{built.text}\n\n"
            f"# the person asks\n{request.question}")

    try:
        turn = _ask(client, system, base, ChatRouterResponse,
                    unwrap=True)
    except _Refused as exc:
        return _store(request, board, built,
                      {"action": "refuse", "refusal": str(exc)}, client)
    except LLMRateLimited:
        # Not a refusal. The caller decides whether to wait or to say "try
        # again in a moment".
        raise

    if turn.action in SPOKEN:
        return _dispatch(request, board, built, turn, client, cards)

    return _task(request, board, built, turn, client, cards, boards,
                 system=system, base=base, allow_changes=allow_changes)


class _Refused(RuntimeError):
    """A model failure the person should be told about in a sentence."""


def _ask(client: LLMClient, system: str, user: str, schema, *,
         unwrap: bool = False):
    """One structured ask, with the retry discipline the whole app shares.

    An answer outside the grammar is retried once with the reason attached,
    because a stated reason is a different question from the same question
    asked twice. A second miss is the grammar telling you something, not
    bad luck.
    """
    reason: str | None = None
    for _ in range(2):
        prompt = user if reason is None else (
            f"{user}\n\nYour previous answer was rejected: {reason}\n"
            f"Return a corrected answer using only the listed vocabulary.")
        try:
            answer = client.ask(system, prompt, schema)
            return answer.turn if unwrap else answer
        except LLMSchemaError as exc:
            reason = str(exc)
        except LLMRateLimited:
            raise
        except LLMError as exc:
            raise _Refused(str(exc)) from exc
    raise _Refused(reason or "I could not put that into a form I can run.")


def _task(request, board, built, turn, client, cards, boards, *,
          system: str, base: str, allow_changes: bool) -> ChatTurnResponse:
    """The second half of a turn that is not prose.

    `run_query` needs no second schema: the person already wrote the
    question, and query_step.ask() is the path that turns a question into a
    validated query everywhere else in this application.
    """
    if turn.kind == "run_query":
        return _run_query(request, board, built, turn, client)

    if not allow_changes:
        return _store(request, board, built,
                      {"action": "refuse", "refusal": NO_CHANGES}, client)

    if chat_store.get_pending_plan(request.thread_id) is not None:
        return _store(request, board, built,
                      {"action": "refuse", "refusal": ALREADY_PENDING}, client)

    user = f"{base}\n\n{detail_instruction(turn.kind, turn.say)}"
    try:
        detail = _ask(client, system, user, DETAIL_SCHEMAS[turn.kind])
        resolved = planner.resolve(turn.kind, detail, board=board,
                                   boards=boards, cards=cards, client=client)
    except _Refused as exc:
        return _store(request, board, built,
                      {"action": "refuse", "refusal": str(exc)}, client)
    except planner.PlanRefused as exc:
        return _store(request, board, built,
                      {"action": "refuse", "refusal": str(exc)}, client)

    # `say` comes from the router turn, which is where the model described
    # what it was about to do. The detail call is not asked to describe it
    # again; it is asked to fill it in.
    try:
        stored_plan = chat_store.save_pending_plan(
            request.thread_id,
            action={"kind": resolved.kind,
                    "detail": detail.model_dump(mode="json")},
            resolved=resolved.stored(say=turn.say),
            basis=resolved.basis,
        )
    except chat_store.PendingPlanExistsError:
        return _store(request, board, built,
                      {"action": "refuse", "refusal": ALREADY_PENDING}, client)

    view = plan_view(stored_plan, say=turn.say)
    response = _store(request, board, built,
                      {"action": resolved.kind, "say": turn.say,
                       "plan_id": str(stored_plan["id"])}, client)
    return response.model_copy(update={"pending_plan": view})


def plan_view(stored: dict, *, say: str = "") -> PendingPlanView:
    """A stored plan as the browser sees it.

    Staleness is computed at read time rather than stored, because the
    thing that makes a plan stale happens somewhere else entirely -- on the
    board, in another tab, after this row was written.
    """
    resolved = stored["resolved"] or {}
    return PendingPlanView(
        id=stored["id"],
        action=resolved.get("kind", ""),
        say=say or resolved.get("say", ""),
        operations=resolved.get("operations", []),
        cards=[{k: v for k, v in c.items() if k != "chart_hint"}
               for c in resolved.get("cards", [])],
        target_board_id=resolved.get("board_id"),
        target_board_title=resolved.get("board_title", ""),
        stale=planner.is_stale(stored.get("basis") or {}),
        created_at=str(stored["created_at"]),
    )


def _dispatch(request, board, built, turn, client,
              cards) -> ChatTurnResponse:
    if turn.action == "clarify":
        return _store(request, board, built,
                      {"action": "clarify", "clarify": turn.question}, client)

    if turn.action == "refuse":
        return _store(request, board, built, {
            "action": "refuse", "refusal": turn.reason,
            "missing_metric": turn.missing_metric,
            # Handed back for the person to copy, never appended to a
            # backlog this application would then have to own.
            "request_text": turn.request_text,
        }, client)

    rows_by_card = {
        UUID(c["id"]): c["render"]["rows"]
        for c in cards
        if c["id"] in built.exact_card_ids
    }
    result = verify_turn(say=turn.say, claims=turn.claims,
                         rows_by_card=rows_by_card)

    titles = {str(c["id"]): c["title"] for c in cards}
    claims = [
        VerifiedClaimView(
            text=c.text, displayed_value=c.displayed_value,
            sources=[SourceRef(card_id=cid, board_id=board["id"],
                               card_title=titles.get(str(cid), ""))
                     for cid in c.source_card_ids],
        ).model_dump(mode="json")
        for c in result.claims
    ]
    say = result.safe_say
    if built.notices:
        say = " ".join([say, *built.notices]).strip()

    return _store(request, board, built,
                  {"action": "answer", "say": say, "claims": claims}, client)


def _run_query(request, board, built, turn, client) -> ChatTurnResponse:
    """A question the cards on screen cannot answer.

    The query is written by query_step.ask(), not by the chat grammar. That
    is the only place in this application that turns English into a
    semantic query, and routing through it means a chat query gets the
    synonym guard, layer validation and the retry that a card's question
    gets -- rather than a second, weaker implementation of all three.
    """
    outcome = ask_model(request.question, LAYER, client, synonyms=SYNONYMS)
    if outcome.refusal:
        return _store(request, board, built,
                      {"action": "refuse", "refusal": outcome.refusal}, client)
    if outcome.clarify:
        return _store(request, board, built,
                      {"action": "clarify", "clarify": outcome.clarify},
                      client)

    query = RunQueryAction(say=turn.say, semantic_query=outcome.query,
                           chart_hint=outcome.chart_hint)
    r = render(query.semantic_query, LAYER, chart_hint=query.chart_hint,
               ttl_seconds=settings.chat_transient_ttl_seconds)

    if r.state != "ready":
        return _store(request, board, built,
                      {"action": "refuse",
                       "refusal": r.error or "That query could not be run."},
                      client)

    stored = chat_store.save_transient(
        request.thread_id,
        query=query.semantic_query.model_dump(mode="json"),
        chart_hint=query.chart_hint, title=query.say or "",
        cache=r.cache or {},
        ttl_seconds=settings.chat_transient_ttl_seconds,
    )

    view = TransientResultView(
        id=stored["id"], restatement=r.restatement or "",
        semantic_query=query.semantic_query, chart_hint=query.chart_hint,
        vega_spec=r.vega_spec, rows=r.rows or [], row_count=r.row_count or 0,
        compiled_sql=r.compiled_sql or "", data_max_ts=str(r.data_max_ts or "")
        or None, fetched_at=str(r.fetched_at or "") or None,
        expires_at=str(stored["expires_at"]),
    )

    # The transcript keeps the question and the cache id, never the rows.
    response = _store(request, board, built, {
        "action": "run_query", "say": query.say,
        "transient_result_id": str(stored["id"]),
        "restatement": r.restatement or "",
    }, client)
    return response.model_copy(update={"transient_result": view})


def _store(request, board, built, body, client) -> ChatTurnResponse:
    body = {**body, "provider": getattr(client, "provider", ""),
            "model": getattr(client, "model", "")}
    # Notices, never payloads: the transcript records that a card was
    # summarised, not what the summary said.
    if built.notices:
        body["notices"] = list(built.notices)

    stored = chat_store.append_message(
        request.thread_id, role="assistant", body=body,
        active_board_id=board["id"], active_board_title=board["title"],
        data_exposed=built.data_exposed,
    )
    return ChatTurnResponse(message=_message_out(stored, body))
