"""The raw text-to-SQL arm.

Same question, same model, but handed the physical schema and asked for
SQL directly. Scored only on whether the result set matches, because
comparing SQL strings is meaningless -- two correct answers rarely look
alike.

The more interesting number is not the accuracy gap but the error counts:
queries that fail outright, name a table that does not exist, or invent a
join. Those are the silent-wrongness class the grammar makes structurally
impossible, and this arm is what shows they were a real risk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import anthropic
from pydantic import BaseModel, ConfigDict

from app.db import warehouse_pool

SCHEMA_DDL = """\
CREATE TABLE ddh.dim_wells (
    well_id integer PRIMARY KEY, well_name text, region_name text,
    field_name text, spud_date date, well_type text);

CREATE TABLE ddh.fct_well_interventions (
    intervention_id integer PRIMARY KEY, well_id integer,
    intervention_date date, intervention_type text, status text,
    net_gain_bbl numeric, cost_usd numeric, contractor text);

CREATE TABLE ddh.fct_production_daily (
    well_id integer, reading_date date, oil_bbl numeric, gas_mcf numeric,
    water_bbl numeric, downtime_hours numeric);
"""

SYSTEM = f"""\
You write a single PostgreSQL SELECT statement answering the user's question \
against this schema. Return only the SQL.

{SCHEMA_DDL}
"""


class SqlAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str


@dataclass
class BaselineResult:
    sql: str | None = None
    rows: list[tuple] | None = None
    error: str | None = None
    error_kind: str | None = None   # unreachable | invalid_sql | missing_object | refused


UNKNOWN_OBJECT = re.compile(r'relation "([^"]+)" does not exist|column "([^"]+)" does not exist')


def run_baseline(question: str, model: str) -> BaselineResult:
    client = anthropic.Anthropic()
    try:
        response = client.messages.parse(
            model=model, max_tokens=2048,
            system=[{"type": "text", "text": SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": question}],
            output_format=SqlAnswer,
        )
    except anthropic.APIError as exc:
        return BaselineResult(error=str(exc), error_kind="unreachable")

    if response.parsed_output is None:
        return BaselineResult(error="no structured output", error_kind="refused")

    sql = response.parsed_output.sql.strip().rstrip(";")

    # The read-only role is the same one the compiled path uses, so a
    # baseline query attempting a write fails here exactly as it would in
    # production -- which is itself part of the comparison.
    try:
        with warehouse_pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return BaselineResult(sql=sql, rows=cur.fetchall())
    except Exception as exc:            # noqa: BLE001 - any failure is a failure
        kind = "missing_object" if UNKNOWN_OBJECT.search(str(exc)) else "invalid_sql"
        return BaselineResult(sql=sql, error=str(exc).strip()[:200], error_kind=kind)
