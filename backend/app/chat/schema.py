"""The chat action contract.

The model decides *intent* inside a closed grammar. It never applies
effects, never writes SQL, and never says a mutation was confirmed. The
server turns an intent into an authoritative plan, and that plan is what
gets applied.

This mirrors the reasoning behind the semantic query grammar: anything
outside the vocabulary is impossible rather than merely discouraged, so the
surface a wrong answer can reach is bounded by the types.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..semantic.query import ChartHint, SemanticQuery

# The dashboard grid the compiler and the frontend already agree on.
GRID_COLS = 12

# Six is what a person reads on one screen, and it bounds a generation job
# to a predictable number of model calls.
MAX_CARDS = 6


class StrictModel(BaseModel):
    """extra="forbid" everywhere. A model that invents a field is making a
    claim about behaviour this contract does not have."""

    model_config = ConfigDict(extra="forbid")


# -- reusable values -----------------------------------------------------

class LayoutRequest(StrictModel):
    """A proposed position. The server clamps and collision-resolves it;
    these bounds only stop obvious nonsense reaching that resolver."""

    x: int = Field(ge=0, lt=GRID_COLS)
    y: int = Field(ge=0)
    w: int = Field(gt=0, le=GRID_COLS)
    h: int = Field(gt=0)

    @model_validator(mode="after")
    def _fits_the_grid(self) -> "LayoutRequest":
        if self.x + self.w > GRID_COLS:
            raise ValueError(
                f"a card at x={self.x} cannot be {self.w} columns wide "
                f"on a {GRID_COLS}-column grid")
        return self


class CardRequest(StrictModel):
    """One card to build, as a question rather than a query.

    Each request goes through the existing validated ask() path, so a
    generated card is refusable and auditable one at a time and a single bad
    card cannot poison a dashboard.
    """

    request_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=120)
    chart_hint: ChartHint | None = None
    layout: LayoutRequest | None = None


class ClaimOperand(StrictModel):
    """Where a number came from: a card, a field, and the keys that pick out
    exactly one row. The model supplies the address, never the value.

    The row is addressed by position, not by matching column values.

    Matching was tried first and failed against real models. Asked three
    ways — a required field, a schema description, and an explicit prompt
    rule — Haiku still sent an empty key set, which matches every row.
    Tightening the type to forbid that did not help either: Anthropic's
    constrained decoding does not enforce `minProperties`, so the model
    emitted `{}` regardless and the whole turn was rejected client-side.

    A position is mechanical. The rows are listed in the prompt in order,
    the server holds the same snapshot for the whole turn, and an index
    that does not exist is simply withdrawn.
    """

    card_id: UUID
    field: str = Field(
        min_length=1, max_length=120,
        description="The column holding the number, e.g. the measure column.")
    row: int = Field(
        ge=0,
        description="Which row of that card the number is read from, "
                    "counting from 0 in the order the rows are listed.")


class Claim(StrictModel):
    """One sentence containing exactly one figure, plus how to recompute it.

    Verification reads the operands out of the rows and redoes the
    arithmetic; `displayed_value` is compared, never trusted.
    """

    text: str = Field(min_length=1, max_length=400)
    displayed_value: str = Field(min_length=1, max_length=64)
    operation: Literal["exact", "rounded", "sum", "difference", "ratio",
                       "percentage", "percentage_change"]
    operands: list[ClaimOperand] = Field(min_length=1, max_length=20)


class LayoutChange(StrictModel):
    card_id: UUID
    layout: LayoutRequest


def _distinct_request_ids(cards: list[CardRequest]) -> list[CardRequest]:
    seen = {c.request_id for c in cards}
    if len(seen) != len(cards):
        raise ValueError("every card needs its own request_id: they address "
                         "placeholders while a dashboard is being built")
    return cards


CardBatch = Annotated[
    list[CardRequest],
    Field(min_length=1, max_length=MAX_CARDS),
]


# -- read-only actions ---------------------------------------------------

class AnswerAction(StrictModel):
    action: Literal["answer"] = "answer"
    say: str = Field(min_length=1, max_length=2000)
    claims: list[Claim] = Field(default_factory=list, max_length=20)


class RunQueryAction(StrictModel):
    """A read-only query answered in the transcript. It renders with the
    same trust surface as a card and can be promoted into one."""

    action: Literal["run_query"] = "run_query"
    say: str = Field(default="", max_length=2000)
    semantic_query: SemanticQuery
    chart_hint: ChartHint | None = None


class ClarifyAction(StrictModel):
    action: Literal["clarify"] = "clarify"
    question: str = Field(min_length=1, max_length=400)


class RefuseAction(StrictModel):
    """Names the wall rather than hiding it, and offers the question to the
    layer author. `missing_metric` and `request_text` power a copyable
    request without this app keeping a backlog."""

    action: Literal["refuse"] = "refuse"
    reason: str = Field(min_length=1, max_length=600)
    missing_metric: str | None = Field(default=None, max_length=120)
    request_text: str | None = Field(default=None, max_length=600)


# -- mutating actions: intent only ---------------------------------------

class NewCardsAction(StrictModel):
    action: Literal["new_cards"] = "new_cards"
    say: str = Field(default="", max_length=2000)
    cards: CardBatch

    @model_validator(mode="after")
    def _ids_are_distinct(self) -> "NewCardsAction":
        _distinct_request_ids(self.cards)
        return self


class EditCardAction(StrictModel):
    """Carries the complete replacement query, not words to re-interpret.

    The confirmation preview shows an exact deterministic diff, and that is
    only possible if the final query is already in hand: re-asking a model
    at confirmation time would mean confirming one thing and applying
    another.
    """

    action: Literal["edit_card"] = "edit_card"
    say: str = Field(default="", max_length=2000)
    card_id: UUID
    semantic_query: SemanticQuery
    chart_hint: ChartHint | None = None
    title: str | None = Field(default=None, max_length=120)


class NewDashboardAction(StrictModel):
    action: Literal["new_dashboard"] = "new_dashboard"
    say: str = Field(default="", max_length=2000)
    title: str = Field(min_length=1, max_length=120)
    cards: CardBatch

    @model_validator(mode="after")
    def _ids_are_distinct(self) -> "NewDashboardAction":
        _distinct_request_ids(self.cards)
        return self


class LayoutAction(StrictModel):
    action: Literal["layout"] = "layout"
    say: str = Field(default="", max_length=2000)
    changes: list[LayoutChange] = Field(min_length=1, max_length=24)


class RenameDashboardAction(StrictModel):
    action: Literal["rename_dashboard"] = "rename_dashboard"
    say: str = Field(default="", max_length=2000)
    board_id: UUID
    title: str = Field(min_length=1, max_length=120)


class ReorderDashboardsAction(StrictModel):
    action: Literal["reorder_dashboards"] = "reorder_dashboards"
    say: str = Field(default="", max_length=2000)
    order: list[UUID] = Field(min_length=1, max_length=64)


class DeleteCardAction(StrictModel):
    action: Literal["delete_card"] = "delete_card"
    say: str = Field(default="", max_length=2000)
    card_id: UUID


class DeleteDashboardAction(StrictModel):
    """Chat deletion is soft and reversible, and the server refuses to
    remove the last remaining dashboard."""

    action: Literal["delete_dashboard"] = "delete_dashboard"
    say: str = Field(default="", max_length=2000)
    board_id: UUID


ChatAction = Annotated[
    AnswerAction | RunQueryAction | ClarifyAction | RefuseAction
    | NewCardsAction | EditCardAction | NewDashboardAction | LayoutAction
    | RenameDashboardAction | ReorderDashboardsAction
    | DeleteCardAction | DeleteDashboardAction,
    Field(discriminator="action"),
]


class ChatModelResponse(StrictModel):
    """The complete action contract.

    This is the type the API and the generated TypeScript speak, and what a
    resolved plan is validated against. It is deliberately *not* what gets
    sent to a provider: see ChatReadOnlyResponse.
    """

    turn: ChatAction


# Read-only actions only. This is what providers are actually asked for
# while the chat cannot mutate anything.
#
# The full twelve-variant union cannot be used as a structured-output
# schema: Anthropic rejects it with "the compiled grammar is too large",
# and Gemini with a bare 400. Measured on claude-haiku-4-5, these four
# variants compile at ~7.2KB while ten variants carrying no SemanticQuery
# already fail at ~9KB, so the ceiling is the size of the compiled grammar
# rather than the number of branches.
#
# Asking with the narrow schema is also the stronger design. A model that
# cannot express a mutation cannot propose one it is not allowed to apply,
# which beats letting it propose one and refusing afterwards.
ReadOnlyAction = Annotated[
    AnswerAction | RunQueryAction | ClarifyAction | RefuseAction,
    Field(discriminator="action"),
]


class ChatReadOnlyResponse(StrictModel):
    """What providers are asked for.

    LLMClient.ask takes `type[T] where T: BaseModel`, and an Annotated union
    alias is not a model. Wrapping it keeps one contract across all four
    providers instead of forking the seam.
    """

    turn: ReadOnlyAction


# -- transport envelopes -------------------------------------------------
#
# What the browser sees. Deliberately not the storage rows: a view carries
# no basis hashes, no prompt context and no warehouse rows.

class SourceRef(StrictModel):
    """A clickable chip back to the card a figure came from."""

    card_id: UUID
    board_id: UUID
    card_title: str


class VerifiedClaimView(StrictModel):
    text: str
    displayed_value: str
    sources: list[SourceRef] = Field(default_factory=list)


class ChatMessageOut(StrictModel):
    id: UUID
    role: Literal["user", "assistant"]
    action: str
    say: str = ""
    claims: list[VerifiedClaimView] = Field(default_factory=list)
    clarify: str | None = None
    refusal: str | None = None
    missing_metric: str | None = None
    request_text: str | None = None
    # Which dashboard was in front of the user when this was said. The
    # transcript is global, so without this a reader cannot tell what "this
    # board" referred to three messages ago.
    active_board_id: UUID | None = None
    active_board_title: str = ""
    # Whether row values were in scope for this turn. Consent can change
    # between turns, and the transcript has to stay honest about it.
    data_exposed: bool = False
    created_at: str


class PlanOperation(StrictModel):
    """One line of an exact preview: what will change, stated in words the
    reader can check against the board in front of them."""

    kind: Literal["create_card", "edit_card", "move_card", "delete_card",
                  "create_dashboard", "rename_dashboard",
                  "reorder_dashboards", "delete_dashboard"]
    summary: str
    board_id: UUID | None = None
    board_title: str = ""
    card_id: UUID | None = None
    card_title: str = ""
    before: str | None = None
    after: str | None = None


class PlanCardPreview(StrictModel):
    request_id: str
    title: str
    question: str
    layout: LayoutRequest


class PendingPlanView(StrictModel):
    """A frozen plan awaiting confirmation.

    Confirmation never calls the planning model again, so what is shown here
    is exactly what will be applied.
    """

    id: UUID
    action: str
    say: str = ""
    operations: list[PlanOperation] = Field(default_factory=list)
    cards: list[PlanCardPreview] = Field(default_factory=list)
    target_board_id: UUID | None = None
    target_board_title: str = ""
    # Set when the board moved underneath the plan; confirming is refused
    # rather than silently applying to a changed dashboard.
    stale: bool = False
    created_at: str


class TransientResultView(StrictModel):
    """A read-only chat query, with the same trust surface a card carries."""

    id: UUID
    restatement: str
    semantic_query: SemanticQuery
    chart_hint: ChartHint | None = None
    vega_spec: dict | None = None
    rows: list[dict] = Field(default_factory=list)
    row_count: int = 0
    compiled_sql: str = ""
    data_max_ts: str | None = None
    fetched_at: str | None = None
    expires_at: str | None = None
    expired: bool = False


class ActionProgressView(StrictModel):
    id: UUID
    action: str
    status: Literal["pending", "running", "stopping", "stopped",
                    "done", "failed"]
    board_id: UUID | None = None
    total: int = 0
    completed: int = 0
    failed: int = 0


class ChatTurnResponse(StrictModel):
    message: ChatMessageOut
    pending_plan: PendingPlanView | None = None
    transient_result: TransientResultView | None = None


class ChatThreadView(StrictModel):
    id: UUID
    messages: list[ChatMessageOut] = Field(default_factory=list)
    pending_plan: PendingPlanView | None = None
    active_actions: list[ActionProgressView] = Field(default_factory=list)


# -- SSE events ----------------------------------------------------------
#
# Every kind gets a typed payload. A dict[str, Any] here would put the
# generation protocol outside the contract the frontend is generated from.

class PlanEventPayload(StrictModel):
    board_id: UUID
    board_title: str
    cards: list[PlanCardPreview] = Field(default_factory=list)


class ItemStartedEventPayload(StrictModel):
    request_id: str
    title: str


class CardEventPayload(StrictModel):
    request_id: str
    card_id: UUID
    board_id: UUID


class ItemFailedEventPayload(StrictModel):
    request_id: str
    reason: str
    # A refusal is a legitimate outcome, not a crash: the placeholder says
    # why rather than disappearing.
    refused: bool = False


class StoppedEventPayload(StrictModel):
    completed: int = 0
    remaining: int = 0


class DoneEventPayload(StrictModel):
    completed: int = 0
    failed: int = 0


class _Event(StrictModel):
    version: Literal[1] = 1
    id: int
    action_id: UUID


class PlanEvent(_Event):
    kind: Literal["plan"] = "plan"
    payload: PlanEventPayload


class ItemStartedEvent(_Event):
    kind: Literal["item_started"] = "item_started"
    payload: ItemStartedEventPayload


class CardEvent(_Event):
    kind: Literal["card"] = "card"
    payload: CardEventPayload


class ItemFailedEvent(_Event):
    kind: Literal["item_failed"] = "item_failed"
    payload: ItemFailedEventPayload


class StoppedEvent(_Event):
    kind: Literal["stopped"] = "stopped"
    payload: StoppedEventPayload


class DoneEvent(_Event):
    kind: Literal["done"] = "done"
    payload: DoneEventPayload


ChatEvent = Annotated[
    PlanEvent | ItemStartedEvent | CardEvent | ItemFailedEvent
    | StoppedEvent | DoneEvent,
    Field(discriminator="kind"),
]


class ChatEventEnvelope(StrictModel):
    """Wraps the union so it can be validated and serialised as a model,
    for the same reason ChatModelResponse wraps ChatAction."""

    event: ChatEvent
