"""What the chat model is allowed to see, assembled deterministically.

Two gates guard row values: a server setting and a per-browser consent.
`share_rows` is the resolved answer to both. With it off this builds a
structural picture — titles, restatements, row counts, chart types — and no
value ever appears. With it on, the rows a card is already showing on
screen go in, bounded so one enormous card cannot blow the context window
or the budget.

Everything here is deterministic. The context is not a place for a model to
be creative about what it saw, and byte-stability is what lets the prompt
cache work at all.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# Beyond this many rows a card contributes a summary instead. It is a crash
# guard, not a policy: compile.py permits 10,000 rows and a board of those
# would exceed a small model's context outright.
DEFAULT_MAX_ROWS = 2_000
DEFAULT_MAX_CHARS = 60_000
DEFAULT_HISTORY_TURNS = 12

TOP_N = 5
WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ContextLimits:
    max_rows: int = DEFAULT_MAX_ROWS
    max_chars: int = DEFAULT_MAX_CHARS
    history_turns: int = DEFAULT_HISTORY_TURNS


@dataclass(frozen=True)
class BuiltContext:
    text: str
    data_exposed: bool
    # Cards whose complete rows are in the context, in the order they were
    # admitted. Claim verification resolves operands against these.
    exact_card_ids: tuple[str, ...]
    notices: tuple[str, ...]


def _dump(value: Any) -> str:
    """Sorted keys and no incidental whitespace, so the same data always
    serialises to the same bytes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      default=str)


def _tokens(text: str) -> set[str]:
    return set(WORD.findall(text.lower()))


def _numeric(value: Any) -> Decimal | None:
    """Nulls, booleans, text and non-finite floats are not quantities.

    NaN in particular must never reach a total: it would poison the sum and
    then be stated to the model as a fact.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _column_kinds(rows: list[dict]) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for row in rows:
        for key, value in row.items():
            if key in kinds:
                continue
            kinds[key] = "number" if _numeric(value) is not None else "text"
    return kinds


def _summarise(rows: list[dict], *, share_rows: bool) -> dict[str, Any]:
    """A card too large to include in full still contributes its shape.

    With sharing off that is the shape alone. With it on, the statistics a
    person could read off the chart anyway are added — which is the point of
    the summary: an answer stays possible without the whole table.
    """
    kinds = _column_kinds(rows)
    summary: dict[str, Any] = {"rows": len(rows), "columns": kinds}
    if not share_rows:
        return summary

    stats: dict[str, Any] = {}
    for column, kind in kinds.items():
        if kind != "number":
            continue
        values = [v for v in (_numeric(r.get(column)) for r in rows)
                  if v is not None]
        if not values:
            continue
        stats[column] = {
            "min": str(min(values)),
            "max": str(max(values)),
            "total": str(sum(values)),
        }
    if stats:
        summary["statistics"] = stats

    lead = next((c for c, k in kinds.items() if k == "number"), None)
    if lead is not None:
        ranked = sorted(
            (r for r in rows if _numeric(r.get(lead)) is not None),
            key=lambda r: _numeric(r.get(lead)),
            reverse=True,
        )
        summary[f"top_{TOP_N}_by_{lead}"] = ranked[:TOP_N]
    return summary


def _rank(card: dict, *, question_tokens: set[str],
          selected_card_id: str | None) -> tuple:
    """Which cards get their complete rows first.

    Explicitly named beats selected beats merely present, and ties break on
    where the card sits on the grid so the order never depends on dict
    iteration.
    """
    title_tokens = _tokens(card.get("title") or "")
    named = bool(title_tokens) and title_tokens <= question_tokens
    overlap = len(title_tokens & question_tokens)
    layout = card.get("layout") or {}
    return (
        0 if named else 1,
        0 if card["id"] == selected_card_id else 1,
        -overlap,
        layout.get("y", 0),
        layout.get("x", 0),
        str(card["id"]),
    )


# Where each kind of turn keeps its words. Getting this wrong is silent:
# the line renders empty and the conversation appears to have no memory.
SAID = {
    "ask": "say",
    "answer": "say",
    "run_query": "say",
    "clarify": "clarify",
    "refuse": "refusal",
}


def _said(body: dict) -> str:
    """What a stored turn actually said.

    A clarifying question was the case that mattered: it is stored under
    `clarify`, and reading only `say` dropped it from the history entirely.
    The model then asked "oil or gas?", was answered "oil", and had no
    record of ever having asked -- which reads exactly like a chat with no
    memory, because that is what it was.
    """
    action = body.get("action", "")
    key = SAID.get(action)
    if key and body.get(key):
        return str(body[key])
    # Plans, applications and undos all keep their sentence in `say`; the
    # fallback is for anything added later that does the same.
    return str(body.get("say") or "")


def _card_header(card: dict) -> dict[str, Any]:
    render = card.get("render") or {}
    return {
        "card_id": str(card["id"]),
        "title": card.get("title") or "Untitled",
        "restatement": render.get("restatement") or "",
        "chart": render.get("chart_type") or "",
        "rows": render.get("row_count") or 0,
        "data_through": render.get("data_max_ts") or "",
    }


def _outstanding_question(messages: list[dict]) -> tuple[str, str] | None:
    """The clarifying question the last assistant turn asked, if it did.

    Only the last one counts. An older clarification was either answered or
    abandoned, and dragging it forward would make every later turn read as
    a reply to it.
    """
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        body = message.get("body") or {}
        if body.get("action") != "clarify":
            return None
        return str(body.get("clarify") or ""), str(body.get("asked") or "")
    return None


def build_context(
    *,
    boards: list[dict],
    active_board: dict,
    rendered_cards: list[dict],
    messages: list[dict],
    question: str,
    selected_card_id: str | None,
    share_rows: bool,
    limits: ContextLimits,
) -> BuiltContext:
    active_id = str(active_board["id"])
    question_tokens = _tokens(question)
    notices: list[str] = []

    lines: list[str] = []
    lines.append("# dashboards")
    for board in sorted(boards, key=lambda b: (b.get("position", 0),
                                               str(b["id"]))):
        mark = " (active)" if str(board["id"]) == active_id else ""
        lines.append(f"- {board['title']}{mark} [{board['id']}]")

    # Only the active dashboard is described in detail. Another board's
    # contents are not on screen, so putting them in scope would share data
    # the user is not looking at.
    on_active = [c for c in rendered_cards
                 if str(c.get("board_id")) == active_id
                 and (c.get("render") or {}).get("state") == "ready"]
    ordered = sorted(on_active, key=lambda c: _rank(
        c, question_tokens=question_tokens, selected_card_id=selected_card_id))

    lines.append("")
    lines.append(f"# cards on {active_board['title']}")

    exact: list[str] = []
    if not on_active:
        lines.append("(none)")

    budget_rows = limits.max_rows
    for card in ordered:
        header = _card_header(card)
        rows = list((card.get("render") or {}).get("rows") or [])
        block = [f"- {_dump(header)}"]

        if not share_rows:
            block.append(f"  shape: {_dump(_summarise(rows, share_rows=False))}")
        elif len(rows) <= budget_rows:
            block.append(f"  rows: {_dump(rows)}")
            budget_rows -= len(rows)
            exact.append(str(card["id"]))
        else:
            # Say so, in the transcript as well as here. An answer built on
            # a summary rather than the rows must not read as if it were
            # built on the rows.
            notice = (f"{header['title']} has {len(rows)} rows; "
                      f"a summary was read instead of the rows")
            notices.append(notice)
            block.append(f"  summary: {_dump(_summarise(rows, share_rows=True))}")
            block.append(f"  note: {notice}")

        candidate = lines + block
        if len("\n".join(candidate)) > limits.max_chars:
            notice = (f"{header['title']} was reduced to its shape because "
                      f"the context limit was reached")
            notices.append(notice)
            fallback = [f"- {_dump(header)}",
                        f"  shape: {_dump(_summarise(rows, share_rows=False))}",
                        f"  note: {notice}"]
            if len("\n".join(lines + fallback)) > limits.max_chars:
                break
            lines.extend(fallback)
            if str(card["id"]) in exact:
                exact.remove(str(card["id"]))
            continue
        lines.extend(block)

    eligible = [m for m in messages
                if share_rows or not m.get("data_exposed")]
    recent = eligible[-limits.history_turns:] if limits.history_turns else []
    if recent:
        lines.append("")
        lines.append("# earlier in this conversation")
        for m in recent:
            body = m.get("body") or {}
            said = _said(body)
            if not said:
                continue
            where = m.get("active_board_title") or ""
            # The kind is stated, not just the words. "I asked a clarifying
            # question" and "I answered" are different things to have done,
            # and a bare sentence does not distinguish them.
            action = body.get("action", "")
            kind = f" ({action})" if action not in ("ask", "answer") else ""
            lines.append(f"- {m.get('role')}{kind} on {where}: {said}")

        # An outstanding clarifying question, stated as one rather than left
        # to be inferred from the list above. This is the chat's version of
        # the card's pending_clarification, and it exists for the same
        # reason: a one-word answer needs something to attach to.
        outstanding = _outstanding_question(eligible)
        if outstanding is not None:
            question, asked = outstanding
            lines.append("")
            lines.append("# a question of yours is outstanding")
            lines.append(f"- you asked: {question}")
            if asked:
                lines.append(f"- it was about: {asked}")
            lines.append("- the message below is their answer to it. Carry "
                         "out what they originally asked, using their answer "
                         "to settle what was ambiguous. Do not ask again.")

    if notices:
        lines.append("")
        lines.append("# limits reached")
        for notice in notices:
            lines.append(f"- {notice}")

    text = "\n".join(lines)
    if len(text) > limits.max_chars:
        text = text[:limits.max_chars]

    return BuiltContext(
        text=text,
        data_exposed=bool(share_rows and exact),
        exact_card_ids=tuple(exact),
        notices=tuple(notices),
    )
