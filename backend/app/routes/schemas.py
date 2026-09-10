"""What the warehouse holds, and how much of it this app can answer.

The picker needs to show real schemas and real tables, because "choose a
schema" is a claim about the database and not about the semantic layer. But
the app cannot query a table -- only an entity, which is a hand-written
description of what its columns mean. A list that showed only modelled
tables would quietly redefine the warehouse as the subset somebody has got
around to describing.

So the list is the catalogue, and every row says which it is. An unmodelled
table is shown, named, and inert, and the reason it is inert is the honest
one: nobody has defined what its columns mean.

Read through the warehouse credential on purpose. `information_schema`
shows a role only what it may access, so a schema this app could not read
cannot appear in a picker that would then fail on it.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..config import settings
from ..db import warehouse_pool
from ..deps import LAYER
from ..layer.scope import schema_of

router = APIRouter(tags=["schemas"])

# Schemas that are never a dashboard's subject. `stg` is landed raw data --
# every column is text, so nothing in it can be aggregated - and `app` is
# this application's own boards and cards, which the warehouse role cannot
# see anyway.
HIDDEN = {"information_schema", "pg_catalog", "pg_toast", "app", "public"}


def _catalogue() -> dict[str, list[tuple[str, str]]]:
    with warehouse_pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_schema <> ALL(%s)
            ORDER BY table_schema, table_name
            """,
            (sorted(HIDDEN),),
        )
        allowed = settings.schema_allowlist
        out: dict[str, list[tuple[str, str]]] = {}
        for schema, table, kind in cur.fetchall():
            if allowed and schema not in allowed:
                continue
            out.setdefault(schema, []).append((table, kind))
        return out


@router.get("/schemas")
def list_schemas():
    """Every schema the app can read, with what each one holds."""
    entities = {
        (schema_of(entity), entity.table.split(".", 1)[1]): entity
        for entity in LAYER.values()
    }
    # A table reached only through a declared join is not answerable on its
    # own, but calling it "not modelled" would be wrong -- it is modelled,
    # as part of something else.
    joined = {
        (schema_of(entity), join.to.split(".", 1)[1])
        for entity in LAYER.values()
        for join in entity.joins.values()
        if "." in join.to
    }

    out = []
    for schema, tables in sorted(_catalogue().items()):
        rows = []
        for table, kind in tables:
            entity = entities.get((schema, table))
            rows.append({
                "table": table,
                "is_view": kind == "VIEW",
                "entity": entity.name if entity else None,
                "label": entity.label if entity else "",
                "unverified": entity.low_confidence_fields() if entity else [],
                "joined_only": entity is None and (schema, table) in joined,
            })
        out.append({
            "schema": schema,
            "tables": rows,
            "answerable": sum(1 for r in rows if r["entity"]),
        })
    return out
