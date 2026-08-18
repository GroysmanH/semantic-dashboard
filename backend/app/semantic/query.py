"""The semantic query grammar — the contract between the LLM, the compiler
and the frontend.

`extra="forbid"` on every model is load-bearing. It is what turns an
invented field into a hard validation failure instead of a silently
ignored key, and it is enforced server-side by the structured-output
schema before the database is ever contacted.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Grain = Literal["day", "month", "quarter", "year"]
FilterOp = Literal["=", "!=", "in", "between", "in_year", "last_n_days"]
ChartHint = Literal["line", "bar", "area", "point", "heatmap", "big_number"]

Scalar = str | int | float | bool


class DimensionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    grain: Grain | None = None


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    op: FilterOp
    value: Scalar | list[Scalar] | None = None


class OrderBy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    dir: Literal["asc", "desc"] = "desc"


class SemanticQuery(BaseModel):
    """One entity, N measures, up to 2 dimensions. Deliberately no joins
    beyond declared `via` paths, no window functions, no subqueries, and
    no DML — those are not hard here, they are impossible."""

    model_config = ConfigDict(extra="forbid")

    entity: str
    measures: list[str] = Field(min_length=1)
    dimensions: list[DimensionRef] = Field(default_factory=list, max_length=2)
    filters: list[Filter] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=10_000)

    def canonical(self) -> dict[str, Any]:
        """Stable key ordering, list order preserved. Exact-match comparison
        and section 9's diff routing both key off this."""
        return json.loads(self.model_dump_json(exclude_none=False))

    def relaxed(self) -> dict[str, Any]:
        """Canonical form with order-insensitive lists, for the eval's
        relaxed metric: two queries that differ only in the order they
        list dimensions or filters mean the same thing."""
        d = self.canonical()
        d["measures"] = sorted(d["measures"])
        d["dimensions"] = sorted(d["dimensions"], key=lambda x: json.dumps(x, sort_keys=True))
        d["filters"] = sorted(d["filters"], key=lambda x: json.dumps(x, sort_keys=True))
        d["order_by"] = sorted(d["order_by"], key=lambda x: json.dumps(x, sort_keys=True))
        return d

    def fingerprint(self) -> str:
        """Byte-stable digest. Section 9 routes a refinement that leaves this
        unchanged straight to a cache re-render, no database contact."""
        return json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
