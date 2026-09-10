"""The app may be let into more of a warehouse than it should look at.

The first real credential this was pointed at could read twenty-five
schemas and three hundred tables -- including employee payroll, personal
data, medical checks and disciplinary records across four HR schemas that
have nothing to do with oil production. Narrowing the grant is the proper
fix and belongs to whoever owns the database. This is what the app can do
in the meantime.

It matters because "nobody has modelled it" is not access control. An
unmodelled schema is already listed, named and browsable in the picker,
and it is one YAML file away from being queryable.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def allowlist(monkeypatch):
    def _set(value: str):
        monkeypatch.setattr(settings, "visible_schemas", value)
    return _set


def test_no_allowlist_means_everything_the_credential_can_read(allowlist):
    """The right default for a warehouse built for this app, and the
    wrong one for a warehouse this app was let into."""
    allowlist("")
    assert settings.schema_allowlist == set()


def test_the_allowlist_is_parsed_forgivingly(allowlist):
    allowlist(" dm_upstream , dm_planning ,, ")
    assert settings.schema_allowlist == {"dm_upstream", "dm_planning"}


def test_the_catalogue_shows_only_allowed_schemas(client, allowlist):
    # Whichever warehouse this suite is pointed at, take a schema it
    # really has: hardcoding one asserts the fixture, not the filter.
    allowlist("")
    everything = [s["schema"] for s in client.get("/schemas").json()]
    assert len(everything) > 1, "need at least two schemas to test a filter"

    allowlist(everything[0])
    listed = {s["schema"] for s in client.get("/schemas").json()}
    assert listed == {everything[0]}


def test_a_dashboard_cannot_be_pointed_outside_the_allowlist(client, allowlist):
    """Even at a schema the layer *can* answer. The allowlist outranks the
    layer, which is the point: it has to still hold on the day somebody
    writes the YAML."""
    allowlist("ddh")
    answer = client.post("/boards", json={"title": "Planning",
                                          "schema_name": "dm_planning"})
    assert answer.status_code == 422
    assert "dm_planning" in answer.json()["detail"]


def test_an_allowed_schema_still_works(client, allowlist):
    allowlist("ddh,dm_planning")
    answer = client.post("/boards", json={"title": "Planning",
                                          "schema_name": "dm_planning"})
    assert answer.status_code == 200
    assert answer.json()["schema_name"] == "dm_planning"


def test_the_layer_endpoint_offers_only_allowed_schemas(client, allowlist):
    """A name offered here that the catalogue will not show is a schema
    somebody can be told about and then cannot reach."""
    allowlist("ddh")
    assert client.get("/layer").json()["schemas"] == ["ddh"]


def test_without_an_allowlist_the_layer_offers_everything_modelled(client,
                                                                   allowlist):
    allowlist("")
    offered = client.get("/layer").json()["schemas"]
    assert {"ddh", "dm_planning"} <= set(offered)
