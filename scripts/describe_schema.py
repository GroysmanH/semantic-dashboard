"""Describe a schema well enough to model it, without reading its rows.

Writing a semantic layer needs to know what the columns *mean*: units,
cardinality, date grain, which column is the grain of the table and which
is a lookup code. The obvious way to find that out is to look at some
rows. Against a confidential warehouse that is the one thing worth not
doing, and it is also unnecessary.

Postgres has already profiled every column, during ANALYZE, and left the
result in `pg_stats`: null fraction, distinct count, most common values,
histogram bounds. Reading it costs nothing -- no table scan, no matter
whether the table holds a thousand rows or seven hundred million -- and it
is *column-wise*. A row links a company to a volume to a date and can
identify a shipment; a column profile says only that this column ranges
0 to 48000, and the link between columns, which is what makes a row
identifying, is exactly what is thrown away.

So this reads catalogue and statistics, and by default nothing else. It
opens the connection read-only twice over and never issues anything but
SELECT.

    python scripts/describe_schema.py dm_upstream -o dm_upstream.md

Review the output before sharing it. `--no-values` drops the most-common-
value lists if a column's contents are themselves sensitive; `--samples N`
opts back in to real rows if the statistics turn out not to be enough.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

# Read-only at the session level and a short leash on any single query.
# Neither is the real protection -- the role should be SELECT-only -- but
# a script pointed at a production warehouse should not be the thing that
# holds a lock or scans a 678M-row table by accident.
SAFE = "-c default_transaction_read_only=on -c statement_timeout=60s"

TABLES = """
SELECT c.relname AS table_name,
       c.relkind = 'v' AS is_view,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
       c.reltuples::bigint AS estimated_rows,
       obj_description(c.oid) AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relkind IN ('r', 'v', 'm', 'p')
ORDER BY c.relname
"""

COLUMNS = """
SELECT c.table_name, c.column_name, c.ordinal_position,
       c.data_type, c.is_nullable,
       col_description(pc.oid, c.ordinal_position) AS comment
FROM information_schema.columns c
JOIN pg_class pc ON pc.relname = c.table_name
JOIN pg_namespace pn
  ON pn.oid = pc.relnamespace AND pn.nspname = c.table_schema
WHERE c.table_schema = %s
ORDER BY c.table_name, c.ordinal_position
"""

# Declared foreign keys. Warehouses often have none -- the modelling tool
# knew the joins and the database was never told -- so an empty result
# here is a finding, not a failure.
KEYS = """
SELECT tc.table_name, kcu.column_name,
       ccu.table_name AS refs_table, ccu.column_name AS refs_column,
       tc.constraint_type
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
 AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name
 AND ccu.table_schema = tc.table_schema
WHERE tc.table_schema = %s
  AND tc.constraint_type IN ('FOREIGN KEY', 'PRIMARY KEY')
ORDER BY tc.table_name, tc.constraint_type, kcu.column_name
"""

# The whole point of the script. pg_stats is visible only for tables the
# role may read, so it cannot leak past the grant.
STATS = """
SELECT tablename, attname, null_frac, n_distinct,
       most_common_vals::text AS common,
       histogram_bounds::text AS bounds
FROM pg_stats
WHERE schemaname = %s
ORDER BY tablename, attname
"""


def _rows(conn, sql: str, schema: str) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, (schema,)).fetchall()


def _cardinality(n_distinct: float | None, estimated: int) -> str:
    """pg_stats reports a count when it is stable and a negative fraction
    of the table when it grows with it. The distinction matters: a column
    with 4 fixed values is a dimension worth listing, one with 0.9 x rows
    is an identifier and never belongs in a `values:` list."""
    if n_distinct is None:
        return "unknown"
    if n_distinct < 0:
        share = -n_distinct
        return f"~{share:.0%} of rows distinct" + (
            " (identifier-like)" if share > 0.5 else "")
    return f"~{int(n_distinct)} distinct"


def _trim(text: str | None, keep: int) -> str:
    if not text:
        return ""
    inner = text.strip("{}")
    parts = [p.strip('"') for p in inner.split(",") if p]
    shown = ", ".join(parts[:keep])
    return shown + (f", … (+{len(parts) - keep} more)" if len(parts) > keep
                    else "")


def describe(conn, schema: str, *, values: bool, keep: int) -> str:
    tables = _rows(conn, TABLES, schema)
    if not tables:
        raise SystemExit(
            f"nothing readable in schema {schema!r}. Either it does not "
            f"exist or this role has no SELECT on it.")

    columns: dict[str, list[dict]] = {}
    for col in _rows(conn, COLUMNS, schema):
        columns.setdefault(col["table_name"], []).append(col)

    stats: dict[tuple[str, str], dict] = {
        (s["tablename"], s["attname"]): s for s in _rows(conn, STATS, schema)}

    constraints: dict[str, list[dict]] = {}
    for key in _rows(conn, KEYS, schema):
        constraints.setdefault(key["table_name"], []).append(key)

    out: list[str] = [
        f"# Schema `{schema}`",
        "",
        "Catalogue and column statistics only. No rows were read: the "
        "figures below come from `pg_stats`, which Postgres computed "
        "during ANALYZE, and describe each column on its own.",
        "",
        ("**Includes real values** from `most_common_vals` and histogram "
         "bounds. Review before sharing -- a code column is harmless, an "
         "identifier column is not." if values else
         "Shapes only: no value from any column appears below. Re-run with "
         "`--values` to include most common values and ranges."),
        "",
        "| table | rows (est.) | size | kind |",
        "| --- | --- | --- | --- |",
    ]
    for t in tables:
        kind = "view" if t["is_view"] else "table"
        out.append(f"| {t['table_name']} | {t['estimated_rows']:,} | "
                   f"{t['size']} | {kind} |")

    for t in tables:
        name = t["table_name"]
        out += ["", f"## {name}", ""]
        if t["comment"]:
            out += [f"> {t['comment']}", ""]

        for key in constraints.get(name, []):
            if key["constraint_type"] == "PRIMARY KEY":
                out.append(f"- **primary key**: `{key['column_name']}`")
            else:
                out.append(
                    f"- **foreign key**: `{key['column_name']}` → "
                    f"`{key['refs_table']}.{key['refs_column']}`")
        if constraints.get(name):
            out.append("")

        out += ["| column | type | null | cardinality | notes |",
                "| --- | --- | --- | --- | --- |"]
        for col in columns.get(name, []):
            stat = stats.get((name, col["column_name"]))
            card = (_cardinality(stat["n_distinct"], t["estimated_rows"])
                    if stat else "not analysed")
            notes: list[str] = []
            if col["comment"]:
                notes.append(col["comment"])
            if stat and stat["null_frac"] and stat["null_frac"] > 0.01:
                notes.append(f"{stat['null_frac']:.0%} null")
            if values and stat:
                # Most common values for a small domain: this is the list
                # that becomes `values:` in the YAML. Ranges for anything
                # continuous, which is how units become visible without a
                # single row being read.
                if stat["common"] and (stat["n_distinct"] or 0) > 0 \
                        and stat["n_distinct"] <= 60:
                    notes.append("values: " + _trim(stat["common"], keep))
                elif stat["bounds"]:
                    edges = _trim(stat["bounds"], 200).split(", ")
                    if len(edges) >= 2:
                        notes.append(f"range: {edges[0]} … {edges[-1]}")
            out.append(
                f"| {col['column_name']} | {col['data_type']} | "
                f"{'yes' if col['is_nullable'] == 'YES' else 'no'} | "
                f"{card} | {'; '.join(notes)} |")

    return "\n".join(out) + "\n"


def sample(conn, schema: str, table: str, limit: int) -> str:
    """Real rows. Off by default and deliberately awkward to reach."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            sql.SQL("SELECT * FROM {}.{} LIMIT %s").format(
                sql.Identifier(schema), sql.Identifier(table)),
            (limit,))
        rows = cur.fetchall()
    if not rows:
        return ""
    head = list(rows[0])
    lines = [f"### {table} — {len(rows)} rows", "",
             "| " + " | ".join(head) + " |",
             "| " + " | ".join("---" for _ in head) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(
            str(row[c]).replace("|", "\\|") for c in head) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("schema")
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="write here instead of stdout")
    ap.add_argument("--dsn", default=None,
                    help="defaults to $PROFILE_URL, then $WAREHOUSE_URL")
    ap.add_argument("--values", action="store_true",
                    help="also list each column's most common values and "
                         "min/max. These are real values out of the table: "
                         "a low-cardinality code column is harmless, but a "
                         "column of tax IDs or customer names is not. Off "
                         "by default; review the output before sharing it.")
    ap.add_argument("--keep", type=int, default=12,
                    help="how many common values to show per column")
    ap.add_argument("--samples", type=int, default=0, metavar="N",
                    help="also include N real rows per table. Off by "
                         "default: this is the one option that puts "
                         "actual records in the output file.")
    args = ap.parse_args()

    dsn = args.dsn or os.environ.get("PROFILE_URL") \
        or os.environ.get("WAREHOUSE_URL")
    if not dsn:
        print("no DSN: pass --dsn or set PROFILE_URL", file=sys.stderr)
        return 2

    with psycopg.connect(make_conninfo(dsn, options=SAFE)) as conn:
        text = describe(conn, args.schema, values=args.values,
                        keep=args.keep)
        if args.samples:
            text += (f"\n## Sample rows\n\n**These are real records from "
                     f"{args.schema}.** Review before sharing.\n\n")
            for t in _rows(conn, TABLES, args.schema):
                text += "\n" + sample(conn, args.schema, t["table_name"],
                                      args.samples)

    if args.out:
        args.out.write_text(text)
        print(f"wrote {args.out} ({len(text):,} chars)", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
