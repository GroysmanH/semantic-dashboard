"""The chat system prompt.

Same two properties the query prompt is held to: it carries no data, and it
is byte-stable so the cached prefix can actually hit. It additionally must
not carry anything that changes between turns — a board title, a consent
state or a provider name in here would invalidate the cache every turn and
tell the model something it must not assume.
"""

import re

import pytest

from app.chat.prompt import build_chat_system_prompt


@pytest.fixture
def prompt(layer):
    return build_chat_system_prompt(layer)


def test_it_is_byte_stable(layer):
    assert build_chat_system_prompt(layer) == build_chat_system_prompt(layer)


def test_it_carries_no_date(prompt):
    # A date here would invalidate the cached prefix once a day forever.
    assert not re.search(r"\b20\d{2}\b", prompt)


def test_it_names_every_action(prompt):
    for action in ["answer", "run_query", "clarify", "refuse", "new_cards",
                   "edit_card", "new_dashboard", "layout",
                   "rename_dashboard", "reorder_dashboards",
                   "delete_card", "delete_dashboard"]:
        assert action in prompt


def test_it_states_that_the_model_does_not_apply_changes(prompt):
    lowered = prompt.lower()
    assert "confirm" in lowered
    assert "propose" in lowered or "intent" in lowered


def test_it_describes_the_numeric_claim_rule(prompt):
    lowered = prompt.lower()
    assert "claim" in lowered
    assert "one number" in lowered or "exactly one" in lowered


def test_it_explains_the_data_off_behaviour(prompt):
    # Consent state is not in the prompt, so the model is told how to behave
    # when values are absent rather than being told whether they are.
    assert "values" in prompt.lower()


def test_it_carries_no_consent_or_provider_state(prompt):
    lowered = prompt.lower()
    for leak in ["anthropic", "gemini", "openai", "nvidia", "deepseek",
                 "claude", "consent granted", "sharing is on"]:
        assert leak not in lowered


def test_it_names_no_board_or_card(prompt):
    for leak in ["operations", "drilling", "card_id", "board_id"]:
        assert leak not in prompt.lower()


def test_it_contains_no_sql(prompt):
    lowered = prompt.lower()
    assert "select " not in lowered
    assert "group by" not in lowered


def test_it_contains_no_row_level_data(prompt, layer):
    """The same claim the query prompt makes. Declared value domains are
    schema; a well name or a measurement is not."""
    assert "kmg-" not in prompt.lower()
    assert not re.search(r"\b\d{4,}\b", prompt)


def test_it_states_the_inactive_dashboard_limit(prompt):
    assert "active" in prompt.lower()


def test_it_lists_the_layer_entities(prompt, layer):
    for name in layer:
        assert name in prompt
