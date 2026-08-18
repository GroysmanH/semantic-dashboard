"""The restatement is the manager's only window onto what the chart means,
so it is generated from the query object -- never by a model."""

import copy
from datetime import date

import pytest

from app.semantic.query import DimensionRef, Filter, OrderBy, SemanticQuery
from app.semantic.restate import restate


@pytest.fixture
def entity(layer):
    e = copy.deepcopy(layer)["well_interventions"]
    e.dimensions["status"].confidence = "high"
    return e


def r(entity, **kw):
    base = {"entity": "well_interventions", "measures": ["net_gain"]}
    return restate(SemanticQuery(**{**base, **kw}), entity, **kw.pop("_tail", {}))


def test_measure_only(entity):
    assert r(entity) == "Sum of net gain, from Well Interventions."


def test_aggregation_is_named_explicitly(entity):
    assert r(entity, measures=["avg_net_gain"]).startswith("Average net gain per job")
    assert r(entity, measures=["n_jobs"]).startswith("Count of jobs")


def test_no_label_repeats_its_own_aggregation(layer):
    """'Average average net gain' is the failure mode: the agg phrase already
    supplies the verb, so a label must not repeat it."""
    for entity in layer.values():
        for name, m in entity.measures.items():
            first = m.label.split()[0].lower()
            assert first not in {"average", "avg", "count", "sum", "total",
                                 "minimum", "maximum"}, f"{entity.name}.{name}"


def test_two_measures_are_joined(entity):
    assert r(entity, measures=["net_gain", "cost"]).startswith(
        "Sum of net gain and Sum of cost")


def test_grain_is_spelled_out(entity):
    out = r(entity, dimensions=[DimensionRef(field="intervention_date", grain="month")])
    assert "by calendar month" in out


def test_two_dimensions(entity):
    out = r(entity, dimensions=[DimensionRef(field="intervention_date", grain="month"),
                                DimensionRef(field="region")])
    assert "by calendar month and region" in out


@pytest.mark.parametrize("f,expected", [
    (Filter(field="intervention_date", op="in_year", value=2026), "2026"),
    (Filter(field="status", op="=", value="COMPLETED"), "status COMPLETED"),
    (Filter(field="status", op="!=", value="CANCELLED"), "status not CANCELLED"),
    (Filter(field="intervention_type", op="in", value=["FRAC", "WORKOVER"]),
     "intervention type one of FRAC and WORKOVER"),
    (Filter(field="intervention_date", op="last_n_days", value=30), "the last 30 days"),
    (Filter(field="intervention_date", op="between", value=["2026-01-01", "2026-06-30"]),
     "intervention date between 2026-01-01 and 2026-06-30"),
])
def test_every_filter_op_has_a_phrase(entity, f, expected):
    assert expected in r(entity, filters=[f])


def test_no_filter_is_silently_dropped(entity):
    out = r(entity, filters=[Filter(field="intervention_date", op="in_year", value=2026),
                             Filter(field="status", op="=", value="COMPLETED"),
                             Filter(field="contractor", op="=", value="SLB")])
    for fragment in ("2026", "status COMPLETED", "contractor SLB"):
        assert fragment in out


def test_freshness_tail(entity):
    out = restate(
        SemanticQuery(entity="well_interventions", measures=["net_gain"],
                      dimensions=[DimensionRef(field="intervention_date", grain="month"),
                                  DimensionRef(field="region")],
                      filters=[Filter(field="intervention_date", op="in_year", value=2026),
                               Filter(field="status", op="=", value="COMPLETED")]),
        entity, row_count=48, data_max_ts=date(2026, 8, 14))
    assert out == (
        "Sum of net gain, by calendar month and region, filtered to 2026 and "
        "status COMPLETED, from Well Interventions — 48 rows, "
        "data through 14 Aug 2026."
    )


def test_single_row_is_not_pluralised(entity):
    assert "1 row," in restate(
        SemanticQuery(entity="well_interventions", measures=["net_gain"]),
        entity, row_count=1, data_max_ts=date(2026, 8, 14))
