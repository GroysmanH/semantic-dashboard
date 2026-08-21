"""Numeric claims are recomputed, never trusted.

The restatement exists because a second thing that can lie is not a
safeguard. Prose about numbers is exactly that second thing, so every
figure the chat states is resolved back to a row and the arithmetic redone.
A claim that does not check out is removed, not styled as doubtful.
"""

import uuid

import pytest

from app.chat.schema import Claim, ClaimOperand
from app.chat.verify import verify_turn

CARD = uuid.uuid4()
OTHER = uuid.uuid4()

ROWS = {
    CARD: [
        {"region": "West Kazakhstan", "oil": 12995827.76, "gas": 54670300},
        {"region": "Atyrau", "oil": 7920646.47, "gas": 11000000},
        {"region": "Aktobe", "oil": 8638069.48, "gas": None},
    ],
    OTHER: [{"well": "KMG-0163", "oil": 600000}],
}


def claim(text, value, operation="exact", operands=None):
    return Claim(
        text=text, displayed_value=value, operation=operation,
        operands=operands or [
            ClaimOperand(card_id=CARD, field="oil",
                         row=0)],
    )


def verify(say, claims):
    return verify_turn(say=say, claims=claims, rows_by_card=ROWS)


UNVERIFIED = "I couldn’t verify that number from the visible card data."


# -- exact figures -------------------------------------------------------

def test_an_exact_figure_present_in_the_rows_is_kept():
    out = verify("", [claim("West Kazakhstan produced 12995827.76 barrels.",
                            "12995827.76")])
    assert len(out.claims) == 1
    assert out.withdrawn == []


def test_comma_grouping_is_accepted():
    out = verify("", [claim("West Kazakhstan produced 12,995,827.76 barrels.",
                            "12,995,827.76")])
    assert len(out.claims) == 1


@pytest.mark.parametrize("shown", ["13M", "13.0M", "12995.83K"])
def test_magnitude_suffixes_are_normalised(shown):
    out = verify("", [claim(f"West Kazakhstan produced {shown} barrels.",
                            shown, operation="rounded")])
    assert len(out.claims) == 1, f"{shown} should verify"


def test_a_rounded_figure_is_accepted_at_its_own_precision():
    out = verify("", [claim("West Kazakhstan produced 12995827.8 barrels.",
                            "12995827.8", operation="rounded")])
    assert len(out.claims) == 1


def test_an_invented_figure_is_withdrawn():
    out = verify("", [claim("West Kazakhstan produced 99999999 barrels.",
                            "99999999")])
    assert out.claims == []
    assert out.withdrawn == ["West Kazakhstan produced 99999999 barrels."]


def test_a_number_close_but_materially_different_is_withdrawn():
    # Half a unit in the last displayed place, never a relative window that
    # would wave through a difference of thousands.
    out = verify("", [claim("West Kazakhstan produced 12995900 barrels.",
                            "12995900", operation="rounded")])
    assert out.claims == []


# -- derived figures -----------------------------------------------------

def two(field="oil"):
    return [
        ClaimOperand(card_id=CARD, field=field, row=1),
        ClaimOperand(card_id=CARD, field=field, row=2),
    ]


def test_a_sum_is_recomputed():
    total = 7920646.47 + 8638069.48
    out = verify("", [claim(f"Together they produced {total}.", str(total),
                            operation="sum", operands=two())])
    assert len(out.claims) == 1


def test_a_difference_is_recomputed():
    # Stated at the precision the data supports. Computing this in float
    # gives -717423.0100000007, which is the next test.
    out = verify("", [claim("The gap is -717423.01.", "-717423.01",
                            operation="difference", operands=two())])
    assert len(out.claims) == 1


def test_spurious_precision_is_withdrawn():
    """A figure carrying ten decimal places the data cannot support is
    making a precision claim that is false, even though it is nearly the
    right number. Tolerance is half a unit of the last place *shown*."""
    out = verify("", [claim("The gap is -717423.0100000007.",
                            "-717423.0100000007",
                            operation="difference", operands=two())])
    assert out.claims == []


def test_a_ratio_is_recomputed():
    out = verify("", [claim("Atyrau is 0.917 of Aktobe.", "0.917",
                            operation="ratio", operands=two())])
    assert len(out.claims) == 1


def test_a_percentage_is_recomputed():
    out = verify("", [claim("Atyrau is 91.7% of Aktobe.", "91.7%",
                            operation="percentage", operands=two())])
    assert len(out.claims) == 1


def test_a_wrong_percentage_is_withdrawn():
    out = verify("", [claim("Atyrau is 12% of Aktobe.", "12%",
                            operation="percentage", operands=two())])
    assert out.claims == []


def test_a_zero_denominator_withdraws_rather_than_raising():
    rows = {CARD: [{"region": "A", "oil": 5}, {"region": "B", "oil": 0}]}
    c = Claim(text="A is 100% of B.", displayed_value="100%",
              operation="percentage", operands=[
                  ClaimOperand(card_id=CARD, field="oil", row=0),
                  ClaimOperand(card_id=CARD, field="oil", row=1)])
    out = verify_turn(say="", claims=[c], rows_by_card=rows)
    assert out.claims == []


@pytest.mark.parametrize("operation", ["difference", "ratio", "percentage",
                                       "percentage_change"])
def test_two_operand_operations_reject_a_single_operand(operation):
    out = verify("", [claim("x is 1.", "1", operation=operation)])  # one operand
    assert out.claims == []


# -- resolution failures -------------------------------------------------

def test_a_row_that_does_not_exist_withdraws():
    c = claim("Something is 1.", "1", operands=[
        ClaimOperand(card_id=CARD, field="oil", row=99)])
    assert verify("", [c]).claims == []


def test_a_negative_row_cannot_be_expressed():
    with pytest.raises(Exception):
        ClaimOperand(card_id=CARD, field="oil", row=-1)


def test_a_single_row_card_is_addressed_as_row_zero():
    rows = {CARD: [{"total": 42}]}
    c = Claim(text="The total is 42.", displayed_value="42",
              operation="exact",
              operands=[ClaimOperand(card_id=CARD, field="total", row=0)])
    assert len(verify_turn(say="", claims=[c], rows_by_card=rows).claims) == 1


def test_a_missing_card_withdraws():
    c = claim("Something is 1.", "1", operands=[
        ClaimOperand(card_id=uuid.uuid4(), field="oil", row=0)])
    assert verify("", [c]).claims == []


def test_a_missing_field_withdraws():
    c = claim("Something is 1.", "1", operands=[
        ClaimOperand(card_id=CARD, field="drilling_cost",
                     row=1)])
    assert verify("", [c]).claims == []





def test_a_null_value_withdraws():
    c = claim("Aktobe gas was 0.", "0", operands=[
        ClaimOperand(card_id=CARD, field="gas", row=2)])
    assert verify("", [c]).claims == []


def test_a_claim_whose_sentence_omits_its_number_is_withdrawn():
    out = verify("", [claim("West Kazakhstan led the country.",
                            "12995827.76")])
    assert out.claims == []


def test_a_sentence_with_two_numbers_is_withdrawn():
    # One figure per claim, so each one is individually checkable.
    out = verify("", [claim("It rose from 7920646.47 to 12995827.76.",
                            "12995827.76")])
    assert out.claims == []


# -- prose scrubbing -----------------------------------------------------

def test_a_bare_number_in_say_is_refused():
    out = verify("Production reached 42000000 barrels.", [])
    assert "42000000" not in out.safe_say
    assert UNVERIFIED in out.safe_say


def test_a_date_in_say_is_not_treated_as_a_claim():
    out = verify("Data runs through 2026-07-31.", [])
    assert "2026-07-31" in out.safe_say
    assert UNVERIFIED not in out.safe_say


def test_prose_with_no_numbers_passes_through():
    out = verify("West Kazakhstan leads the other regions.", [])
    assert out.safe_say == "West Kazakhstan leads the other regions."
    assert UNVERIFIED not in out.safe_say


def test_the_disclaimer_appears_once_however_many_failed():
    out = verify("", [claim("A is 99999999.", "99999999"),
                      claim("B is 88888888.", "88888888")])
    assert out.safe_say.count(UNVERIFIED) == 1


def test_a_verified_number_may_appear_in_say():
    out = verify("West Kazakhstan produced 12995827.76 barrels.",
                 [claim("West Kazakhstan produced 12995827.76 barrels.",
                        "12995827.76")])
    assert "12995827.76" in out.safe_say
    assert UNVERIFIED not in out.safe_say


def test_sources_name_the_card_a_figure_came_from():
    out = verify("", [claim("West Kazakhstan produced 12995827.76 barrels.",
                            "12995827.76")])
    assert out.claims[0].source_card_ids == [CARD]


# -- magnitude words -----------------------------------------------------

@pytest.mark.parametrize("text,shown", [
    ("West Kazakhstan produced 13.0 million barrels.", "13.0M"),
    ("West Kazakhstan produced about 13 million barrels.", "13M"),
    ("West Kazakhstan produced 12995.83 thousand barrels.", "12995.83K"),
], ids=["million", "bare-million", "thousand"])
def test_a_magnitude_written_as_a_word_is_the_same_figure(text, shown):
    """A model writes "13.0 million" in the sentence and "13.0M" as the
    value. That is one number spelled two ways, and rejecting it would
    withdraw correct answers for a formatting difference."""
    assert len(verify("", [claim(text, shown, operation="rounded")]).claims) == 1


def test_a_word_magnitude_that_is_still_wrong_is_withdrawn():
    # Normalising the spelling must not normalise away the check.
    out = verify("", [claim("West Kazakhstan produced 40 million barrels.",
                            "40M", operation="rounded")])
    assert out.claims == []


def test_a_year_followed_by_a_word_is_not_treated_as_a_magnitude():
    out = verify("Data runs through 2026-07-31.", [])
    assert UNVERIFIED not in out.safe_say


def test_a_decimal_point_is_not_a_sentence_boundary():
    """Splitting on a bare "." chopped "25.4%" in half and left "4% of
    total production" standing as an assertion with no evidence."""
    out = verify("West Kazakhstan led, at 25.4% of total production.", [])
    assert "4%" not in out.safe_say
    assert "25.4" not in out.safe_say
    assert "total production" not in out.safe_say


def test_a_clean_sentence_survives_beside_a_scrubbed_one():
    out = verify("West Kazakhstan leads. It produced 42000000 barrels.", [])
    assert "West Kazakhstan leads." in out.safe_say
    assert "42000000" not in out.safe_say


def test_a_word_magnitude_in_unverified_prose_is_scrubbed():
    """Stray figures are found in collapsed form ("7.92m"); matching that
    against the original prose ("7.92 million") never fires, so the
    unverified number survived into the answer."""
    out = verify("Atyrau produced 7.92 million barrels.", [])
    assert "7.92" not in out.safe_say
    assert "million" not in out.safe_say
    assert UNVERIFIED in out.safe_say


def test_prose_may_round_what_the_declared_value_states_exactly():
    """A model writes "7.92 million" in the sentence and the exact figure
    as the value. Both are true; each is checked at its own precision."""
    out = verify("", [Claim(
        text="Atyrau produced 7.92 million barrels.",
        displayed_value="7920646.47", operation="exact",
        operands=[ClaimOperand(card_id=CARD, field="oil",
                               row=1)])])
    assert len(out.claims) == 1


def test_rounded_prose_that_is_actually_wrong_is_still_withdrawn():
    out = verify("", [Claim(
        text="Atyrau produced 4.10 million barrels.",
        displayed_value="7920646.47", operation="exact",
        operands=[ClaimOperand(card_id=CARD, field="oil",
                               row=1)])])
    assert out.claims == []


def test_a_share_stored_as_a_fraction_may_be_stated_as_a_percentage():
    """A percent_of_total column holds 0.2644; a model writes "26.4%".
    Same number, two conventions."""
    rows = {CARD: [{"region": "West Kazakhstan", "share": 0.2644649630068269}]}
    c = Claim(text="West Kazakhstan is 26.4% of the total.",
              displayed_value="26.4%", operation="exact",
              operands=[ClaimOperand(card_id=CARD, field="share", row=0)])
    assert len(verify_turn(say="", claims=[c], rows_by_card=rows).claims) == 1


def test_the_fraction_reading_needs_an_actual_percent_sign():
    # Without a % the second reading is not offered, so a figure that is
    # merely a hundred times off stays withdrawn.
    rows = {CARD: [{"region": "A", "share": 0.2644}]}
    c = Claim(text="A is 26.4 of the total.", displayed_value="26.4",
              operation="exact",
              operands=[ClaimOperand(card_id=CARD, field="share", row=0)])
    assert verify_turn(say="", claims=[c], rows_by_card=rows).claims == []
