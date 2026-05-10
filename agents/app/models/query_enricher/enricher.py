import re

from agents import Runner

from app.contracts import EnrichedQuery, UserRequest
from app.core.settings import AgentSettings
from app.models.query_enricher.factory import create_query_enricher_agent


class QueryEnricher:
    def __init__(self) -> None:
        self._settings: AgentSettings | None = None
        self._agent = None

    async def enrich(self, request: UserRequest) -> EnrichedQuery:
        agent = self._get_agent()
        result = await Runner.run(agent, [{"role": "user", "content": self._build_prompt(request)}])
        return self._parse(str(result.final_output or ""), request.message)

    def _get_agent(self):
        if self._agent is None:
            self._settings = AgentSettings.from_env()
            self._agent = create_query_enricher_agent(self._settings)
        return self._agent

    @staticmethod
    def _build_prompt(request: UserRequest) -> str:
        history_lines = [
            f"{item.get('role', 'user')}: {item.get('content', '')}"
            for item in request.history[-8:]
            if item.get("content")
        ]
        history = "\n".join(history_lines) if history_lines else "Нет истории."
        return (
            "Собери одну строку search_text для поиска датасетов по описанию.\n\n"
            f"История:\n{history}\n\n"
            f"Запрос пользователя:\n{request.message}\n\n"
            "Ответь только строкой search_text."
        )

    @staticmethod
    def _parse(raw_output: str, original: str) -> EnrichedQuery:
        cleaned = raw_output.strip()
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

        if "ввп" in lowered:
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
