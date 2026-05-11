import json
from typing import Literal

from agents import function_tool
from pydantic import BaseModel, ConfigDict, Field


class StrictSchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataComponent(StrictSchemaModel):
    name: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    geography: str = ""
    period: str = ""
    unit_or_form: str = ""
    role: Literal["target", "comparison", "input", "control", "dimension"] = "target"
    reason: str = Field(min_length=1)


class EvidenceTask(StrictSchemaModel):
    goal: str = Field(min_length=1)
    search_text: str = Field(min_length=1)
    expected_output: str = Field(min_length=1)


class DataAcquisitionPlan(StrictSchemaModel):
    task_type: Literal[
        "direct_data",
        "comparison",
        "derived_metric",
        "research_relationship",
        "no_data_expected",
        "no_data_needed",
        "other",
    ]
    goal: str = Field(min_length=1)
    assumptions: tuple[str, ...] = ()
    required_data_components: tuple[DataComponent, ...] = ()
    evidence_tasks: tuple[EvidenceTask, ...] = ()
    needs_clarification: bool = False
    clarification_fields: tuple[Literal["period", "geography", "metric", "formula", "other"], ...] = ()
    calculation_strategy: str = ""
    no_data_exit_rule: str = Field(min_length=1)
    finalization_strategy: str = Field(min_length=1)


@function_tool(strict_mode=True)
async def submit_data_acquisition_plan(plan: str) -> dict[str, object]:
    """Submit the first-step plan as a JSON string.

    Args:
        plan: JSON string matching the DataAcquisitionPlan shape.
    """

    parsed = _parse_json_object(plan)
    return {
        "type": "data_acquisition_plan",
        "plan": _normalize_plan(parsed, raw_plan=plan),
    }


def _parse_json_object(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    if isinstance(value, dict) and isinstance(value.get("plan"), dict):
        return dict(value["plan"])
    return dict(value) if isinstance(value, dict) else {}


def _normalize_plan(value: dict[str, object], *, raw_plan: str) -> dict[str, object]:
    try:
        return DataAcquisitionPlan.model_validate(value).model_dump(mode="json")
    except Exception:
        return {
            "task_type": _task_type(value.get("task_type")),
            "goal": _text(value.get("goal")) or "Получить и проверить данные по запросу пользователя.",
            "assumptions": _string_list(value.get("assumptions")),
            "required_data_components": _component_list(value.get("required_data_components")),
            "evidence_tasks": _evidence_task_list(value.get("evidence_tasks")),
            "needs_clarification": bool(value.get("needs_clarification")),
            "clarification_fields": _clarification_fields(value.get("clarification_fields")),
            "calculation_strategy": _text(value.get("calculation_strategy")),
            "no_data_exit_rule": (
                _text(value.get("no_data_exit_rule"))
                or _text(value.get("exit_rule"))
                or "Если evidence-поиск и SQL-проверки не подтвердят данные, честно сообщить об отсутствии данных."
            ),
            "finalization_strategy": (
                _text(value.get("finalization_strategy"))
                or "Отвечать только по evidence pack, SQL-проверкам и расчетам."
            ),
            "raw_plan": raw_plan,
        }


def _task_type(value: object) -> str:
    aliases = {
        "data": "direct_data",
        "data_extraction": "direct_data",
        "data_lookup": "direct_data",
        "data_lookup_dynamics": "direct_data",
        "data_like": "direct_data",
        "data_request": "direct_data",
        "data_retrieval": "direct_data",
        "direct": "direct_data",
        "direct_request": "direct_data",
        "direct_data_request": "direct_data",
        "extract_data": "direct_data",
        "retrieval": "direct_data",
        "compare": "comparison",
        "comparison_request": "comparison",
        "calculation": "derived_metric",
        "calculated": "derived_metric",
        "computed": "derived_metric",
        "research": "research_relationship",
        "relationship": "research_relationship",
        "relationship_research": "research_relationship",
        "no_data": "no_data_expected",
        "none": "no_data_needed",
    }
    allowed = {
        "direct_data",
        "comparison",
        "derived_metric",
        "research_relationship",
        "no_data_expected",
        "no_data_needed",
        "other",
    }
    text = _text(value)
    text = aliases.get(text, text)
    return text if text in allowed else "other"


def _clarification_fields(value: object) -> list[str]:
    allowed = {"period", "geography", "metric", "formula", "other"}
    return [item for item in _string_list(value) if item in allowed]


def _component_list(value: object) -> list[dict[str, object]]:
    components: list[dict[str, object]] = []
    for item in _as_list(value):
        if isinstance(item, str):
            metric = _text(item)
            if not metric:
                continue
            components.append(
                {
                    "name": metric,
                    "metric": metric,
                    "geography": "",
                    "period": "",
                    "unit_or_form": "",
                    "role": "target",
                    "reason": "Компонент нужен для ответа на запрос.",
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        metric = (
            _text(item.get("metric"))
            or _text(item.get("indicator"))
            or _text(item.get("name"))
            or _text(item.get("component_name"))
        )
        components.append(
            {
                "name": _text(item.get("name")) or metric or "Компонент данных",
                "metric": metric or "Неуточненный показатель",
                "geography": _text(item.get("geography")),
                "period": _text(item.get("period")),
                "unit_or_form": _text(item.get("unit_or_form")),
                "role": _component_role(item.get("role")),
                "reason": _text(item.get("reason")) or "Компонент нужен для ответа на запрос.",
            }
        )
    return components


def _evidence_task_list(value: object) -> list[dict[str, object]]:
    tasks: list[dict[str, object]] = []
    for item in _as_list(value):
        if isinstance(item, str):
            search_text = _text(item)
            if not search_text:
                continue
            tasks.append(
                {
                    "goal": search_text,
                    "search_text": search_text,
                    "expected_output": "Evidence pack с SQL-проверками.",
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        search_texts = _string_list(
            item.get("search_texts")
            or item.get("search_queries")
            or item.get("queries")
            or item.get("search_text")
            or item.get("query_focus")
        )
        if not search_texts:
            search_texts = _numbered_search_texts(item)
        if not search_texts:
            search_texts = [
                _text(item.get("goal"))
                or _text(item.get("description"))
                or _text(item.get("additional_instructions"))
                or _text(item.get("expected_output"))
                or "данные статистика"
            ]
        for search_text in search_texts:
            tasks.append(
                {
                    "goal": (
                        _text(item.get("goal"))
                        or _text(item.get("description"))
                        or _text(item.get("additional_instructions"))
                        or search_text
                        or "Найти и проверить данные."
                    ),
                    "search_text": search_text or _text(item.get("goal")) or "данные статистика",
                    "expected_output": _text(item.get("expected_output")) or "Evidence pack с SQL-проверками.",
                }
            )
    return tasks


def _component_role(value: object) -> str:
    allowed = {"target", "comparison", "input", "control", "dimension"}
    text = _text(value)
    return text if text in allowed else "target"


def _numbered_search_texts(item: dict[object, object]) -> list[str]:
    texts: list[str] = []
    for index in range(1, 5):
        text = _text(item.get(f"search_text_{index}"))
        if text:
            texts.append(text)
    return texts


def _string_list(value: object) -> list[str]:
    return [text for item in _as_list(value) if not isinstance(item, dict) and (text := _text(item))]


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _text(value: object) -> str:
    return " ".join(str(value or "").split())
