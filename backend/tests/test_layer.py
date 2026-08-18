"""Layer loading is fail-fast: a malformed layer is a startup error, not a
mystery at query time."""

import textwrap

import pytest

from app.layer.loader import load_layer, synonym_index
from app.layer.models import LayerError

MINIMAL = """
entity: jobs
label: Jobs
table: ddh.fct_well_interventions
time_column: intervention_date
joins:
  wells:
    to: ddh.dim_wells
    condition: wells.well_id = fct_well_interventions.well_id
dimensions:
  intervention_date:
    label: date
    type: date
    grains: [day, month]
  region:
    label: region
    type: string
    via: wells.region_name
measures:
  net_gain:
    label: net gain
    agg: sum
    column: net_gain_bbl
synonyms:
  net_gain: [uplift]
"""


def write(tmp_path, name, body):
    p = tmp_path / f"{name}.yaml"
    p.write_text(textwrap.dedent(body))
    return tmp_path


# -- the real layer ------------------------------------------------------

def test_real_layer_loads_two_entities(layer):
    assert set(layer) == {"well_interventions", "production"}


def test_via_dimension_resolves_to_a_declared_join(layer):
    entity = layer["well_interventions"]
    assert entity.dimensions["region"].via == "wells.region_name"
    assert "wells" in entity.joins


def test_plain_dimension_column_defaults_to_its_key(layer):
    assert layer["well_interventions"].dimensions["contractor"].column == "contractor"


def test_status_is_flagged_low_confidence(layer):
    """The seeded unverified field, which the confidence gate fires on."""
    entity = layer["well_interventions"]
    assert entity.has_low_confidence
    assert "dimension status" in entity.low_confidence_fields()


def test_production_entity_is_fully_verified(layer):
    assert not layer["production"].has_low_confidence


# -- structural validation ----------------------------------------------

def test_minimal_layer_loads(tmp_path):
    layer = load_layer(write(tmp_path, "jobs", MINIMAL))
    assert layer["jobs"].label == "Jobs"


def test_via_pointing_at_undeclared_join_is_rejected(tmp_path):
    body = MINIMAL.replace("via: wells.region_name", "via: rigs.region_name")
    with pytest.raises(LayerError, match="undeclared join alias"):
        load_layer(write(tmp_path, "jobs", body))


def test_grains_on_a_string_dimension_are_rejected(tmp_path):
    body = MINIMAL.replace(
        "    label: region\n    type: string\n",
        "    label: region\n    type: string\n    grains: [month]\n",
    )
    with pytest.raises(LayerError, match="only valid on date dimensions"):
        load_layer(write(tmp_path, "jobs", body))


def test_synonym_pointing_at_an_unknown_field_is_rejected(tmp_path):
    body = MINIMAL.replace("  net_gain: [uplift]", "  revenue: [money]")
    with pytest.raises(LayerError, match="unknown field"):
        load_layer(write(tmp_path, "jobs", body))


def test_star_column_outside_count_is_rejected(tmp_path):
    body = MINIMAL.replace("    agg: sum\n    column: net_gain_bbl",
                           '    agg: sum\n    column: "*"')
    with pytest.raises(LayerError, match="only valid with agg: count"):
        load_layer(write(tmp_path, "jobs", body))


def test_dimension_declaring_both_via_and_column_is_rejected(tmp_path):
    body = MINIMAL.replace("    via: wells.region_name",
                           "    via: wells.region_name\n    column: region_name")
    with pytest.raises(LayerError, match="either `via` or `column`"):
        load_layer(write(tmp_path, "jobs", body))


def test_unknown_key_in_a_definition_is_rejected(tmp_path):
    """extra=forbid: a typo'd key is an error, never a silently ignored one."""
    body = MINIMAL.replace("    agg: sum\n", "    agg: sum\n    aggregation: sum\n")
    with pytest.raises(LayerError):
        load_layer(write(tmp_path, "jobs", body))


def test_empty_directory_is_rejected(tmp_path):
    with pytest.raises(LayerError, match="no entity definitions"):
        load_layer(tmp_path)


# -- synonym index (feeds deterministic ambiguity detection) -------------

def test_synonym_index_maps_terms_to_fields(layer):
    index = synonym_index(layer)
    assert index["uplift"]["well_interventions"] == ["net_gain"]
    assert "region" in index
    # 'area' is a synonym for region on both entities.
    assert set(index["area"]) == {"well_interventions", "production"}


def test_bare_yaml_on_key_is_named_as_the_cause(tmp_path):
    """The design doc writes `on:`; YAML 1.1 reads that as the boolean true.
    Layer files are hand-edited, so the error has to name the trap."""
    body = MINIMAL.replace("    condition: wells.well_id", "    on: wells.well_id")
    with pytest.raises(LayerError, match="bare `on:` as true"):
        load_layer(write(tmp_path, "jobs", body))


def test_quoted_on_key_still_works(tmp_path):
    body = MINIMAL.replace("    condition: wells.well_id", '    "on": wells.well_id')
    layer = load_layer(write(tmp_path, "jobs", body))
    assert layer["jobs"].joins["wells"].condition.startswith("wells.well_id")
