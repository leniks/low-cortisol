import asyncio
import json
from typing import Any, AsyncGenerator

import httpx

from app.core.settings import Settings


class AgentService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=self._settings.agent_service_url,
            timeout=httpx.Timeout(None, connect=10.0),
        )

    async def run_stream(
        self,
        user_query: str,
        conversation_id: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        if self._settings.mock_mode:
            thought = "Анализирую запрос и подготавливаю набор данных."
            thought_parts = [thought[i : i + 25] for i in range(0, len(thought), 25)]
            for part in thought_parts:
                yield {"type": "thought", "text": part}
                await asyncio.sleep(0.05)

            final = f"Моковый ответ на: {user_query}"
            final_parts = [final[i : i + 30] for i in range(0, len(final), 30)]
            for part in final_parts:
                yield {"type": "final", "text": part}
                await asyncio.sleep(0.05)
            return

        url = "/invoke/stream"
        payload = {
            "message": user_query,
            "conversation_id": conversation_id,
            "history": history or [],
        }

        async with self._client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    try:
                        yield json.loads(line.removeprefix("data: "))
                    except json.JSONDecodeError:
                        continue
