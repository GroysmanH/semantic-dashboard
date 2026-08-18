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


@pytest.fixture(scope="session", autouse=True)
def pools():
    """The app opens its pools in the FastAPI lifespan, which pytest never
    runs; open them here so render-level tests can reach the database."""
    from app.db import close_pools, open_pools

    open_pools()
    yield
    close_pools()
