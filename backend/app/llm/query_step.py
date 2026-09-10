"""Natural language -> validated semantic query.

Ambiguity gets two independent triggers. The design doc calls for
detection "by margin", but models do not emit calibrated scores and
self-reported confidence is noise, so the model-reported signal is backed
by a deterministic one that does not depend on the model being honest.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, ConfigDict

from ..layer.models import Layer
from ..semantic.query import ChartHint, SemanticQuery
from ..semantic.validate import QueryValidationError, validate_query
from .client import LLMClient, LLMError, LLMRateLimited, LLMSchemaError
from .prompt import build_system_prompt

logger = logging.getLogger(__name__)
SAFE_FORMAT_REFUSAL = "I couldn’t prepare that query safely. Nothing changed."


class Ambiguity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    candidates: list[str]
    question: str


class AskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_query: SemanticQuery
    chart_hint: ChartHint | None = None
    ambiguity: Ambiguity | None = None
    title: str = ""


@dataclass
class AskOutcome:
    """Exactly one of these is populated: a query to run, a question to ask
    back, or a refusal naming what is undefined."""

    query: SemanticQuery | None = None
    chart_hint: ChartHint | None = None
    title: str = ""
    clarify: str | None = None
    refusal: str | None = None
    # Transport, credential, and capacity failures are not something a
    # person can clarify. Semantic refusals remain replyable by default.
    replyable: bool = True
    attempts: int = 0
    failure_code: str | None = None


WORD = re.compile(r"[a-z_]+")
NOISE = re.compile(r"[^a-z0-9]+")


def _echoes(said: str, question: str) -> bool:
    """True when the model's clarifying question is the request back again.

    Models under-supply this field: asked what is ambiguous, one of them
    fills `question` with a verbatim copy of what the person typed. The card
    then shows somebody their own sentence and waits, which is the one
    outcome worse than not asking.
    """
    a = NOISE.sub(" ", said.lower()).strip()
    b = NOISE.sub(" ", question.lower()).strip()
    if not a or not b:
        return True
    return a in b or b in a


def _ask_back(flagged: Ambiguity, question: str, layer: Layer,
              entity_name: str) -> str:
    """The question actually put to the person.

    Written here rather than taken on trust, for the same reason the
    restatement is: the sentence a card stakes its usefulness on should not
    be one the model can degrade by filling a field carelessly. `term` and
    `candidates` are structural and survive that carelessness, so a usable
    question can always be built from them.
    """
    if "?" in flagged.question and not _echoes(flagged.question, question):
        return flagged.question

    entity = layer.get(entity_name)
    labels = []
    for name in flagged.candidates:
        field = None
        if entity is not None:
            field = entity.measures.get(name) or entity.dimensions.get(name)
        labels.append(field.label if field is not None else name)

    term = flagged.term.strip()
    if not term:
        return "I could not tell which measure you meant. Which one should I use?"
    if len(labels) > 1:
        return f"By {term!r} do you mean {' or '.join(labels)}?"
    if labels:
        return f"By {term!r} do you mean {labels[0]}?"
    return f"What do you mean by {term!r}?"


def _answering_a_question(clarifying: dict | None) -> bool:
    """A clarifying question was answered, as opposed to a refusal replied to.

    The difference decides whether asking again is allowed. Asking twice
    after being answered is the failure the memory exists to prevent; a
    refusal is not a question, so a reply to one is a fresh request and gets
    the same ambiguity protection as any other.
    """
    return bool(clarifying) and clarifying.get("kind", "clarify") == "clarify"


def _user_body(question: str, current: SemanticQuery | None,
               clarifying: dict | None) -> str:
    """What the model is asked, in the order it needs to read it.

    Three things can be true at once -- the card is mid-exchange, the card
    already shows a query, and something has just been typed -- and all
    three have to arrive as one instruction. Composing them is not
    cosmetic: an edit that also answered a clarifying question used to
    discard the exchange outright, so a card with a chart on it asked a
    question and then forgot it had.
    """
    shown = ("The card currently shows this semantic query:\n"
             f"{current.model_dump_json(indent=2)}\n\n"
             if current is not None else "")
    closing = ("Return the complete replacement query."
               if current is not None else "")

    if not clarifying:
        if current is not None:
            return (f"{shown}Change it as follows, returning the complete "
                    f"replacement query: {question}")
        return question

    said = str(clarifying.get("question") or "")
    asked = str(clarifying.get("asked") or "")

    if _answering_a_question(clarifying):
        # A clarifying question with no memory of itself is worse than not
        # asking: the card asks "oil or gas?", the person types "oil", and
        # the next request arrives as a single word attached to nothing.
        # The card carries the exchange, which keeps the existing rule that
        # card state is the context rather than a conversation.
        return (f"You asked this clarifying question: {said!r}\n"
                f"It was about this original request: {asked!r}\n\n"
                f"They have now answered: {question}\n\n"
                f"{shown}"
                f"Build the query they originally asked for, using their "
                f"answer to resolve what was ambiguous. Do not ask again. "
                f"{closing}").strip()

    return (f"You could not answer this request: {asked!r}\n"
            f"You said why: {said!r}\n\n"
            f"They have replied: {question}\n\n"
            f"{shown}"
            f"Read the reply as their response to what you said. If it "
            f"clears the obstacle, build the query. If it does not, say so "
            f"again rather than guessing. "
            f"{closing}").strip()


def deterministic_ambiguity(question: str, entity_name: str,
                            synonyms: dict[str, dict[str, list[str]]],
                            layer: Layer) -> Ambiguity | None:
    """Fires when a term in the question maps to two or more measures on the
    chosen entity. Unlike the model-reported signal this is testable and
    does not rely on the model volunteering its own uncertainty."""
    entity = layer.get(entity_name)
    if entity is None:
        return None

    text = question.lower()
    for term, per_entity in synonyms.items():
        if term not in text:
            continue
        # Match on a word boundary so 'gas' does not fire inside 'gasket'.
        if not re.search(rf"\b{re.escape(term)}\b", text):
            continue
        fields = per_entity.get(entity_name, [])
        measures = [f for f in fields if f in entity.measures]
        if len(measures) > 1:
            labels = [entity.measures[m].label for m in sorted(measures)]
            return Ambiguity(
                term=term,
                candidates=sorted(measures),
                question=f"By {term!r} do you mean {' or '.join(labels)}?",
            )
    return None


def ask(question: str, layer: Layer, client: LLMClient,
        synonyms: dict[str, dict[str, list[str]]] | None = None,
        current: SemanticQuery | None = None,
        today: date | None = None,
        clarifying: dict | None = None,
        request_id: str = "",
        stage: str = "query_step") -> AskOutcome:
    """`current` carries an existing card's query for refinement: the card's
    state is the context, which sidesteps multi-turn drift entirely.

    `today` is stated to the model rather than assumed by it. Without an
    anchor a model resolves "last year" against its training cutoff, which
    is a chart about the wrong period with nothing on it to say so.

    `clarifying` carries the card's unfinished exchange -- a question it
    asked, or a refusal it gave -- so that whatever is typed next can be
    read as a reply to it. Its `kind` decides how: see `_user_body`.
    """
    system = build_system_prompt(layer)

    # Deliberately in the question, not the system prompt. The system block
    # sits behind a cache breakpoint; a date in there would invalidate that
    # cache once a day for every user, forever. After the breakpoint it
    # costs nothing, and the layer prompt stays byte-stable.
    today = today or date.today()

    user = f"Today is {today:%d %B %Y}.\n\n{_user_body(question, current, clarifying)}"

    attempts = 0
    last_error: str | None = None
    terminal_schema_failure = False

    for attempt in range(2):
        attempts += 1
        prompt = user if last_error is None else (
            f"{user}\n\nYour previous answer was rejected: {last_error}\n"
            f"Return a corrected query using only the listed fields."
        )
        try:
            answer = client.ask(system, prompt, AskResponse)
        except LLMSchemaError as exc:
            # An answer outside the grammar is the same kind of event as one
            # that fails layer validation: retry once with the reason, then
            # refuse. Questions needing three dimensions land here, and the
            # grammar not holding them is the point.
            last_error = str(exc)
            terminal_schema_failure = True
            logger.warning(
                "query model schema validation failed",
                extra={
                    "request_id": request_id,
                    "provider": getattr(client, "provider", ""),
                    "model": getattr(client, "model", ""),
                    "stage": stage,
                    "schema": AskResponse.__name__,
                    "retry_count": attempts,
                },
                exc_info=True,
            )
            continue
        except LLMRateLimited:
            # Deliberately not turned into a refusal here, and listed before
            # LLMError because it is a subclass. A card should say "try
            # again in a moment"; a batch run pointed at a free tier should
            # wait. Only the caller knows which it is.
            raise
        except LLMError as exc:
            # Transport and credential failures already carry a sentence for
            # the reader, and no retry will fix them.
            return AskOutcome(
                refusal=str(exc), replyable=False, attempts=attempts,
            )

        terminal_schema_failure = False
        try:
            validate_query(answer.semantic_query, layer)
        except QueryValidationError as exc:
            # The confidence gate is not the model's fault and no retry will
            # fix it, so surface it immediately.
            if exc.reason == "unverified_layer":
                return AskOutcome(refusal=exc.detail, attempts=attempts)
            last_error = exc.detail
            continue

        # Deterministic guard first: it does not depend on the model
        # volunteering that it was unsure.
        answered = _answering_a_question(clarifying)
        flagged = None
        if synonyms and not answered:
            flagged = deterministic_ambiguity(
                question, answer.semantic_query.entity, synonyms, layer)
        flagged = flagged or (None if answered else answer.ambiguity)

        if flagged is not None:
            return AskOutcome(
                clarify=_ask_back(flagged, question, layer,
                                  answer.semantic_query.entity),
                attempts=attempts)

        return AskOutcome(query=answer.semantic_query,
                          chart_hint=answer.chart_hint,
                          title=answer.title or question,
                          attempts=attempts)

    # Twice-failed validation is the layer telling you something is missing,
    # not a prompt that needs another roll of the dice.
    if terminal_schema_failure:
        return AskOutcome(
            refusal=SAFE_FORMAT_REFUSAL,
            attempts=attempts,
            failure_code="model_format_invalid",
            replyable=False,
        )
    return AskOutcome(refusal=last_error, attempts=attempts)
