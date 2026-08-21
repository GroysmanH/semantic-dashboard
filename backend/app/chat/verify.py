"""Recompute every number the chat states.

The deterministic restatement exists because a second thing that can lie is
not a safeguard. Prose about numbers is precisely that second thing: a
model can produce a fluent sentence containing a figure that appears
nowhere in the data, and it will read exactly like one that does.

So the model supplies an *address* — card, field, and the keys picking out
one row — plus the operation. This module reads the value out of the rows
and redoes the arithmetic. `displayed_value` is compared, never trusted.
A claim that does not check out is removed rather than shown with a warning
next to it: a flagged number still reads as probably true.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .schema import Claim, ClaimOperand

UNVERIFIED = "I couldn’t verify that number from the visible card data."

SUFFIXES = {"k": Decimal(10) ** 3, "m": Decimal(10) ** 6,
            "b": Decimal(10) ** 9, "bn": Decimal(10) ** 9}

# A number, optionally comma-grouped, optionally signed, optionally with a
# magnitude suffix or a percent sign.
NUMBER = re.compile(
    r"(?<![\w.])[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:bn|[kmb])?%?(?![\w])"
    r"|(?<![\w.])[-+]?\d+(?:\.\d+)?(?:bn|[kmb])?%?(?![\w])",
    re.IGNORECASE,
)
# Removed before scanning prose: an ISO date is not a quantity being claimed.
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{4}-\d{2}\b|\b(19|20)\d{2}\b")

# "13.0 million" and "13.0M" are the same figure spelled two ways, and a
# model routinely writes one in the sentence and the other in the value.
# Collapsing the word form first makes the comparison about the number
# rather than about the spelling.
WORD_SCALE = re.compile(
    r"(\d)\s+(thousand|million|billion)\b", re.IGNORECASE)
WORD_SUFFIX = {"thousand": "k", "million": "m", "billion": "b"}

# Sentence boundaries need whitespace after the stop, so the decimal point
# in "25.4%" is not mistaken for the end of a sentence. Splitting on a bare
# "." chops a figure in half and leaves "4% of total production" behind.
SENTENCE = re.compile(r"(?<=[.!?])\s+")

TWO_OPERAND = {"difference", "ratio", "percentage", "percentage_change"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerifiedClaim(StrictModel):
    text: str
    displayed_value: str
    computed_value: Decimal
    source_card_ids: list[UUID] = Field(default_factory=list)


class VerificationResult(StrictModel):
    safe_say: str
    claims: list[VerifiedClaim] = Field(default_factory=list)
    withdrawn: list[str] = Field(default_factory=list)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_displayed(text: str) -> tuple[Decimal, int, Decimal, bool] | None:
    """Returns the value, the decimal places actually shown, and the
    magnitude the suffix multiplied by.

    The shown precision sets the tolerance. "13M" claims a great deal less
    than "12995827.76" does, and must be judged at what it actually says.
    """
    raw = text.strip()
    percent = raw.endswith("%")
    if percent:
        raw = raw[:-1].strip()

    scale = Decimal(1)
    lowered = raw.lower()
    for suffix in ("bn", "k", "m", "b"):
        if lowered.endswith(suffix):
            scale = SUFFIXES[suffix]
            raw = raw[: -len(suffix)]
            break

    raw = raw.replace(",", "").strip()
    try:
        magnitude = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None

    places = -magnitude.as_tuple().exponent
    return magnitude * scale, max(places, 0), scale, percent


def _resolve(operand: ClaimOperand,
             rows_by_card: dict[UUID, list[dict]]) -> Decimal | None:
    """Read the value at the addressed position.

    The index refers to the same snapshot that was put in the prompt, held
    for the whole turn, so there is nothing to match and nothing to guess
    between.
    """
    rows = rows_by_card.get(operand.card_id)
    if not rows or operand.row >= len(rows):
        return None
    row = rows[operand.row]
    if operand.field not in row:
        return None
    return _to_decimal(row[operand.field])


def _compute(operation: str, values: list[Decimal]) -> Decimal | None:
    if operation in TWO_OPERAND and len(values) != 2:
        return None
    try:
        if operation in ("exact", "rounded"):
            return values[0] if len(values) == 1 else None
        if operation == "sum":
            return sum(values, Decimal(0))
        if operation == "difference":
            return values[0] - values[1]
        if operation == "ratio":
            return values[0] / values[1]
        if operation == "percentage":
            return values[0] / values[1] * 100
        if operation == "percentage_change":
            return (values[1] - values[0]) / values[0] * 100
    except (DivisionByZero, InvalidOperation):
        return None
    return None


def _matches(shown: Decimal, places: int, scale: Decimal,
             computed: Decimal) -> bool:
    """Half a unit of the last place the figure actually shows.

    "13M" shows whole millions, so it is judged to half a million; and
    "12995827.76" shows hundredths, so it is judged to half a hundredth.
    Deliberately absolute rather than a relative window: a percentage
    tolerance on a large number waves through a discrepancy of thousands of
    barrels, which is exactly the error worth catching.
    """
    tolerance = scale * (Decimal(10) ** -places) / 2
    return abs(computed - shown) <= tolerance


def _collapse_word_scales(text: str) -> str:
    return WORD_SCALE.sub(
        lambda m: m.group(1) + WORD_SUFFIX[m.group(2).lower()], text)


def _holds(shown: Decimal, places: int, scale: Decimal, percent: bool,
           computed: Decimal) -> bool:
    """A figure written as a percentage may sit in the data as a fraction.

    A share column holds 0.2644 and a model writes "26.4%". Those are the
    same number in two conventions, and rejecting one of them withdraws a
    correct answer. Only a literal actually carrying a % sign is given the
    second reading, so this cannot quietly rescue an unrelated figure that
    happens to be a hundred times off.
    """
    if _matches(shown, places, scale, computed):
        return True
    return percent and _matches(shown, places, scale, computed * 100)


def _numbers_in(text: str) -> list[str]:
    return NUMBER.findall(_collapse_word_scales(DATE.sub(" ", text)))


def verify_turn(*, say: str, claims: list[Claim],
                rows_by_card: dict[UUID, list[dict]]) -> VerificationResult:
    verified: list[VerifiedClaim] = []
    withdrawn: list[str] = []

    for claim in claims:
        parsed = _parse_displayed(claim.displayed_value)
        found = _numbers_in(claim.text)

        # One figure per sentence, so every number a reader sees is
        # individually checkable.
        if parsed is None or len(found) != 1:
            withdrawn.append(claim.text)
            continue

        in_text = _parse_displayed(found[0])
        if in_text is None:
            withdrawn.append(claim.text)
            continue

        values = [_resolve(o, rows_by_card) for o in claim.operands]
        if any(v is None for v in values):
            withdrawn.append(claim.text)
            continue

        computed = _compute(claim.operation, values)  # type: ignore[arg-type]
        if computed is None:
            withdrawn.append(claim.text)
            continue

        # Both the figure in the sentence and the declared value are
        # checked, each at the precision it states. Requiring them to be
        # the same string instead would withdraw a correct claim whenever a
        # model writes "7.92 million" in prose and the exact figure as the
        # value — which is good prose, not a wrong answer. What the reader
        # sees is the sentence, so that is the one that must hold.
        if not all(_holds(v, p, sc, pct, computed)
                   for v, p, sc, pct in (parsed, in_text)):
            withdrawn.append(claim.text)
            continue

        verified.append(VerifiedClaim(
            text=claim.text,
            displayed_value=claim.displayed_value,
            computed_value=computed,
            source_card_ids=[o.card_id for o in claim.operands],
        ))

    kept = {c.text for c in verified}
    allowed = {n for c in verified for n in _numbers_in(c.text)}

    # Prose is held to the same rule: any figure in `say` that no verified
    # claim accounts for is a number nobody checked.
    stray = [n for n in _numbers_in(say) if n not in allowed]

    safe = say
    for text in withdrawn:
        safe = safe.replace(text, " ")

    # Drop whole sentences rather than excising figures from them: a
    # sentence with its number cut out still asserts something, and now
    # asserts it without the evidence.
    if stray:
        # Compare like with like. Stray numbers are found in collapsed form
        # ("7.92m"), so substring-matching them against the original prose
        # ("7.92 million") never fires and the unverified figure survives.
        # Re-scan each sentence through the same function instead.
        stray_set = set(stray)
        kept_sentences = [
            sentence for sentence in SENTENCE.split(safe)
            if not stray_set & set(_numbers_in(sentence))
        ]
        safe = " ".join(kept_sentences)

    safe = re.sub(r"\s+", " ", safe).strip()

    if (withdrawn or stray) and UNVERIFIED not in safe:
        safe = f"{safe} {UNVERIFIED}".strip()

    return VerificationResult(safe_say=safe, claims=verified,
                              withdrawn=withdrawn)
