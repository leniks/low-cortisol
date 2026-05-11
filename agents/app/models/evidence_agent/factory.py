from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Any

from agents import Agent, function_tool, set_default_openai_client, set_tracing_disabled
from agents.model_settings import ModelRetryBackoffSettings, ModelRetrySettings, ModelSettings

from app.core.settings import AgentSettings
from app.models.main_agent.factory import create_openai_client
from app.models.openai_compat import NullableUsageChatCompletionsModel


SUBMIT_EVIDENCE_PACK_TOOL_NAME = "submit_evidence_pack"
PROVIDER_RETRY_SETTINGS = ModelRetrySettings(
    max_retries=5,
    backoff=ModelRetryBackoffSettings(
        initial_delay=1.0,
        max_delay=20.0,
        multiplier=2.0,
        jitter=True,
    ),
)


@function_tool(name_override=SUBMIT_EVIDENCE_PACK_TOOL_NAME, strict_mode=True)
async def submit_evidence_pack(pack: str) -> dict[str, object]:
    """Submit the compact evidence pack as a JSON string after SQL checks are complete.

    Args:
        pack: JSON string containing status, reason, coverage, datasets_used, facts,
            sql_checks, limitations, and data_verdict.
    """

    return {"pack": _normalize_pack(_parse_json_object(pack), raw_pack=pack)}


def create_evidence_agent(settings: AgentSettings, tools: Sequence[Any]) -> Agent:
    client = create_openai_client(settings)
    set_default_openai_client(client, use_for_tracing=False)
    set_tracing_disabled(True)

    return Agent(
        name="Dataset Evidence Agent",
        instructions=(
            """
            You are a dataset evidence subagent for an economist analyst.

            Your task is not to write the final user answer. Your task is to inspect selected
            dataset candidates, run only necessary DuckDB checks, and return a compact evidence
            pack that the main analyst can use.

            Input is strict JSON with type "evidence_agent_structured_input".
            The input may include data_acquisition_plan from the main analyst. Use it as the
            intended component plan, but still verify every claim through selected_datasets and SQL.

            Rules:
            - Use only selected_datasets from the input. Never use outside knowledge.
            - RAG metadata is only a hint. Verify schema and rows before making evidence claims.
            - If previous_successful_sql_checks is empty, your first tool call must be either
              get_parquet_columns or query_parquet_with_duckdb. Do not call submit_evidence_pack
              before at least one DuckDB tool result exists.
            - First inspect schemas with get_parquet_columns when needed.
            - Use query_parquet_with_duckdb for row checks, examples, aggregations, and joins.
            - You write the SQL yourself. The orchestrator will only pass selected_datasets,
              previous DuckDB outputs, and error messages back to you.
            - If a dataset does not match the requested indicator form, geography, period, or
              unit after inspection, mark it as insufficient instead of forcing an answer.
            - Respect requested measurement form: absolute value, rate, share, ratio, index,
              percentage of GDP, per-capita, growth rate, and similar forms are not interchangeable.
            - Prefer the dataset and rows that match the requested source, methodology, geography,
              period, frequency, and unit/form. Do not use a nearby indicator, deflator, broad
              index, rate, or proxy when the requested form is available in selected_datasets.
            - For simple time-series requests, return rows at the requested grain and period only.
              Include source metadata in datasets_used and put the answer SQL check before preview
              or schema checks.
            - For comparative requests, verify that the same indicator definition can be used
              across all requested objects; mark missing object-period cells instead of changing
              methodology.
            - If a direct dataset or direct rows for the requested result are absent, check whether
              the result can be calculated from component indicators before returning no_data.
            - For derived metrics or missing direct targets, gather each input component separately
              and report whether the requested or standard formula/base period can be calculated
              from explicit rows.
            - For derived metrics, do not treat absence of a ready-made target indicator as final
              no-data when selected datasets may contain input components. Return component rows,
              component coverage, and coverage.next_action calculate_from_parts or
              request_more_evidence. Use no_data only when the selected datasets cannot provide the
              target and cannot provide any required component slices.
            - When a base-year index is requested, explicitly check that the base-year row exists
              for every required component. Report missing base-year rows in coverage.missing_slices
              instead of changing the base year.
            - For research relationship requests, gather all stated indicators and control
              variables needed by the main plan. Keep indicator definitions explicit.
            - For relationship research, do not treat separate one-indicator previews as sufficient
              evidence of a relationship. Build one joined analytical SQL result at the planned
              observation grain, for example country-year or latest country observation.
            - For urbanization, prefer the direct share of urban population in total population (%)
              when available. Do not substitute population in large urban agglomerations, urban
              growth, or urban population counts when the plan asks for urbanization level/share.
            - When DuckDB supports it, compute relationship summaries in SQL with transparent
              aggregates such as count(*), corr(x, y), and grouped averages. If the main plan
              includes controls, join those control variables into the analytical rows or report
              which controls are missing.
            - The evidence pack must distinguish raw component checks from relationship checks.
              Put the joined relationship SQL check first. If no joined paired rows can be built,
              set status to insufficient_data and coverage.next_action to request_more_evidence
              or no_data; do not claim a correlation.
            - If several datasets are needed, join them in DuckDB with read_parquet('...') aliases.
            - Keep SQL transparent and small. Prefer max_rows 100 unless a smaller preview is enough.
            - If a DuckDB call fails with a SQL/schema/path error, inspect the returned error,
              adjust the SQL or schema assumptions, and call query_parquet_with_duckdb again while
              remaining_sql_checks is above zero.
            - Do not stop after the first failed SQL when there are remaining SQL checks.
            - If previous_successful_sql_checks are provided in a retry input, reuse them in the final pack
              instead of rerunning the same SQL.
            - If previous_duckdb_outputs include zero rows, inspect the returned columns/rows and
              change the filters, shape, or selected dataset yourself.
            - Do not ask the user questions. Report missing fields or missing data in limitations.
            - Do not produce a narrative final answer.
            - Finish by calling submit_evidence_pack exactly once. Pass the `pack` argument as a
              JSON string, not as a nested object. Do not write the pack as text.
            - In submit_evidence_pack, put the best answer-producing sql_check first. Omit broad
              preview SQL checks if a narrower answer SQL check supersedes them.

            Coverage rules:
            - Use answer_directly when SQL rows cover the requested indicator, geography, and period well enough.
            - Use request_more_evidence when another clearly named indicator/component is needed and no user
              clarification is required.
            - Use calculate_from_parts when all required parts are available or can be requested and the formula
              is explicit, standard from the user's wording, or a defensible standard alternative to
              a missing direct target.
            - Use ask_clarification only when the missing formula, metric definition, geography, or period cannot
              be inferred safely.
            - Use no_data only when both direct datasets/rows and sufficient formula components are
              absent after checks.
            """
        ),
        tools=[*tools, submit_evidence_pack],
        tool_use_behavior={"stop_at_tool_names": [SUBMIT_EVIDENCE_PACK_TOOL_NAME]},
        model_settings=ModelSettings(
            parallel_tool_calls=False,
            retry=PROVIDER_RETRY_SETTINGS,
        ),
        model=NullableUsageChatCompletionsModel(
            model=settings.yandex_chat_model,
            openai_client=client,
        ),
    )


def _parse_json_object(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    if isinstance(value, dict) and isinstance(value.get("pack"), dict):
        return dict(value["pack"])
    return dict(value) if isinstance(value, dict) else {}


def _normalize_pack(value: dict[str, object], *, raw_pack: str) -> dict[str, object]:
    status = _status(value.get("status"))
    reason = _text(value.get("reason")) or _text(value.get("data_verdict")) or "Evidence pack submitted."
    sql_checks = _sql_checks(value.get("sql_checks"))
    facts = _string_list(value.get("facts"))
    limitations = _string_list(value.get("limitations"))
    return {
        "status": status,
        "reason": reason,
        "coverage": _coverage(value.get("coverage"), status=status, sql_checks=sql_checks, limitations=limitations),
        "datasets_used": _datasets_used(value.get("datasets_used")),
        "facts": facts,
        "sql_checks": sql_checks,
        "limitations": limitations,
        "data_verdict": _text(value.get("data_verdict")) or reason,
        "raw_pack": raw_pack if not value else "",
    }


def _status(value: object) -> str:
    allowed = {"ok", "no_relevant_dataset", "no_rows", "insufficient_data", "error"}
    text = _text(value)
    return text if text in allowed else "insufficient_data"


def _coverage(
    value: object,
    *,
    status: str,
    sql_checks: list[dict[str, object]],
    limitations: list[str],
) -> dict[str, object]:
    raw = value if isinstance(value, dict) else {}
    next_action = _text(raw.get("next_action"))
    allowed_actions = {"answer_directly", "request_more_evidence", "calculate_from_parts", "ask_clarification", "no_data"}
    if next_action not in allowed_actions:
        next_action = "answer_directly" if _has_rows(sql_checks) else "no_data"
        if status == "insufficient_data" and not _has_rows(sql_checks):
            next_action = "ask_clarification" if limitations else "request_more_evidence"
    return {
        "requested_indicators": _string_list(raw.get("requested_indicators")),
        "requested_geographies": _string_list(raw.get("requested_geographies")),
        "requested_period": _text(raw.get("requested_period")),
        "found_slices": _string_list(raw.get("found_slices")),
        "missing_slices": _string_list(raw.get("missing_slices")) or limitations,
        "computable_from_parts": bool(raw.get("computable_from_parts")),
        "required_parts": _string_list(raw.get("required_parts")),
        "next_action": next_action,
        "reason": _text(raw.get("reason")) or "Coverage normalized from evidence pack.",
    }


def _datasets_used(value: object) -> list[dict[str, object]]:
    datasets: list[dict[str, object]] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name")) or _text(item.get("dataset_id"))
        if not name:
            continue
        datasets.append(
            {
                "dataset_id": _text(item.get("dataset_id")),
                "name": name,
                "source": _text(item.get("source")),
                "unit": _text(item.get("unit")),
            }
        )
    return datasets


def _sql_checks(value: object) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        sql = _text(item.get("sql"))
        if not sql:
            continue
        rows = _as_list(item.get("rows"))
        columns = _string_list(item.get("columns"))
        checks.append(
            {
                "purpose": _text(item.get("purpose")) or "SQL-проверка данных.",
                "sql": sql,
                "row_count": _int(item.get("row_count"), default=len(rows)),
                "columns": columns,
                "rows": rows,
                "used_dataset_names": _string_list(item.get("used_dataset_names")),
            }
        )
    return checks


def _has_rows(sql_checks: list[dict[str, object]]) -> bool:
    return any(_as_list(check.get("rows")) for check in sql_checks)


def _string_list(value: object) -> list[str]:
    return [text for item in _as_list(value) if (text := _text(item))]


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


def _text(value: object) -> str:
    return " ".join(str(value or "").split())
