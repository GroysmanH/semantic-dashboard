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


class Derived(BaseModel):
    """A measure defined as arithmetic over other measures.

    Declared, not composed at query time -- and declared sparingly. Pure
    arithmetic (`gas / oil`) belongs in the `ratio` transform, where twenty
    measures yield four hundred ratios with no YAML at all. What earns a
    declaration is a *convention*: water cut is water/(oil+water), not
    water/oil, and no amount of schema introspection will ever discover
    that. This block is for the definitions a business has an opinion about.

    The formula composes **measure names**, which already carry their own
    aggregation, so `water_cut` compiles to
    `SUM(water) / NULLIF(SUM(oil) + SUM(water), 0)`. Writing it against raw
    columns would give `SUM(water / (oil + water))` -- a sum of ratios,
    which is a different and wrong number. `_check_formula` is what stops
    that, and it also keeps arbitrary SQL out.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    formula: str
    description: str = ""
    confidence: Confidence = "high"

    def operands(self) -> set[str]:
        """Measure names the formula references."""
        import ast

        tree = ast.parse(self.formula, mode="eval")
        return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

    @field_validator("formula")
    @classmethod
    def _parseable(cls, v: str) -> str:
        import ast

        try:
            tree = ast.parse(v, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"formula does not parse: {v!r}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                raise ValueError(
                    f"formula may not call functions: {v!r}. Aggregation comes "
                    "from the measures it names, not from the formula.")
            if isinstance(node, ast.Attribute):
                raise ValueError(f"formula may not reference attributes: {v!r}")
            if not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp,
                                     ast.Name, ast.Constant, ast.Load,
                                     ast.Add, ast.Sub, ast.Mult, ast.Div,
                                     ast.USub, ast.UAdd)):
                raise ValueError(
                    f"formula uses an unsupported construct "
                    f"({type(node).__name__}): {v!r}")
        return v


class Geo(BaseModel):
    """Where a row sits on the earth.

    Deliberately not two ordinary dimensions. As dimensions, `latitude` and
    `longitude` would be selectable and groupable, so "oil by latitude"
    would compile cleanly, execute cleanly and mean nothing -- a wrong chart
    made entirely of valid fields, which is the one failure this design is
    built to prevent. Here a map is a *rendering* of a per-`of` grouping,
    and the nonsense question has no expression.

    This block is never rendered into the model's prompt.
    """

    model_config = ConfigDict(extra="forbid")

    lat: str                      # "wells.latitude"
    lon: str                      # "wells.longitude"
    of: str                       # the dimension the coordinates belong to


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
    derived: dict[str, Derived] = Field(default_factory=dict)
    geo: Geo | None = None
    synonyms: dict[str, list[str]] = Field(default_factory=dict)

    def measure(self, name: str) -> Measure | Derived | None:
        """Either kind, by name. Callers that need to tell them apart use
        isinstance -- most do not."""
        return self.measures.get(name) or self.derived.get(name)

    @property
    def measure_names(self) -> list[str]:
        return sorted([*self.measures, *self.derived])

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

        for key in [*self.measures, *self.derived]:
            if not IDENTIFIER.match(key):
                raise ValueError(f"measure key is not an identifier: {key!r}")

        overlap = set(self.dimensions) & set(self.measures) | \
            set(self.dimensions) & set(self.derived)
        if overlap:
            raise ValueError(f"names used as both dimension and measure: {sorted(overlap)}")
        if both := set(self.measures) & set(self.derived):
            raise ValueError(f"names declared as both measure and derived: {sorted(both)}")

        # Every operand must be a base measure on this entity. A formula
        # naming a raw column would compile to a sum of ratios rather than a
        # ratio of sums, and a formula naming another derived measure would
        # let definitions nest until nobody can read them.
        for key, d in self.derived.items():
            unknown = d.operands() - set(self.measures)
            if unknown:
                nested = unknown & set(self.derived)
                hint = (f" ({sorted(nested)} are derived; formulas compose "
                        f"base measures only)" if nested else
                        f"; base measures here: {sorted(self.measures)}")
                raise ValueError(
                    f"derived {key!r} references {sorted(unknown)}, which "
                    f"{'is' if len(unknown) == 1 else 'are'} not a base "
                    f"measure on this entity{hint}")

        if self.geo:
            if self.geo.of not in self.dimensions:
                raise ValueError(
                    f"geo.of names {self.geo.of!r}, which is not a dimension "
                    f"on this entity; declared: {sorted(self.dimensions)}")
            for side in ("lat", "lon"):
                ref = getattr(self.geo, side)
                if not QUALIFIED.match(ref):
                    raise ValueError(f"geo.{side} is not a column reference: {ref!r}")
                if "." in ref and (alias := ref.split(".", 1)[0]) not in self.joins:
                    raise ValueError(
                        f"geo.{side} routes via undeclared join alias {alias!r}")

        # synonyms are keyed by the field they resolve to; the values are
        # the free-text terms a manager might actually type.
        for field, terms in self.synonyms.items():
            if field not in self.dimensions and field not in self.measures \
                    and field not in self.derived:
                raise ValueError(
                    f"synonyms declared for unknown field {field!r}; "
                    f"known: {sorted([*self.dimensions, *self.measures, *self.derived])}"
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
        out += [f"measure {k}" for k, d in self.derived.items() if d.confidence == "low"]
        out += [f"join {k}" for k, j in self.joins.items() if j.confidence == "low"]
        return sorted(out)

    @property
    def has_low_confidence(self) -> bool:
        return bool(self.low_confidence_fields())


Layer = dict[str, Entity]
