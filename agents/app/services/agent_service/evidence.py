from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
import asyncio
import json
import re
from typing import Any

from agents import Runner, function_tool

from app.contracts import EnrichedQuery, ParquetCandidate, UserRequest
from app.core.settings import AgentSettings
from app.models.evidence_agent import create_evidence_agent
from app.models.main_agent.duckdb_tools import (
    DEFAULT_RESULT_ROWS,
    get_parquet_columns_impl,
    query_parquet_with_duckdb_impl,
)
from app.models.query_enricher import QueryEnricherModel
from app.models.rag_retriever import RagRetrieverModel


DEFAULT_MAX_RETRIEVALS = 4
DEFAULT_MAX_DUCKDB_CHECKS = 10
DEFAULT_MAX_EVIDENCE_AGENT_ATTEMPTS = 10


@dataclass(frozen=True)
class EvidenceRequest:
    current_user_message: str
    analysis_goal: str
    search_texts: tuple[str, ...]
    conversation_id: str | None
    history_payload: list[dict[str, object]]
    data_acquisition_plan: dict[str, object] | None


@dataclass
class EvidenceBudget:
    max_retrievals: int = DEFAULT_MAX_RETRIEVALS
    max_duckdb_checks: int = DEFAULT_MAX_DUCKDB_CHECKS
    retrievals_used: int = 0
    duckdb_checks_used: int = 0

    @property
    def retrievals_remaining(self) -> int:
        return max(self.max_retrievals - self.retrievals_used, 0)

    @property
    def duckdb_checks_remaining(self) -> int:
        return max(self.max_duckdb_checks - self.duckdb_checks_used, 0)

    def reserve_retrieval(self) -> bool:
        if self.retrievals_remaining <= 0:
            return False
        self.retrievals_used += 1
        return True

    def reserve_duckdb_check(self) -> bool:
        if self.duckdb_checks_remaining <= 0:
            return False
        self.duckdb_checks_used += 1
        return True

    def payload(self) -> dict[str, int]:
        return {
            "max_retrievals": self.max_retrievals,
            "retrievals_used": self.retrievals_used,
            "retrievals_remaining": self.retrievals_remaining,
            "max_duckdb_checks": self.max_duckdb_checks,
            "duckdb_checks_used": self.duckdb_checks_used,
            "duckdb_checks_remaining": self.duckdb_checks_remaining,
        }


@dataclass
class EvidenceTraceBuffer:
    events: list[dict[str, object]] = field(default_factory=list)

    def add(
        self,
        event_type: str,
        *,
        title: str,
        tool: str,
        payload: object,
        phase: str | None = None,
        status: str | None = None,
        visibility: str | None = None,
    ) -> None:
        event: dict[str, object] = {
            "type": event_type,
            "title": title,
            "tool": tool,
            "payload": payload,
        }
        if phase is not None:
            event["phase"] = phase
        if status is not None:
            event["status"] = status
        if visibility is not None:
            event["visibility"] = visibility
        self.events.append(event)


@dataclass
class EvidenceSqlState:
    schema_outputs: list[dict[str, object]] = field(default_factory=list)
    query_outputs: list[dict[str, object]] = field(default_factory=list)

    @property
    def successful_query_outputs(self) -> list[dict[str, object]]:
        return [
            output
            for output in self.query_outputs
            if isinstance(output, dict) and not output.get("error")
        ]

    @property
    def failed_query_outputs(self) -> list[dict[str, object]]:
        return [
            output
            for output in self.query_outputs
            if isinstance(output, dict) and output.get("error")
        ]


class EvidenceService:
    def __init__(
        self,
        *,
        query_enricher: QueryEnricherModel,
        rag_retriever_factory: Callable[[], RagRetrieverModel],
    ) -> None:
        self._query_enricher = query_enricher
        self._rag_retriever_factory = rag_retriever_factory
        self._settings: AgentSettings | None = None

    async def collect(
        self,
        request: EvidenceRequest,
        *,
        budget: EvidenceBudget,
    ) -> dict[str, object]:
        trace = EvidenceTraceBuffer()
        settings = self._get_settings()
        search_texts = self._request_search_texts(request)

        trace.add(
            "tool_call",
            title="Подбираю данные для анализа",
            tool="request_evidence",
            payload={
                "analysis_goal": request.analysis_goal,
                "requested_search_texts": search_texts,
                "budget": budget.payload(),
            },
            phase="retrieval",
            status="running",
            visibility="summary",
        )

        if not search_texts:
            return self._pack(
                status="insufficient_data",
                reason="Не передан поисковый запрос для подбора датасетов.",
                budget=budget,
                trace=trace,
                limitations=["Основной агент не сформулировал search_text для evidence-поиска."],
            )

        enriched_queries: list[EnrichedQuery] = []
        retrieved_batches: list[tuple[ParquetCandidate, ...]] = []
        for query_index, search_text in enumerate(search_texts, start=1):
            if not budget.reserve_retrieval():
                trace.add(
                    "tool_result",
                    title="Evidence: лимит RAG исчерпан",
                    tool="pgvector_rag_search",
                    payload={"skipped_search_text": search_text, "budget": budget.payload()},
                    phase="retrieval",
                    status="retry",
                    visibility="detail",
                )
                break

            user_request = UserRequest(
                message=search_text,
                conversation_id=request.conversation_id,
                history=tuple(request.history_payload),
            )
            trace.add(
                "tool_call",
                title=f"Evidence: обогащение запроса #{query_index}",
                tool="query_enricher",
                payload={"message": search_text},
                phase="retrieval",
                status="running",
                visibility="detail",
            )
            try:
                enriched = await self._query_enricher.enrich(user_request)
            except Exception as exc:
                enriched = EnrichedQuery(
                    original=search_text,
                    enriched=search_text,
                    metadata={
                        "source": "evidence_query_enricher_fallback",
                        "query_enricher_error": str(exc),
                    },
                )
            enriched_queries.append(enriched)
            trace.add(
                "tool_result",
                title=f"Evidence: search text #{query_index}",
                tool="query_enricher",
                payload={"search_text": enriched.enriched, "metadata": enriched.metadata},
                phase="retrieval",
                status="done",
                visibility="detail",
            )

            trace.add(
                "tool_call",
                title=f"Ищу датасеты #{query_index}",
                tool="pgvector_rag_search",
                payload={
                    "search_text": enriched.enriched,
                    "table": settings.rag_vector_table,
                    "embedding_column": settings.rag_embedding_column,
                    "top_k": settings.rag_top_k,
                },
                phase="retrieval",
                status="running",
                visibility="summary",
            )
            try:
                batch = await self._rag_retriever_factory().retrieve(enriched)
            except Exception as exc:
                trace.add(
                    "tool_result",
                    title=f"Evidence: ошибка RAG-поиска #{query_index}",
                    tool="pgvector_rag_search",
                    payload={"error": str(exc)},
                    phase="retrieval",
                    status="error",
                    visibility="detail",
                )
                continue
            tagged_batch = self._tag_retrieved_candidates(
                batch,
                query_index=query_index,
                retrieval_query=enriched.enriched,
            )
            retrieved_batches.append(tagged_batch)
            trace.add(
                "tool_result",
                title=f"Найдены кандидаты датасетов #{query_index}",
                tool="pgvector_rag_search",
                payload={
                    "count": len(tagged_batch),
                    "datasets": _dataset_cards(tagged_batch),
                },
                phase="retrieval",
                status="done",
                visibility="summary",
            )

        candidates = self._merge_retrieved_batches(retrieved_batches)
        selected_candidates = self._select_retrieved_candidates(
            candidates,
            settings.rag_rerank_max_keep,
        )
        trace.add(
            "tool_result",
            title="Отобраны датасеты для SQL-проверки",
            tool="technical_dataset_filter",
            payload={
                "input_count": len(candidates),
                "output_count": len(selected_candidates),
                "max_keep": settings.rag_rerank_max_keep,
                "selection_policy": "requires_parquet_uri_only_sql_subagent_validates_relevance_and_has_data",
                "filter_stats": _candidate_filter_stats(candidates),
                "datasets": _dataset_cards(selected_candidates),
            },
            phase="retrieval",
            status="done",
            visibility="summary",
        )

        if not selected_candidates:
            return self._pack(
                status="no_relevant_dataset",
                reason="RAG не вернул датасеты с parquet_uri/data_path, поэтому SQL-проверка невозможна.",
                budget=budget,
                trace=trace,
                searched_texts=[query.enriched for query in enriched_queries],
                datasets=_dataset_cards(candidates),
                limitations=[
                    "Нет выбранных датасетов для SQL-проверки: у найденных RAG-кандидатов отсутствует parquet_uri/data_path."
                ],
            )

        sql_state = EvidenceSqlState()
        tools = self._create_budgeted_duckdb_tools(
            budget=budget,
            candidates=selected_candidates,
            trace=trace,
            sql_state=sql_state,
        )
        evidence_agent = create_evidence_agent(settings, tools=tools)
        input_payload = {
            "type": "evidence_agent_structured_input",
            "current_user_message": request.current_user_message,
            "analysis_goal": request.analysis_goal,
            "data_acquisition_plan": request.data_acquisition_plan,
            "retrieval_queries": [query.enriched for query in enriched_queries],
            "selected_datasets": _dataset_cards(selected_candidates),
            "budget": budget.payload(),
        }

        parsed: dict[str, object] | None = None
        raw_output = ""
        attempt_errors: list[str] = []
        max_attempts = min(DEFAULT_MAX_EVIDENCE_AGENT_ATTEMPTS, budget.max_duckdb_checks)
        for attempt in range(1, max_attempts + 1):
            if budget.duckdb_checks_remaining <= 0 and not sql_state.successful_query_outputs:
                trace.add(
                    "tool_result",
                    title="Evidence: лимит SQL-проверок исчерпан",
                    tool="evidence_agent",
                    payload={"attempt": attempt, "budget": budget.payload()},
                    phase="sql",
                    status="retry",
                    visibility="detail",
                )
                break

            trace.add(
                "tool_call",
                title=f"Запускаю SQL-проверку #{attempt}",
                tool="evidence_agent",
                payload={
                    "analysis_goal": request.analysis_goal,
                    "selected_datasets_count": len(selected_candidates),
                    "budget": budget.payload(),
                    "previous_sql_attempts": len(sql_state.query_outputs),
                    "previous_sql_errors": _sql_error_summaries(sql_state.failed_query_outputs),
                },
                phase="sql",
                status="running",
                visibility="summary" if attempt == 1 else "detail",
            )
            attempt_payload = dict(input_payload)
            attempt_payload.update(
                {
                    "attempt": attempt,
                    "remaining_sql_checks": budget.duckdb_checks_remaining,
                    "previous_sql_errors": _sql_error_summaries(sql_state.failed_query_outputs),
                    "previous_duckdb_outputs": _compact_outputs(sql_state.query_outputs[-3:]),
                    "previous_successful_sql_checks": _sql_outputs_to_checks(sql_state.successful_query_outputs),
                    "retry_instruction": _evidence_retry_instruction(sql_state),
                }
            )

            try:
                result = await Runner.run(
                    evidence_agent,
                    [{"role": "user", "content": json.dumps(attempt_payload, ensure_ascii=False, default=str)}],
                )
            except Exception as exc:
                error = str(exc)
                attempt_errors.append(error)
                trace.add(
                    "tool_result",
                    title=f"Evidence: ошибка SQL-субагента #{attempt}",
                    tool="evidence_agent",
                    payload={
                        "attempt": attempt,
                        "error": error,
                        "will_retry": attempt < max_attempts and budget.duckdb_checks_remaining > 0,
                        "budget": budget.payload(),
                    },
                    phase="sql",
                    status="retry" if attempt < max_attempts and budget.duckdb_checks_remaining > 0 else "error",
                    visibility="detail",
                )
                if budget.duckdb_checks_remaining <= 0:
                    break
                await asyncio.sleep(min(1.5 * attempt, 8.0))
                continue

            raw_output = str(result.final_output or "").strip()
            parsed = _parse_evidence_pack_output(result.final_output)
            retry_reasons = _evidence_retry_reasons(parsed, sql_state)
            trace.add(
                "tool_result",
                title=f"SQL-проверка #{attempt} завершена",
                tool="evidence_agent",
                payload={
                    "attempt": attempt,
                    "submitted_pack": isinstance(parsed, dict),
                    "successful_sql_checks": len(sql_state.successful_query_outputs),
                    "failed_sql_checks": len(sql_state.failed_query_outputs),
                    "retry_reasons": retry_reasons,
                    "will_retry": bool(retry_reasons and attempt < max_attempts and budget.duckdb_checks_remaining > 0),
                    "budget": budget.payload(),
                },
                phase="sql",
                status="retry" if retry_reasons and attempt < max_attempts and budget.duckdb_checks_remaining > 0 else "done",
                visibility="summary",
            )
            if not retry_reasons:
                break
            if budget.duckdb_checks_remaining <= 0:
                break

        if not isinstance(parsed, dict):
            if sql_state.successful_query_outputs:
                parsed = {
                    "status": "ok",
                    "reason": "DuckDB SQL выполнен; evidence pack собран из результатов SQL.",
                    "facts": [],
                    "sql_checks": [],
                    "limitations": attempt_errors,
                    "data_verdict": "SQL-проверки см. в сохранённых DuckDB outputs.",
                }
            else:
                parsed = {
                    "status": "error",
                    "reason": "Evidence-субагент не вызвал submit_evidence_pack и не смог выполнить DuckDB SQL.",
                    "facts": [],
                    "sql_checks": [],
                    "limitations": ["Evidence-субагент не вызвал submit_evidence_pack.", *attempt_errors],
                    "data_verdict": "SQL-проверки не были выполнены.",
                }

        pack = self._normalize_agent_pack(
            parsed,
            selected_candidates=selected_candidates,
            searched_texts=[query.enriched for query in enriched_queries],
            budget=budget,
            sql_state=sql_state,
        )
        trace.add(
            "tool_result",
            title="Данные для ответа подготовлены",
            tool="evidence_agent",
            payload={
                "status": pack.get("status"),
                "coverage": pack.get("coverage"),
                "datasets_used": pack.get("datasets_used"),
                "facts": pack.get("facts"),
                "sql_checks_count": len(pack.get("sql_checks") if isinstance(pack.get("sql_checks"), list) else []),
                "limitations": pack.get("limitations"),
                "budget": budget.payload(),
            },
            phase="retrieval",
            status="done",
            visibility="summary",
        )
        pack["_trace_events"] = trace.events
        return pack

    def _get_settings(self) -> AgentSettings:
        if self._settings is None:
            self._settings = AgentSettings.from_env()
        return self._settings

    def _request_search_texts(self, request: EvidenceRequest) -> tuple[str, ...]:
        texts: list[str] = []
        for value in request.search_texts:
            text = " ".join(str(value or "").split())
            if text and text not in texts:
                texts.append(text)

        for fallback in (request.analysis_goal, request.current_user_message):
            text = " ".join(str(fallback or "").split())
            if text and text not in texts:
                texts.append(text)

        return tuple(texts[:DEFAULT_MAX_RETRIEVALS])

    def _create_budgeted_duckdb_tools(
        self,
        *,
        budget: EvidenceBudget,
        candidates: tuple[ParquetCandidate, ...],
        trace: EvidenceTraceBuffer,
        sql_state: EvidenceSqlState,
    ) -> tuple[Any, Any]:
        @function_tool
        def get_parquet_columns(parquet_path: str) -> dict[str, object]:
            if not budget.reserve_duckdb_check():
                return {
                    "error": "duckdb_budget_exhausted",
                    "message": "Evidence DuckDB/schema check budget is exhausted.",
                    "budget": budget.payload(),
                }

            trace.add(
                "tool_call",
                title="Evidence SQL: схема parquet",
                tool="get_parquet_columns",
                payload={"parquet_path": parquet_path, "budget": budget.payload()},
                phase="sql",
                status="running",
                visibility="detail",
            )
            try:
                output = get_parquet_columns_impl(parquet_path)
            except Exception as exc:
                output = {
                    "parquet_path": parquet_path,
                    "error": str(exc),
                }
            sql_state.schema_outputs.append(dict(output))
            trace.add(
                "tool_result",
                title="Evidence SQL: схема parquet",
                tool="get_parquet_columns",
                payload=_compact_tool_output(output),
                phase="sql",
                status="error" if output.get("error") else "done",
                visibility="detail",
            )
            return output

        @function_tool
        def query_parquet_with_duckdb(sql: str, parquet_path: str = "", max_rows: int = DEFAULT_RESULT_ROWS) -> dict[str, object]:
            if not budget.reserve_duckdb_check():
                return {
                    "error": "duckdb_budget_exhausted",
                    "message": "Evidence DuckDB/schema check budget is exhausted.",
                    "budget": budget.payload(),
                }

            trace.add(
                "tool_call",
                title="Evidence SQL: DuckDB-запрос",
                tool="query_parquet_with_duckdb",
                payload={
                    "sql": sql,
                    "parquet_path": parquet_path,
                    "max_rows": max_rows,
                    "budget": budget.payload(),
                },
                phase="sql",
                status="running",
                visibility="detail",
            )
            try:
                output = query_parquet_with_duckdb_impl(
                    sql=sql,
                    parquet_path=parquet_path,
                    max_rows=max_rows,
                )
                used_dataset_names = _sql_used_dataset_names(output, candidates)
                if used_dataset_names:
                    output["used_dataset_names"] = used_dataset_names
                    trace.add(
                        "tool_result",
                        title="Определил датасеты для SQL",
                        tool="sql_dataset_usage",
                        payload="\n".join(used_dataset_names),
                        phase="sql",
                        status="done",
                        visibility="summary",
                    )
            except Exception as exc:
                output = {
                    "sql": sql,
                    "parquet_path": parquet_path or None,
                    "error": str(exc),
                }
            sql_state.query_outputs.append(dict(output))
            trace.add(
                "tool_result",
                title="Evidence SQL: результат DuckDB",
                tool="query_parquet_with_duckdb",
                payload=_compact_tool_output(output),
                phase="sql",
                status="error" if output.get("error") else "done",
                visibility="detail",
            )
            return output

        return get_parquet_columns, query_parquet_with_duckdb

    @staticmethod
    def _tag_retrieved_candidates(
        candidates: tuple[ParquetCandidate, ...],
        *,
        query_index: int,
        retrieval_query: str,
    ) -> tuple[ParquetCandidate, ...]:
        tagged: list[ParquetCandidate] = []
        for candidate in candidates:
            metadata = dict(candidate.metadata)
            metadata.update(
                {
                    "retrieval_query_index": query_index,
                    "retrieval_query": retrieval_query,
                }
            )
            tagged.append(replace(candidate, metadata=metadata))
        return tuple(tagged)

    @staticmethod
    def _merge_retrieved_batches(
        batches: list[tuple[ParquetCandidate, ...]],
    ) -> tuple[ParquetCandidate, ...]:
        merged: list[ParquetCandidate] = []
        seen: set[tuple[str, str]] = set()
        for batch in batches:
            for candidate in batch:
                key = (candidate.dataset_id, candidate.parquet_uri)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(candidate)
        return tuple(merged)

    @staticmethod
    def _select_retrieved_candidates(
        candidates: tuple[ParquetCandidate, ...],
        max_keep: int,
    ) -> tuple[ParquetCandidate, ...]:
        limit = max_keep if max_keep > 0 else len(candidates)
        groups: dict[int, list[ParquetCandidate]] = {}
        for candidate in candidates:
            if not candidate.parquet_uri:
                continue
            query_index = _safe_int(candidate.metadata.get("retrieval_query_index")) or 0
            groups.setdefault(query_index, []).append(candidate)

        selected: list[ParquetCandidate] = []
        offsets = {query_index: 0 for query_index in groups}
        group_order = sorted(groups)
        while len(selected) < limit:
            added = False
            for query_index in group_order:
                group = groups[query_index]
                offset = offsets[query_index]
                if offset >= len(group):
                    continue
                candidate = group[offset]
                offsets[query_index] = offset + 1
                metadata = dict(candidate.metadata)
                metadata.update(
                    {
                        "selected_by": "evidence_balanced_vector_score",
                        "selected_rank": len(selected) + 1,
                    }
                )
                selected.append(replace(candidate, metadata=metadata))
                added = True
                if len(selected) >= limit:
                    break
            if not added:
                break

        return tuple(selected)

    @staticmethod
    def _pack(
        *,
        status: str,
        reason: str,
        budget: EvidenceBudget,
        trace: EvidenceTraceBuffer,
        datasets: list[dict[str, object]] | None = None,
        searched_texts: list[str] | None = None,
        facts: list[str] | None = None,
        sql_checks: list[dict[str, object]] | None = None,
        limitations: list[str] | None = None,
        raw_output: str | None = None,
    ) -> dict[str, object]:
        pack: dict[str, object] = {
            "type": "evidence_pack",
            "status": status,
            "reason": reason,
            "searched_texts": searched_texts or [],
            "candidate_datasets": datasets or [],
            "coverage": _fallback_coverage(
                status=status,
                sql_checks=sql_checks or [],
                facts=facts or [],
                limitations=limitations or [],
                selected_candidates=(),
            ),
            "datasets_used": [],
            "facts": facts or [],
            "sql_checks": sql_checks or [],
            "limitations": limitations or [],
            "data_verdict": reason,
            "budget": budget.payload(),
            "_trace_events": trace.events,
        }
        if raw_output:
            pack["raw_output"] = raw_output
        return pack

    @staticmethod
    def _normalize_agent_pack(
        value: dict[str, object],
        *,
        selected_candidates: tuple[ParquetCandidate, ...],
        searched_texts: list[str],
        budget: EvidenceBudget,
        sql_state: EvidenceSqlState,
    ) -> dict[str, object]:
        status = str(value.get("status") or "insufficient_data").strip() or "insufficient_data"
        reason = str(value.get("reason") or value.get("data_verdict") or "Evidence pack готов.").strip()
        sql_checks = _merge_sql_checks(
            _normalize_sql_checks(value.get("sql_checks")),
            _sql_outputs_to_checks(sql_state.successful_query_outputs),
        )
        limitations = _string_list(value.get("limitations"))
        failed_sql_errors = _sql_error_summaries(sql_state.failed_query_outputs)
        for error in failed_sql_errors:
            if error not in limitations:
                limitations.append(error)
        data_verdict = str(value.get("data_verdict") or reason)
        return {
            "type": "evidence_pack",
            "status": status,
            "reason": reason,
            "searched_texts": searched_texts,
            "candidate_datasets": _dataset_cards(selected_candidates),
            "coverage": _normalize_coverage(
                value.get("coverage"),
                status=status,
                sql_checks=sql_checks,
                facts=_string_list(value.get("facts")),
                limitations=limitations,
                selected_candidates=selected_candidates,
            ),
            "datasets_used": _normalize_datasets_used(
                value.get("datasets_used"),
                selected_candidates,
                sql_checks,
                allow_candidate_fallback=status == "ok" or bool(sql_checks),
            ),
            "facts": _string_list(value.get("facts")),
            "sql_checks": sql_checks,
            "limitations": limitations,
            "data_verdict": data_verdict,
            "budget": budget.payload(),
        }


def _dataset_cards(candidates: tuple[ParquetCandidate, ...]) -> list[dict[str, object]]:
    return [
        {
            "rank": index,
            "dataset_id": candidate.dataset_id,
            "score": round(candidate.score, 6),
            "parquet_uri": candidate.parquet_uri,
            "description": candidate.description,
            "source": candidate.metadata.get("source"),
            "source_name": candidate.metadata.get("source_name"),
            "source_url": candidate.metadata.get("source_url"),
            "indicator_id": candidate.metadata.get("indicator_id"),
            "indicator_name": candidate.metadata.get("name"),
            "unit": candidate.metadata.get("unit"),
            "period": [
                candidate.metadata.get("period_start"),
                candidate.metadata.get("period_end"),
            ],
            "geography_type": candidate.metadata.get("geography_type"),
            "dimensions": candidate.metadata.get("dimensions"),
            "topics": candidate.metadata.get("topics"),
            "has_data": candidate.metadata.get("has_data"),
            "missing_parquet_uri": candidate.metadata.get("missing_parquet_uri"),
            "selected_by": candidate.metadata.get("selected_by"),
            "selected_rank": candidate.metadata.get("selected_rank"),
            "retrieval_query_index": candidate.metadata.get("retrieval_query_index"),
            "retrieval_query": candidate.metadata.get("retrieval_query"),
        }
        for index, candidate in enumerate(candidates, start=1)
    ]


def _parse_evidence_pack_output(raw_output: object) -> dict[str, object] | None:
    if isinstance(raw_output, dict):
        candidate = raw_output.get("pack") if isinstance(raw_output.get("pack"), dict) else raw_output
        return dict(candidate)
    if hasattr(raw_output, "model_dump"):
        try:
            value = raw_output.model_dump()
        except Exception:
            value = None
        if isinstance(value, dict):
            return value

    return _parse_json_object(str(raw_output or ""))


def _parse_json_object(raw_output: str) -> dict[str, object] | None:
    cleaned = raw_output.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _candidate_filter_stats(candidates: tuple[ParquetCandidate, ...]) -> dict[str, int]:
    return {
        "total": len(candidates),
        "without_parquet_uri": sum(1 for candidate in candidates if not candidate.parquet_uri),
        "with_explicit_has_data_false": sum(
            1 for candidate in candidates if _is_false(candidate.metadata.get("has_data"))
        ),
        "with_parquet_uri": sum(1 for candidate in candidates if bool(candidate.parquet_uri)),
    }


def _evidence_retry_reasons(
    parsed: dict[str, object] | None,
    sql_state: EvidenceSqlState,
) -> list[str]:
    reasons: list[str] = []
    if not isinstance(parsed, dict) and not sql_state.successful_query_outputs:
        reasons.append("missing_evidence_pack")
    return reasons


def _evidence_retry_instruction(sql_state: EvidenceSqlState) -> str:
    return (
        "Use previous_duckdb_outputs. If the last output has error, rewrite SQL using that error text. "
        "If the task is covered, call submit_evidence_pack; otherwise call DuckDB again."
    )


def _output_has_rows(output: dict[str, object]) -> bool:
    rows = output.get("rows")
    return isinstance(rows, list) and bool(rows)


def _columns_from_rows(rows: list[object]) -> list[str]:
    columns: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in row:
            column = str(key)
            if column not in columns:
                columns.append(column)
    return columns


def _sql_outputs_to_checks(outputs: list[dict[str, object]]) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    ranked_outputs = sorted(
        enumerate(outputs, start=1),
        key=lambda item: _sql_output_quality_score(item[1]),
        reverse=True,
    )
    for original_index, output in ranked_outputs:
        rows = output.get("rows")
        if not isinstance(rows, list):
            rows = []
        columns = output.get("columns")
        if not isinstance(columns, list):
            columns = _columns_from_rows(rows)
        checks.append(
            {
                "purpose": _sql_check_purpose(output, original_index=original_index),
                "sql": str(output.get("sql") or ""),
                "row_count": _safe_int(output.get("row_count")),
                "columns": [str(column) for column in columns],
                "rows": rows[:30],
                "used_dataset_names": _string_list(output.get("used_dataset_names")),
            }
        )
    return checks[:10]


def _sql_output_quality_score(output: dict[str, object]) -> int:
    score = 0
    rows = _as_list(output.get("rows"))
    row_count = _safe_int(output.get("row_count"))
    columns = [str(column) for column in _as_list(output.get("columns"))]
    sql = " ".join(str(output.get("sql") or "").lower().split())

    if rows:
        score += 100
    if row_count > 0:
        score += min(row_count, 50)
    if _string_list(output.get("used_dataset_names")):
        score += 25
    if columns and not _all_columns_are_generic(columns):
        score += 30
    if any(_looks_like_time_column(column) for column in columns):
        score += 20
    if "select *" in sql:
        score -= 70
    if re.search(r"\blimit\s+[1-5]\b", sql):
        score -= 20
    if not rows:
        score -= 50
    return score


def _sql_check_purpose(output: dict[str, object], *, original_index: int) -> str:
    sql = " ".join(str(output.get("sql") or "").lower().split())
    if "select *" in sql and re.search(r"\blimit\s+[1-5]\b", sql):
        return f"Предварительный просмотр DuckDB #{original_index}"
    return f"Аналитическая DuckDB SQL-проверка #{original_index}"


def _all_columns_are_generic(columns: list[str]) -> bool:
    return bool(columns) and all(re.fullmatch(r"column\d+", column.lower()) for column in columns)


def _looks_like_time_column(column: str) -> bool:
    normalized = column.lower()
    return (
        normalized in {"year", "date", "period", "год", "период", "дата"}
        or bool(re.fullmatch(r"year[_\s-]?\d{4}", normalized))
        or bool(re.fullmatch(r"\d{4}", normalized))
    )


def _merge_sql_checks(
    model_checks: list[dict[str, object]],
    tool_checks: list[dict[str, object]],
) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen_sql: set[str] = set()
    for check in [*model_checks, *tool_checks]:
        sql = " ".join(str(check.get("sql") or "").split())
        key = sql or json.dumps(check, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen_sql:
            continue
        seen_sql.add(key)
        merged.append(check)
    return merged[:10]


def _sql_error_summaries(outputs: list[dict[str, object]]) -> list[str]:
    errors: list[str] = []
    for output in outputs[-5:]:
        error = " ".join(str(output.get("error") or "").split())
        sql = " ".join(str(output.get("sql") or "").split())
        if not error:
            continue
        summary = f"SQL error: {error}"
        if sql:
            summary = f"{summary}; sql={sql[:500]}"
        if summary not in errors:
            errors.append(summary)
    return errors


def _normalize_sql_checks(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    checks: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rows = item.get("rows")
        if not isinstance(rows, list):
            rows = []
        columns = item.get("columns")
        if not isinstance(columns, list):
            columns = []
        normalized_columns = [str(column) for column in columns]
        normalized_rows = _normalize_check_rows(rows, normalized_columns)
        checks.append(
            {
                "purpose": str(item.get("purpose") or ""),
                "sql": str(item.get("sql") or ""),
                "row_count": _safe_int(item.get("row_count")),
                "columns": normalized_columns,
                "rows": normalized_rows[:30],
                "used_dataset_names": _string_list(item.get("used_dataset_names")),
            }
        )
    return checks[:10]


def _normalize_check_rows(rows: list[object], columns: list[str]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append({str(key): _jsonable(value) for key, value in row.items()})
            continue
        if isinstance(row, list | tuple):
            normalized.append(
                {
                    columns[index] if index < len(columns) else f"column_{index + 1}": _jsonable(value)
                    for index, value in enumerate(row)
                }
            )
    return normalized


def _normalize_datasets_used(
    value: object,
    candidates: tuple[ParquetCandidate, ...],
    sql_checks: list[dict[str, object]],
    *,
    allow_candidate_fallback: bool,
) -> list[dict[str, object]]:
    datasets: list[dict[str, object]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                dataset = {
                    "dataset_id": str(item.get("dataset_id") or ""),
                    "name": str(item.get("name") or ""),
                    "source": str(item.get("source") or ""),
                    "unit": str(item.get("unit") or ""),
                }
                if dataset["dataset_id"] or dataset["name"]:
                    datasets.append(dataset)
            elif isinstance(item, str) and item.strip():
                datasets.append({"dataset_id": "", "name": " ".join(item.split()), "source": "", "unit": ""})

    if datasets:
        return datasets[:10]

    names: list[str] = []
    for check in sql_checks:
        for name in _string_list(check.get("used_dataset_names")):
            if name and name not in names:
                names.append(name)
    if names:
        resolved: list[dict[str, object]] = []
        for name in names[:10]:
            candidate = _candidate_by_dataset_name(candidates, name)
            if candidate is None:
                resolved.append({"dataset_id": "", "name": name, "source": "", "unit": ""})
                continue
            resolved.append(
                {
                    "dataset_id": candidate.dataset_id,
                    "name": _dataset_text_name(candidate),
                    "source": str(candidate.metadata.get("source_name") or candidate.metadata.get("source") or ""),
                    "unit": str(candidate.metadata.get("unit") or ""),
                }
            )
        return resolved

    if not allow_candidate_fallback:
        return []

    return [
        {
            "dataset_id": candidate.dataset_id,
            "name": _dataset_text_name(candidate),
            "source": str(candidate.metadata.get("source_name") or candidate.metadata.get("source") or ""),
            "unit": str(candidate.metadata.get("unit") or ""),
        }
        for candidate in candidates[:3]
    ]


def _normalize_coverage(
    value: object,
    *,
    status: str,
    sql_checks: list[dict[str, object]],
    facts: list[str],
    limitations: list[str],
    selected_candidates: tuple[ParquetCandidate, ...],
) -> dict[str, object]:
    fallback = _fallback_coverage(
        status=status,
        sql_checks=sql_checks,
        facts=facts,
        limitations=limitations,
        selected_candidates=selected_candidates,
    )
    if not isinstance(value, dict):
        return fallback

    next_action = str(value.get("next_action") or fallback["next_action"])
    if next_action not in {
        "answer_directly",
        "request_more_evidence",
        "calculate_from_parts",
        "ask_clarification",
        "no_data",
    }:
        next_action = str(fallback["next_action"])

    return {
        "requested_indicators": _string_list(value.get("requested_indicators")) or fallback["requested_indicators"],
        "requested_geographies": _string_list(value.get("requested_geographies")) or fallback["requested_geographies"],
        "requested_period": str(value.get("requested_period") or fallback["requested_period"]),
        "found_slices": _string_list(value.get("found_slices")) or fallback["found_slices"],
        "missing_slices": _string_list(value.get("missing_slices")) or fallback["missing_slices"],
        "computable_from_parts": _bool_value(value.get("computable_from_parts")),
        "required_parts": _string_list(value.get("required_parts")),
        "next_action": next_action,
        "reason": str(value.get("reason") or fallback["reason"]),
    }


def _fallback_coverage(
    *,
    status: str,
    sql_checks: list[dict[str, object]],
    facts: list[str],
    limitations: list[str],
    selected_candidates: tuple[ParquetCandidate, ...],
) -> dict[str, object]:
    has_rows = any(_as_list(check.get("rows")) for check in sql_checks if isinstance(check, dict))
    has_dataset = bool(selected_candidates)
    next_action = "answer_directly" if has_rows or facts else "no_data"
    if status in {"no_relevant_dataset", "no_rows"} and not has_rows:
        next_action = "no_data"
    if status == "insufficient_data" and not has_rows:
        next_action = "ask_clarification" if limitations else "request_more_evidence"

    found_slices: list[str] = []
    for candidate in selected_candidates[:5]:
        name = _dataset_text_name(candidate)
        if name:
            found_slices.append(name)
    if has_rows and not found_slices:
        found_slices.append("SQL вернул строки по выбранным датасетам.")

    return {
        "requested_indicators": [],
        "requested_geographies": [],
        "requested_period": "",
        "found_slices": found_slices,
        "missing_slices": limitations if not has_rows else [],
        "computable_from_parts": False,
        "required_parts": [],
        "next_action": next_action,
        "reason": (
            "SQL вернул строки, можно отвечать по evidence pack."
            if has_rows or facts
            else "SQL не вернул достаточных строк для ответа." if has_dataset else "Релевантные датасеты не найдены."
        ),
    }


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "да"}
    return False


def _string_list(value: object) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            continue
        text = " ".join(str(item or "").split())
        if text:
            result.append(text)
    return result


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _compact_tool_output(output: Any) -> object:
    jsonable = _jsonable(output)
    if isinstance(jsonable, dict):
        compact = dict(jsonable)
        rows = compact.get("rows")
        if isinstance(rows, list) and len(rows) > 10:
            compact["rows"] = rows[:10]
            compact["rows_truncated_for_trace"] = len(rows) - 10
        return compact
    return jsonable


def _compact_outputs(outputs: list[dict[str, object]]) -> list[object]:
    return [_compact_tool_output(output) for output in outputs]


def _jsonable(value: Any) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return str(value)


_READ_PARQUET_CALL_RE = re.compile(r"read_parquet\s*\((?P<args>.*?)\)", re.IGNORECASE | re.DOTALL)
_SQL_STRING_RE = re.compile(r"'((?:''|[^'])*)'|\"((?:\"\"|[^\"])*)\"")


def _sql_used_dataset_names(
    duckdb_output: dict[str, object],
    candidates: tuple[ParquetCandidate, ...],
) -> list[str]:
    paths = _duckdb_used_parquet_paths(duckdb_output)
    if not paths:
        return []

    names: list[str] = []
    for candidate in candidates:
        candidate_path = _normalize_parquet_reference(candidate.parquet_uri)
        if not candidate_path:
            continue
        if any(_parquet_references_match(candidate_path, path) for path in paths):
            name = _dataset_text_name(candidate)
            if name and name not in names:
                names.append(name)
    return names


def _duckdb_used_parquet_paths(duckdb_output: dict[str, object]) -> list[str]:
    paths: list[str] = []
    parquet_path = duckdb_output.get("parquet_path")
    if isinstance(parquet_path, str):
        normalized = _normalize_parquet_reference(parquet_path)
        if normalized:
            paths.append(normalized)

    sql = duckdb_output.get("sql")
    if isinstance(sql, str):
        for call in _READ_PARQUET_CALL_RE.finditer(sql):
            for match in _SQL_STRING_RE.finditer(call.group("args")):
                value = match.group(1) if match.group(1) is not None else match.group(2)
                if value is None:
                    continue
                value = value.replace("''", "'").replace('""', '"')
                normalized = _normalize_parquet_reference(value)
                if normalized and normalized not in paths:
                    paths.append(normalized)
    return paths


def _normalize_parquet_reference(value: object) -> str:
    normalized = str(value or "").strip().strip("'\"")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _parquet_references_match(candidate_path: str, used_path: str) -> bool:
    if candidate_path == used_path:
        return True
    return candidate_path.endswith(used_path) or used_path.endswith(candidate_path)


def _dataset_text_name(candidate: ParquetCandidate) -> str:
    metadata = candidate.metadata
    source_row = metadata.get("source_row")
    candidates = (
        metadata.get("name"),
        metadata.get("title"),
        source_row.get("name") if isinstance(source_row, dict) else None,
        source_row.get("title") if isinstance(source_row, dict) else None,
        candidate.description,
        candidate.dataset_id,
    )
    for value in candidates:
        text = " ".join(str(value or "").split())
        if text:
            return text
    return ""


def _candidate_by_dataset_name(
    candidates: tuple[ParquetCandidate, ...],
    name: str,
) -> ParquetCandidate | None:
    expected = " ".join(name.split())
    for candidate in candidates:
        if _dataset_text_name(candidate) == expected:
            return candidate
    return None


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _is_false(value: Any) -> bool:
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "no"}
    return False
