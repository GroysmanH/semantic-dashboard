"""TTL cache for card results.

Query-on-load feels broken against a warehouse where a scan takes twenty
seconds, and a scheduler is not worth operating yet. The TTL also gives
the honest version of freshness, which the header needs anyway.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any


def cache_key(sql: str, params: list[Any]) -> str:
    return hashlib.sha256(f"{sql}|{params!r}".encode()).hexdigest()


def now() -> datetime:
    return datetime.now(timezone.utc)


def _fetched_at(cache: Any) -> datetime | None:
    """Validate the persisted cache envelope and return its aware timestamp.

    ``None`` and the legacy empty-object sentinel both mean no cache. Every
    non-empty JSON value is an input boundary and must be a complete envelope
    before freshness or query-key decisions can use it.
    """
    if cache is None or cache == {}:
        return None
    if not isinstance(cache, dict):
        raise ValueError("cache envelope is not an object")
    required = {
        "key", "result", "compiled_sql", "fetched_at", "row_count",
        "data_max_ts", "truncated",
    }
    if not required <= set(cache):
        raise ValueError("cache envelope is incomplete")
    if not isinstance(cache["key"], str) or not isinstance(
        cache["compiled_sql"], str
    ):
        raise ValueError("cache envelope metadata is invalid")
    if cache["data_max_ts"] is not None and not isinstance(
        cache["data_max_ts"], str
    ):
        raise ValueError("cache data timestamp is invalid")
    if (
        not isinstance(cache["result"], list)
        or any(not isinstance(row, dict) for row in cache["result"])
        or isinstance(cache["row_count"], bool)
        or not isinstance(cache["row_count"], int)
        or cache["row_count"] != len(cache["result"])
        or not isinstance(cache["truncated"], bool)
    ):
        raise ValueError("cache result envelope is invalid")
    raw = cache["fetched_at"]
    if not isinstance(raw, str):
        raise ValueError("cache freshness timestamp is invalid")
    fetched = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if fetched.tzinfo is None or fetched.utcoffset() is None:
        raise ValueError("cache freshness timestamp must include a timezone")
    return fetched


def is_fresh(cache: Any, ttl_seconds: int,
             key: str | None = None) -> bool:
    fetched = _fetched_at(cache)
    if fetched is None:
        return False
    if key is not None and cache.get("key") != key:
        return False          # the query changed; the old result is not an answer
    return now() - fetched < timedelta(seconds=ttl_seconds)


def envelope(key: str, rows: list[dict], compiled_sql: str,
             data_max_ts: str | None, truncated: bool = False) -> dict[str, Any]:
    return {
        "key": key,
        "result": rows,
        "compiled_sql": compiled_sql,
        "fetched_at": now().isoformat(),
        "row_count": len(rows),
        "data_max_ts": data_max_ts,
        # Kept in the envelope, not recomputed on read. A cached partial
        # answer is still a partial answer, and the cache is what the card
        # draws from on every reload after the first.
        "truncated": truncated,
    }
