"""A partial answer must say that it is partial.

"Production by company" against a real warehouse returned a hundred rows
of a hundred and twenty-five companies -- 71% of the actual total -- and
the card reported "100 rows" and nothing else. Nobody reading it had any
way to know a quarter of the volume was missing, because a hundred rows
looks exactly like a complete answer that happens to have a hundred rows.

The grand total was never affected: LIMIT bounds output rows, and a
question with no dimensions produces one. It is breakdowns that truncate.
That distinction is worth a test of its own, because it is the difference
between "the app under-reports" and "the app under-reports when you cut
by something wide".
"""

from app.deps import LAYER
from app.render import render
from app.semantic.compile import compile_query
from app.semantic.query import SemanticQuery


def _render(**q):
    return render(SemanticQuery.model_validate(
        {"entity": "production", "measures": ["oil"], **q}), LAYER)


def test_a_complete_answer_does_not_claim_to_be_partial():
    r = _render(dimensions=[{"field": "region"}])
    assert r.state == "ready"
    assert r.truncated is False
    assert "more exist" not in r.restatement


def test_a_truncated_answer_says_so_in_the_sentence():
    r = _render(dimensions=[{"field": "well_name"}], limit=3)
    assert r.truncated is True
    assert r.row_count == 3
    assert "showing the top 3; more rows exist" in r.restatement


def test_the_probe_row_is_never_shown():
    """One row over the limit is fetched. Exactly one row over the limit
    is dropped -- a card that displayed 4 rows for limit=3 would be a
    worse bug than the one this fixes."""
    r = _render(dimensions=[{"field": "well_name"}], limit=3)
    assert len(r.rows) == 3


def test_a_collapsed_chart_and_a_truncated_query_read_as_one_sentence():
    """A chart that collapsed its own tail already says "top 30 of 100
    shown". Following that with "top 100, more exist" hands the reader two
    top-somethings to reconcile."""
    r = _render(dimensions=[{"field": "well_name"}], limit=40)
    assert r.truncated is True
    assert "out of more than 40 rows" in r.restatement
    assert "; more rows exist" not in r.restatement


def test_an_exact_fit_is_not_reported_as_truncated():
    """The reason for fetching a probe row rather than comparing the row
    count to the limit: a result that happens to be exactly the limit is
    complete, and saying "more exist" would be a lie."""
    whole = _render(dimensions=[{"field": "region"}], limit=10_000)
    exact = _render(dimensions=[{"field": "region"}], limit=len(whole.rows))
    assert exact.truncated is False
    assert "more exist" not in exact.restatement


def test_a_grand_total_is_never_truncated():
    """LIMIT bounds output rows, and this question has one. The total is
    over every row the filters allow, however many companies that is."""
    r = _render(dimensions=[])
    assert r.truncated is False
    assert r.row_count == 1


def test_the_total_equals_the_sum_of_the_whole_breakdown():
    """The claim above, checked against real numbers rather than asserted
    from how LIMIT is supposed to work."""
    total = _render(dimensions=[])
    parts = _render(dimensions=[{"field": "region"}], limit=10_000)
    assert parts.truncated is False
    assert abs(total.rows[0]["oil"] - sum(r["oil"] for r in parts.rows)) < 1


def test_the_truncation_survives_the_cache():
    """The cache is what the card draws from on every reload after the
    first. A cached partial answer is still a partial answer."""
    first = _render(dimensions=[{"field": "well_name"}], limit=3)
    again = render(SemanticQuery.model_validate(
        {"entity": "production", "measures": ["oil"],
         "dimensions": [{"field": "well_name"}], "limit": 3}),
        LAYER, cache=first.cache)
    assert again.from_cache is True
    assert again.truncated is True
    assert "showing the top 3; more rows exist" in again.restatement
