import asyncio
from typing import Any, AsyncGenerator

import httpx

from app.core.settings import Settings


class AgentService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(base_url=self._settings.agent_service_url, timeout=30.0)

    async def run_stream(self, user_query: str, conversation_id: str | None = None) -> AsyncGenerator[dict[str, Any], None]:
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
        params = {"message": user_query}
        if conversation_id:
            params["conversation_id"] = conversation_id

        async with self._client.stream("GET", url, params=params) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    try:
                        event = httpx.sse.Event.from_raw(line.encode())
                        if event.data:
                            yield event.json()
                    except Exception:
                        continue
