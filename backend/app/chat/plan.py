"""Intent in, an exact plan out -- and later, that plan applied.

Resolution takes a change the chat proposed, checks it against the
dashboards that actually exist, and freezes what would happen into a
preview a person can read line by line. Application takes the frozen
document back and carries it out. Both halves live here because they are
two readings of the same object and keeping them apart is how they drift.

Two properties are the point of the module:

*Confirmation applies what was previewed.* `apply` reads `resolved` and
never the raw action, so there is no second interpretation between the
preview and the effect. In particular no model is asked anything at
confirmation time.

*A plan knows what it was computed against.* `basis` records the revision
of every board the plan touches. If any of them moved, confirming is
refused rather than applied to a dashboard that is no longer the one on
screen.

Card questions are the exception, and deliberately so: a generated card
carries a question, not a query, and the query is written when the card is
built. That keeps one path from English to SQL. What the preview promises
for those is the title, the question and the position -- which is what it
shows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from ..deps import LAYER, SYNONYMS
from ..llm.client import LLMClient
from ..llm.query_step import ask as ask_model
from ..render import is_persistable, render
from ..semantic.diff import diff_queries
from ..semantic.query import SemanticQuery
from ..semantic.restate import restate
from ..store import cards as store
from .schema import (
    GRID_COLS,
    PlanCardPreview,
    PlanOperation,
)

# The same numbers next_slot uses. Imported rather than restated so a card
# the chat creates lands where a card the person creates would.
CARD_W, CARD_H = store.CARD_W, store.CARD_H


class PlanRefused(RuntimeError):
    """The intent cannot be turned into a plan, with a sentence saying why.

    Distinct from a validation error: this is the outcome for a proposal
    that named a card that is gone, a reorder that is not a permutation, or
    the deletion of the last dashboard. The person gets the sentence.
    """


@dataclass(frozen=True)
class Resolved:
    kind: str
    say: str
    resolved: dict[str, Any]
    basis: dict[str, int]
    operations: list[PlanOperation] = field(default_factory=list)
    cards: list[PlanCardPreview] = field(default_factory=list)
    target_board_id: uuid.UUID | None = None
    target_board_title: str = ""

    def stored(self, *, say: str = "") -> dict[str, Any]:
        """One JSON document holding both what will be applied and what was
        shown.

        They are stored together on purpose. A preview kept apart from the
        effect is a preview that can drift from it; keeping them in one row
        means the sentence a person read and the change they authorised
        cannot come from different decisions.
        """
        return {
            **self.resolved,
            "say": say or self.say,
            "operations": [o.model_dump(mode="json")
                           for o in self.operations],
        }


# -- placement -----------------------------------------------------------

def _overlaps(a: dict, b: dict) -> bool:
    return not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
                or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])


def _clamp(layout: dict) -> dict:
    w = max(1, min(int(layout.get("w", CARD_W)), GRID_COLS))
    h = max(1, int(layout.get("h", CARD_H)))
    x = max(0, min(int(layout.get("x", 0)), GRID_COLS - w))
    y = max(0, int(layout.get("y", 0)))
    return {"x": x, "y": y, "w": w, "h": h}


def _sequence(index: int) -> dict:
    """The nth slot in the same left-to-right, top-to-bottom order
    `store.next_slot` walks."""
    per_row = GRID_COLS // CARD_W
    return {"x": (index % per_row) * CARD_W,
            "y": (index // per_row) * CARD_H,
            "w": CARD_W, "h": CARD_H}


def _first_free(occupied: list[dict]) -> dict:
    index = 0
    while True:
        slot = _sequence(index)
        if not any(_overlaps(slot, other) for other in occupied):
            return slot
        index += 1


def place(requests, existing: list[dict]) -> list[dict]:
    """Where each proposed card goes.

    A layout the model asked for is honoured when it fits and does not land
    on anything; otherwise the card takes the next free slot. Placement is
    the application's decision either way -- a model that could put a card
    on top of another one would be able to hide the board.
    """
    occupied = [_clamp(layout) for layout in existing]
    placed: list[dict] = []
    for request in requests:
        wanted = (_clamp(request.layout.model_dump())
                  if request.layout is not None else None)
        slot = (wanted if wanted and not any(_overlaps(wanted, o)
                                             for o in occupied)
                else _first_free(occupied))
        occupied.append(slot)
        placed.append(slot)
    return placed


def _where(layout: dict) -> str:
    return (f"column {layout['x'] + 1}, row {layout['y'] + 1}, "
            f"{layout['w']}x{layout['h']}")


# -- resolution ----------------------------------------------------------

def _card_previews(requests, existing: list[dict]) -> list[PlanCardPreview]:
    slots = place(requests, existing)
    return [
        PlanCardPreview(request_id=r.request_id, title=r.title,
                        question=r.question, layout=slot)
        for r, slot in zip(requests, slots)
    ]


def _new_cards(action, *, board: dict, cards: list[dict]) -> Resolved:
    previews = _card_previews(action.cards,
                              [c.get("layout") or {} for c in cards])
    return Resolved(
        kind="new_cards", say=action.say,
        resolved={
            "kind": "new_cards",
            "board_id": str(board["id"]),
            "board_title": board["title"],
            "cards": [
                {**p.model_dump(mode="json"),
                 "chart_hint": r.chart_hint}
                for p, r in zip(previews, action.cards)
            ],
        },
        basis={},
        operations=[
            PlanOperation(kind="create_card",
                          summary=f"Add “{p.title}” to {board['title']} "
                                  f"at {_where(p.layout.model_dump())}",
                          board_id=board["id"], board_title=board["title"],
                          card_title=p.title, after=p.question)
            for p in previews
        ],
        cards=previews,
        target_board_id=board["id"], target_board_title=board["title"],
    )


def _new_dashboard(action, *, board: dict) -> Resolved:
    # A new dashboard starts empty, so placement is the plain sequence.
    previews = _card_previews(action.cards, [])
    return Resolved(
        kind="new_dashboard", say=action.say,
        resolved={
            "kind": "new_dashboard",
            "board_title": action.title,
            "cards": [
                {**p.model_dump(mode="json"), "chart_hint": r.chart_hint}
                for p, r in zip(previews, action.cards)
            ],
        },
        basis={},
        operations=[
            PlanOperation(kind="create_dashboard",
                          summary=f"Create the dashboard “{action.title}”",
                          board_title=action.title, after=action.title),
            *[
                PlanOperation(kind="create_card",
                              summary=f"Add “{p.title}” at "
                                      f"{_where(p.layout.model_dump())}",
                              board_title=action.title, card_title=p.title,
                              after=p.question)
                for p in previews
            ],
        ],
        cards=previews,
        # No board id yet: it does not exist until this is confirmed.
        target_board_id=None, target_board_title=action.title,
    )


def _edit_card(intent, *, boards: list[dict], client: LLMClient) -> Resolved:
    """The one resolution that calls a model, and it calls the same one the
    card's own edit box calls.

    The replacement query is written here, before confirmation, because the
    preview is a diff and a diff needs both sides. Re-asking at confirmation
    time would mean showing one change and applying another.
    """
    card = store.get_card(intent.card_id)
    if card is None:
        raise PlanRefused("That card is not on any dashboard any more.")

    board = next((b for b in boards if b["id"] == card["board_id"]), None)
    if board is None:
        raise PlanRefused("That card's dashboard is not available.")
    if not card.get("semantic_query"):
        raise PlanRefused("That card has no query to change yet. Ask it a "
                          "question first, then refine it.")

    current = SemanticQuery.model_validate(card["semantic_query"])
    outcome = ask_model(intent.instruction, LAYER, client, synonyms=SYNONYMS,
                        current=current)
    if outcome.refusal:
        raise PlanRefused(outcome.refusal)
    if outcome.clarify:
        raise PlanRefused(outcome.clarify)

    entity = LAYER.get(outcome.query.entity)
    changed = diff_queries(current, outcome.query, entity) if entity else []
    if not changed:
        raise PlanRefused("That would leave the card exactly as it is.")

    return Resolved(
        kind="edit_card", say=intent.instruction,
        resolved={
            "kind": "edit_card",
            "board_id": str(board["id"]),
            "board_title": board["title"],
            "card_id": str(card["id"]),
            "card_title": card.get("title") or "",
            "instruction": intent.instruction,
            "semantic_query": outcome.query.model_dump(mode="json"),
            "chart_hint": outcome.chart_hint,
        },
        basis={},
        operations=[
            PlanOperation(
                kind="edit_card",
                summary=f"Change “{card.get('title') or 'this card'}”: "
                        + "; ".join(changed),
                board_id=board["id"], board_title=board["title"],
                card_id=card["id"], card_title=card.get("title") or "",
                before=restate(current, entity) if entity else None,
                after=restate(outcome.query, entity) if entity else None,
            )
        ],
        target_board_id=board["id"], target_board_title=board["title"],
    )


def _layout(action, *, board: dict, cards: list[dict]) -> Resolved:
    by_id = {str(c["id"]): c for c in cards}
    layouts: dict[str, dict] = {}
    operations: list[PlanOperation] = []

    for change in action.changes:
        card = by_id.get(str(change.card_id))
        if card is None:
            raise PlanRefused("One of those cards is not on this dashboard.")
        before = _clamp(card.get("layout") or {})
        after = _clamp(change.layout.model_dump())
        if before == after:
            continue
        layouts[str(change.card_id)] = after
        operations.append(PlanOperation(
            kind="move_card",
            summary=f"Move “{card.get('title') or 'a card'}” to "
                    f"{_where(after)}",
            board_id=board["id"], board_title=board["title"],
            card_id=card["id"], card_title=card.get("title") or "",
            before=_where(before), after=_where(after),
        ))

    if not layouts:
        raise PlanRefused("Those cards are already where that would put them.")

    return Resolved(
        kind="layout", say=action.say,
        resolved={"kind": "layout", "board_id": str(board["id"]),
                  "board_title": board["title"], "layouts": layouts},
        basis={}, operations=operations,
        target_board_id=board["id"], target_board_title=board["title"],
    )


def _rename_dashboard(action, *, boards: list[dict]) -> Resolved:
    board = next((b for b in boards if b["id"] == action.board_id), None)
    if board is None:
        raise PlanRefused("There is no dashboard with that name any more.")
    if board["title"] == action.title:
        raise PlanRefused(f"That dashboard is already called "
                          f"“{action.title}”.")
    return Resolved(
        kind="rename_dashboard", say=action.say,
        resolved={"kind": "rename_dashboard", "board_id": str(board["id"]),
                  "board_title": board["title"], "title": action.title},
        basis={},
        operations=[PlanOperation(
            kind="rename_dashboard",
            summary=f"Rename “{board['title']}” to “{action.title}”",
            board_id=board["id"], board_title=board["title"],
            before=board["title"], after=action.title)],
        target_board_id=board["id"], target_board_title=board["title"],
    )


def _reorder_dashboards(action, *, boards: list[dict]) -> Resolved:
    visible = [b["id"] for b in boards]
    wanted = list(action.order)
    if len(set(wanted)) != len(wanted) or set(wanted) != set(visible):
        # The store raises on this too. Catching it here turns a 500 into a
        # sentence, and names every dashboard so the answer is checkable.
        raise PlanRefused(
            "A reorder has to list every dashboard exactly once: "
            + ", ".join(f"“{b['title']}”" for b in boards) + ".")
    if wanted == visible:
        raise PlanRefused("The tabs are already in that order.")

    titles = {b["id"]: b["title"] for b in boards}
    return Resolved(
        kind="reorder_dashboards", say=action.say,
        resolved={"kind": "reorder_dashboards",
                  "order": [str(b) for b in wanted]},
        basis={},
        operations=[PlanOperation(
            kind="reorder_dashboards",
            summary="Reorder the tabs",
            before=" → ".join(titles[b] for b in visible),
            after=" → ".join(titles[b] for b in wanted))],
    )


def _delete_card(action, *, boards: list[dict]) -> Resolved:
    card = store.get_card(action.card_id)
    if card is None:
        raise PlanRefused("That card has already gone.")
    board = next((b for b in boards if b["id"] == card["board_id"]), None)
    if board is None:
        raise PlanRefused("That card's dashboard is not available.")
    return Resolved(
        kind="delete_card", say=action.say,
        resolved={"kind": "delete_card", "board_id": str(board["id"]),
                  "board_title": board["title"],
                  "card_id": str(card["id"]),
                  "card_title": card.get("title") or ""},
        basis={},
        operations=[PlanOperation(
            kind="delete_card",
            summary=f"Remove “{card.get('title') or 'this card'}” from "
                    f"{board['title']}",
            board_id=board["id"], board_title=board["title"],
            card_id=card["id"], card_title=card.get("title") or "",
            before=card.get("title") or "")],
        target_board_id=board["id"], target_board_title=board["title"],
    )


def _delete_dashboard(action, *, boards: list[dict]) -> Resolved:
    board = next((b for b in boards if b["id"] == action.board_id), None)
    if board is None:
        raise PlanRefused("That dashboard has already gone.")
    if len(boards) <= 1:
        raise PlanRefused("That is the only dashboard left, so removing it "
                          "would leave nothing to look at.")
    count = len(store.list_cards(board["id"]))
    return Resolved(
        kind="delete_dashboard", say=action.say,
        resolved={"kind": "delete_dashboard", "board_id": str(board["id"]),
                  "board_title": board["title"]},
        basis={},
        operations=[PlanOperation(
            kind="delete_dashboard",
            summary=f"Remove the dashboard “{board['title']}” and its "
                    f"{count} card{'' if count == 1 else 's'}",
            board_id=board["id"], board_title=board["title"],
            before=board["title"])],
        target_board_id=board["id"], target_board_title=board["title"],
    )


def resolve(kind: str, detail, *, board: dict, boards: list[dict],
            cards: list[dict], client: LLMClient) -> Resolved:
    """One mutating intent, checked against what exists, frozen."""
    if kind == "new_cards":
        out = _new_cards(detail, board=board, cards=cards)
    elif kind == "new_dashboard":
        out = _new_dashboard(detail, board=board)
    elif kind == "edit_card":
        out = _edit_card(detail, boards=boards, client=client)
    elif kind == "layout":
        out = _layout(detail, board=board, cards=cards)
    elif kind == "rename_dashboard":
        out = _rename_dashboard(detail, boards=boards)
    elif kind == "reorder_dashboards":
        out = _reorder_dashboards(detail, boards=boards)
    elif kind == "delete_card":
        out = _delete_card(detail, boards=boards)
    elif kind == "delete_dashboard":
        out = _delete_dashboard(detail, boards=boards)
    else:
        raise PlanRefused(f"I do not know how to plan a {kind}.")

    # Every board the plan reads or writes, plus the one it was proposed
    # from. Anything that moves under the preview must invalidate it.
    touched = {str(board["id"])}
    for operation in out.operations:
        if operation.board_id:
            touched.add(str(operation.board_id))
    if out.kind == "reorder_dashboards":
        touched.update(str(b["id"]) for b in boards)

    basis = store.board_basis([uuid.UUID(b) for b in sorted(touched)])
    return replace(out, basis=basis)


def is_stale(basis: dict[str, Any]) -> bool:
    """Has anything the preview was computed from moved since?

    A board that has been deleted counts as moved: its revision is simply
    absent from the fresh reading, which is not the number recorded.
    """
    if not basis:
        return False
    ids = [uuid.UUID(str(b)) for b in basis]
    current = store.board_basis(ids)
    return any(current.get(str(b)) != int(rev) for b, rev in basis.items())


# -- application ---------------------------------------------------------

@dataclass(frozen=True)
class Applied:
    """What actually happened, for the transcript and for the browser."""

    summary: str
    board_id: uuid.UUID | None = None
    created_card_ids: list[uuid.UUID] = field(default_factory=list)
    # Set when cards still have to be built. Their questions are answered
    # one model call at a time, after this returns.
    action_id: uuid.UUID | None = None


def apply_immediate(resolved: dict) -> Applied:
    """Everything that does not need a model.

    Read from `resolved`, never from the action the chat proposed, so the
    effect cannot differ from the preview that was confirmed.
    """
    kind = resolved["kind"]

    if kind == "layout":
        board_id = uuid.UUID(resolved["board_id"])
        store.save_layouts(board_id, resolved["layouts"])
        moved = len(resolved["layouts"])
        return Applied(summary=f"Moved {moved} card"
                               f"{'' if moved == 1 else 's'}.",
                       board_id=board_id)

    if kind == "rename_dashboard":
        board_id = uuid.UUID(resolved["board_id"])
        if store.update_board(board_id, title=resolved["title"]) is None:
            raise PlanRefused("That dashboard has gone.")
        return Applied(summary=f"Renamed to “{resolved['title']}”.",
                       board_id=board_id)

    if kind == "reorder_dashboards":
        order = [uuid.UUID(b) for b in resolved["order"]]
        try:
            store.reorder_boards(order)
        except store.BoardOrderError as exc:
            raise PlanRefused(str(exc)) from exc
        return Applied(summary="Reordered the tabs.")

    if kind == "delete_card":
        card_id = uuid.UUID(resolved["card_id"])
        if store.soft_delete_card(card_id) is None:
            raise PlanRefused("That card has already gone.")
        return Applied(summary=f"Removed “{resolved['card_title']}”.",
                       board_id=uuid.UUID(resolved["board_id"]))

    if kind == "delete_dashboard":
        board_id = uuid.UUID(resolved["board_id"])
        try:
            removed = store.soft_delete_board(board_id)
        except store.LastVisibleBoardError as exc:
            raise PlanRefused("That is the only dashboard left.") from exc
        if removed is None:
            raise PlanRefused("That dashboard has already gone.")
        return Applied(summary=f"Removed “{resolved['board_title']}”.")

    if kind == "edit_card":
        card_id = uuid.UUID(resolved["card_id"])
        query = SemanticQuery.model_validate(resolved["semantic_query"])
        r = render(query, LAYER, chart_hint=resolved.get("chart_hint"))
        if not is_persistable(r):
            raise PlanRefused(r.error or "That change could not be run.")
        card = store.get_card(card_id)
        if card is None:
            raise PlanRefused("That card has gone.")
        store.update_card(
            card_id,
            semantic_query=r.semantic_query.model_dump(mode="json"),
            chart_hint=r.chart_hint, vega_spec=r.vega_spec, state=r.state,
            cache=r.cache, prompt=resolved["instruction"],
            # The card's own one-step undo, filled the same way the edit box
            # fills it, so Undo on the card still works after a chat edit.
            previous={"semantic_query": card["semantic_query"],
                      "chart_hint": card["chart_hint"],
                      "vega_spec": card["vega_spec"]},
        )
        return Applied(summary=f"Updated “{resolved['card_title']}”.",
                       board_id=uuid.UUID(resolved["board_id"]))

    raise PlanRefused(f"I do not know how to apply a {kind}.")


def create_placeholders(resolved: dict) -> tuple[uuid.UUID, list[dict]]:
    """Make the dashboard and the empty cards, before any question is asked.

    They appear immediately and fill in one at a time. Creating them up
    front is what keeps the grid still while it happens: a card that
    arrived only once its query succeeded would push the others around.
    """
    if resolved["kind"] == "new_dashboard":
        board = store.create_board(resolved["board_title"])
    else:
        board = store.get_board(uuid.UUID(resolved["board_id"]))
        if board is None:
            raise PlanRefused("That dashboard has gone.")

    placed = []
    for request in resolved["cards"]:
        card = store.create_card(board["id"], layout=request["layout"])
        if card is None:
            raise PlanRefused("That dashboard has gone.")
        # The title is set now so the empty card says what it is about to
        # be rather than sitting blank with no explanation.
        store.update_card(card["id"], title=request["title"],
                          prompt=request["question"])
        placed.append({**request, "card_id": card["id"]})
    return board["id"], placed


def build_card(card_id: uuid.UUID, question: str, *, chart_hint: str | None,
               client: LLMClient) -> str | None:
    """One generated card, through the path every other card takes.

    Returns None on success, or a sentence saying why this one card could
    not be built. A refusal is a legitimate outcome for a single card and
    must not take the rest of the dashboard down with it.
    """
    outcome = ask_model(question, LAYER, client, synonyms=SYNONYMS)
    if outcome.refusal:
        return outcome.refusal
    if outcome.clarify:
        # Nobody is watching this card to answer, so an unanswerable
        # question is reported as one rather than left hanging.
        return outcome.clarify

    r = render(outcome.query, LAYER,
               chart_hint=outcome.chart_hint or chart_hint)
    if not is_persistable(r):
        return r.error or "That question could not be run."

    card = store.get_card(card_id)
    if card is None:
        return "That card was removed while it was being built."

    store.update_card(
        card_id,
        semantic_query=r.semantic_query.model_dump(mode="json"),
        chart_hint=r.chart_hint, vega_spec=r.vega_spec, state=r.state,
        cache=r.cache, prompt=question,
        title=card.get("title") or outcome.title,
    )
    return None
