from collections.abc import AsyncIterator

from agents import Runner

from app.core.settings import AgentSettings
from app.models.main_agent.factory import create_main_agent
from app.schemas.invoke import ChatMessage


class MainAgentChatService:
    def __init__(self) -> None:
        self._settings: AgentSettings | None = None
        self._agent = None

    async def run_stream(
        self,
        *,
        message: str,
        history: list[ChatMessage],
        conversation_id: str | None = None,
    ) -> AsyncIterator[str]:
        agent = self._get_agent()
        input_messages = [item.model_dump() for item in history]
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

