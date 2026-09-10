"""The SQL a card shows should be SQL you can run.

What executes is always the parameterised form -- values travel beside
the query, never inside it, which is why nothing a person or a model
types can change what a query does. The cost is that the statement is not
valid SQL on its own: paste it into a database client and it stops at the
first `%s`, which is exactly what happened the first time somebody tried.

So the panel gets a second rendering with the values written in, quoted
by psycopg rather than by hand. What is tested here is that the two stay
different things: the readable one really runs, and the one that really
runs still binds.
"""

import pytest

from app.deps import LAYER
from app.db import warehouse_pool
from app.semantic.compile import compile_query
from app.semantic.query import SemanticQuery

QUERY = {
    "entity": "production",
    "measures": ["oil"],
    "dimensions": [{"field": "region"}],
    "filters": [{"field": "well_type", "op": "=", "value": "PRODUCER"}],
}


@pytest.fixture
def compiled():
    return compile_query(SemanticQuery.model_validate(QUERY), LAYER)


def test_the_shown_sql_has_no_placeholders_left(compiled):
    assert "%s" in compiled.sql
    assert "%s" not in compiled.sql_readable


def test_the_shown_sql_carries_the_values(compiled):
    assert "'PRODUCER'" in compiled.sql_readable
    assert "PRODUCER" not in compiled.sql


def test_what_executes_still_binds(compiled):
    """The whole guarantee. If this ever compiled values into the text,
    the injection protection would be gone with it."""
    assert compiled.params
    for value in compiled.params:
        assert str(value) not in compiled.sql


def test_the_shown_sql_actually_runs_and_agrees(compiled):
    """Not 'looks runnable'. Run it, and check it answers the same."""
    with warehouse_pool.connection() as conn:
        bound = conn.execute(compiled.sql, compiled.params).fetchall()
        shown = conn.execute(compiled.sql_readable).fetchall()
    assert bound == shown
    assert bound, "fixture query returned nothing; the test proves little"


def test_a_value_containing_a_quote_is_escaped_not_concatenated():
    """The readable form is display output, but it is still handed to a
    database by whoever copies it. Naive concatenation would turn a name
    with an apostrophe into a syntax error at best."""
    q = SemanticQuery.model_validate(
        QUERY | {"filters": [{"field": "region", "op": "=",
                              "value": "O'Brien Field"}]})
    readable = compile_query(q, LAYER).sql_readable
    assert "'O''Brien Field'" in readable


def test_the_card_is_shown_the_readable_form():
    from app.render import render

    result = render(SemanticQuery.model_validate(QUERY), LAYER)
    assert result.compiled_sql
    assert "%s" not in result.compiled_sql
