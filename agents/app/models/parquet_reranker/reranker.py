from dataclasses import replace
import json
import re
from typing import Any

from agents import Runner

from app.contracts import EnrichedQuery, ParquetCandidate
from app.core.settings import AgentSettings
from app.models.parquet_reranker.factory import create_parquet_reranker_agent
from app.models.parquet_reranker.schemas import RerankDecision, RerankResult


class ParquetReranker:
    def __init__(self) -> None:
        self._settings: AgentSettings | None = None
        self._agent = None

    async def rerank(
        self,
        query: EnrichedQuery,
        candidates: tuple[ParquetCandidate, ...],
    ) -> tuple[ParquetCandidate, ...]:
        if not candidates:
            return candidates

        settings = self._get_settings()
        agent = self._get_agent()
        result = await Runner.run(agent, [{"role": "user", "content": self._build_prompt(query, candidates, settings)}])
        parsed = self._parse(str(result.final_output or ""))
        if parsed is None:
            return self._fallback(candidates, "Reranker returned non-JSON output.")

        return self._apply_decisions(candidates, parsed.items, settings.rag_rerank_max_keep)

    def _get_settings(self) -> AgentSettings:
        if self._settings is None:
            self._settings = AgentSettings.from_env()
        return self._settings

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_parquet_reranker_agent(self._get_settings())
        return self._agent

    @staticmethod
    def _build_prompt(
        query: EnrichedQuery,
        candidates: tuple[ParquetCandidate, ...],
        settings: AgentSettings,
    ) -> str:
        payload: dict[str, Any] = {
            "task": "Filter contextually irrelevant parquet dataset candidates.",
            "original_user_request": query.original,
            "enriched_search_text": query.enriched,
            "max_keep": settings.rag_rerank_max_keep,
            "candidates": [
                {
                    "rank": index,
                    "dataset_id": candidate.dataset_id,
                    "vector_score": round(candidate.score, 6),
                    "description": candidate.description,
                    "source": candidate.metadata.get("source"),
                    "source_name": candidate.metadata.get("source_name"),
                    "source_url": candidate.metadata.get("source_url"),
                    "indicator_id": candidate.metadata.get("indicator_id"),
                    "indicator_name": candidate.metadata.get("name"),
                    "unit": candidate.metadata.get("unit"),
                    "period_start": candidate.metadata.get("period_start"),
                    "period_end": candidate.metadata.get("period_end"),
                    "geography_type": candidate.metadata.get("geography_type"),
                    "dimensions": candidate.metadata.get("dimensions"),
                    "topics": candidate.metadata.get("topics"),
                    "has_data": candidate.metadata.get("has_data"),
                }
                for index, candidate in enumerate(candidates, start=1)
            ],
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def _parse(raw_output: str) -> RerankResult | None:
        cleaned = raw_output.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        for candidate in _json_candidates(cleaned):
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            if isinstance(payload, list):
                payload = {"items": payload}
            if isinstance(payload, dict):
                if "items" not in payload:
                    for key in ("candidates", "decisions", "results"):
                        if key in payload:
                            payload = {"items": payload[key]}
                            break
                try:
                    return RerankResult.model_validate(payload)
                except Exception:
                    continue

        return None

    @staticmethod
    def _apply_decisions(
        candidates: tuple[ParquetCandidate, ...],
        decisions: tuple[RerankDecision, ...],
        max_keep: int,
    ) -> tuple[ParquetCandidate, ...]:
        by_id = {decision.dataset_id: decision for decision in decisions}
        kept: list[tuple[float, ParquetCandidate]] = []

        for candidate in candidates:
            if _is_false(candidate.metadata.get("has_data")):
                continue

            decision = by_id.get(candidate.dataset_id)
            if decision is None:
                continue
            if not decision.keep:
                continue

            metadata = dict(candidate.metadata)
            metadata.update(
                {
                    "reranker_kept": True,
                    "reranker_relevance": decision.relevance,
                    "reranker_reason": decision.reason,
                }
            )
            kept.append((decision.relevance, replace(candidate, metadata=metadata)))

        kept.sort(key=lambda item: (item[0], item[1].score), reverse=True)
        reranked = [candidate for _, candidate in kept]
        if max_keep > 0:
            reranked = reranked[:max_keep]

        return tuple(
            replace(candidate, metadata={**candidate.metadata, "reranker_rank": index})
            for index, candidate in enumerate(reranked, start=1)
        )

    @staticmethod
    def _fallback(candidates: tuple[ParquetCandidate, ...], reason: str) -> tuple[ParquetCandidate, ...]:
        return tuple(
            replace(
                candidate,
                metadata={
                    **candidate.metadata,
                    "reranker_kept": True,
                    "reranker_relevance": None,
                    "reranker_reason": reason,
                    "reranker_fallback": True,
                },
            )
            for candidate in candidates
            if not _is_false(candidate.metadata.get("has_data"))
        )


def _json_candidates(value: str) -> tuple[str, ...]:
    candidates = [value]

    first_object = value.find("{")
    last_object = value.rfind("}")
    if first_object >= 0 and last_object > first_object:
        candidates.append(value[first_object : last_object + 1])

    first_array = value.find("[")
    last_array = value.rfind("]")
    if first_array >= 0 and last_array > first_array:
        candidates.append(value[first_array : last_array + 1])

    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate.strip()))


def _is_false(value: Any) -> bool:
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "no"}
    return False
