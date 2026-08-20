"""Natural language -> validated semantic query.

Ambiguity gets two independent triggers. The design doc calls for
detection "by margin", but models do not emit calibrated scores and
self-reported confidence is noise, so the model-reported signal is backed
by a deterministic one that does not depend on the model being honest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, ConfigDict

from ..layer.models import Layer
from ..semantic.query import ChartHint, SemanticQuery
from ..semantic.validate import QueryValidationError, validate_query
from .client import LLMClient, LLMError, LLMRateLimited, LLMSchemaError
from .prompt import build_system_prompt


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
    attempts: int = 0


WORD = re.compile(r"[a-z_]+")


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
        today: date | None = None) -> AskOutcome:
    """`current` carries an existing card's query for refinement: the card's
    state is the context, which sidesteps multi-turn drift entirely.

    `today` is stated to the model rather than assumed by it. Without an
    anchor a model resolves "last year" against its training cutoff, which
    is a chart about the wrong period with nothing on it to say so.
    """
    system = build_system_prompt(layer)

    # Deliberately in the question, not the system prompt. The system block
    # sits behind a cache breakpoint; a date in there would invalidate that
    # cache once a day for every user, forever. After the breakpoint it
    # costs nothing, and the layer prompt stays byte-stable.
    today = today or date.today()

    body = question
    if current is not None:
        body = (f"The card currently shows this semantic query:\n"
                f"{current.model_dump_json(indent=2)}\n\n"
                f"Change it as follows, returning the complete replacement "
                f"query: {question}")
    user = f"Today is {today:%d %B %Y}.\n\n{body}"

    attempts = 0
    last_error: str | None = None

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
            return AskOutcome(refusal=str(exc), attempts=attempts)

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
        flagged = None
        if synonyms:
            flagged = deterministic_ambiguity(
                question, answer.semantic_query.entity, synonyms, layer)
        flagged = flagged or answer.ambiguity

        if flagged is not None:
            return AskOutcome(clarify=flagged.question, attempts=attempts)

        return AskOutcome(query=answer.semantic_query,
                          chart_hint=answer.chart_hint,
                          title=answer.title or question,
                          attempts=attempts)

    # Twice-failed validation is the layer telling you something is missing,
    # not a prompt that needs another roll of the dice.
    return AskOutcome(refusal=last_error, attempts=attempts)
