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


def is_fresh(cache: dict[str, Any] | None, ttl_seconds: int,
             key: str | None = None) -> bool:
    if not cache or not cache.get("fetched_at"):
        return False
    if key is not None and cache.get("key") != key:
        return False          # the query changed; the old result is not an answer
    fetched = datetime.fromisoformat(cache["fetched_at"])
    return now() - fetched < timedelta(seconds=ttl_seconds)


def envelope(key: str, rows: list[dict], compiled_sql: str,
             data_max_ts: str | None) -> dict[str, Any]:
    return {
        "key": key,
        "result": rows,
        "compiled_sql": compiled_sql,
        "fetched_at": now().isoformat(),
        "row_count": len(rows),
        "data_max_ts": data_max_ts,
    }
