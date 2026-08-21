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
                         keys={"region": "West Kazakhstan"})],
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
        ClaimOperand(card_id=CARD, field=field, keys={"region": "Atyrau"}),
        ClaimOperand(card_id=CARD, field=field, keys={"region": "Aktobe"}),
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
                  ClaimOperand(card_id=CARD, field="oil", keys={"region": "A"}),
                  ClaimOperand(card_id=CARD, field="oil", keys={"region": "B"})])
    out = verify_turn(say="", claims=[c], rows_by_card=rows)
    assert out.claims == []


@pytest.mark.parametrize("operation", ["difference", "ratio", "percentage",
                                       "percentage_change"])
def test_two_operand_operations_reject_a_single_operand(operation):
    out = verify("", [claim("x is 1.", "1", operation=operation)])
    assert out.claims == []


# -- resolution failures -------------------------------------------------

def test_a_missing_card_withdraws():
    c = claim("Something is 1.", "1", operands=[
        ClaimOperand(card_id=uuid.uuid4(), field="oil", keys={})])
    assert verify("", [c]).claims == []


def test_a_missing_field_withdraws():
    c = claim("Something is 1.", "1", operands=[
        ClaimOperand(card_id=CARD, field="drilling_cost",
                     keys={"region": "Atyrau"})])
    assert verify("", [c]).claims == []


def test_keys_matching_more_than_one_row_withdraw():
    """An operand must address exactly one row. Two matches means the model
    does not know which number it is quoting."""
    rows = {CARD: [{"region": "A", "oil": 1}, {"region": "A", "oil": 2}]}
    c = Claim(text="A is 1.", displayed_value="1", operation="exact",
              operands=[ClaimOperand(card_id=CARD, field="oil",
                                     keys={"region": "A"})])
    assert verify_turn(say="", claims=[c], rows_by_card=rows).claims == []


def test_a_null_value_withdraws():
    c = claim("Aktobe gas was 0.", "0", operands=[
        ClaimOperand(card_id=CARD, field="gas", keys={"region": "Aktobe"})])
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
