"""What the card last said has to be answerable.

Without this the card asks "oil or gas?", the person answers "oil", and the
next request arrives as the single word "oil" attached to nothing. The card
either asks again or guesses, and both are worse than not having asked.

A refusal is kept for the same reason. "The layer has no per-region
ranking" is a sentence somebody answers with "then rank them overall", and
that answer means nothing on its own. The difference is who decides it is a
reply: a question is owed an answer, so whatever is typed next is taken as
one, while a refusal ended the exchange and only becomes context when the
person says they are replying to it.

The memory lives on the card, not in a conversation: the card's state is
the context everywhere else in this app, and that stays true here.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.llm.query_step import AskOutcome
from app.llm.client import LLMRateLimited
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


# -- refusals ------------------------------------------------------------

def test_a_refusal_is_kept_so_it_can_be_answered(client, card):
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(refusal="No per-region ranking.")):
        answer = client.post("/ask", json={"question": "top well per region",
                                           "card_id": card["id"]}).json()

    assert answer["state"] == "refused"
    stored = store.get_card(card["id"])["pending_clarification"]
    assert stored["kind"] == "refused"
    assert stored["question"] == "No per-region ranking."
    assert stored["asked"] == "top well per region"


def test_a_refusal_does_not_colour_the_next_question_by_itself(client, card):
    """It ended the exchange. Somebody who types something else next is
    asking something else, not arguing with it."""
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(refusal="No per-region ranking.")):
        client.post("/ask", json={"question": "top well per region",
                                  "card_id": card["id"]})

    seen = {}

    def capture(question, layer, llm, **kwargs):
        seen.update(kwargs)
        return AskOutcome(query=OIL, title="t")

    with patch("app.routes.ask.ask_model", side_effect=capture):
        client.post("/ask", json={"question": "oil by region",
                                  "card_id": card["id"]})

    assert seen["clarifying"] is None


def test_replying_to_a_refusal_hands_back_what_was_said(client, card):
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(refusal="No per-region ranking.")):
        client.post("/ask", json={"question": "top well per region",
                                  "card_id": card["id"]})

    seen = {}

    def capture(question, layer, llm, **kwargs):
        seen.update(kwargs)
        return AskOutcome(query=OIL, title="t")

    with patch("app.routes.ask.ask_model", side_effect=capture):
        client.post("/ask", json={"question": "then rank them overall",
                                  "card_id": card["id"], "reply": True})

    assert seen["clarifying"]["kind"] == "refused"
    assert seen["clarifying"]["question"] == "No per-region ranking."
    assert seen["clarifying"]["asked"] == "top well per region"


def test_a_reply_that_is_refused_again_keeps_the_original_request(client, card):
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(refusal="No per-region ranking.")):
        client.post("/ask", json={"question": "top well per region",
                                  "card_id": card["id"]})
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(refusal="Still no ranking by group.")):
        client.post("/ask", json={"question": "rank them within the region",
                                  "card_id": card["id"], "reply": True})

    stored = store.get_card(card["id"])["pending_clarification"]
    assert stored["question"] == "Still no ranking by group."
    assert stored["asked"] == "top well per region"


def test_a_resolved_refusal_is_cleared(client, card):
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(refusal="No per-region ranking.")):
        client.post("/ask", json={"question": "top well per region",
                                  "card_id": card["id"]})
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(query=OIL, title="Oil by region")):
        client.post("/ask", json={"question": "then rank them overall",
                                  "card_id": card["id"], "reply": True})

    assert store.get_card(card["id"])["pending_clarification"] is None


# -- walking away from an exchange ---------------------------------------

def test_declining_to_reply_drops_the_question(client, card):
    """The card asked something and the person asked something else
    instead. Replaying the question would answer it with a sentence that
    was never meant for it."""
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(clarify="Oil or gas?")):
        client.post("/ask", json={"question": "show me production",
                                  "card_id": card["id"]})

    seen = {}

    def capture(question, layer, llm, **kwargs):
        seen.update(kwargs)
        return AskOutcome(refusal="No such field.")

    with patch("app.routes.ask.ask_model", side_effect=capture):
        client.post("/ask", json={"question": "downtime by rig",
                                  "card_id": card["id"], "reply": False})

    assert seen["clarifying"] is None
    stored = store.get_card(card["id"])["pending_clarification"]
    assert stored["asked"] == "downtime by rig"


def test_declining_to_reply_clears_an_exchange_that_then_succeeds(client, card):
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(clarify="Oil or gas?")):
        client.post("/ask", json={"question": "show me production",
                                  "card_id": card["id"]})
    with patch("app.routes.ask.ask_model",
               return_value=AskOutcome(query=OIL, title="Oil by region")):
        client.post("/ask", json={"question": "oil by region",
                                  "card_id": card["id"], "reply": False})

    assert store.get_card(card["id"])["pending_clarification"] is None


def test_dismissing_an_exchange_clears_it_without_asking_again(client, card):
    store.update_card(
        card["id"],
        pending_clarification={
            "kind": "clarify",
            "question": "Oil or gas?",
            "asked": "show me production",
        },
    )

    response = client.post(f"/cards/{card['id']}/dismiss-exchange")

    assert response.status_code == 204
    assert store.get_card(card["id"])["pending_clarification"] is None


def test_dismissing_an_exchange_from_a_missing_card_is_not_silently_accepted(client):
    response = client.post(f"/cards/{uuid.uuid4()}/dismiss-exchange")

    assert response.status_code == 404


def test_a_rate_limit_is_not_presented_as_something_to_reply_to(client, card):
    with patch("app.routes.ask.ask_model",
               side_effect=LLMRateLimited("Try again in a moment.")):
        response = client.post(
            "/ask",
            json={"question": "oil by month", "card_id": card["id"]},
        ).json()

    assert response["state"] == "refused"
    assert response["message"] == "Try again in a moment."
    assert response["replyable"] is False
    assert store.get_card(card["id"])["pending_clarification"] is None
