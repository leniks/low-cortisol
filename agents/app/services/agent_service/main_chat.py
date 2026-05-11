from collections.abc import AsyncIterator, Callable
import asyncio
import json
import re
from typing import Any

from agents import Runner

from app.core.settings import AgentSettings
from app.models.main_agent.factory import create_main_agent
from app.models.query_enricher import QueryEnricherModel
from app.models.rag_retriever import RagRetrieverModel
from app.schemas.invoke import ChatMessage
from app.services.agent_service.evidence import EvidenceBudget, EvidenceRequest, EvidenceService
from app.utils.structured_history import build_recent_history_signals, build_request_facts


DEFAULT_MAIN_AGENT_MAX_ATTEMPTS = 3
MAX_MAIN_AGENT_MAX_ATTEMPTS = 6
RECENT_DIALOG_CONTEXT_LIMIT = 6
RECENT_DIALOG_CONTEXT_CHARS = 4000


class MainAgentChatService:
    def __init__(
        self,
        query_enricher: QueryEnricherModel,
        rag_retriever_factory: Callable[[], RagRetrieverModel],
    ) -> None:
        self._settings: AgentSettings | None = None
        self._query_enricher = query_enricher
        self._rag_retriever_factory = rag_retriever_factory
        self._evidence_service = EvidenceService(
            query_enricher=query_enricher,
            rag_retriever_factory=rag_retriever_factory,
        )

    async def run_stream(
        self,
        *,
        message: str,
        history: list[ChatMessage],
        conversation_id: str | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        history_payload = [item.model_dump() for item in history]

        evidence_budget = EvidenceBudget()
        evidence_trace_buffer: list[dict[str, object]] = []
        evidence_packs: list[dict[str, object]] = []
        data_acquisition_plan: dict[str, object] | None = None

        async def evidence_provider(tool_payload: dict[str, object]) -> dict[str, object]:
            search_texts = _tool_payload_search_texts(tool_payload)
            evidence_request = EvidenceRequest(
                current_user_message=message,
                analysis_goal=str(tool_payload.get("analysis_goal") or message),
                search_texts=search_texts,
                conversation_id=conversation_id,
                history_payload=history_payload,
                data_acquisition_plan=data_acquisition_plan,
            )
            pack = await self._evidence_service.collect(evidence_request, budget=evidence_budget)
            trace_events = pack.pop("_trace_events", [])
            if isinstance(trace_events, list):
                evidence_trace_buffer.extend(
                    event for event in trace_events if isinstance(event, dict)
                )
            return pack

        agent = create_main_agent(self._get_settings(), evidence_provider=evidence_provider)
        structured_main_input = self._main_agent_structured_input(
            message=message,
            history_payload=history_payload,
        )
        input_messages = [
            {
                "role": "user",
                "content": json.dumps(structured_main_input, ensure_ascii=False, default=str),
            }
        ]

        yield self._trace_event(
            "tool_call",
            title="Анализирую запрос и контекст",
            tool="main_agent",
            payload={
                "user_message": message,
                "history_messages": len(history_payload),
                "raw_history_forwarded": False,
                "evidence_context": {
                    "mode": "on_demand_request_evidence_tool",
                    "initial_pre_rag": False,
                    "planner": "main_agent_submit_data_acquisition_plan",
                    "budget": evidence_budget.payload(),
                },
            },
            phase="analysis",
            status="running",
            visibility="summary",
        )
        clarification: dict[str, object] | None = None
        last_evidence_pack: dict[str, object] | None = None
        emitted_evidence_dataset_signatures: set[str] = set()
        answer = ""
        attempts_used = 0
        max_attempts = self._main_agent_max_attempts()
        for attempt in range(1, max_attempts + 1):
            attempts_used = attempt
            if attempt > 1:
                await asyncio.sleep(min(0.4 * (attempt - 1), 1.2))
                yield self._trace_event(
                    "tool_call",
                    title=f"Повторная попытка основного агента #{attempt}",
                    tool="main_agent",
                    payload={
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "reason": "Предыдущая попытка завершилась без финального текста.",
                        "has_evidence_pack": last_evidence_pack is not None,
                    },
                    phase="validation",
                    status="retry",
                    visibility="detail",
                )

            streamed_result = Runner.run_streamed(
                agent,
                self._main_agent_input_for_attempt(
                    input_messages,
                    attempt=attempt,
                    last_evidence_pack=last_evidence_pack,
                    evidence_packs=evidence_packs,
                    data_acquisition_plan=data_acquisition_plan,
                ),
            )
            answer_parts: list[str] = []
            tool_call_names_by_id: dict[str, str] = {}
            attempt_tool_calls: list[str] = []
            emitted_text = False

            try:
                async for event in streamed_result.stream_events():
                    if getattr(event, "type", None) == "raw_response_event":
                        delta = self._stream_text_delta(getattr(event, "data", None))
                        if delta and clarification is None:
                            answer_parts.append(delta)
                            emitted_text = True
                        continue

                    if getattr(event, "type", None) == "run_item_stream_event":
                        if str(_read_attr(event, "name") or "") == "tool_called":
                            item = _read_attr(event, "item")
                            tool_name = self._tool_name(item)
                            if tool_name:
                                attempt_tool_calls.append(tool_name)
                        trace_event = self._main_agent_run_item_trace(event, tool_call_names_by_id)
                        if trace_event:
                            yield trace_event
                        tool_output = self._main_agent_tool_output(event, tool_call_names_by_id)
                        if tool_output is not None:
                            tool_name, output = tool_output
                            if tool_name == "submit_data_acquisition_plan" and isinstance(output, dict):
                                data_acquisition_plan = _extract_data_acquisition_plan(output)
                                yield {
                                    "type": "artifact_source",
                                    "artifact_type": "collection_plan",
                                    "title": "План получения данных",
                                    "payload": data_acquisition_plan or output,
                                }
                            if tool_name == "request_evidence" and isinstance(output, dict):
                                last_evidence_pack = output
                                evidence_packs.append(output)
                                yield {
                                    "type": "artifact_source",
                                    "artifact_type": "sql_evidence",
                                    "title": "SQL evidence pack",
                                    "payload": output,
                                }
                                used_dataset_names = _evidence_pack_used_dataset_names(output)
                                if used_dataset_names:
                                    signature = "\n".join(used_dataset_names)
                                    if signature not in emitted_evidence_dataset_signatures:
                                        emitted_evidence_dataset_signatures.add(signature)
                                        yield self._trace_event(
                                            "tool_result",
                                            title="Определил датасеты для SQL",
                                            tool="sql_dataset_usage",
                                            payload=signature,
                                            phase="sql",
                                            status="done",
                                            visibility="summary",
                                        )
                                while evidence_trace_buffer:
                                    yield evidence_trace_buffer.pop(0)
                        message_output = self._main_agent_message_output(event)
                        if message_output and not answer_parts and clarification is None:
                            answer_parts.append(message_output)
                            emitted_text = True
                        clarification = clarification or self._main_agent_clarification(event, tool_call_names_by_id)
            except Exception as exc:
                yield self._trace_event(
                    "tool_result",
                    title=f"Ошибка основного агента на попытке #{attempt}",
                    tool="main_agent",
                    payload={
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "error": str(exc),
                        "will_retry": attempt < max_attempts,
                        "has_evidence_pack": last_evidence_pack is not None,
                        "tool_calls_seen": attempt_tool_calls,
                    },
                    phase="validation",
                    status="retry" if attempt < max_attempts else "error",
                    visibility="detail",
                )
                if attempt < max_attempts:
                    continue
                if last_evidence_pack is None:
                    raise

            if clarification is not None:
                yield {
                    "type": "clarification",
                    "title": "Основной агент запросил уточнение",
                    "tool": "request_user_clarification",
                    "text": str(clarification.get("question") or "Нужно уточнение."),
                    "clarification": clarification,
                }
                return

            final_output_text = str(streamed_result.final_output or "").strip()
            answer_fallback = "".join(answer_parts).strip() if not attempt_tool_calls else ""
            answer = _normalize_final_answer(final_output_text or answer_fallback)
            if answer:
                yield {"type": "final", "text": answer}
                break

            yield self._trace_event(
                "tool_result",
                title=f"Основной агент вернул пустой финал на попытке #{attempt}",
                tool="main_agent",
                payload={
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "will_retry": attempt < max_attempts,
                    "has_evidence_pack": last_evidence_pack is not None,
                    "tool_calls_seen": attempt_tool_calls,
                    "streamed_text_chars": len("".join(answer_parts)),
                    "final_output_type": type(streamed_result.final_output).__name__,
                },
                phase="validation",
                status="retry" if attempt < max_attempts else "done",
                visibility="detail",
            )

        yield self._trace_event(
            "tool_result",
            title="Ответ готов",
            tool="main_agent",
            payload={
                "answer_chars": len(answer),
                "attempts_used": attempts_used,
                "fallback_used": False,
            },
            phase="finalization",
            status="done",
            visibility="summary",
        )

    def _get_settings(self) -> AgentSettings:
        if self._settings is None:
            self._settings = AgentSettings.from_env()
        return self._settings

    def _main_agent_max_attempts(self) -> int:
        configured = self._get_settings().main_agent_max_attempts
        if configured < 1:
            return DEFAULT_MAIN_AGENT_MAX_ATTEMPTS
        return min(configured, MAX_MAIN_AGENT_MAX_ATTEMPTS)

    @staticmethod
    def _main_agent_structured_input(
        *,
        message: str,
        history_payload: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "type": "main_agent_structured_input",
            "current_user_message": message,
            "raw_chat_history_forwarded": False,
            "recent_dialog_context_forwarded": True,
            "request_facts": build_request_facts(message, history_payload),
            "recent_dialog_context": MainAgentChatService._recent_dialog_context(history_payload),
            "recent_history_signals": build_recent_history_signals(history_payload, limit=8),
            "planning_contract": {
                "first_tool": "submit_data_acquisition_plan",
                "must_submit_before": [
                    "request_evidence",
                    "calculate_basic",
                    "request_user_clarification",
                    "final_answer",
                ],
                "plan_visible_in_trace": True,
            },
            "evidence_context": {
                "initial_pre_rag": False,
                "main_agent_must_call_request_evidence_for_data_requests": True,
                "available_tool": "request_evidence",
                "spawning_model": "one request_evidence call per data component or tight component group",
                "evidence_pack_contract": {
                    "coverage_next_actions": [
                        "answer_directly",
                        "request_more_evidence",
                        "calculate_from_parts",
                        "ask_clarification",
                        "no_data",
                    ],
                    "partial_rows_must_be_reported": True,
                },
                "main_decides_after_evidence": True,
            },
            "execution_contract": {
                "allowed_evidence": [
                    "submit_data_acquisition_plan tool output",
                    "request_evidence tool outputs",
                    "calculate_basic tool outputs",
                    "user clarifications",
                ],
                "raw_prior_assistant_text_is_untrusted": True,
                "final_answer_language": "ru",
                "required_final_sections": [
                    "Использованные датасеты",
                    "Сгенерированный SQL",
                    "Результаты анализа",
                    "Краткий вывод",
                ],
            },
        }


    @staticmethod
    def _recent_dialog_context(history_payload: list[dict[str, object]]) -> list[dict[str, object]]:
        context: list[dict[str, object]] = []
        for item in history_payload[-RECENT_DIALOG_CONTEXT_LIMIT:]:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            context.append(
                {
                    "role": role,
                    "content": content[:RECENT_DIALOG_CONTEXT_CHARS],
                    "truncated": len(content) > RECENT_DIALOG_CONTEXT_CHARS,
                }
            )
        return context

    @staticmethod
    def _main_agent_input_for_attempt(
        base_input_messages: list[dict[str, object]],
        *,
        attempt: int,
        last_evidence_pack: dict[str, object] | None,
        evidence_packs: list[dict[str, object]],
        data_acquisition_plan: dict[str, object] | None,
    ) -> list[dict[str, object]]:
        if attempt <= 1:
            return base_input_messages

        retry_payload = {
            "type": "main_agent_retry_structured_input",
            "attempt": attempt,
            "previous_attempt_issue": "empty_or_non_russian_final_answer_or_missing_plan",
            "required_action": "produce_non_empty_final_answer",
            "plan_submitted": data_acquisition_plan is not None,
            "required_first_tool_if_missing": "submit_data_acquisition_plan",
            "invalid_output_must_not_repeat": [
                "template tags such as <result>",
                "English phrases such as Results of the analysis",
                "meta phrases about context or direct text",
                "generic dataset labels without dataset IDs",
            ],
            "language_contract": {
                "final_answer_language": "ru",
                "headings_language": "ru",
                "methodology_notes_language": "ru",
                "analytical_summary_language": "ru",
                "forbidden": ["Chinese characters", "English prose", "English section headings"],
                "allowed_unchanged": ["SQL keywords", "dataset IDs", "raw column names"],
            },
            "required_final_sections": [
                "Использованные датасеты",
                "Сгенерированный SQL",
                "Результаты анализа",
                "Краткий вывод",
            ],
            "last_evidence_pack": last_evidence_pack,
            "evidence_packs": evidence_packs[-3:],
            "main_decision_contract": {
                "if_evidence_satisfies_request": "answer_from_evidence_pack",
                "if_data_absent_and_not_computable": "state_no_data",
                "if_computable_from_parts": "request_missing_parts_then_calculate_basic",
                "if_missing_definition_or_filter": "request_user_clarification",
            },
            "data_acquisition_plan": data_acquisition_plan,
        }
        return [
            *base_input_messages,
            {
                "role": "user",
                "content": json.dumps(retry_payload, ensure_ascii=False, default=str),
            },
        ]

    @staticmethod
    def _stream_text_delta(raw_event: Any) -> str:
        event_type = str(_read_attr(raw_event, "type") or "")
        if event_type not in {
            "response.output_text.delta",
            "response.text.delta",
            "response.refusal.delta",
        }:
            return ""

        delta = _read_attr(raw_event, "delta")
        return delta if isinstance(delta, str) else ""

    def _main_agent_run_item_trace(
        self,
        stream_event: Any,
        tool_call_names_by_id: dict[str, str],
    ) -> dict[str, object] | None:
        name = str(_read_attr(stream_event, "name") or "")
        item = _read_attr(stream_event, "item")

        if name == "tool_called":
            tool_name = self._tool_name(item)
            call_id = self._call_id(item)
            if tool_name and call_id:
                tool_call_names_by_id[call_id] = tool_name
            title = (
                "Основной агент формирует план получения данных"
                if tool_name == "submit_data_acquisition_plan"
                else f"Запускается: {self._tool_display_name(tool_name)}"
            )
            return self._trace_event(
                "tool_call",
                title=title,
                tool=tool_name or "main_agent_tool",
                payload={
                    "event": name,
                    "call_id": call_id,
                    "arguments": self._tool_arguments(item),
                },
                phase=self._tool_trace_phase(tool_name),
                status="running",
                visibility=(
                    "summary"
                    if tool_name in {
                        "submit_data_acquisition_plan",
                        "request_evidence",
                        "calculate_basic",
                        "request_user_clarification",
                    }
                    else "detail"
                ),
            )

        if name == "tool_output":
            call_id = self._call_id(item)
            tool_name = self._tool_name(item) or (tool_call_names_by_id.get(call_id) if call_id else None)
            title = (
                "План получения данных"
                if tool_name == "submit_data_acquisition_plan"
                else f"Завершено: {self._tool_display_name(tool_name)}"
            )
            return self._trace_event(
                "tool_result",
                title=title,
                tool=tool_name or "main_agent_tool",
                payload={
                    "event": name,
                    "call_id": call_id,
                    "output": self._compact_tool_output(_read_attr(item, "output")),
                },
                phase=self._tool_trace_phase(tool_name),
                status="done",
                visibility=(
                    "summary"
                    if tool_name in {"submit_data_acquisition_plan", "calculate_basic"}
                    else "detail"
                ),
            )

        return None

    def _main_agent_clarification(
        self,
        stream_event: Any,
        tool_call_names_by_id: dict[str, str],
    ) -> dict[str, object] | None:
        if str(_read_attr(stream_event, "name") or "") != "tool_output":
            return None

        item = _read_attr(stream_event, "item")
        call_id = self._call_id(item)
        tool_name = self._tool_name(item) or (tool_call_names_by_id.get(call_id) if call_id else None)
        if tool_name != "request_user_clarification":
            return None

        output = _jsonable(_read_attr(item, "output"))
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                return None
        if not isinstance(output, dict):
            return None

        options = output.get("options")
        steps = output.get("steps")
        missing_fields = output.get("missing_fields")
        if not isinstance(options, list):
            options = []
        if not isinstance(steps, list):
            steps = []
        if not isinstance(missing_fields, list):
            missing_fields = ["other"]

        return {
            "is_complete": False,
            "question": str(output.get("question") or "Нужно уточнение."),
            "missing_fields": [str(field) for field in missing_fields],
            "options": [
                {"label": str(option.get("label")), "value": str(option.get("value"))}
                for option in options
                if isinstance(option, dict) and option.get("label") and option.get("value")
            ],
            "steps": [
                {
                    "field": str(step.get("field")),
                    "question": str(step.get("question") or "Уточните параметр запроса."),
                    "reason": str(step.get("reason") or ""),
                    "options": [
                        {"label": str(option.get("label")), "value": str(option.get("value"))}
                        for option in step.get("options", [])
                        if isinstance(option, dict) and option.get("label") and option.get("value")
                    ],
                }
                for step in steps
                if isinstance(step, dict) and step.get("field") and isinstance(step.get("options"), list)
            ],
            "reason": str(output.get("reason") or "Основной агент запросил уточнение."),
        }

    def _main_agent_tool_output(
        self,
        stream_event: Any,
        tool_call_names_by_id: dict[str, str],
    ) -> tuple[str, object] | None:
        if str(_read_attr(stream_event, "name") or "") != "tool_output":
            return None

        item = _read_attr(stream_event, "item")
        call_id = self._call_id(item)
        tool_name = self._tool_name(item) or (tool_call_names_by_id.get(call_id) if call_id else None)
        if not tool_name:
            return None

        output = _jsonable(_read_attr(item, "output"))
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                pass
        return tool_name, output

    @staticmethod
    def _main_agent_message_output(stream_event: Any) -> str:
        if str(_read_attr(stream_event, "name") or "") != "message_output_created":
            return ""

        item = _read_attr(stream_event, "item")
        raw_item = _read_attr(item, "raw_item")
        candidates = (
            _read_attr(item, "content"),
            _read_attr(item, "text"),
            _read_attr(raw_item, "content"),
            _read_attr(raw_item, "text"),
        )
        for candidate in candidates:
            text = _extract_text(candidate)
            if text:
                return text
        return ""

    @staticmethod
    def _tool_name(item: Any) -> str | None:
        tool_name = _read_attr(item, "tool_name")
        if isinstance(tool_name, str) and tool_name:
            return tool_name

        raw_item = _read_attr(item, "raw_item")
        name = _read_attr(raw_item, "name")
        return name if isinstance(name, str) and name else None

    @staticmethod
    def _call_id(item: Any) -> str | None:
        call_id = _read_attr(item, "call_id")
        if call_id is None:
            raw_item = _read_attr(item, "raw_item")
            call_id = _read_attr(raw_item, "call_id") or _read_attr(raw_item, "id")
        return str(call_id) if call_id is not None else None

    @staticmethod
    def _tool_arguments(item: Any) -> object:
        raw_item = _read_attr(item, "raw_item")
        arguments = _read_attr(raw_item, "arguments")
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except json.JSONDecodeError:
                return arguments
        return _jsonable(arguments)

    @staticmethod
    def _tool_trace_phase(tool_name: str | None) -> str:
        tool = str(tool_name or "")
        if tool == "submit_data_acquisition_plan":
            return "planning"
        if tool in {"request_evidence", "query_enricher", "pgvector_rag_search", "technical_dataset_filter"}:
            return "retrieval"
        if tool in {"evidence_agent", "get_parquet_columns", "query_parquet_with_duckdb", "sql_dataset_usage"}:
            return "sql"
        if tool == "calculate_basic":
            return "calculation"
        if tool == "request_user_clarification":
            return "clarification"
        return "analysis"

    @staticmethod
    def _tool_display_name(tool_name: str | None) -> str:
        names = {
            "main_agent": "основной агент",
            "submit_data_acquisition_plan": "план данных",
            "request_evidence": "подбор данных",
            "query_enricher": "уточнение поискового запроса",
            "pgvector_rag_search": "поиск датасетов",
            "technical_dataset_filter": "отбор датасетов",
            "evidence_agent": "SQL-анализ",
            "get_parquet_columns": "схема данных",
            "query_parquet_with_duckdb": "SQL-запрос",
            "sql_dataset_usage": "использованные датасеты",
            "calculate_basic": "расчёт",
            "request_user_clarification": "уточнение",
        }
        if not tool_name:
            return "операция"
        return names.get(tool_name, tool_name.replace("_", " "))

    @staticmethod
    def _compact_tool_output(output: Any) -> object:
        jsonable = _jsonable(output)
        if isinstance(jsonable, dict):
            compact = dict(jsonable)
            rows = compact.get("rows")
            if isinstance(rows, list) and len(rows) > 10:
                compact["rows"] = rows[:10]
                compact["rows_truncated_for_trace"] = len(rows) - 10
            return compact
        if isinstance(jsonable, list) and len(jsonable) > 20:
            return {"items": jsonable[:20], "items_truncated_for_trace": len(jsonable) - 20}
        return jsonable

    @staticmethod
    def _trace_event(
        event_type: str,
        *,
        title: str,
        tool: str,
        payload: object,
        phase: str | None = None,
        status: str | None = None,
        visibility: str | None = None,
    ) -> dict[str, object]:
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
        return event


def _read_attr(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _jsonable(value: Any) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {
            key: _jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"].strip()
        if isinstance(value.get("content"), str):
            return value["content"].strip()
        if isinstance(value.get("content"), list):
            return _extract_text(value["content"])
    if isinstance(value, list | tuple):
        parts = [_extract_text(item) for item in value]
        return "".join(part for part in parts if part).strip()

    text = _read_attr(value, "text")
    if isinstance(text, str):
        return text.strip()
    content = _read_attr(value, "content")
    if content is not None:
        return _extract_text(content)
    return ""


def _tool_payload_search_texts(payload: dict[str, object]) -> tuple[str, ...]:
    values = payload.get("search_texts")
    texts: list[str] = []
    if isinstance(values, list | tuple):
        for value in values:
            text = " ".join(str(value or "").split())
            if text and text not in texts:
                texts.append(text)
    return tuple(texts)


def _extract_data_acquisition_plan(output: dict[str, object]) -> dict[str, object]:
    plan = output.get("plan")
    if isinstance(plan, dict):
        return plan
    return output


def _evidence_pack_used_dataset_names(pack: dict[str, object]) -> list[str]:
    names: list[str] = []
    for dataset in _as_list(pack.get("datasets_used")):
        if not isinstance(dataset, dict):
            continue
        name = " ".join(str(dataset.get("name") or dataset.get("dataset_id") or "").split())
        if name and name not in names:
            names.append(name)
    for check in _as_list(pack.get("sql_checks")):
        if not isinstance(check, dict):
            continue
        for name in _as_list(check.get("used_dataset_names")):
            text = " ".join(str(name or "").split())
            if text and text not in names:
                names.append(text)
    return names


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


_HEADING_NORMALIZATIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?im)^(\s*#{0,6}\s*)Датасеты\s*$"), r"\1Использованные датасеты"),
    (re.compile(r"(?im)^(\s*#{0,6}\s*)Наборы данных\s*$"), r"\1Использованные датасеты"),
    (re.compile(r"(?im)^(\s*#{0,6}\s*)SQL(?:-запрос)?\s*$"), r"\1Сгенерированный SQL"),
    (re.compile(r"(?im)^(\s*#{0,6}\s*)Результат анализа\s*$"), r"\1Результаты анализа"),
    (re.compile(r"(?im)^(\s*#{0,6}\s*)Анализ результатов\s*$"), r"\1Результаты анализа"),
    (re.compile(r"(?im)^(\s*#{0,6}\s*)Краткий аналитический вывод\s*$"), r"\1Краткий вывод"),
    (re.compile(r"(?im)^(\s*#{0,6}\s*)Краткий итог\s*$"), r"\1Краткий вывод"),
)


def _normalize_final_answer(answer: str) -> str:
    normalized = answer
    for pattern, replacement in _HEADING_NORMALIZATIONS:
        normalized = pattern.sub(replacement, normalized)
    return normalized.strip()
