"""The model layer, tested without spending a token.

The two prompt properties asserted here are the ones the project's claims
rest on: no row-level data reaches the model, and the prompt is byte-stable
so caching actually engages.
"""

import copy

import pytest

from app.layer.loader import synonym_index
from app.llm.client import LLMError
from app.llm.prompt import build_system_prompt
from app.llm.query_step import AskResponse, ask, deterministic_ambiguity
from app.semantic.query import SemanticQuery


class FakeClient:
    """Returns queued answers; records what it was asked."""

    def __init__(self, *answers, error: Exception | None = None):
        self.answers = list(answers)
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def ask(self, system, user, schema):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def response(entity="production", measures=("oil",), **kw):
    return AskResponse(
        semantic_query=SemanticQuery(entity=entity, measures=list(measures),
                                     **kw.pop("query", {})),
        **kw)


@pytest.fixture
def verified(layer):
    return copy.deepcopy(layer)


# -- the headline claim --------------------------------------------------

def test_the_prompt_contains_no_row_level_data(layer, warehouse_conn):
    """Sampled straight from the warehouse: no seeded value appears in what
    the model is sent."""
    prompt = build_system_prompt(layer)
    with warehouse_conn.cursor() as cur:
        cur.execute("SELECT DISTINCT well_name, region_name, field_name "
                    "FROM ddh.dim_wells LIMIT 40")
        rows = cur.fetchall()
        cur.execute("SELECT DISTINCT contractor FROM ddh.fct_well_interventions")
        contractors = [r[0] for r in cur.fetchall()]

    for well, region, field in rows:
        assert well not in prompt          # NF-0001 etc
        assert region not in prompt        # Mangystau etc
        assert field not in prompt         # Uzen etc
    for c in contractors:
        assert c not in prompt


def test_declared_value_domains_are_schema_not_data(layer):
    """status codes are part of the layer definition, so they do appear --
    that is the low_cardinality_values level, and it is deliberate."""
    prompt = build_system_prompt(layer)
    assert "COMPLETED" in prompt


def test_the_prompt_is_byte_stable(layer):
    """Without this, prompt caching silently never hits."""
    assert build_system_prompt(layer) == build_system_prompt(layer)


def test_the_prompt_never_contains_sql(layer):
    """The model is given vocabulary, not a schema to write SQL against.
    ('join' as an English word is fine -- the preamble forbids inventing
    one; what must not appear is a SQL construct.)"""
    prompt = build_system_prompt(layer).lower()
    for token in ("select ", "group by", "left join", "from ddh.", "date_trunc"):
        assert token not in prompt


def test_the_prompt_names_no_physical_columns(layer):
    """The model works in layer vocabulary: net_gain, never net_gain_bbl."""
    prompt = build_system_prompt(layer)
    for entity in layer.values():
        for m in entity.measures.values():
            if m.column != "*":
                assert m.column not in prompt
        assert entity.table not in prompt


def test_reviewed_operational_layer_has_no_manager_facing_confidence_block(layer):
    assert "unverified fields" not in build_system_prompt(layer)


# -- deterministic ambiguity --------------------------------------------

def test_a_term_meaning_two_measures_is_flagged(verified):
    """'gain' maps to both net_gain and avg_net_gain, so the question is
    asked back rather than guessed at."""
    l = copy.deepcopy(verified)
    l["well_interventions"].synonyms["avg_net_gain"] = ["gain"]
    syn = synonym_index(l)
    flagged = deterministic_ambiguity("show me gain by region",
                                      "well_interventions", syn, l)
    assert flagged is not None
    assert set(flagged.candidates) == {"net_gain", "avg_net_gain"}


def test_an_unambiguous_term_is_not_flagged(verified):
    syn = synonym_index(verified)
    assert deterministic_ambiguity("uplift by region", "well_interventions",
                                   syn, verified) is None


def test_the_guard_matches_whole_words_only(verified):
    l = copy.deepcopy(verified)
    l["production"].synonyms["avg_oil"] = ["oil"]
    syn = synonym_index(l)
    assert deterministic_ambiguity("show gasket wear", "production", syn, l) is None
    assert deterministic_ambiguity("show oil by region", "production", syn, l) is not None


# -- the ask pipeline ----------------------------------------------------

def test_a_valid_answer_is_returned(verified):
    client = FakeClient(response(title="Oil"))
    out = ask("oil please", verified, client)
    assert out.query.entity == "production"
    assert out.attempts == 1


def test_an_invalid_answer_is_retried_once_with_the_reason(verified):
    bad = response(measures=("revenue",))
    good = response()
    client = FakeClient(bad, good)
    out = ask("oil please", verified, client)
    assert out.query is not None
    assert out.attempts == 2
    assert "revenue" in client.calls[1][1]      # the reason was fed back


def test_two_failures_refuse_rather_than_roll_again(verified):
    client = FakeClient(response(measures=("revenue",)), response(measures=("profit",)))
    out = ask("revenue please", verified, client)
    assert out.query is None
    assert "profit" in out.refusal
    assert out.attempts == 2


def test_the_confidence_gate_refuses_without_a_retry(layer):
    """No amount of rephrasing fixes an unverified layer, so do not spend a
    second call pretending otherwise."""
    gated = copy.deepcopy(layer)
    gated["well_interventions"].dimensions["status"].confidence = "low"
    client = FakeClient(response(entity="well_interventions", measures=("n_jobs",)))
    out = ask("how many jobs", gated, client)
    assert out.refusal is not None
    assert "dimension status" in out.refusal
    assert out.attempts == 1


def test_a_model_reported_ambiguity_asks_back(verified):
    from app.llm.query_step import Ambiguity

    client = FakeClient(response(ambiguity=Ambiguity(
        term="performance", candidates=["net_gain", "cost"],
        question="Do you mean net gain or cost?")))
    out = ask("best performance by region", verified, client)
    assert out.query is None
    assert out.clarify == "Do you mean net gain or cost?"


def test_a_clarifying_question_that_echoes_the_request_is_rewritten(verified):
    """Observed live: asked what was ambiguous, a model filled `question`
    with a verbatim copy of what the person had typed. The card then showed
    somebody their own sentence and waited, which is the one outcome worse
    than not asking. `term` and `candidates` survive that carelessness, so
    a usable question is built from them instead."""
    from app.llm.query_step import Ambiguity

    client = FakeClient(response(ambiguity=Ambiguity(
        term="top", candidates=["oil", "gas"],
        question="what give me top 1 well in each region")))
    out = ask("what give me top 1 well in each region", verified, client)

    assert out.clarify == "By 'top' do you mean oil production or gas production?"


def test_a_clarifying_question_with_nothing_to_rebuild_from_still_asks(verified):
    """No candidates and no labels left, but naming the word is still a
    question somebody can answer."""
    from app.llm.query_step import Ambiguity

    client = FakeClient(response(ambiguity=Ambiguity(
        term="best", candidates=[], question="best wells")))
    out = ask("best wells", verified, client)

    assert out.clarify == "What do you mean by 'best'?"


def test_a_real_clarifying_question_is_left_alone(verified):
    """The guard replaces what the model wrote only when it said nothing."""
    from app.llm.query_step import Ambiguity

    client = FakeClient(response(ambiguity=Ambiguity(
        term="performance", candidates=["oil", "gas"],
        question="Do you mean oil or gas?")))
    out = ask("best performance by region", verified, client)

    assert out.clarify == "Do you mean oil or gas?"


def test_an_edit_that_answers_a_question_carries_both(verified):
    """The card's query and the exchange it is in the middle of are two
    different facts and the model needs both. Composing them used to be a
    reassignment, so the exchange was silently dropped and a card with a
    chart on it asked a question and then forgot it had."""
    current = SemanticQuery(entity="production", measures=["oil"])
    client = FakeClient(response(measures=("oil",)))
    ask("oil", verified, client, current=current,
        clarifying={"kind": "clarify", "question": "Oil or gas?",
                    "asked": "break it down by product"})

    sent = client.calls[0][1]
    assert "Oil or gas?" in sent
    assert "break it down by product" in sent
    assert "The card currently shows" in sent


def test_a_reply_to_a_refusal_is_framed_as_one(verified):
    """A refusal is not a question, so replaying it as one would tell the
    model it had asked something and put words in the person's mouth."""
    client = FakeClient(response())
    ask("then rank them overall", verified, client,
        clarifying={"kind": "refused",
                    "question": "The layer has no per-region ranking.",
                    "asked": "top well in each region"})

    sent = client.calls[0][1]
    assert "could not answer" in sent
    assert "The layer has no per-region ranking." in sent
    assert "They have replied: then rank them overall" in sent
    assert "Do not ask again" not in sent


def test_replying_to_a_refusal_may_still_be_ambiguous(verified):
    """Answering a question means it must not be asked again. Replying to a
    refusal is a fresh request, and gets the same ambiguity protection as
    any other."""
    from app.llm.query_step import Ambiguity

    client = FakeClient(response(ambiguity=Ambiguity(
        term="production", candidates=["oil", "gas"],
        question="Oil or gas?")))
    out = ask("show production instead", verified, client,
              clarifying={"kind": "refused", "question": "No such ranking.",
                          "asked": "top well in each region"})

    assert out.clarify == "Oil or gas?"


def test_an_unreachable_model_refuses_clearly(verified):
    client = FakeClient(error=LLMError("The model service could not be reached."))
    out = ask("oil", verified, client)
    assert out.refusal == "The model service could not be reached."
    assert out.replyable is False


def test_refinement_sends_the_card_state_not_a_conversation(verified):
    """The card's current query is the whole context, which sidesteps
    multi-turn drift."""
    current = SemanticQuery(entity="production", measures=["oil"])
    client = FakeClient(response(measures=("oil", "gas")))
    ask("add gas", verified, client, current=current)
    sent = client.calls[0][1]
    assert '"entity": "production"' in sent
    assert "complete replacement" in sent


def test_the_layer_prompt_is_identical_across_calls(verified):
    """It rides behind the cache breakpoint, so any per-call variation would
    silently cost full price every time."""
    client = FakeClient(response(), response())
    ask("oil", verified, client)
    ask("gas", verified, client)
    assert client.calls[0][0] == client.calls[1][0]


@pytest.mark.parametrize("key_var", ["ANTHROPIC_API_KEY", "GOOGLE_API_KEY"])
@pytest.mark.parametrize("status,fragment", [
    (401, "credential was rejected"),
    (404, "not available on this account"),
    (429, "rate limited"),
    (503, "unavailable"),
])
def test_model_errors_are_written_for_the_person_reading_them(status, fragment,
                                                             key_var):
    """A raw provider payload in the card is noise to a manager. Both
    providers get the same sentences, naming their own key variable."""
    from app.llm.client import _explain

    message = _explain(status, "claude-sonnet-5", key_var)
    assert fragment in message
    assert "{" not in message and "'type'" not in message


def test_an_out_of_grammar_answer_is_retried_then_refused(verified):
    """Three dimensions is unrepresentable, not merely wrong. Structured
    output does not enforce every schema bound, so this arrives as a
    validation failure and must refuse rather than crash the caller."""
    from app.llm.client import LLMSchemaError

    client = FakeClient(error=LLMSchemaError(
        "semantic_query.dimensions: List should have at most 2 items"))
    out = ask("oil by month, region and well type", verified, client)
    assert out.query is None
    assert out.refusal == "I couldn’t prepare that query safely. Nothing changed."
    assert out.failure_code == "model_format_invalid"
    assert "at most 2 items" not in out.refusal
    assert out.attempts == 2          # retried once, then gave up


def test_a_schema_failure_then_a_good_answer_succeeds(verified):
    from app.llm.client import LLMSchemaError

    class Flaky:
        def __init__(self):
            self.calls = []

        def ask(self, system, user, schema):
            self.calls.append(user)
            if len(self.calls) == 1:
                raise LLMSchemaError("dimensions: at most 2 items")
            return response()

    client = Flaky()
    out = ask("oil by lots of things", verified, client)
    assert out.query is not None
    assert "at most 2 items" in client.calls[1]     # the reason was fed back


def test_semantic_failure_then_terminal_schema_failure_is_always_safe(verified):
    from app.llm.client import LLMSchemaError

    client = FakeClient(
        response(measures=("not_a_real_measure",)),
        LLMSchemaError("raw terminal schema path and provider payload"),
    )

    out = ask("show an unavailable measure", verified, client)

    assert out.refusal == "I couldn’t prepare that query safely. Nothing changed."
    assert out.failure_code == "model_format_invalid"
    assert "not_a_real_measure" not in out.refusal
    assert "provider payload" not in out.refusal


def test_schema_failure_then_terminal_semantic_failure_uses_only_safe_semantics(
    verified,
):
    from app.llm.client import LLMSchemaError

    client = FakeClient(
        LLMSchemaError("raw first schema path and provider payload"),
        response(measures=("not_a_real_measure",)),
    )

    out = ask("show an unavailable measure", verified, client)

    assert out.failure_code is None
    assert "not_a_real_measure" in (out.refusal or "")
    assert "schema path" not in (out.refusal or "")
    assert "provider payload" not in (out.refusal or "")


def test_code_vocabularies_are_declared_but_names_are_not(layer):
    """Where the line sits, made explicit so a later change cannot quietly
    cross it: a closed set of codes (PRODUCER, COMPLETED, FRAC) is schema and
    belongs in the layer. The names of real wells, fields and regions are
    data -- they reveal where a company operates -- and declaring them would
    put warehouse values in every prompt."""
    NAME_DIMENSIONS = {
        "region", "field_name", "well_name", "contractor", "asset",
        "operator", "basin",
    }

    for entity in layer.values():
        for name, dim in entity.dimensions.items():
            if name in NAME_DIMENSIONS:
                assert dim.values is None, (
                    f"{entity.name}.{name} declares values; those are the names "
                    f"of real things and would reach the model")

    well_type = layer["production"].dimensions["well_type"]
    assert well_type.values == ["PRODUCER", "INJECTOR", "OBSERVATION"]


def test_prompt_contains_no_geo_columns():
    """Coordinates are a rendering detail, not vocabulary. If they reached
    the prompt the model could ask for "oil by latitude", which compiles
    cleanly, executes cleanly and means nothing -- a wrong chart made
    entirely of valid fields."""
    from app.deps import LAYER
    from app.llm.prompt import build_system_prompt

    prompt = build_system_prompt(LAYER)
    assert LAYER["production"].geo is not None, "the guard needs something to guard"
    for leak in ("latitude", "longitude", "wells.latitude", "wells.longitude"):
        assert leak not in prompt


# -- date grounding -------------------------------------------------------

def test_the_system_prompt_carries_no_date():
    """The caching guarantee. The system block sits behind a cache
    breakpoint, so a date in it would invalidate that cache once a day for
    every user, forever -- and the failure would be a bill, not an error."""
    import re

    from app.deps import LAYER
    from app.llm.prompt import build_system_prompt

    prompt = build_system_prompt(LAYER)
    assert build_system_prompt(LAYER) == prompt
    assert not re.search(r"\b(19|20)\d{2}\b", prompt)


def test_the_question_carries_todays_date(verified):
    """Without an anchor a model resolves "last year" against its training
    cutoff. Gemini answered 2023 for a question whose answer was 2025."""
    from datetime import date

    client = FakeClient(response())
    ask("oil last year", verified, client, today=date(2026, 8, 19))

    system, user = client.calls[0][0], client.calls[0][1]
    assert "19 August 2026" in user
    assert "19 August 2026" not in system


def test_a_refinement_still_gets_the_date_and_the_card(verified):
    """Both contexts, once each -- an edit needs to know what "this year"
    means just as much as a fresh question does."""
    from datetime import date

    client = FakeClient(response())
    current = SemanticQuery.model_validate(
        {"entity": "production", "measures": ["oil"]})
    ask("filter it to this year", verified, client, current=current,
        today=date(2026, 8, 19))

    user = client.calls[0][1]
    assert user.count("19 August 2026") == 1
    assert "The card currently shows" in user
