"""Verify database results against the compiler's runtime contract."""

from __future__ import annotations

import math
from datetime import date, datetime
from functools import cmp_to_key
from typing import Any

from .compile import CompiledQuery


class ResultContractError(ValueError):
    """The database or cache returned something the compiled query cannot mean."""


def nominal_sort_key(value: str) -> bytes:
    """Mirror PostgreSQL ``COLLATE \"C\"`` for UTF-8 text outputs."""
    if not isinstance(value, str):
        raise ResultContractError("nominal ordered value is not text")
    return value.encode("utf-8")


def _parse_temporal(value: Any) -> None:
    if isinstance(value, (date, datetime)):
        return
    if not isinstance(value, str):
        raise ResultContractError("temporal value is not an ISO date")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResultContractError("temporal value is not an ISO date") from exc


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _ordering(compiled: CompiledQuery) -> list[tuple[str, str]]:
    requested = [(item.field, item.dir) for item in compiled.query.order_by]
    if requested:
        names = {field for field, _ in requested}
        return requested + [
            (column, "asc") for column in compiled.columns
            if compiled.column_kinds[column] != "quantitative" and column not in names
        ]
    if not compiled.query.dimensions:
        return []
    return [(compiled.query.measures[0].output_name, "desc")] + [
        (column, "asc") for column in compiled.columns
        if compiled.column_kinds[column] != "quantitative"
    ]


def _compare(
    compiled: CompiledQuery,
    order: list[tuple[str, str]],
    left: dict[str, Any],
    right: dict[str, Any],
) -> int:
    for field, direction in order:
        a, b = left[field], right[field]
        if a == b:
            continue
        # This mirrors SQL's explicit NULLS LAST for both ascending and
        # descending rankings.
        if a is None:
            return 1
        if b is None:
            return -1
        dimension = compiled.entity.dimensions.get(field)
        if dimension is not None and dimension.type == "string":
            a, b = nominal_sort_key(a), nominal_sort_key(b)
        try:
            result = -1 if a < b else 1
        except TypeError as exc:
            raise ResultContractError("ordered values cannot be compared") from exc
        return result if direction == "asc" else -result
    return 0


def verify_result(
    compiled: CompiledQuery,
    rows: list[dict[str, Any]],
    *,
    row_count: int,
    truncated: bool,
) -> None:
    """Raise when a fresh or cached result violates the compiled contract."""
    expected = set([*compiled.columns, *compiled.geo_columns])
    dimensions = [ref.field for ref in compiled.query.dimensions]
    seen: set[tuple[Any, ...]] = set()

    if isinstance(row_count, bool) or not isinstance(row_count, int):
        raise ResultContractError("row count is invalid")
    if not isinstance(truncated, bool):
        raise ResultContractError("truncation flag is invalid")
    if row_count != len(rows):
        raise ResultContractError("row count does not match result")
    if truncated and len(rows) != compiled.row_limit:
        raise ResultContractError("truncation does not match result")
    if not truncated and len(rows) > compiled.row_limit:
        raise ResultContractError("result exceeds its limit")

    for row in rows:
        if set(row) != expected:
            raise ResultContractError("result columns do not match query")
        for column, kind in compiled.column_kinds.items():
            value = row[column]
            if kind == "quantitative" and value is not None:
                if not _is_finite_number(value):
                    raise ResultContractError("quantitative value is not finite")
            elif kind == "temporal":
                _parse_temporal(value)
        for column in compiled.geo_columns:
            value = row[column]
            if value is not None and not _is_finite_number(value):
                raise ResultContractError("geo value is not finite")
        if dimensions:
            key = tuple(row[column] for column in dimensions)
            if key in seen:
                raise ResultContractError("grouped dimension key is duplicated")
            seen.add(key)

    order = _ordering(compiled)
    if order and rows != sorted(
        rows,
        key=cmp_to_key(lambda a, b: _compare(compiled, order, a, b)),
    ):
        raise ResultContractError("result is not in compiled order")


def verify_result_envelope(
    compiled: CompiledQuery, envelope: Any
) -> list[dict[str, Any]]:
    """Put all untrusted result-envelope shapes behind one safe exception."""
    try:
        if not isinstance(envelope, dict):
            raise ResultContractError("result envelope is not an object")
        rows = envelope["result"]
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) for row in rows
        ):
            raise ResultContractError("result rows are not objects")
        verify_result(
            compiled,
            rows,
            row_count=envelope["row_count"],
            truncated=envelope.get("truncated", False),
        )
        return rows
    except ResultContractError:
        raise
    except Exception as exc:  # arbitrary persisted JSON becomes one contract error
        raise ResultContractError("result envelope is invalid") from exc
