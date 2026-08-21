"""Resolution and application.

The property under test throughout is that a plan is a promise: what the
preview says is what the confirmation does, and anything that cannot be
promised is refused with a sentence rather than half-applied.
"""

import uuid

import pytest

from app.chat import plan as planner
from app.chat.plan import PlanRefused
from app.chat.schema import (
    CardRequest,
    DeleteCardAction,
    DeleteDashboardAction,
    EditCardIntent,
    LayoutAction,
    NewCardsAction,
    NewDashboardAction,
    RenameDashboardAction,
    ReorderDashboardsAction,
)
from app.llm.query_step import AskResponse
from app.render import render
from app.semantic.query import SemanticQuery
from app.store import cards as store

BY_REGION = {"entity": "production", "measures": ["oil"],
             "dimensions": [{"field": "region"}]}
BY_MONTH = {"entity": "production", "measures": ["oil"],
            "dimensions": [{"field": "reading_date", "grain": "month"}]}


class FakeAsk:
    """Stands in for the query step, which is the only model call the
    planner makes."""

    provider = "gemini"
    model = "fake-1"

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.asked: list[str] = []

    def ask(self, system, user, schema):
        self.asked.append(user)
        nxt = self.payloads.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return AskResponse.model_validate(nxt)


def answer(query=None, title="Oil by region"):
    return {"semantic_query": query or BY_REGION, "title": title}


@pytest.fixture
def board():
    return store.create_board("Operations")


@pytest.fixture
def boards(board):
    return store.list_boards()


def a_card(board, query=BY_REGION, title="Oil by region"):
    """A card in the state a real one is in after a question: rendered,
    cached, and carrying its query."""
    card = store.create_card(board["id"])
    r = render(SemanticQuery.model_validate(query), _layer())
    store.update_card(card["id"], title=title, state=r.state,
                      semantic_query=r.semantic_query.model_dump(mode="json"),
                      chart_hint=r.chart_hint, vega_spec=r.vega_spec,
                      cache=r.cache)
    return store.get_card(card["id"])


def _layer():
    from app.deps import LAYER
    return LAYER


def cards_of(board):
    return store.list_cards(board["id"])


# -- placement -----------------------------------------------------------

def test_generated_cards_take_the_slots_a_person_would_get():
    """A card the chat creates has to land where a card the person creates
    would, or the two ways of adding one produce two different boards."""
    requests = [CardRequest(request_id=str(i), question="q", title="t")
                for i in range(3)]
    assert planner.place(requests, []) == [
        {"x": 0, "y": 0, "w": 6, "h": 10},
        {"x": 6, "y": 0, "w": 6, "h": 10},
        {"x": 0, "y": 10, "w": 6, "h": 10},
    ]


def test_a_card_is_placed_after_what_is_already_on_the_board():
    existing = [{"x": 0, "y": 0, "w": 6, "h": 10}]
    requests = [CardRequest(request_id="r1", question="q", title="t")]
    assert planner.place(requests, existing) == [
        {"x": 6, "y": 0, "w": 6, "h": 10}]


def test_a_requested_layout_is_honoured_when_it_fits():
    requests = [CardRequest(request_id="r1", question="q", title="t",
                            layout={"x": 0, "y": 0, "w": 12, "h": 8})]
    assert planner.place(requests, []) == [
        {"x": 0, "y": 0, "w": 12, "h": 8}]


def test_a_requested_layout_that_lands_on_a_card_is_overruled():
    """A model that could put one card on top of another could hide the
    board, so placement stays the application's decision."""
    existing = [{"x": 0, "y": 0, "w": 6, "h": 10}]
    requests = [CardRequest(request_id="r1", question="q", title="t",
                            layout={"x": 0, "y": 0, "w": 6, "h": 10})]
    assert planner.place(requests, existing) == [
        {"x": 6, "y": 0, "w": 6, "h": 10}]


# -- new cards -----------------------------------------------------------

def test_resolving_new_cards_creates_nothing(board, boards):
    before = len(cards_of(board))
    action = NewCardsAction(say="Adding one.", cards=[
        CardRequest(request_id="r1", question="oil by region",
                    title="Oil by region")])

    out = planner.resolve("new_cards", action, board=board, boards=boards,
                          cards=cards_of(board), client=FakeAsk())

    assert len(cards_of(board)) == before
    assert [c.title for c in out.cards] == ["Oil by region"]
    assert out.operations[0].kind == "create_card"
    assert "Oil by region" in out.operations[0].summary


def test_a_new_dashboard_has_no_board_id_until_it_exists(board, boards):
    action = NewDashboardAction(say="Building it.", title="Wells", cards=[
        CardRequest(request_id="r1", question="oil by region", title="Oil")])

    out = planner.resolve("new_dashboard", action, board=board, boards=boards,
                          cards=[], client=FakeAsk())

    assert out.target_board_id is None
    assert out.target_board_title == "Wells"
    assert [o.kind for o in out.operations] == ["create_dashboard",
                                                "create_card"]


# -- editing -------------------------------------------------------------

def test_an_edit_freezes_the_replacement_query_before_confirmation(board,
                                                                   boards):
    """The preview is a diff, and a diff needs both sides. Re-asking at
    confirmation time would mean showing one change and applying another."""
    card = a_card(board)
    client = FakeAsk(answer(BY_MONTH))

    out = planner.resolve("edit_card",
                          EditCardIntent(card_id=card["id"],
                                         instruction="by month instead"),
                          board=board, boards=boards, cards=cards_of(board),
                          client=client)

    assert out.resolved["semantic_query"]["dimensions"][0]["field"] \
        == "reading_date"
    assert out.operations[0].before != out.operations[0].after
    assert store.get_card(card["id"])["semantic_query"]["dimensions"][0][
        "field"] == "region", "the card must not have moved yet"


def test_an_edit_that_changes_nothing_is_refused(board, boards):
    card = a_card(board)
    client = FakeAsk(answer(BY_REGION))

    with pytest.raises(PlanRefused, match="exactly as it is"):
        planner.resolve("edit_card",
                        EditCardIntent(card_id=card["id"],
                                       instruction="same thing"),
                        board=board, boards=boards, cards=cards_of(board),
                        client=client)


def test_an_edit_to_a_card_that_is_gone_is_refused(board, boards):
    with pytest.raises(PlanRefused, match="not on any dashboard"):
        planner.resolve("edit_card",
                        EditCardIntent(card_id=uuid.uuid4(),
                                       instruction="anything"),
                        board=board, boards=boards, cards=[],
                        client=FakeAsk())


def test_an_edit_to_a_blank_card_says_to_ask_it_something_first(board, boards):
    card = store.create_card(board["id"])

    with pytest.raises(PlanRefused, match="no query to change"):
        planner.resolve("edit_card",
                        EditCardIntent(card_id=card["id"],
                                       instruction="by month"),
                        board=board, boards=boards, cards=cards_of(board),
                        client=FakeAsk())


# -- layout, names and order ---------------------------------------------

def test_a_layout_change_that_moves_nothing_is_refused(board, boards):
    card = a_card(board)
    layout = card["layout"]

    with pytest.raises(PlanRefused, match="already where"):
        planner.resolve("layout", LayoutAction(changes=[{
            "card_id": str(card["id"]), "layout": layout}]),
            board=board, boards=boards, cards=cards_of(board),
            client=FakeAsk())


def test_a_layout_change_reports_both_sides(board, boards):
    card = a_card(board)

    out = planner.resolve("layout", LayoutAction(changes=[{
        "card_id": str(card["id"]),
        "layout": {"x": 0, "y": 0, "w": 12, "h": 10}}]),
        board=board, boards=boards, cards=cards_of(board), client=FakeAsk())

    assert out.operations[0].kind == "move_card"
    assert out.operations[0].before != out.operations[0].after
    assert out.resolved["layouts"][str(card["id"])]["w"] == 12


def test_renaming_to_the_current_name_is_refused(board, boards):
    with pytest.raises(PlanRefused, match="already called"):
        planner.resolve("rename_dashboard",
                        RenameDashboardAction(board_id=board["id"],
                                              title="Operations"),
                        board=board, boards=boards, cards=[],
                        client=FakeAsk())


def test_a_reorder_that_is_not_a_permutation_names_every_dashboard(board,
                                                                   boards):
    with pytest.raises(PlanRefused, match="Operations"):
        planner.resolve("reorder_dashboards",
                        ReorderDashboardsAction(order=[uuid.uuid4()]),
                        board=board, boards=boards, cards=[],
                        client=FakeAsk())


# -- removals ------------------------------------------------------------

def test_removing_the_only_dashboard_is_refused(board):
    """The list is passed in rather than read here, so this asserts the
    rule and not the state of whatever database the suite is pointed at."""
    with pytest.raises(PlanRefused, match="only dashboard"):
        planner.resolve("delete_dashboard",
                        DeleteDashboardAction(board_id=board["id"]),
                        board=board, boards=[board], cards=[],
                        client=FakeAsk())


def test_removing_a_card_counts_what_goes(board, boards):
    card = a_card(board)

    out = planner.resolve("delete_card",
                          DeleteCardAction(card_id=card["id"]),
                          board=board, boards=boards, cards=cards_of(board),
                          client=FakeAsk())

    assert out.operations[0].kind == "delete_card"
    assert store.get_card(card["id"]) is not None


# -- staleness -----------------------------------------------------------

def test_a_plan_knows_the_board_it_was_computed_against(board, boards):
    action = RenameDashboardAction(board_id=board["id"], title="Wells")
    out = planner.resolve("rename_dashboard", action, board=board,
                          boards=boards, cards=[], client=FakeAsk())

    assert not planner.is_stale(out.basis)
    store.create_card(board["id"])
    assert planner.is_stale(out.basis), (
        "a card was added under the plan and nothing noticed")


def test_a_deleted_board_counts_as_moved(board, boards):
    out = planner.resolve("rename_dashboard",
                          RenameDashboardAction(board_id=board["id"],
                                                title="Wells"),
                          board=board, boards=boards, cards=[],
                          client=FakeAsk())
    store.create_board("Second")
    store.soft_delete_board(board["id"])

    assert planner.is_stale(out.basis)


# -- application ---------------------------------------------------------

def test_applying_reads_the_frozen_document_not_the_proposal(board, boards):
    card = a_card(board)
    out = planner.resolve("layout", LayoutAction(changes=[{
        "card_id": str(card["id"]),
        "layout": {"x": 0, "y": 0, "w": 12, "h": 10}}]),
        board=board, boards=boards, cards=cards_of(board), client=FakeAsk())

    planner.apply_immediate(out.stored())

    assert store.get_card(card["id"])["layout"]["w"] == 12


def test_a_chat_edit_leaves_the_card_undoable(board, boards):
    """The card's own one-step undo has to keep working after a chat edit,
    or the two ways of changing a card behave differently."""
    card = a_card(board)
    out = planner.resolve("edit_card",
                          EditCardIntent(card_id=card["id"],
                                         instruction="by month"),
                          board=board, boards=boards, cards=cards_of(board),
                          client=FakeAsk(answer(BY_MONTH)))

    planner.apply_immediate(out.stored())

    saved = store.get_card(card["id"])
    assert saved["semantic_query"]["dimensions"][0]["field"] == "reading_date"
    assert saved["previous"]["semantic_query"]["dimensions"][0]["field"] \
        == "region"


def test_placeholders_appear_before_any_question_is_asked(board, boards):
    action = NewCardsAction(say="Adding two.", cards=[
        CardRequest(request_id="r1", question="oil by region", title="One"),
        CardRequest(request_id="r2", question="gas by region", title="Two")])
    out = planner.resolve("new_cards", action, board=board, boards=boards,
                          cards=cards_of(board), client=FakeAsk())

    board_id, placed = planner.create_placeholders(out.stored())

    assert board_id == board["id"]
    titles = {c["title"] for c in store.list_cards(board_id)}
    assert {"One", "Two"} <= titles
    assert all(c["state"] == "empty" for c in store.list_cards(board_id)
               if c["title"] in {"One", "Two"})


def test_a_new_dashboard_is_created_by_applying_it(board, boards):
    action = NewDashboardAction(say="Building.", title="Wells", cards=[
        CardRequest(request_id="r1", question="oil by region", title="Oil")])
    out = planner.resolve("new_dashboard", action, board=board, boards=boards,
                          cards=[], client=FakeAsk())

    board_id, placed = planner.create_placeholders(out.stored())

    assert store.get_board(board_id)["title"] == "Wells"
    assert len(placed) == 1


def test_one_card_that_cannot_be_built_reports_itself(board):
    """A refusal is this card's outcome, not the dashboard's."""
    card = store.create_card(board["id"])
    client = FakeAsk({"semantic_query": BY_REGION, "ambiguity": {
        "term": "oil", "candidates": ["oil", "oil_equivalent"],
        "question": "Oil or oil equivalent?"}})

    reason = planner.build_card(card["id"], "oil by region", chart_hint=None,
                               client=client)

    assert reason is not None
    assert store.get_card(card["id"])["state"] == "empty"


def test_a_built_card_is_indistinguishable_from_one_a_person_asked_for(board):
    card = store.create_card(board["id"])

    reason = planner.build_card(card["id"], "oil by region", chart_hint=None,
                                client=FakeAsk(answer()))

    assert reason is None
    saved = store.get_card(card["id"])
    assert saved["state"] == "ready"
    assert saved["semantic_query"]["dimensions"][0]["field"] == "region"
    # Provenance, the same field the ask box fills.
    assert saved["prompt"] == "oil by region"
