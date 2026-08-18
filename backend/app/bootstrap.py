"""Seed on first start so `docker compose up` really is one command.

Idempotent: it checks for rows before doing anything, so a restart against
a populated volume is a single cheap query.
"""

from __future__ import annotations

import logging
import runpy
import sys
from pathlib import Path

from .db import warehouse_pool

log = logging.getLogger("bootstrap")
SEED = Path("/db/seed/seed.py")


def warehouse_is_empty() -> bool:
    with warehouse_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM ddh.dim_wells)")
        return not cur.fetchone()[0]


def ensure_seeded() -> None:
    if not SEED.exists():
        log.warning("seed script not mounted at %s; skipping", SEED)
        return
    try:
        if not warehouse_is_empty():
            return
    except Exception as exc:                      # noqa: BLE001
        log.warning("could not check whether the warehouse is seeded: %s", exc)
        return

    log.info("empty warehouse — seeding")
    sys.argv = [str(SEED)]
    runpy.run_path(str(SEED), run_name="__main__")
