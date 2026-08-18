"""Semantic layer models.

Hand-curated YAML, one file per entity. Two fields go beyond the design
doc's sketch: `label` (the deterministic restatement needs human names)
and `time_column` (the trust surface needs a data-max timestamp).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

Grain = Literal["day", "month", "quarter", "year"]
Confidence = Literal["low", "high"]
FieldType = Literal["date", "string", "number"]
Agg = Literal["sum", "count", "avg", "min", "max"]

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
QUALIFIED = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")
# A join condition is the one place raw SQL text enters the compiler. It is
# human-authored, never model-authored, but it is still constrained to
# equijoins between qualified columns so the layer cannot smuggle in a
# subquery or a second statement.
EQUIJOIN = re.compile(
    r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*\s*=\s*[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$",
    re.IGNORECASE,
)


class LayerError(ValueError):
    """A structural problem in the layer definition, raised at load time."""


class Join(BaseModel):
    """A declared join path. Join conditions are authored here, never
    generated -- which is what makes an invented join impossible rather
    than merely unlikely.

    The key is `condition`, not `on`: YAML 1.1 parses a bare `on:` as the
    boolean true, so a hand-authored `on: a.id = b.id` silently becomes a
    key of True. A quoted `"on"` is still accepted.
    """

    model_config = ConfigDict(extra="forbid")

    to: str                       # "ddh.dim_wells"
    condition: str = Field(validation_alias=AliasChoices("condition", "on"))
    confidence: Confidence = "high"

    @field_validator("to")
    @classmethod
    def _valid_table(cls, v: str) -> str:
        if not QUALIFIED.match(v):
            raise ValueError(f"join target is not a valid table name: {v!r}")
        return v

    @field_validator("condition")
    @classmethod
    def _valid_condition(cls, v: str) -> str:
        for part in re.split(r"\s+and\s+", v.strip(), flags=re.IGNORECASE):
            if not EQUIJOIN.match(part.strip()):
                raise ValueError(
                    f"join condition must be equijoins of the form "
                    f"alias.column = alias.column, optionally AND-ed; got {part.strip()!r}"
                )
        return v


class Dimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    type: FieldType
    column: str | None = None     # defaults to the dimension's key
    via: str | None = None        # "wells.region_name"
    grains: list[Grain] = Field(default_factory=list)
    values: list[str] | None = None
    confidence: Confidence = "high"

    @model_validator(mode="after")
    def _check(self) -> Dimension:
        if self.grains and self.type != "date":
            raise ValueError(
                f"grains are only valid on date dimensions, not {self.type!r}"
            )
        if self.via and self.column:
            raise ValueError("a dimension declares either `via` or `column`, not both")
        if self.via and "." not in self.via:
            raise ValueError(f"`via` must be alias.column, got {self.via!r}")
        if self.column and not IDENTIFIER.match(self.column):
            raise ValueError(f"column is not a valid identifier: {self.column!r}")
        return self


class Measure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    agg: Agg
    column: str
    description: str = ""
    confidence: Confidence = "high"

    @model_validator(mode="after")
    def _check(self) -> Measure:
        if self.column == "*":
            if self.agg != "count":
                raise ValueError("column '*' is only valid with agg: count")
        elif not IDENTIFIER.match(self.column):
            raise ValueError(f"column is not a valid identifier: {self.column!r}")
        return self


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(alias="entity")
    label: str
    table: str
    description: str = ""
    time_column: str | None = None
    joins: dict[str, Join] = Field(default_factory=dict)
    dimensions: dict[str, Dimension]
    measures: dict[str, Measure]
    synonyms: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("table")
    @classmethod
    def _valid_table(cls, v: str) -> str:
        if not QUALIFIED.match(v):
            raise ValueError(f"table is not a valid table name: {v!r}")
        return v

    @model_validator(mode="after")
    def _resolve_and_check(self) -> Entity:
        for key, dim in self.dimensions.items():
            if not IDENTIFIER.match(key):
                raise ValueError(f"dimension key is not an identifier: {key!r}")
            # A dimension with no explicit column and no join path reads a
            # column of the same name off the base table.
            if dim.column is None and dim.via is None:
                dim.column = key
            if dim.via:
                alias = dim.via.split(".", 1)[0]
                if alias not in self.joins:
                    raise ValueError(
                        f"dimension {key!r} routes via undeclared join alias "
                        f"{alias!r}; declared joins: {sorted(self.joins)}"
                    )

        for key in self.measures:
            if not IDENTIFIER.match(key):
                raise ValueError(f"measure key is not an identifier: {key!r}")

        overlap = set(self.dimensions) & set(self.measures)
        if overlap:
            raise ValueError(f"names used as both dimension and measure: {sorted(overlap)}")

        # synonyms are keyed by the field they resolve to; the values are
        # the free-text terms a manager might actually type.
        for field, terms in self.synonyms.items():
            if field not in self.dimensions and field not in self.measures:
                raise ValueError(
                    f"synonyms declared for unknown field {field!r}; "
                    f"known: {sorted([*self.dimensions, *self.measures])}"
                )
            if not terms:
                raise ValueError(f"synonyms for {field!r} is empty")

        if self.time_column and not IDENTIFIER.match(self.time_column):
            raise ValueError(f"time_column is not an identifier: {self.time_column!r}")

        return self

    # -- confidence gate (design doc section 4) ---------------------------
    # Entity-level on purpose: one unverified field refuses every query on
    # the entity. That harshness is the forcing function for layer review.

    def low_confidence_fields(self) -> list[str]:
        out = [f"dimension {k}" for k, d in self.dimensions.items() if d.confidence == "low"]
        out += [f"measure {k}" for k, m in self.measures.items() if m.confidence == "low"]
        out += [f"join {k}" for k, j in self.joins.items() if j.confidence == "low"]
        return sorted(out)

    @property
    def has_low_confidence(self) -> bool:
        return bool(self.low_confidence_fields())


Layer = dict[str, Entity]
