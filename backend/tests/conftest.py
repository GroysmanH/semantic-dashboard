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


@pytest.fixture(autouse=True)
def no_leftover_boards(pools):
    """Delete boards a test created, so the suite does not accumulate them.

    Route tests create boards through the API and nothing ever removed them:
    a few hundred runs had left 217 behind. That was invisible while the UI
    showed a single implicit board, and became the entire interface the
    moment boards were rendered as tabs. Cards cascade, so removing the
    board is enough.
    """
    from app.store import cards as store

    try:
        before = {b["id"] for b in store.list_boards()}
    except Exception:
        # Tests that do not need a database must not fail on its absence.
        yield
        return

    yield

    for board in store.list_boards():
        if board["id"] not in before:
            store.delete_board(board["id"])
