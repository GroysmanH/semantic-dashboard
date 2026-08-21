"""Two APIs, one contract.

The point of the seam is that a provider switch changes who answers and
nothing else: the same grammar, the same bounds, the same three error
classes. These tests pin that without spending anything -- the SDK call is
faked and the schema translation is checked offline.
"""

import json

import pytest
from pydantic import ValidationError

from app.config import settings
from app.llm.client import (
    CLIENTS,
    AnthropicClient,
    GeminiClient,
    LLMError,
    LLMSchemaError,
    NvidiaClient,
    OpenAIClient,
    _extract_json,
    configured_providers,
    make_client,
)
from app.llm.query_step import AskResponse

GOOD = {
    "semantic_query": {"entity": "production", "measures": ["oil"],
                       "dimensions": [{"field": "reading_date", "grain": "month"}]},
    "title": "Oil by month",
}


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.candidates = []
        self.usage_metadata = None


def gemini_returning(text):
    """A GeminiClient whose SDK call is replaced by a canned response, so
    the parsing and error mapping are exercised without a network call."""
    client = GeminiClient("gemini-3.5-flash")

    class FakeModels:
        def generate_content(self, **kw):
            self.kw = kw
            return FakeResponse(text)

    class FakeSDK:
        models = FakeModels()

    client._sdk = FakeSDK()
    return client


# -- the registry ---------------------------------------------------------

def test_every_provider_is_registered():
    assert set(CLIENTS) == {"anthropic", "gemini", "openai", "nvidia"}


def test_an_unknown_provider_is_refused_by_name():
    with pytest.raises(LLMError, match="Unknown model provider"):
        make_client("mistral")


@pytest.mark.parametrize("provider,cls", [
    ("anthropic", AnthropicClient), ("gemini", GeminiClient),
    ("openai", OpenAIClient), ("nvidia", NvidiaClient)])
def test_the_factory_returns_the_right_client(provider, cls):
    client = make_client(provider)
    assert isinstance(client, cls)
    assert client.provider == provider


@pytest.mark.parametrize("provider,hard,expected", [
    ("anthropic", False, "claude-haiku-4-5"),
    ("anthropic", True, "claude-sonnet-5"),
    ("gemini", False, "gemini-3.5-flash"),
    ("gemini", True, "gemini-3.6-flash"),
    ("openai", False, "gpt-5-mini"),
    ("openai", True, "gpt-5"),
    ("nvidia", False, "minimaxai/minimax-m3"),
    ("nvidia", True, "moonshotai/kimi-k3"),
])
def test_hard_escalates_within_the_chosen_provider(provider, hard, expected):
    """Switching provider must not re-price what `hard` means: each side
    has its own cheap and strong tier, and `hard` picks the strong one."""
    assert make_client(provider, hard=hard).model == expected


def test_no_provider_named_falls_back_to_the_configured_default():
    assert make_client().provider == settings.llm_provider


def test_building_a_client_never_needs_a_credential():
    """The SDK is built on first ask, so a process that never calls the
    model -- the whole test suite, for one -- needs no keys."""
    for cls in CLIENTS.values():
        assert cls()._sdk is None


def test_only_providers_with_a_key_are_offered(monkeypatch):
    for attr in ("anthropic_api_key", "google_api_key", "openai_api_key",
                 "nvidia_api_key"):
        monkeypatch.setattr(settings, attr, "")
    for var in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
                "OPENAI_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert configured_providers() == []

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-x")
    assert configured_providers() == ["anthropic"]

    monkeypatch.setattr(settings, "nvidia_api_key", "nvapi-x")
    assert configured_providers() == ["anthropic", "nvidia"]


# -- the eval defaults to the one that can finish -------------------------

def test_the_eval_defaults_to_a_provider_that_can_actually_run_it():
    """This asserted "gemini" on the reasoning that a 360-call sweep should
    not bill. The reasoning was fine and the fact was wrong: Google AI
    Studio's free tier allows 20 requests per DAY per model, so the sweep
    takes about three weeks there. Haiku runs it for roughly thirty cents.
    Free is only cheaper when it finishes."""
    assert settings.eval_provider == "anthropic"


# -- Gemini holds the same contract ---------------------------------------

def test_gemini_parses_a_well_formed_answer():
    got = gemini_returning(json.dumps(GOOD)).ask("sys", "oil by month", AskResponse)
    assert got.semantic_query.entity == "production"
    assert got.semantic_query.dimensions[0].grain == "month"


def test_gemini_treats_an_invented_field_as_a_retryable_schema_error():
    """`extra="forbid"` stays load-bearing on this path: the response is
    re-validated here rather than trusted from the SDK."""
    bad = {**GOOD, "semantic_query": {**GOOD["semantic_query"], "having": "x"}}
    with pytest.raises(LLMSchemaError, match="having"):
        gemini_returning(json.dumps(bad)).ask("sys", "q", AskResponse)


def test_gemini_treats_an_exceeded_bound_as_a_retryable_schema_error():
    """Structured output constrains the JSON shape, not max_length=2 --
    so this arrives the same way on both providers, and is worth a retry."""
    bad = {**GOOD, "semantic_query": {
        **GOOD["semantic_query"],
        "dimensions": [{"field": "reading_date"}, {"field": "region"},
                       {"field": "well_type"}, {"field": "well_name"}]}}
    with pytest.raises(LLMSchemaError, match="at most 3"):
        gemini_returning(json.dumps(bad)).ask("sys", "q", AskResponse)


def test_gemini_reports_an_empty_answer_as_a_terminal_error():
    """A blank body is not a grammar miss -- retrying it just spends
    again -- so it is an LLMError, which stops the loop."""
    with pytest.raises(LLMError, match="no structured output"):
        gemini_returning("").ask("sys", "q", AskResponse)


def test_gemini_sends_the_system_prompt_and_the_grammar():
    client = gemini_returning(json.dumps(GOOD))
    client.ask("SYSTEM TEXT", "the question", AskResponse)
    kw = client._sdk.models.kw
    assert kw["model"] == "gemini-3.5-flash"
    assert kw["contents"] == "the question"
    assert kw["config"].system_instruction == "SYSTEM TEXT"
    assert kw["config"].response_mime_type == "application/json"
    assert kw["config"].response_json_schema == AskResponse.model_json_schema()


def test_the_grammar_goes_over_as_json_schema_not_the_sdks_own_type():
    """Regression, and the reason this test asserts on the payload rather
    than on a conversion.

    `response_schema` looks like the obvious choice and translates the
    model without complaint locally -- but the SDK's Schema type carries
    `additional_properties` with nowhere to put it on the wire, so
    `extra="forbid"` comes back as a 400 rather than a constraint. An
    earlier version of this test checked that local conversion, passed,
    and proved nothing: every request still failed.
    """
    client = gemini_returning(json.dumps(GOOD))
    client.ask("sys", "q", AskResponse)
    config = client._sdk.models.kw["config"]

    assert config.response_schema is None
    sent = json.dumps(config.response_json_schema)
    assert "additional_properties" not in sent
    assert '"additionalProperties": false' in sent


def test_the_grammar_reaches_gemini_with_its_bounds_intact():
    """The bounds that make a wrong answer impossible -- the two-dimension
    cap, the closed operator set, the closed grain set -- have to survive
    into the payload, or Gemini is being asked a laxer question than Claude
    and the eval comparison means nothing."""
    client = gemini_returning(json.dumps(GOOD))
    client.ask("sys", "q", AskResponse)
    schema = client._sdk.models.kw["config"].response_json_schema
    defs = schema["$defs"]

    sq = defs["SemanticQuery"]["properties"]
    assert sq["dimensions"]["maxItems"] == 3
    assert sq["measures"]["minItems"] == 1
    assert sq["limit"]["maximum"] == 10_000
    assert set(defs["Filter"]["properties"]["op"]["enum"]) == {
        "=", "!=", "in", "between", "in_year", "last_n_days"}
    grain = defs["DimensionRef"]["properties"]["grain"]
    assert set(grain["anyOf"][0]["enum"]) == {"day", "month", "quarter", "year"}
    # The polymorphic filter value is the one field that could quietly
    # collapse to a string and take numeric and list filters with it.
    value = defs["Filter"]["properties"]["value"]
    assert {b.get("type") for b in value["anyOf"]} == {
        "string", "integer", "number", "boolean", "array", "null"}
