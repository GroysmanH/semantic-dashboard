"""What the chat may see, and what it may never see.

The project's headline claim is that no row-level data reaches the model.
That still holds absolutely on the query-writing path. The chat path
relaxes it deliberately and behind two gates, so these tests pin exactly
where the line now sits — in both positions of the switch.
"""

import json
import uuid

import pytest

from app.chat.context import ContextLimits, build_context

SECRET = 987654321
OTHER_SECRET = 123456789


def card(title, *, board_id, rows=None, restatement=None, x=0, y=0,
         card_id=None, row_count=None):
    rows = [{"region": "Atyrau", "oil": SECRET}] if rows is None else rows
    return {
        "id": card_id or str(uuid.uuid4()),
        "board_id": board_id,
        "title": title,
        "layout": {"x": x, "y": y, "w": 6, "h": 10},
        "render": {
            "state": "ready",
            "restatement": restatement or f"Sum of oil production, for {title}.",
            "chart_type": "bar",
            "rows": rows,
            "row_count": len(rows) if row_count is None else row_count,
            "data_max_ts": "2026-07-31",
        },
    }


@pytest.fixture
def board_a():
    return {"id": str(uuid.uuid4()), "title": "Operations", "position": 0}


@pytest.fixture
def board_b():
    return {"id": str(uuid.uuid4()), "title": "Drilling", "position": 1}


def build(boards, active, cards, **over):
    kwargs = dict(
        boards=boards, active_board=active, rendered_cards=cards,
        messages=[], question="which region leads?", selected_card_id=None,
        share_rows=False, limits=ContextLimits(),
    )
    kwargs.update(over)
    return build_context(**kwargs)


# -- the gate ------------------------------------------------------------

def test_with_sharing_off_no_row_value_appears(board_a):
    cards = [card("Oil by region", board_id=board_a["id"])]
    built = build([board_a], board_a, cards)

    assert str(SECRET) not in built.text
    assert built.data_exposed is False


def test_with_sharing_off_structure_still_appears(board_a):
    cards = [card("Oil by region", board_id=board_a["id"])]
    built = build([board_a], board_a, cards)

    assert "Oil by region" in built.text
    assert "Sum of oil production" in built.text
    assert "Operations" in built.text
    assert '"rows":1' in built.text


def test_with_sharing_on_values_appear(board_a):
    cards = [card("Oil by region", board_id=board_a["id"])]
    built = build([board_a], board_a, cards, share_rows=True)

    assert str(SECRET) in built.text
    assert built.data_exposed is True


def test_sharing_off_leaks_nothing_from_an_inactive_dashboard(board_a, board_b):
    cards = [
        card("Oil by region", board_id=board_a["id"]),
        card("Depth by well", board_id=board_b["id"],
             rows=[{"well": "KMG-1", "depth": OTHER_SECRET}]),
    ]
    built = build([board_a, board_b], board_a, cards)

    assert str(OTHER_SECRET) not in built.text
    assert str(SECRET) not in built.text


def test_inactive_dashboards_are_listed_but_not_detailed(board_a, board_b):
    """The model needs to know other dashboards exist to answer "put this on
    the drilling board", without their contents entering the prompt."""
    cards = [
        card("Oil by region", board_id=board_a["id"]),
        card("Depth by well", board_id=board_b["id"],
             rows=[{"well": "KMG-1", "depth": OTHER_SECRET}]),
    ]
    built = build([board_a, board_b], board_a, cards, share_rows=True)

    assert "Drilling" in built.text
    assert str(OTHER_SECRET) not in built.text
    assert str(SECRET) in built.text


# -- history -------------------------------------------------------------

def message(role, say, *, board_title="Operations", data_exposed=False):
    return {"id": str(uuid.uuid4()), "role": role,
            "body": {"say": say, "action": "answer"},
            "active_board_title": board_title,
            "active_board_id": None,
            "data_exposed": data_exposed}


def test_history_carries_the_dashboard_each_turn_was_asked_on(board_a):
    """The transcript is global, so without a label a reader — and the model
    — cannot tell what "this board" meant three turns ago."""
    messages = [message("user", "what is oil doing?", board_title="Drilling")]
    built = build([board_a], board_a, [], messages=messages)

    assert "Drilling" in built.text


def test_a_turn_that_saw_data_is_dropped_once_consent_is_withdrawn(board_a):
    messages = [
        message("assistant", f"Atyrau produced {SECRET}.", data_exposed=True),
        message("user", "and gas?"),
    ]
    off = build([board_a], board_a, [], messages=messages)
    on = build([board_a], board_a, [], messages=messages, share_rows=True)

    assert str(SECRET) not in off.text
    assert "and gas?" in off.text
    assert str(SECRET) in on.text


def test_history_is_capped_at_the_configured_turn_count(board_a):
    messages = [message("user", f"question {i}") for i in range(20)]
    built = build([board_a], board_a, [], messages=messages,
                  limits=ContextLimits(history_turns=6))

    assert "question 19" in built.text
    assert "question 13" not in built.text


# -- exact rows and priority --------------------------------------------

def test_the_selected_card_gets_its_rows_before_the_others(board_a):
    wanted = card("Second", board_id=board_a["id"], y=5,
                  rows=[{"region": "A", "oil": 1}])
    cards = [card("First", board_id=board_a["id"], y=0), wanted]

    built = build([board_a], board_a, cards, share_rows=True,
                  selected_card_id=wanted["id"])

    assert built.exact_card_ids[0] == wanted["id"]


def test_a_card_named_in_the_question_outranks_the_selection(board_a):
    named = card("Downtime by rig", board_id=board_a["id"], y=9)
    selected = card("Oil by region", board_id=board_a["id"], y=0)
    cards = [selected, named]

    built = build([board_a], board_a, cards, share_rows=True,
                  question="what does downtime by rig show?",
                  selected_card_id=selected["id"])

    assert built.exact_card_ids[0] == named["id"]


def test_ties_break_on_layout_order_so_the_result_is_stable(board_a):
    top = card("Top", board_id=board_a["id"], x=0, y=0)
    bottom = card("Bottom", board_id=board_a["id"], x=0, y=10)
    built = build([board_a], board_a, [bottom, top], share_rows=True)

    assert built.exact_card_ids == (top["id"], bottom["id"])


# -- budgets -------------------------------------------------------------

def big_card(board_id, n, title="Big"):
    rows = [{"i": i, "v": i * 3} for i in range(n)]
    return card(title, board_id=board_id, rows=rows)


def test_a_card_over_the_row_budget_degrades_to_a_summary(board_a):
    cards = [big_card(board_a["id"], 3000)]
    built = build([board_a], board_a, cards, share_rows=True,
                  limits=ContextLimits(max_rows=2000))

    assert "3000" in built.text            # the row count is still stated
    # A mid-table row proves the whole table did not go in. The extremes
    # legitimately survive in the top-five, so they are the wrong probe.
    assert '"i":1500' not in built.text
    assert len(built.text) < 5_000


def test_a_summarised_card_is_disclosed_by_name(board_a):
    cards = [big_card(board_a["id"], 3000, title="Wells map")]
    built = build([board_a], board_a, cards, share_rows=True,
                  limits=ContextLimits(max_rows=2000))

    assert any("Wells map" in n for n in built.notices)
    assert any("Wells map" in n for n in [built.text])


def test_the_character_ceiling_is_respected(board_a):
    cards = [big_card(board_a["id"], 400, title=f"C{i}") for i in range(10)]
    built = build([board_a], board_a, cards, share_rows=True,
                  limits=ContextLimits(max_rows=100_000, max_chars=6_000))

    assert len(built.text) <= 6_000


def test_summaries_report_shape_in_both_modes(board_a):
    cards = [big_card(board_a["id"], 3000)]
    off = build([board_a], board_a, cards, limits=ContextLimits(max_rows=10))
    on = build([board_a], board_a, cards, share_rows=True,
               limits=ContextLimits(max_rows=10))

    for built in (off, on):
        assert "3000" in built.text

    # Only the sharing-on summary may carry statistics of the values.
    assert "min" in on.text and "max" in on.text
    assert "min" not in off.text


def test_totals_skip_nulls_and_non_finite_values(board_a):
    rows = [{"v": 10}, {"v": None}, {"v": float("nan")}, {"v": 5}]
    cards = [card("Mixed", board_id=board_a["id"], rows=rows)]
    built = build([board_a], board_a, cards, share_rows=True,
                  limits=ContextLimits(max_rows=1))

    assert "nan" not in built.text.lower()
    assert "15" in built.text


# -- determinism ---------------------------------------------------------

def test_the_same_inputs_produce_byte_identical_context(board_a, board_b):
    cards = [card("Oil by region", board_id=board_a["id"]),
             card("Gas by region", board_id=board_a["id"], y=10)]
    messages = [message("user", "hello")]
    args = dict(messages=messages, share_rows=True)

    first = build([board_a, board_b], board_a, cards, **args)
    second = build([board_a, board_b], board_a, cards, **args)

    assert first.text == second.text


def test_row_serialisation_does_not_depend_on_key_order(board_a):
    one = card("X", board_id=board_a["id"], card_id="fixed",
               rows=[{"a": 1, "b": 2}])
    two = card("X", board_id=board_a["id"], card_id="fixed",
               rows=[{"b": 2, "a": 1}])

    assert build([board_a], board_a, [one], share_rows=True).text == \
           build([board_a], board_a, [two], share_rows=True).text


def test_context_is_json_free_of_python_repr(board_a):
    """Anything not JSON-serialisable must be coerced, not repr'd: a
    datetime rendered as a Python object is noise the model has to guess at."""
    from datetime import date
    rows = [{"d": date(2026, 7, 31), "v": 1}]
    cards = [card("Dated", board_id=board_a["id"], rows=rows)]
    built = build([board_a], board_a, cards, share_rows=True)

    assert "datetime.date" not in built.text
    assert "2026-07-31" in built.text


def test_empty_cards_do_not_claim_to_have_rows(board_a):
    empty = {"id": str(uuid.uuid4()), "board_id": board_a["id"],
             "title": "", "layout": {"x": 0, "y": 0, "w": 6, "h": 10},
             "render": {"state": "empty"}}
    built = build([board_a], board_a, [empty], share_rows=True)

    assert built.exact_card_ids == ()
    assert json.dumps(built.notices) is not None


# -- the conversation remembers itself -----------------------------------
#
# The failure this section exists for: the assistant asked "do you mean the
# mean of the daily totals, or a moving average?", the person answered
# "first one", and the next turn had no record of the question. It read as
# a chat with no memory because that is what it was -- a clarifying
# question is stored under `clarify`, and the history renderer only read
# `say`.

def _messages(*turns):
    return [
        {"role": role, "body": body, "active_board_title": "Operations",
         "data_exposed": False}
        for role, body in turns
    ]


def _built(messages, question="first one"):
    from app.chat.context import ContextLimits, build_context

    return build_context(
        boards=[{"id": "b1", "title": "Operations", "position": 0}],
        active_board={"id": "b1", "title": "Operations"},
        rendered_cards=[], messages=messages, question=question,
        selected_card_id=None, share_rows=False, limits=ContextLimits(),
    )


def test_a_clarifying_question_survives_into_the_next_turn():
    built = _built(_messages(
        ("user", {"action": "ask", "say": "average daily oil for May"}),
        ("assistant", {"action": "clarify",
                       "clarify": "The mean of the daily totals, or a "
                                  "moving average?",
                       "asked": "average daily oil for May"}),
    ))

    assert "moving average?" in built.text


def test_an_outstanding_question_is_stated_as_one():
    """Not left to be inferred from a list. A one-word answer needs
    something to attach to, and the model should not have to work out
    which line of the transcript that is."""
    built = _built(_messages(
        ("user", {"action": "ask", "say": "average daily oil for May"}),
        ("assistant", {"action": "clarify", "clarify": "Mean or moving?",
                       "asked": "average daily oil for May"}),
    ))

    assert "# a question of yours is outstanding" in built.text
    assert "it was about: average daily oil for May" in built.text
    assert "Do not ask again." in built.text


def test_only_the_last_clarification_is_outstanding():
    """An older one was answered or abandoned. Carrying it forward would
    make every later turn read as a reply to it."""
    built = _built(_messages(
        ("assistant", {"action": "clarify", "clarify": "Oil or gas?"}),
        ("user", {"action": "ask", "say": "oil"}),
        ("assistant", {"action": "answer", "say": "Here is oil by region."}),
    ))

    assert "# a question of yours is outstanding" not in built.text


def test_a_refusal_survives_into_the_next_turn():
    """Same bug, same shape: a refusal keeps its sentence under `refusal`,
    so it too rendered as an empty line."""
    built = _built(_messages(
        ("user", {"action": "ask", "say": "drilling cost by region"}),
        ("assistant", {"action": "refuse",
                       "refusal": "There is no drilling-cost metric."}),
    ))

    assert "no drilling-cost metric" in built.text


def test_each_turn_says_what_kind_of_turn_it_was():
    built = _built(_messages(
        ("assistant", {"action": "clarify", "clarify": "Mean or moving?"}),
    ))

    assert "(clarify)" in built.text


def test_a_turn_with_nothing_to_say_takes_no_line():
    """A blank history line is worse than none: it implies somebody spoke
    and the record of it was lost."""
    built = _built(_messages(
        ("user", {"action": "ask", "say": "hello"}),
        ("assistant", {"action": "answer", "say": ""}),
    ))

    assert ": \n" not in built.text
    assert not any(line.endswith(": ") for line in built.text.splitlines())
