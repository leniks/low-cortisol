from __future__ import annotations

import json
import math
from statistics import mean
from typing import Any

from agents import function_tool


@function_tool
def calculate_basic(operation: str, values_json: str, precision: int = 4) -> dict[str, object]:
    """Run deterministic arithmetic on explicit evidence values.

    Args:
        operation: One of sum, average, min, max, difference, percentage_change, ratio,
            share_percent, correlation.
        values_json: JSON array of numbers, or for correlation either
            {"x": [...], "y": [...]} or [{"x": 1, "y": 2}, ...].
        precision: Decimal places for numeric output.
    """

    op = " ".join(operation.strip().lower().split())
    digits = max(0, min(int(precision or 4), 10))
    payload = _parse_values(values_json)

    try:
        result = _calculate(op, payload)
    except ValueError as exc:
        return {
            "operation": op,
            "error": str(exc),
            "result": None,
        }

    return {
        "operation": op,
        "result": _round_number(result, digits),
        "precision": digits,
    }


def _parse_values(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("values_json must be valid JSON.") from exc


def _calculate(operation: str, payload: Any) -> float:
    if operation == "correlation":
        x_values, y_values = _paired_values(payload)
        if len(x_values) < 2:
            raise ValueError("correlation requires at least two paired values.")
        return _correlation(x_values, y_values)

    values = _numeric_values(payload)
    if not values:
        raise ValueError("operation requires at least one numeric value.")

    if operation == "sum":
        return sum(values)
    if operation in {"average", "avg", "mean"}:
        return mean(values)
    if operation == "min":
        return min(values)
    if operation == "max":
        return max(values)
    if operation == "difference":
        if len(values) != 2:
            raise ValueError("difference requires exactly two values: [left, right].")
        return values[0] - values[1]
    if operation == "percentage_change":
        if len(values) != 2:
            raise ValueError("percentage_change requires exactly two values: [old, new].")
        if values[0] == 0:
            raise ValueError("percentage_change cannot use zero as the old value.")
        return (values[1] - values[0]) / values[0] * 100
    if operation == "ratio":
        if len(values) != 2:
            raise ValueError("ratio requires exactly two values: [numerator, denominator].")
        if values[1] == 0:
            raise ValueError("ratio denominator cannot be zero.")
        return values[0] / values[1]
    if operation == "share_percent":
        if len(values) != 2:
            raise ValueError("share_percent requires exactly two values: [part, total].")
        if values[1] == 0:
            raise ValueError("share_percent total cannot be zero.")
        return values[0] / values[1] * 100

    raise ValueError(
        "unsupported operation. Use sum, average, min, max, difference, "
        "percentage_change, ratio, share_percent, or correlation."
    )


def _numeric_values(payload: Any) -> list[float]:
    if not isinstance(payload, list):
        raise ValueError("values_json must be a JSON array for this operation.")

    values: list[float] = []
    for item in payload:
        if isinstance(item, bool) or item is None:
            continue
        if isinstance(item, int | float):
            if math.isfinite(float(item)):
                values.append(float(item))
            continue
        if isinstance(item, str):
            try:
                value = float(item.replace(",", "."))
            except ValueError:
                continue
            if math.isfinite(value):
                values.append(value)
    return values


def _paired_values(payload: Any) -> tuple[list[float], list[float]]:
    if isinstance(payload, dict):
        return _numeric_values(payload.get("x")), _numeric_values(payload.get("y"))

    if not isinstance(payload, list):
        raise ValueError("correlation requires a JSON object with x/y arrays or an array of x/y objects.")

    x_values: list[float] = []
    y_values: list[float] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        x = _coerce_number(item.get("x"))
        y = _coerce_number(item.get("y"))
        if x is None or y is None:
            continue
        x_values.append(x)
        y_values.append(y)
    return x_values, y_values


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.replace(",", "."))
        except ValueError:
            return None
    else:
        return None

    return number if math.isfinite(number) else None


def _correlation(x_values: list[float], y_values: list[float]) -> float:
    if len(x_values) != len(y_values):
        raise ValueError("correlation requires equal x and y lengths.")

    x_mean = mean(x_values)
    y_mean = mean(y_values)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values, strict=True))
    x_var = sum((x - x_mean) ** 2 for x in x_values)
    y_var = sum((y - y_mean) ** 2 for y in y_values)
    denominator = math.sqrt(x_var * y_var)
    if denominator == 0:
        raise ValueError("correlation is undefined when one series has zero variance.")
    return numerator / denominator


def _round_number(value: float, precision: int) -> int | float:
    rounded = round(value, precision)
    if precision == 0:
        return int(rounded)
    return rounded
