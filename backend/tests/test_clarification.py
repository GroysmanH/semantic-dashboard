"""A clarifying question has to remember itself.

Without this the card asks "oil or gas?", the person answers "oil", and the
next request arrives as the single word "oil" attached to nothing. The card
either asks again or guesses, and both are worse than not having asked.

The memory lives on the card, not in a conversation: the card's state is
the context everywhere else in this app, and that stays true here.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.llm.query_step import AskOutcome
from app.main import app
from app.semantic.query import SemanticQuery
from app.store import cards as store

OIL = SemanticQuery.model_validate(
    {"entity": "production", "measures": ["oil"],
     "dimensions": [{"field": "region"}]})


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def card(client):
    board = client.post("/boards", json={"title": "clarify"}).json()
    return client.post(f"/boards/{board['id']}/cards").json()


def test_a_clarifying_question_is_remembered_on_the_card(client, card):
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(clarify="Oil or gas?")):
        answer = client.post("/ask", json={"question": "show me production",
                                           "card_id": card["id"]}).json()

    assert answer["state"] == "clarify"
    stored = store.get_card(card["id"])["pending_clarification"]
    assert stored["question"] == "Oil or gas?"
    assert stored["asked"] == "show me production"


def test_the_answer_is_given_back_with_the_question_it_answers(client, card):
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(clarify="Oil or gas?")):
        client.post("/ask", json={"question": "show me production",
                                  "card_id": card["id"]})

    seen = {}

    def capture(question, layer, llm, **kwargs):
        seen.update(kwargs)
        return AskOutcome(query=OIL, title="Oil by region")

    with patch("app.routes.ask.ask_model", side_effect=capture):
        client.post("/ask", json={"question": "oil", "card_id": card["id"]})

    assert seen["clarifying"]["question"] == "Oil or gas?"
    assert seen["clarifying"]["asked"] == "show me production"


def test_the_original_request_survives_a_second_clarification(client, card):
    """Two rounds must still rebuild what was first asked for, not the
    single word that answered round one."""
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(clarify="Oil or gas?")):
        client.post("/ask", json={"question": "show me production",
                                  "card_id": card["id"]})
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(clarify="By region or by well?")):
        client.post("/ask", json={"question": "oil", "card_id": card["id"]})

    stored = store.get_card(card["id"])["pending_clarification"]
    assert stored["question"] == "By region or by well?"
    assert stored["asked"] == "show me production"


def test_a_resolved_clarification_is_cleared(client, card):
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(clarify="Oil or gas?")):
        client.post("/ask", json={"question": "show me production",
                                  "card_id": card["id"]})
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(query=OIL, title="Oil by region")):
        client.post("/ask", json={"question": "oil", "card_id": card["id"]})

    assert store.get_card(card["id"])["pending_clarification"] is None


def test_a_later_unrelated_question_is_not_coloured_by_it(client, card):
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(clarify="Oil or gas?")):
        client.post("/ask", json={"question": "show me production",
                                  "card_id": card["id"]})
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(query=OIL, title="Oil by region")):
        client.post("/ask", json={"question": "oil", "card_id": card["id"]})

    seen = {}

    def capture(question, layer, llm, **kwargs):
        seen.update(kwargs)
        return AskOutcome(query=OIL, title="t")

    with patch("app.routes.ask.ask_model", side_effect=capture):
        client.post("/ask", json={"question": "now show gas",
                                  "card_id": card["id"]})

    assert seen["clarifying"] is None


def test_the_deterministic_ambiguity_guard_does_not_fire_twice(layer):
    """The ambiguous word is still in the original request, so re-running
    the synonym check while answering would ask the same question again."""
    from app.deps import SYNONYMS
    from app.llm.query_step import AskResponse, ask

    class Client:
        provider, model = "fake", "fake"

        def ask(self, system, user, schema):
            return AskResponse(semantic_query=OIL)

    outcome = ask("oil", layer, Client(), synonyms=SYNONYMS,
                  clarifying={"question": "Oil or gas?",
                              "asked": "show me production"})

    assert outcome.clarify is None
    assert outcome.query is not None
