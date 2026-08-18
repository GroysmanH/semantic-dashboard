import os

import psycopg
import pytest


@pytest.fixture(scope="session")
def warehouse_dsn() -> str:
    return os.environ["WAREHOUSE_URL"]


@pytest.fixture(scope="session")
def app_dsn() -> str:
    return os.environ["APP_URL"]


@pytest.fixture
def warehouse_conn(warehouse_dsn):
    with psycopg.connect(warehouse_dsn) as conn:
        yield conn


@pytest.fixture
def app_conn(app_dsn):
    with psycopg.connect(app_dsn) as conn:
        yield conn


@pytest.fixture(scope="session")
def layer():
    from app.config import settings
    from app.layer.loader import load_layer

    return load_layer(settings.layer_dir)
