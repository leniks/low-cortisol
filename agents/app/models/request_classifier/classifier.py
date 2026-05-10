import json
import re
from typing import Any

from agents import Runner

from app.core.settings import AgentSettings
from app.models.request_classifier.factory import create_request_classifier_agent
from app.models.request_classifier.schemas import RagRouteDecision


class RequestClassifier:
    def __init__(self) -> None:
        self._settings: AgentSettings | None = None
        self._agent = None

    async def classify(self, *, message: str, history: list[dict[str, str]]) -> RagRouteDecision:
        agent = self._get_agent()
        result = await Runner.run(agent, [{"role": "user", "content": self._build_prompt(message, history)}])
        return self._parse(str(result.final_output or ""))

    def _get_agent(self):
        if self._agent is None:
            self._settings = AgentSettings.from_env()
            self._agent = create_request_classifier_agent(self._settings)
        return self._agent

    @staticmethod
    def _build_prompt(message: str, history: list[dict[str, str]]) -> str:
        recent_history = history[-12:]
        payload: dict[str, Any] = {
            "current_user_message": message,
            "recent_chat_history": recent_history,
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _parse(raw_output: str) -> RagRouteDecision:
        cleaned = raw_output.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            decision = RagRouteDecision.model_validate_json(cleaned)
            return decision.model_copy(update={"needs_rag": decision.decision == "needs_rag"})
        except Exception:
            return RagRouteDecision(
                decision="needs_rag",
                needs_rag=True,
                reason="Classifier returned non-JSON output; defaulting to RAG retrieval.",
                confidence=0.0,
            )
