from __future__ import annotations

import json

from agents import function_tool


_ALLOWED_FIELDS = {"period", "geography", "metric", "formula", "other"}


@function_tool
def request_user_clarification(
    question: str,
    missing_field: str,
    reason: str,
    option_1_label: str,
    option_1_value: str,
    option_2_label: str,
    option_2_value: str,
    option_3_label: str = "",
    option_3_value: str = "",
    option_4_label: str = "",
    option_4_value: str = "",
    steps_json: str = "",
) -> dict[str, object]:
    """Ask the user for the missing parameters before continuing analysis.

    Use this tool when the available datasets, metadata, schema, or user request are not enough
    to safely generate analyst-reviewable SQL or calculations. Do not bundle geography, period,
    metric, formula, or other fields into one option. If several parameters are missing, pass
    steps_json as a JSON array of sequential clarification steps.

    Args:
        question: One concise Russian prompt for the first/current missing field.
        missing_field: One field, or comma-separated fields when steps_json is provided, from period,
            geography, metric, formula, other.
        reason: Short Russian reason explaining what is missing.
        option_1_label: First option label shown to the user.
        option_1_value: First option value appended to the user request.
        option_2_label: Second option label shown to the user.
        option_2_value: Second option value appended to the user request.
        option_3_label: Optional third option label.
        option_3_value: Optional third option value.
        option_4_label: Optional fourth option label.
        option_4_value: Optional fourth option value.
        steps_json: Optional JSON array string. Each item must contain field, question, options,
            and may contain reason. Each item's options are objects with label and value.
    """

    fields = _missing_fields(missing_field)

    options = _options_from_pairs(
        (option_1_label, option_1_value),
        (option_2_label, option_2_value),
        (option_3_label, option_3_value),
        (option_4_label, option_4_value),
    )
    steps = _steps_from_json(steps_json)

    return {
        "is_complete": False,
        "question": question.strip() or "Нужно уточнить запрос.",
        "missing_fields": fields,
        "options": options[:4],
        "steps": steps,
        "reason": reason.strip() or "Основному агенту не хватает данных для безопасного расчета.",
    }


def _options_from_pairs(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    options = []
    for label, value in pairs:
        label = label.strip()
        value = value.strip()
        if label and value:
            if _is_manual_option(label, value):
                label = "Ввести вручную"
                value = "manual"
            options.append({"label": label, "value": value})

    if not any(option["value"] == "manual" for option in options) and len(options) < 4:
        options.append({"label": "Ввести вручную", "value": "manual"})

    return options


def _steps_from_json(raw: str) -> list[dict[str, object]]:
    if not raw.strip():
        return []

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []

    if not isinstance(payload, list):
        return []

    steps: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue

        field = str(item.get("field") or "").strip().lower()
        if field not in _ALLOWED_FIELDS:
            continue

        raw_options = item.get("options")
        if not isinstance(raw_options, list):
            continue

        option_pairs: list[tuple[str, str]] = []
        for raw_option in raw_options:
            if not isinstance(raw_option, dict):
                continue
            label = str(raw_option.get("label") or "").strip()
            value = str(raw_option.get("value") or "").strip()
            if label and value:
                option_pairs.append((label, value))

        options = _options_from_pairs(*option_pairs)
        if not options:
            continue

        question = str(item.get("question") or "").strip() or "Уточните параметр запроса."
        reason = str(item.get("reason") or "").strip()
        steps.append({"field": field, "question": question, "reason": reason, "options": options[:4]})

    return steps[:5]


def _missing_fields(raw: str) -> list[str]:
    fields: list[str] = []
    for item in raw.replace("|", ",").replace(";", ",").split(","):
        field = item.strip().lower()
        if field in _ALLOWED_FIELDS and field not in fields:
            fields.append(field)
    return fields or ["other"]


def _is_manual_option(label: str, value: str) -> bool:
    normalized_label = _normalize_manual_token(label)
    normalized_value = _normalize_manual_token(value)
    return (
        normalized_value in {"manual", "__manual__", "ввести вручную", "введите вручную"}
        or normalized_label in {"ввести вручную", "введите вручную"}
        or "вручную" in normalized_label
    )


def _normalize_manual_token(value: str) -> str:
    return " ".join(value.strip().lower().replace("ё", "е").split())
