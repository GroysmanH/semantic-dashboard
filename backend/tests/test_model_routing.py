"""Model routing is a spending decision, so it is pinned by tests.

The default must stay cheap, and the expensive model must be reachable
only when the asker explicitly says the question is hard.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.llm.client import DEFAULT_MODEL, AnthropicClient
from app.main import app


def test_the_default_model_is_the_cheap_one():
    assert settings.llm_model == "claude-haiku-4-5"
    assert DEFAULT_MODEL == "claude-haiku-4-5"


def test_the_escalation_model_is_named_separately():
    assert settings.llm_model_strong == "claude-sonnet-5"
    assert settings.llm_model != settings.llm_model_strong


def test_a_client_with_no_model_uses_the_default():
    assert AnthropicClient().model == settings.llm_model


def test_the_request_cannot_name_a_model():
    """A caller passing a model id would be choosing how much to spend, and
    would ask a manager to reason about model names."""
    from app.routes.ask import AskIn

    assert "model" not in AskIn.model_fields
    assert "hard" in AskIn.model_fields
    assert AskIn(question="x").hard is False


def test_an_unknown_key_on_the_ask_body_is_ignored_not_honoured():
    from app.routes.ask import AskIn

    body = AskIn.model_validate({"question": "x", "model": "claude-opus-5"})
    assert not hasattr(body, "model")


@pytest.mark.parametrize("hard,expected", [
    (False, "claude-haiku-4-5"),
    (True, "claude-sonnet-5"),
])
def test_the_hard_flag_selects_the_model(monkeypatch, hard, expected):
    """Routing is asserted without calling the API: the fake records which
    model it was constructed with and then refuses."""
    seen = {}

    class FakeClient:
        def __init__(self, model=None, **kw):
            seen["model"] = model

        def ask(self, system, user, schema):
            from app.llm.client import LLMError

            raise LLMError("stopped before spending anything")

    monkeypatch.setattr("app.routes.ask.AnthropicClient", FakeClient)

    # Not a `with` block: entering TestClient's context runs the FastAPI
    # lifespan, and its shutdown closes the pools the rest of the suite
    # shares. The session fixture has already opened them.
    r = TestClient(app).post("/ask", json={"question": "oil by month", "hard": hard})

    assert r.status_code == 200
    assert seen["model"] == expected
    assert r.json()["model"] == expected
