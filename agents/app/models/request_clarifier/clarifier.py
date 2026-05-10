import json
import re
from typing import Any

from agents import Runner

from app.core.settings import AgentSettings
from app.models.request_clarifier.factory import create_request_clarifier_agent
from app.models.request_clarifier.schemas import ClarificationOption, ClarificationResult


class RequestClarifier:
    def __init__(self) -> None:
        self._settings: AgentSettings | None = None
        self._agent = None

    async def clarify(self, *, message: str, history: list[dict[str, str]]) -> ClarificationResult:
        agent = self._get_agent()
        result = await Runner.run(agent, [{"role": "user", "content": self._build_prompt(message, history)}])
        return self._parse(str(result.final_output or ""), message)

    def _get_agent(self):
        if self._agent is None:
            self._settings = AgentSettings.from_env()
            self._agent = create_request_clarifier_agent(self._settings)
        return self._agent

    @staticmethod
    def _build_prompt(message: str, history: list[dict[str, str]]) -> str:
        payload: dict[str, Any] = {
            "current_user_message": message,
            "recent_chat_history": history[-12:],
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _parse(raw_output: str, message: str) -> ClarificationResult:
        cleaned = raw_output.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            return ClarificationResult.model_validate_json(cleaned)
        except Exception:
            return RequestClarifier._fallback(message)

    @staticmethod
    def _fallback(message: str) -> ClarificationResult:
        has_period = bool(re.search(r"\b(?:19|20)\d{2}\b|за\s+все\s+время|за\s+всё\s+время", message.lower()))
        data_like = bool(re.search(r"\b(ввп|инфляц|населен|экспорт|импорт|динамик|данн|показател)", message.lower()))
        if data_like and not has_period:
            return ClarificationResult(
                is_complete=False,
                question="За какой период подготовить данные?",
                missing_fields=("period",),
                options=(
                    ClarificationOption(label="За всё время", value="за всё время"),
                    ClarificationOption(label="2024", value="за 2024 год"),
                    ClarificationOption(label="2023-2024", value="за 2023-2024 годы"),
                    ClarificationOption(label="Ввести вручную", value="manual"),
                ),
                reason="В запросе есть показатель, но не указан период.",
            )

        return ClarificationResult(
            is_complete=True,
            question=None,
            missing_fields=(),
            options=(),
            reason="Достаточно информации для следующего шага.",
        )

