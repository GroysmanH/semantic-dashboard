"""The warehouse connection must not be able to write, and must be
checked rather than trusted.

The login this was first pointed at -- a real warehouse account --
holds INSERT, UPDATE, DELETE and CREATE. Nothing about the grant stops a
write. The only thing that does is `default_transaction_read_only` in the
pool's `options`, which makes one line of application configuration the
entire protection for a production database.

Options are easy to lose: a DSN carrying its own `options` string, a
pooler that drops them, an ordinary edit. So the guarantee is measured at
startup, and a warehouse that turns out to be writable stops the app
instead of serving from it.
"""

import pytest

from app import db


class _Fake:
    def __init__(self, value):
        self.value = value

    def execute(self, _sql):
        return self

    def fetchone(self):
        return (self.value,) if self.value is not None else None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _pool_reporting(value, monkeypatch):
    monkeypatch.setattr(db.warehouse_pool, "connection",
                        lambda: _Fake(value))


def test_a_read_only_session_starts_normally(monkeypatch):
    _pool_reporting("on", monkeypatch)
    db._assert_warehouse_is_read_only()


def test_a_writable_session_refuses_to_start(monkeypatch):
    _pool_reporting("off", monkeypatch)
    with pytest.raises(db.WarehouseIsWritable, match="not read-only"):
        db._assert_warehouse_is_read_only()


def test_an_unanswerable_session_refuses_too(monkeypatch):
    """Absence of proof is not proof. If the server will not say, the
    answer is no."""
    _pool_reporting(None, monkeypatch)
    with pytest.raises(db.WarehouseIsWritable):
        db._assert_warehouse_is_read_only()


def test_the_pool_asks_for_read_only_and_a_timeout():
    options = db.warehouse_pool.kwargs["options"]
    assert "default_transaction_read_only=on" in options
    assert "statement_timeout=" in options
