"""The chat action contract.

The model chooses intent inside a closed grammar; it never applies effects.
Every guard here exists so that a malformed or over-reaching intent is
rejected before the server turns it into a plan, in the same spirit as the
semantic query grammar.
"""

import uuid

import pytest
from pydantic import BaseModel, ValidationError

from app.chat.schema import (
    CardRequest,
    ChatAction,
    ChatModelResponse,
    Claim,
    ClaimOperand,
    LayoutRequest,
)

CARD = str(uuid.uuid4())
BOARD = str(uuid.uuid4())
QUERY = {"entity": "production", "measures": ["oil"]}


def parse(payload: dict):
    return ChatModelResponse(turn=payload).turn


def a_card(**over):
    return {"request_id": "r1", "question": "oil by region",
            "title": "Oil by region", **over}


# -- one valid instance of every action ----------------------------------

VALID = [
    {"action": "answer", "say": "West Kazakhstan leads.", "claims": []},
    {"action": "run_query", "say": "Here it is.", "semantic_query": QUERY},
    {"action": "clarify", "question": "Oil or gas?"},
    {"action": "refuse", "reason": "There is no drilling-cost metric."},
    {"action": "new_cards", "say": "Adding one.", "cards": [a_card()]},
    {"action": "edit_card", "card_id": CARD, "semantic_query": QUERY},
    {"action": "new_dashboard", "title": "Production", "cards": [a_card()]},
    {"action": "layout", "changes": [
        {"card_id": CARD, "layout": {"x": 0, "y": 0, "w": 6, "h": 10}}]},
    {"action": "rename_dashboard", "board_id": BOARD, "title": "Drilling"},
    {"action": "reorder_dashboards", "order": [BOARD]},
    {"action": "delete_card", "card_id": CARD},
    {"action": "delete_dashboard", "board_id": BOARD},
]


@pytest.mark.parametrize("payload", VALID, ids=lambda p: p["action"])
def test_every_action_variant_parses(payload):
    assert parse(payload).action == payload["action"]


@pytest.mark.parametrize("payload", VALID, ids=lambda p: p["action"])
def test_no_action_accepts_an_unknown_field(payload):
    # extra="forbid" is what stops a model smuggling effects, SQL or a
    # confirmation flag into an intent object.
    with pytest.raises(ValidationError):
        parse({**payload, "effects": ["something"]})


def test_an_unknown_action_is_rejected():
    with pytest.raises(ValidationError):
        parse({"action": "drop_table", "say": "hi"})


# -- targets are required ------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"action": "edit_card", "semantic_query": QUERY},
    {"action": "delete_card"},
    {"action": "delete_dashboard"},
    {"action": "rename_dashboard", "title": "x"},
], ids=["edit_card", "delete_card", "delete_dashboard", "rename"])
def test_an_action_without_its_target_is_rejected(payload):
    with pytest.raises(ValidationError):
        parse(payload)


def test_edit_card_carries_a_replacement_query_not_an_instruction():
    """The preview must show an exact diff without a second model call, so
    the edit arrives as a complete query rather than words to re-interpret."""
    with pytest.raises(ValidationError):
        parse({"action": "edit_card", "card_id": CARD,
               "instruction": "break it down by region"})


# -- card batches --------------------------------------------------------

@pytest.mark.parametrize("action", ["new_cards", "new_dashboard"])
def test_an_empty_card_list_is_rejected(action):
    payload = {"action": action, "cards": []}
    if action == "new_dashboard":
        payload["title"] = "Production"
    with pytest.raises(ValidationError):
        parse(payload)


@pytest.mark.parametrize("action", ["new_cards", "new_dashboard"])
def test_more_than_six_cards_is_rejected(action):
    cards = [a_card(request_id=f"r{i}") for i in range(7)]
    payload = {"action": action, "cards": cards}
    if action == "new_dashboard":
        payload["title"] = "Production"
    with pytest.raises(ValidationError):
        parse(payload)


def test_duplicate_request_ids_are_rejected():
    # Request ids address placeholders during streaming; two cards sharing
    # one would make progress events ambiguous.
    with pytest.raises(ValidationError):
        parse({"action": "new_cards",
               "cards": [a_card(), a_card()]})


def test_six_cards_with_distinct_ids_are_accepted():
    cards = [a_card(request_id=f"r{i}") for i in range(6)]
    assert len(parse({"action": "new_cards", "cards": cards}).cards) == 6


# -- layouts -------------------------------------------------------------

@pytest.mark.parametrize("layout", [
    {"x": 0, "y": 0, "w": 0, "h": 4},
    {"x": 0, "y": 0, "w": 4, "h": 0},
    {"x": 0, "y": 0, "w": -1, "h": 4},
    {"x": -1, "y": 0, "w": 4, "h": 4},
    {"x": 0, "y": -1, "w": 4, "h": 4},
], ids=["zero-w", "zero-h", "negative-w", "negative-x", "negative-y"])
def test_a_card_cannot_be_placed_off_grid_or_at_no_size(layout):
    with pytest.raises(ValidationError):
        LayoutRequest(**layout)


def test_a_layout_wider_than_the_grid_is_rejected():
    with pytest.raises(ValidationError):
        LayoutRequest(x=8, y=0, w=6, h=4)


# -- claims --------------------------------------------------------------

def test_a_claim_needs_at_least_one_operand():
    with pytest.raises(ValidationError):
        Claim(text="It rose.", displayed_value="12", operation="exact",
              operands=[])


def test_a_claim_operand_names_a_card_and_a_field():
    operand = ClaimOperand(card_id=CARD, field="oil", row=0)
    assert operand.field == "oil"


# -- the transport wrapper ----------------------------------------------

def test_the_response_wraps_the_union_so_providers_receive_a_model():
    """LLMClient.ask takes `type[T] where T: BaseModel`; a bare Annotated
    union alias is not a model and would break every provider."""
    assert issubclass(ChatModelResponse, BaseModel)


def test_the_json_schema_exposes_action_as_the_discriminator():
    schema = ChatModelResponse.model_json_schema()
    turn = schema["properties"]["turn"]
    assert turn.get("discriminator", {}).get("propertyName") == "action"


def test_card_requests_bound_free_text():
    with pytest.raises(ValidationError):
        CardRequest(request_id="r1", question="", title="Oil")
    with pytest.raises(ValidationError):
        CardRequest(request_id="r1", question="x" * 501, title="Oil")


# -- what actually compiles as a provider grammar ------------------------

def test_the_read_only_union_is_what_providers_are_asked_for():
    """The full twelve-variant union is rejected by Anthropic with "the
    compiled grammar is too large" and by Gemini with a bare 400. Measured
    on claude-haiku-4-5: these four variants compile, ten variants carrying
    no SemanticQuery already do not."""
    from app.chat.schema import ChatReadOnlyResponse

    for action in ["answer", "run_query", "clarify", "refuse"]:
        payload = dict(VALID[[v["action"] for v in VALID].index(action)])
        assert ChatReadOnlyResponse(turn=payload).turn.action == action


@pytest.mark.parametrize("payload", [v for v in VALID if v["action"] not in
                                     {"answer", "run_query", "clarify",
                                      "refuse"}],
                         ids=lambda p: p["action"])
def test_a_mutation_cannot_be_expressed_in_the_read_only_schema(payload):
    from app.chat.schema import ChatReadOnlyResponse

    with pytest.raises(ValidationError):
        ChatReadOnlyResponse(turn=payload)


def test_the_read_only_schema_stays_a_narrow_union():
    """A canary, not a proof.

    Measured against claude-haiku-4-5: four variants compiled at 7.2KB and
    again at 8.1KB once field descriptions were added, while ten variants
    carrying no SemanticQuery failed at 9.0KB. So size alone does not
    decide it — the number of branches dominates, and descriptions appear
    to cost nothing. This asserts the branch count, which is the variable
    that actually moved, and flags size only as something to re-test live.
    """
    import json

    from app.chat.schema import ChatReadOnlyResponse

    schema = ChatReadOnlyResponse.model_json_schema()
    branches = schema["properties"]["turn"]["oneOf"]
    assert len(branches) == 4, (
        "the provider-facing union grew a branch; re-run a live call before "
        "trusting it, because the grammar ceiling is not a byte count")

    size = len(json.dumps(schema))
    assert size < 12_000, f"schema grew to {size} bytes; re-test the providers"
