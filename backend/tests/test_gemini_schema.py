"""What actually goes over the wire to Gemini.

An earlier version of this file tested the SDK's local schema conversion
and passed while every real request 400'd. So this asserts on the payload
handed to generate_content, not on anything computed alongside it.
"""

import json

import pytest

from app.chat.schema import ChatModelResponse
from app.llm.client import GeminiClient, _gemini_schema
from app.llm.query_step import AskResponse


def sent(monkeypatch, schema_model):
    """Capture the config Gemini would receive, without a network call."""
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop here: only the payload matters")

    class FakeSDK:
        models = FakeModels()

    client = GeminiClient()
    client._sdk = FakeSDK()
    with pytest.raises(Exception):
        client.ask("system", "user", schema_model)
    return captured["config"].response_json_schema


def walk(node, seen):
    if isinstance(node, dict):
        seen.update(node.keys())
        for v in node.values():
            walk(v, seen)
    elif isinstance(node, list):
        for v in node:
            walk(v, seen)


def test_no_oneof_reaches_the_wire(monkeypatch):
    seen: set[str] = set()
    walk(sent(monkeypatch, ChatModelResponse), seen)
    assert "oneOf" not in seen
    assert "discriminator" not in seen


def test_the_union_survives_as_anyof(monkeypatch):
    seen: set[str] = set()
    walk(sent(monkeypatch, ChatModelResponse), seen)
    # The branches must still be described, or generation is unguided.
    assert "anyOf" in seen


def test_every_action_variant_is_still_described(monkeypatch):
    payload = json.dumps(sent(monkeypatch, ChatModelResponse))
    for action in ["answer", "run_query", "clarify", "refuse", "new_cards",
                   "edit_card", "new_dashboard", "layout",
                   "rename_dashboard", "reorder_dashboards",
                   "delete_card", "delete_dashboard"]:
        assert action in payload


def test_the_existing_query_schema_is_unchanged(monkeypatch):
    """AskResponse has no discriminated union, so the rewrite must be a
    no-op for it and the query path cannot regress."""
    assert sent(monkeypatch, AskResponse) == AskResponse.model_json_schema()


def test_extra_forbid_still_crosses_the_wire(monkeypatch):
    payload = json.dumps(sent(monkeypatch, ChatModelResponse))
    assert '"additionalProperties": false' in payload


def test_the_rewrite_leaves_ordinary_schemas_alone():
    plain = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert _gemini_schema(plain) == plain
