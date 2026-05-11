import json
import re

from agents import Runner

from app.contracts import EnrichedQuery, UserRequest
from app.core.settings import AgentSettings
from app.models.query_enricher.factory import create_query_enricher_agent
from app.utils.structured_history import build_recent_history_signals


class QueryEnricher:
    def __init__(self) -> None:
        self._settings: AgentSettings | None = None
        self._agent = None

    async def enrich(self, request: UserRequest) -> EnrichedQuery:
        agent = self._get_agent()
        result = await Runner.run(agent, [{"role": "user", "content": self._build_prompt(request)}])
        return self._parse(result.final_output, request.message)

    def _get_agent(self):
        if self._agent is None:
            self._settings = AgentSettings.from_env()
            self._agent = create_query_enricher_agent(self._settings)
        return self._agent

    @staticmethod
    def _build_prompt(request: UserRequest) -> str:
        payload = {
            "type": "query_enricher_structured_input",
            "task": "build_dataset_search_text",
            "current_user_message": request.message,
            "raw_chat_history_forwarded": False,
            "recent_dialog_context": _recent_dialog_context(request.history),
            "recent_history_signals": build_recent_history_signals(request.history, limit=8),
            "output_contract": {
                "format": "plain_text",
                "field": "search_text",
                "no_markdown": True,
                "no_explanations": True,
            },
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _parse(raw_output: object, original: str) -> EnrichedQuery:
        if isinstance(raw_output, dict):
            search_text = raw_output.get("search_text")
            if isinstance(search_text, str) and search_text.strip():
                return EnrichedQuery(
                    original=original,
                    enriched=" ".join(search_text.split()),
                    metadata={"source": "llm_search_text_tool"},
                )
        if hasattr(raw_output, "model_dump"):
            try:
                dumped = raw_output.model_dump()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                search_text = dumped.get("search_text")
                if isinstance(search_text, str) and search_text.strip():
                    return EnrichedQuery(
                        original=original,
                        enriched=" ".join(search_text.split()),
                        metadata={"source": "llm_search_text_tool"},
                    )

        cleaned = str(raw_output or "").strip()
        cleaned = re.sub(r"^```(?:text|json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = re.sub(r"^(?:search_text|search text)\s*[:=-]\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = " ".join(cleaned.split())

        if cleaned.startswith("{"):
            extracted = _extract_text_from_json_like(cleaned)
            if extracted:
                cleaned = extracted

        if not cleaned:
            return QueryEnricher._fallback(original)

        return EnrichedQuery(original=original, enriched=cleaned, metadata={"source": "llm_search_text"})

    @staticmethod
    def _fallback(original: str) -> EnrichedQuery:
        lowered = original.lower()
        english_parts: list[str] = []
        enriched_parts = [original]

        if "ввп" in lowered or re.search(r"\bgdp\b", lowered):
            english_parts.append("GDP gross domestic product")
            enriched_parts.append("ВВП валовой внутренний продукт GDP gross domestic product макроэкономический показатель")

        if re.search(r"\bросси[ия]\b|рф\b", lowered):
            english_parts.append("Russia")
        if "казахстан" in lowered:
            english_parts.append("Kazakhstan")

        english_query = " ".join(dict.fromkeys(english_parts))
        if english_query:
            enriched_parts.append(english_query)

        return EnrichedQuery(
            original=original,
            enriched=" ".join(enriched_parts),
            metadata={
                "source": "fallback_search_text",
            },
        )


def _extract_text_from_json_like(value: str) -> str:
    for key in ("search_text", "searchText", "enriched"):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', value)
        if match:
            return " ".join(match.group(1).split())
    return ""


def _recent_dialog_context(history: tuple[dict[str, object], ...]) -> list[dict[str, object]]:
    context: list[dict[str, object]] = []
    for item in history[-6:]:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        context.append(
            {
                "role": role,
                "content": content[:4000],
                "truncated": len(content) > 4000,
            }
        )
    return context
