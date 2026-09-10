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

from pydantic import BaseModel, ConfigDict, Field, field_validator

Grain = Literal["day", "month", "quarter", "year"]
FilterOp = Literal["=", "!=", "in", "between", "in_year", "last_n_days"]
ChartHint = Literal[
    "line", "bar", "area", "point", "heatmap", "big_number",
    "pie", "donut", "stacked_bar", "normalised_bar", "scatter", "bubble", "map",
]

# A closed set the compiler implements, never an expression the model
# writes. Each one is an OVER (...) clause composed from validated
# identifiers, so growing analytical reach does not grow the trust surface.
Transform = Literal[
    "percent_of_total",   # share within the current grouping
    "previous_period",    # the same measure, one grain back
    "period_change",      # signed delta against the previous period
    "period_change_pct",  # (current - previous) / previous
    "cumulative",         # running total along the time axis
    "moving_average",     # n-period mean, n bound as a validated integer
    "rank",               # position within the grouping
    "ratio",              # this measure over another measure on the entity
]

Scalar = str | int | float | bool

MAX_WINDOW = 52


class DimensionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    grain: Grain | None = None


class MeasureRef(BaseModel):
    """A measure, optionally transformed.

    Bare strings are still legal everywhere a measure is named -- every
    saved card and every existing fixture predates this model -- and are
    normalised into `MeasureRef(name=...)` on the way in.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    transform: Transform | None = None
    per: str | None = None      # ratio only: the denominator measure
    window: int | None = Field(default=None, ge=2, le=MAX_WINDOW)

    @property
    def output_name(self) -> str:
        """The SQL alias, and therefore the chart's column name. Distinct
        per transform so `oil` and its running total can coexist in one
        result set without one silently overwriting the other."""
        if self.transform is None:
            return self.name
        if self.transform == "ratio":
            return f"{self.name}_per_{self.per}"
        if self.transform == "moving_average":
            return f"{self.name}_ma{self.window}"
        return f"{self.name}_{self.transform}"


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
    """One entity, N measures, up to 3 dimensions.

    Deliberately still no joins beyond declared `via` paths, no subqueries,
    no free-form expressions and no DML — those are not hard here, they are
    impossible. Window functions arrived as a closed `Transform` enum the
    compiler implements, which is a different thing from letting the model
    write one.
    """

    model_config = ConfigDict(extra="forbid")

    entity: str
    measures: list[MeasureRef] = Field(min_length=1)
    # Three, not two: the third becomes a facet. Whether it *may* is a
    # question about the data's cardinality, answered in the chart builder
    # where the rows are, not asserted here.
    dimensions: list[DimensionRef] = Field(default_factory=list, max_length=3)
    filters: list[Filter] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=10_000)

    @field_validator("measures", mode="before")
    @classmethod
    def _accept_bare_names(cls, v: Any) -> Any:
        """`["oil"]` and `[{"name": "oil"}]` mean the same thing. Keeping the
        short form legal is what stops this change from invalidating every
        card already saved in the database."""
        if isinstance(v, list):
            return [{"name": m} if isinstance(m, str) else m for m in v]
        return v

    @property
    def measure_names(self) -> list[str]:
        """Output column names, in select order."""
        return [m.output_name for m in self.measures]

    def canonical(self) -> dict[str, Any]:
        """Stable key ordering, list order preserved. Exact-match comparison
        and section 9's diff routing both key off this."""
        return json.loads(self.model_dump_json(exclude_none=False))

    def relaxed(self) -> dict[str, Any]:
        """Canonical form with order-insensitive lists, for the eval's
        relaxed metric: two queries that differ only in the order they
        list dimensions or filters mean the same thing."""
        d = self.canonical()
        d["measures"] = sorted(d["measures"],
                               key=lambda x: json.dumps(x, sort_keys=True))
        d["dimensions"] = sorted(d["dimensions"], key=lambda x: json.dumps(x, sort_keys=True))
        d["filters"] = sorted(d["filters"], key=lambda x: json.dumps(x, sort_keys=True))
        d["order_by"] = sorted(d["order_by"], key=lambda x: json.dumps(x, sort_keys=True))
        return d

    def fingerprint(self) -> str:
        """Byte-stable digest. Section 9 routes a refinement that leaves this
        unchanged straight to a cache re-render, no database contact."""
        return json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
