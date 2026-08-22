"""Turn discipline.

Mirrors the guarantees query_step.ask() already makes, because the failure
modes are the same: one retry on a malformed answer, a rate limit that the
caller decides about rather than being turned into a refusal, and a
transport failure that becomes an honest sentence instead of a chart.

The chat adds one of its own: a mutation must never be applied by the turn
loop. It proposes; the plan resolver and the person confirm.
"""

import uuid

import pytest

from app.chat.turn import TurnRequest, run_turn
from app.llm.client import LLMError, LLMRateLimited, LLMSchemaError
from app.store import cards as store


class FakeClient:
    """Returns queued payloads, recording what it was asked.

    A turn is one call when the answer is prose and two when it is not, so
    a payload here is validated against whatever schema the caller asked
    for: the router's wrapper for the first call, a bare action model for
    the second.
    """

    provider = "gemini"
    model = "fake-1"

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.seen: list[tuple[str, str]] = []

    def ask(self, system, user, schema):
        self.seen.append((system, user))
        nxt = self.payloads.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if "turn" in schema.model_fields:
            return schema.model_validate({"turn": nxt})
        return schema.model_validate(nxt)


def task(kind, say="Here is what I will do."):
    return {"action": "task", "say": say, "kind": kind}


def answer(say="West Kazakhstan leads.", claims=None):
    return {"action": "answer", "say": say, "claims": claims or []}


@pytest.fixture
def board():
    return store.create_board("Operations")


@pytest.fixture
def thread():
    from app.store import chat
    return chat.create_thread()


def request_for(thread, board, **over):
    kwargs = dict(
        thread_id=thread["id"], active_board_id=board["id"],
        question="which region leads?", provider="gemini", hard=False,
        share_visible_data=False,
    )
    kwargs.update(over)
    return TurnRequest(**kwargs)


# -- retries and errors --------------------------------------------------

def test_a_malformed_answer_is_retried_once_then_accepted(thread, board):
    client = FakeClient(LLMSchemaError("action is not in the vocabulary"),
                        answer())
    out = run_turn(request_for(thread, board), client=client)

    assert out.message.action == "answer"
    assert len(client.seen) == 2
    # The reason is stated back so the retry is informed rather than a
    # second roll of the dice.
    assert "vocabulary" in client.seen[1][1]


def test_two_malformed_answers_become_a_refusal(thread, board):
    client = FakeClient(LLMSchemaError("bad"), LLMSchemaError("bad again"))
    out = run_turn(request_for(thread, board), client=client)

    assert out.message.action == "refuse"
    assert len(client.seen) == 2


def test_a_rate_limit_is_raised_for_the_caller_to_decide(thread, board):
    """A card should say "try again in a moment"; a batch should wait. Only
    the caller knows which it is, so this must not become a refusal."""
    client = FakeClient(LLMRateLimited("slow down"))
    with pytest.raises(LLMRateLimited):
        run_turn(request_for(thread, board), client=client)


def test_a_transport_failure_becomes_a_refusal(thread, board):
    client = FakeClient(LLMError("no API key configured"))
    out = run_turn(request_for(thread, board), client=client)

    assert out.message.action == "refuse"
    assert "API key" in (out.message.refusal or "")


def test_the_provider_and_model_are_reported(thread, board):
    client = FakeClient(answer())
    run_turn(request_for(thread, board), client=client)
    from app.store import chat
    stored = chat.list_messages(thread["id"])
    assert any(m["body"].get("provider") == "gemini" for m in stored)


# -- the prompt ----------------------------------------------------------

def test_the_system_block_carries_no_context(thread, board):
    client = FakeClient(answer())
    run_turn(request_for(thread, board), client=client)
    system, user = client.seen[0]

    assert "Operations" not in system
    assert "Operations" in user


def test_the_date_is_stated_in_the_user_block(thread, board):
    client = FakeClient(answer())
    run_turn(request_for(thread, board), client=client)
    system, user = client.seen[0]

    import re
    assert not re.search(r"\b20\d\d\b", system)
    assert re.search(r"\b20\d\d\b", user)


# -- changes are proposals ----------------------------------------------

def test_a_change_with_changes_switched_off_refuses(thread, board):
    before = len(store.list_cards(board["id"]))
    client = FakeClient(task("new_cards"))

    out = run_turn(request_for(thread, board), client=client,
                   allow_changes=False)

    assert out.message.action == "refuse"
    assert len(store.list_cards(board["id"])) == before
    # It never got as far as asking for the details.
    assert len(client.seen) == 1


def test_a_proposed_card_is_planned_and_not_created(thread, board):
    """The turn writes a plan. Only a confirmation applies one, and that is
    a separate request from the browser."""
    before = len(store.list_cards(board["id"]))
    client = FakeClient(task("new_cards", "I will add a card about oil."),
                        {"action": "new_cards", "say": "", "cards": [
                            {"request_id": "r1", "question": "oil by region",
                             "title": "Oil by region"}]})

    out = run_turn(request_for(thread, board), client=client)

    assert out.pending_plan is not None
    assert out.pending_plan.action == "new_cards"
    assert [c.title for c in out.pending_plan.cards] == ["Oil by region"]
    assert len(store.list_cards(board["id"])) == before


def test_the_detail_call_is_told_which_kind_it_is_detailing(thread, board):
    """The router turn is not in the conversation the second call sees, so
    without this the model is asked to fill in a decision it has no record
    of making."""
    client = FakeClient(task("rename_dashboard", "I will rename this tab."),
                        {"action": "rename_dashboard", "say": "",
                         "board_id": str(board["id"]), "title": "Wells"})

    run_turn(request_for(thread, board), client=client)

    assert len(client.seen) == 2
    assert "rename_dashboard" in client.seen[1][1]
    assert "I will rename this tab." in client.seen[1][1]


@pytest.mark.parametrize("kind,detail", [
    ("delete_card", {"action": "delete_card", "say": "",
                     "card_ids": [str(uuid.uuid4())]}),
    ("delete_dashboard", {"action": "delete_dashboard", "say": "",
                          "board_id": str(uuid.uuid4())}),
    ("rename_dashboard", {"action": "rename_dashboard", "say": "",
                          "board_id": str(uuid.uuid4()),
                          "title": "Renamed"}),
], ids=["delete_card", "delete_dashboard", "rename"])
def test_no_destructive_action_reaches_the_store(thread, board, kind, detail):
    client = FakeClient(task(kind), detail)
    out = run_turn(request_for(thread, board), client=client)

    assert out.message.action == "refuse"
    assert store.get_board(board["id"]) is not None
    assert store.get_board(board["id"])["title"] == "Operations"


def test_a_second_plan_is_refused_while_one_is_waiting(thread, board):
    """One pending plan per conversation. Two would mean confirming the
    older one after reading the newer one's preview."""
    first = FakeClient(task("rename_dashboard"),
                       {"action": "rename_dashboard", "say": "",
                        "board_id": str(board["id"]), "title": "Wells"})
    run_turn(request_for(thread, board), client=first)

    second = FakeClient(task("rename_dashboard"))
    out = run_turn(request_for(thread, board), client=second)

    assert out.message.action == "refuse"
    assert "confirm" in (out.message.refusal or "")
    # Refused before spending a second call on the details.
    assert len(second.seen) == 1


# -- read-only variants --------------------------------------------------

def test_a_clarification_is_persisted_with_no_effects(thread, board):
    client = FakeClient({"action": "clarify", "question": "Oil or gas?"})
    out = run_turn(request_for(thread, board), client=client)

    assert out.message.action == "clarify"
    assert out.message.clarify == "Oil or gas?"
    assert out.pending_plan is None


def test_a_clarification_records_what_it_was_about(thread, board):
    """A one-word answer needs something to attach to. The card stores the
    same pair for the same reason -- see routes/ask.py."""
    from app.store import chat

    client = FakeClient({"action": "clarify", "question": "Oil or gas?"})
    run_turn(request_for(thread, board, question="production by region"),
             client=client)

    stored = [m for m in chat.list_messages(thread["id"])
              if m["body"].get("action") == "clarify"]
    assert stored[0]["body"]["asked"] == "production by region"


def test_a_refusal_names_the_missing_metric_and_offers_the_request(thread, board):
    client = FakeClient({
        "action": "refuse", "reason": "There is no drilling-cost metric.",
        "missing_metric": "drilling cost",
        "request_text": "add a drilling cost metric"})
    out = run_turn(request_for(thread, board), client=client)

    assert out.message.missing_metric == "drilling cost"
    assert out.message.request_text == "add a drilling cost metric"


def test_an_unverifiable_number_does_not_survive_into_the_transcript(thread, board):
    client = FakeClient(answer(say="Production hit 42000000 barrels."))
    out = run_turn(request_for(thread, board), client=client)

    assert "42000000" not in out.message.say
    from app.store import chat
    stored = chat.list_messages(thread["id"])
    assert all("42000000" not in str(m["body"]) for m in stored)


# -- the data gate -------------------------------------------------------

def test_with_sharing_off_the_turn_is_not_marked_as_exposed(thread, board):
    client = FakeClient(answer())
    run_turn(request_for(thread, board), client=client)

    from app.store import chat
    assert all(not m["data_exposed"] for m in chat.list_messages(thread["id"]))


def test_the_active_dashboard_is_recorded_on_every_message(thread, board):
    client = FakeClient(answer())
    run_turn(request_for(thread, board), client=client)

    from app.store import chat
    stored = chat.list_messages(thread["id"])
    assert stored, "the turn stored nothing"
    assert all(m["active_board_title"] == "Operations" for m in stored)


def test_the_question_is_stored_before_the_answer(thread, board):
    client = FakeClient(answer())
    run_turn(request_for(thread, board), client=client)

    from app.store import chat
    roles = [m["role"] for m in chat.list_messages(thread["id"])]
    assert roles == ["user", "assistant"]
