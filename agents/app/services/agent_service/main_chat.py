from collections.abc import AsyncIterator, Callable
import json

from agents import Runner

from app.contracts import EnrichedQuery, ParquetCandidate, UserRequest
from app.core.settings import AgentSettings
from app.models.main_agent.factory import create_main_agent
from app.models.parquet_reranker import ParquetRerankerModel
from app.models.query_enricher import QueryEnricherModel
from app.models.rag_retriever import RagRetrieverModel
from app.models.request_classifier import RagRouteDecision, RequestClassifier
from app.schemas.invoke import ChatMessage


class MainAgentChatService:
    def __init__(
        self,
        classifier: RequestClassifier,
        query_enricher: QueryEnricherModel,
        rag_retriever_factory: Callable[[], RagRetrieverModel],
        parquet_reranker: ParquetRerankerModel,
    ) -> None:
        self._settings: AgentSettings | None = None
        self._agent = None
        self._classifier = classifier
        self._query_enricher = query_enricher
        self._rag_retriever_factory = rag_retriever_factory
        self._parquet_reranker = parquet_reranker

    async def run_stream(
        self,
        *,
        message: str,
        history: list[ChatMessage],
        conversation_id: str | None = None,
        route_decision: RagRouteDecision | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        agent = self._get_agent()
        settings = self._get_settings()
        history_payload = [item.model_dump() for item in history]
        if route_decision is None:
            yield self._trace_event(
                "tool_call",
                title="Классификация запроса",
                tool="request_classifier",
                payload={
                    "message": message,
                    "recent_history": history_payload[-8:],
                },
            )
            route_decision = await self._classifier.classify(message=message, history=history_payload)

        yield self._trace_event(
            "tool_result",
            title="Решение классификатора",
            tool="request_classifier",
            payload=route_decision.model_dump(),
        )
        enriched_query: EnrichedQuery | None = None
        parquet_candidates: tuple[ParquetCandidate, ...] = ()

        if route_decision.needs_rag:
            request = UserRequest(
                message=message,
                conversation_id=conversation_id,
                history=tuple(history_payload),
            )
            yield self._trace_event(
                "tool_call",
                title="Обогащение запроса для RAG",
                tool="query_enricher",
                payload={
                    "message": request.message,
                    "recent_history": list(request.history[-8:]),
                    "expected_output": "plain text search_text",
                },
            )
            enriched_query = await self._query_enricher.enrich(request)
            yield self._trace_event(
                "tool_result",
                title="Search text",
                tool="query_enricher",
                payload={
                    "search_text": enriched_query.enriched,
                    "metadata": enriched_query.metadata,
                },
            )
            yield self._trace_event(
                "tool_call",
                title="Векторный поиск датасетов",
                tool="pgvector_rag_search",
                payload={
                    "search_text": enriched_query.enriched,
                    "table": settings.rag_vector_table,
                    "embedding_column": settings.rag_embedding_column,
                    "top_k": settings.rag_top_k,
                    "embedding_model": settings.yandex_query_embedding_model,
                },
            )
            parquet_candidates = await self._rag_retriever_factory().retrieve(enriched_query)
            yield self._trace_event(
                "tool_result",
                title="Найденные датасеты",
                tool="pgvector_rag_search",
                payload={
                    "count": len(parquet_candidates),
                    "datasets": self._dataset_cards(parquet_candidates),
                },
            )
            yield self._trace_event(
                "tool_call",
                title="Контекстная фильтрация датасетов",
                tool="parquet_reranker",
                payload={
                    "input_count": len(parquet_candidates),
                    "search_text": enriched_query.enriched,
                },
            )
            reranked_candidates = await self._parquet_reranker.rerank(enriched_query, parquet_candidates)
            yield self._trace_event(
                "tool_result",
                title="Датасеты после фильтрации",
                tool="parquet_reranker",
                payload={
                    "input_count": len(parquet_candidates),
                    "output_count": len(reranked_candidates),
                    "datasets": self._dataset_cards(reranked_candidates),
                },
            )
            parquet_candidates = reranked_candidates

        input_messages = [
            {
                "role": "user",
                "content": (
                    "Internal routing context for the next answer. Do not show this block verbatim.\n"
                    f"decision={route_decision.decision}\n"
                    f"needs_rag={route_decision.needs_rag}\n"
                    f"reason={route_decision.reason}\n"
                    f"confidence={route_decision.confidence}\n"
                    f"{self._format_rag_context(enriched_query, parquet_candidates)}\n"
                    "If needs_rag=true, use the retrieved dataset candidates as the available context. "
                    "Do not invent dataset-backed facts that are not supported by this context."
                ),
            }
        ]
        input_messages.extend(history_payload)
        input_messages.append({"role": "user", "content": message})

        yield self._trace_event(
            "tool_call",
            title="Запуск основного агента",
            tool="main_agent",
            payload={
                "user_message": message,
                "history_messages": len(history_payload),
                "route_decision": route_decision.model_dump(),
                "rag_context": {
                    "search_text": enriched_query.enriched if enriched_query else None,
                    "datasets": self._dataset_cards(parquet_candidates),
                },
            },
        )
        result = await Runner.run(agent, input_messages)
        answer = str(result.final_output or "").strip()
        if not answer:
            answer = "Пустой ответ основного агента."

        yield self._trace_event(
            "tool_result",
            title="Ответ основного агента готов",
            tool="main_agent",
            payload={
                "answer_chars": len(answer),
            },
        )

        for chunk in self._chunk(answer):
            yield {"type": "final", "text": chunk}

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_main_agent(self._get_settings())
        return self._agent

    def _get_settings(self) -> AgentSettings:
        if self._settings is None:
            self._settings = AgentSettings.from_env()
        return self._settings

    @staticmethod
    def _chunk(text: str, size: int = 80) -> list[str]:
        return [text[index : index + size] for index in range(0, len(text), size)]

    @staticmethod
    def _format_rag_context(
        query: EnrichedQuery | None,
        candidates: tuple[ParquetCandidate, ...],
    ) -> str:
        if query is None:
            return "RAG retrieval was not requested."

        payload = {
            "search_text": query.enriched,
            "selected_datasets_count": len(candidates),
            "selected_datasets": MainAgentChatService._dataset_cards(candidates),
        }
        return (
            "RAG dataset context. Do not show this raw JSON verbatim.\n"
            f"{json.dumps(payload, ensure_ascii=False, default=str)}"
        )

    @staticmethod
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
                "reranker_rank": candidate.metadata.get("reranker_rank"),
                "reranker_relevance": candidate.metadata.get("reranker_relevance"),
                "reranker_reason": candidate.metadata.get("reranker_reason"),
            }
            for index, candidate in enumerate(candidates, start=1)
        ]

    @staticmethod
    def _trace_event(
        event_type: str,
        *,
        title: str,
        tool: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        return {
            "type": event_type,
            "title": title,
            "tool": tool,
            "payload": payload,
        }
