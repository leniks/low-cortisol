from collections.abc import AsyncIterator

from agents import Runner

from app.core.settings import AgentSettings
from app.models.main_agent.factory import create_main_agent
from app.models.request_classifier import RagRouteDecision, RequestClassifier
from app.schemas.invoke import ChatMessage


class MainAgentChatService:
    def __init__(self, classifier: RequestClassifier) -> None:
        self._settings: AgentSettings | None = None
        self._agent = None
        self._classifier = classifier

    async def run_stream(
        self,
        *,
        message: str,
        history: list[ChatMessage],
        conversation_id: str | None = None,
        route_decision: RagRouteDecision | None = None,
    ) -> AsyncIterator[str]:
        agent = self._get_agent()
        history_payload = [item.model_dump() for item in history]
        route_decision = route_decision or await self._classifier.classify(message=message, history=history_payload)

        input_messages = [
            {
                "role": "user",
                "content": (
                    "Internal routing context for the next answer. Do not show this block verbatim.\n"
                    f"decision={route_decision.decision}\n"
                    f"needs_rag={route_decision.needs_rag}\n"
                    f"reason={route_decision.reason}\n"
                    f"confidence={route_decision.confidence}\n"
                    "If needs_rag=true, do not invent missing dataset-backed facts. "
                    "Say that data retrieval is required until the RAG step is connected."
                ),
            }
        ]
        input_messages.extend(history_payload)
        input_messages.append({"role": "user", "content": message})

        result = await Runner.run(agent, input_messages)
        answer = str(result.final_output or "").strip()
        if not answer:
            answer = "Пустой ответ основного агента."

        for chunk in self._chunk(answer):
            yield chunk

    def _get_agent(self):
        if self._agent is None:
            self._settings = AgentSettings.from_env()
            self._agent = create_main_agent(self._settings)
        return self._agent

    @staticmethod
    def _chunk(text: str, size: int = 80) -> list[str]:
        return [text[index : index + size] for index in range(0, len(text), size)]
