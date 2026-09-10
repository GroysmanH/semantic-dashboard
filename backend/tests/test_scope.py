"""A dashboard asks one schema's questions.

The scoping itself is easy. What is not easy is that narrowing the menu
makes the model *more* dangerous, not less: shown only the planning mart
and asked about oil production, it has no right answer available, and a
model with no right answer available does not stop -- it picks the closest
wrong one. Most of what is tested here is the guard that runs before the
model is ever asked.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.deps import LAYER, SYNONYMS
from app.layer.scope import elsewhere, layer_for, schema_of, schemas
from app.llm.query_step import AskOutcome
from app.main import app
from app.semantic.query import SemanticQuery
from app.store import cards as store

TARGETS = SemanticQuery.model_validate(
    {"entity": "field_targets", "measures": ["target_oil"],
     "dimensions": [{"field": "field_name"}]})


@pytest.fixture
def client():
    return TestClient(app)


# -- deriving the schema -------------------------------------------------

def test_the_schema_is_the_prefix_of_the_table_an_entity_declares():
    assert schema_of(LAYER["production"]) == "ddh"
    assert schema_of(LAYER["field_targets"]) == "dm_planning"


def test_filtering_keeps_only_what_that_schema_can_answer():
    assert set(layer_for(LAYER, "dm_planning")) == {"field_targets"}
    assert "field_targets" not in layer_for(LAYER, "ddh")
    assert schemas(LAYER) == ["ddh", "dm_planning", "dm_upstream"]


def test_an_unknown_schema_filters_to_nothing_rather_than_everything():
    """Failing open here would hand the whole warehouse to a dashboard
    pointed at a schema that does not exist."""
    assert layer_for(LAYER, "dm_nonexistent") == {}


# -- the cross-schema guard ----------------------------------------------

def test_a_question_whose_subject_lives_elsewhere_is_named():
    assert elsewhere("oil production by region", LAYER, SYNONYMS,
                     "dm_planning") == "ddh"
    assert elsewhere("planned production by month", LAYER, SYNONYMS,
                     "ddh") == "dm_planning"


def test_a_question_in_scope_passes():
    assert elsewhere("oil target by field", LAYER, SYNONYMS,
                     "dm_planning") is None
    assert elsewhere("oil production by region", LAYER, SYNONYMS,
                     "ddh") is None


def test_a_shared_dimension_does_not_rescue_an_out_of_scope_question():
    """The whole reason the guard reads measures and not dimensions.

    'region' means something in operations and in planning both, so any
    rule that counted it would let "oil production by region" through on a
    planning dashboard -- straight into a model with no oil measure to
    offer and every incentive to substitute one.
    """
    assert "region" in SYNONYMS
    assert {"production", "field_targets"} <= set(SYNONYMS["region"])
    assert elsewhere("oil production by region", LAYER, SYNONYMS,
                     "dm_planning") == "ddh"


def test_a_question_about_nothing_at_all_is_left_to_the_ordinary_path():
    """Unanswerable is a refusal this guard has no opinion about. Claiming
    it belongs to some other schema would be a lie with a schema name in
    it."""
    assert elsewhere("what is the weather in Almaty", LAYER, SYNONYMS,
                     "ddh") is None


# -- the card path -------------------------------------------------------

@pytest.fixture
def planning_card(client):
    board = client.post("/boards", json={"title": "Planning",
                                         "schema_name": "dm_planning"}).json()
    return client.post(f"/boards/{board['id']}/cards").json()


def test_an_out_of_scope_question_never_reaches_a_model(client, planning_card):
    """Asserted on the client, not the outcome. The point of the guard is
    the API call that does not happen -- a free tier has a daily budget and
    a paid one has a bill."""
    with patch("app.routes.ask.make_client") as made:
        answer = client.post("/ask", json={
            "question": "oil production by region",
            "card_id": planning_card["id"]}).json()

    made.assert_not_called()
    assert answer["state"] == "refused"
    assert "ddh" in answer["message"]
    assert "dm_planning" in answer["message"]


def test_the_refusal_is_something_the_person_can_reply_to(client, planning_card):
    client.post("/ask", json={"question": "oil production by region",
                              "card_id": planning_card["id"]})

    stored = store.get_card(planning_card["id"])["pending_clarification"]
    assert stored["kind"] == "refused"
    assert stored["asked"] == "oil production by region"


def test_an_in_scope_question_is_asked_against_that_schema_only(
        client, planning_card):
    seen = {}

    def capture(question, layer, llm, **kwargs):
        seen["entities"] = set(layer)
        return AskOutcome(query=TARGETS, title="Targets by field")

    with patch("app.routes.ask.ask_model", side_effect=capture):
        client.post("/ask", json={"question": "oil target by field",
                                  "card_id": planning_card["id"]})

    assert seen["entities"] == {"field_targets"}


def test_a_board_with_no_schema_of_its_own_uses_the_configured_default(client):
    """Only reachable by a row that predates the column. It must still
    answer rather than fall through to an empty layer."""
    board = client.post("/boards", json={"title": "Legacy"}).json()
    store.update_board(uuid.UUID(board["id"]), schema_name=None)
    card = client.post(f"/boards/{board['id']}/cards").json()

    seen = {}

    def capture(question, layer, llm, **kwargs):
        seen["entities"] = set(layer)
        return AskOutcome(refusal="no")

    with patch("app.routes.ask.ask_model", side_effect=capture):
        client.post("/ask", json={"question": "oil production by region",
                                  "card_id": card["id"]})

    assert "production" in seen["entities"]


# -- switching -----------------------------------------------------------

def test_switching_a_dashboard_leaves_its_cards_exactly_where_they_are(client):
    """The promise the whole feature rests on. A card built before the
    switch keeps its query, its render and its place."""
    board = client.post("/boards", json={"title": "Ops",
                                         "schema_name": "ddh"}).json()
    card = client.post(f"/boards/{board['id']}/cards").json()
    store.update_card(uuid.UUID(card["id"]),
                      semantic_query={"entity": "production",
                                      "measures": ["oil"], "dimensions": []},
                      state="ready")

    client.patch(f"/boards/{board['id']}", json={"schema_name": "dm_planning"})

    after = store.get_card(uuid.UUID(card["id"]))
    assert after["semantic_query"]["entity"] == "production"
    assert after["state"] == "ready"
    assert after["layout"] == card["layout"]


def test_a_schema_nothing_is_modelled_in_is_refused(client):
    """stg is a real schema with real tables and no entities. A dashboard
    pointed at it could ask nothing at all, so saying no once beats failing
    every question afterwards."""
    answer = client.post("/boards", json={"title": "Raw",
                                          "schema_name": "stg"})
    assert answer.status_code == 422
    assert "stg" in answer.json()["detail"]


def test_a_duplicate_asks_the_same_schema_as_its_source(client):
    board = client.post("/boards", json={"title": "Planning",
                                         "schema_name": "dm_planning"}).json()
    copy = client.post(f"/boards/{board['id']}/duplicate").json()
    assert copy["schema_name"] == "dm_planning"


# -- the catalogue -------------------------------------------------------

def test_the_catalogue_lists_real_tables_and_marks_the_answerable_ones(client):
    listing = {s["schema"]: s for s in client.get("/schemas").json()}

    planning = listing["dm_planning"]
    assert planning["answerable"] == 1
    assert planning["tables"][0]["entity"] == "field_targets"
    assert planning["tables"][0]["is_view"] is True

    ddh = {t["table"]: t for t in listing["ddh"]["tables"]}
    # Reached only through a declared join: modelled, but not on its own.
    assert ddh["dim_wells"]["entity"] is None
    assert ddh["dim_wells"]["joined_only"] is True
    # Really nothing: nobody has said what its columns mean.
    assert ddh["fct_field_targets_monthly"]["entity"] is None
    assert ddh["fct_field_targets_monthly"]["joined_only"] is False


def test_the_catalogue_shows_a_schema_with_nothing_modelled_in_it(client):
    """Browsing the warehouse and choosing a dashboard's subject are two
    different acts. stg is shown, honestly, as answerable by nothing."""
    listing = {s["schema"]: s for s in client.get("/schemas").json()}
    assert listing["stg"]["answerable"] == 0
    assert listing["stg"]["tables"]


def test_the_catalogue_never_exposes_the_application_s_own_tables(client):
    listing = {s["schema"] for s in client.get("/schemas").json()}
    assert "app" not in listing


# -- what the empty card suggests ----------------------------------------

def test_examples_come_from_the_schema_the_dashboard_is_on(client):
    planning = client.get("/layer", params={"schema": "dm_planning"}).json()
    assert planning["examples"]
    assert all("target" in q for q in planning["examples"])
    assert [e["name"] for e in planning["entities"]] == ["field_targets"]

    operations = client.get("/layer", params={"schema": "ddh"}).json()
    assert not any("target" in q for q in operations["examples"])
