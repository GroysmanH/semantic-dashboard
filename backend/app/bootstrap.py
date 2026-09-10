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


def ensure_board_schemas() -> None:
    """Give every dashboard a schema, once.

    Migration 0005 adds the column nullable because the migration runner is
    raw SQL: it cannot read the semantic layer, so it cannot know which
    schema an existing board's cards belong to. That question is answerable
    here, where the layer is loaded. A board asks about whatever its cards
    already ask about; a board with no cards, or cards naming entities the
    layer no longer has, falls back to the configured default.

    Idempotent, and one cheap query on every start after the first.
    """
    from .config import settings
    from .db import app_pool
    from .deps import LAYER
    from .layer.scope import schema_of

    by_entity = {name: schema_of(entity) for name, entity in LAYER.items()}

    with app_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM app.board WHERE schema_name IS NULL")
        boards = [row[0] for row in cur.fetchall()]
        if not boards:
            return

        for board_id in boards:
            # Most-used entity wins, ties broken by name so the same board
            # never resolves two different ways on two different starts.
            cur.execute(
                """
                SELECT semantic_query ->> 'entity' AS entity, count(*) AS n
                FROM app.card
                WHERE board_id = %s
                  AND deleted_at IS NULL
                  AND semantic_query IS NOT NULL
                GROUP BY 1
                ORDER BY n DESC, entity
                """,
                (board_id,),
            )
            chosen = settings.default_schema
            for entity, _ in cur.fetchall():
                schema = by_entity.get(entity or "")
                if schema:
                    chosen = schema
                    break
            cur.execute(
                "UPDATE app.board SET schema_name = %s WHERE id = %s",
                (chosen, board_id),
            )

    log.info("assigned a schema to %d dashboard(s)", len(boards))
