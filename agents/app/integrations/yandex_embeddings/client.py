import httpx


class YandexEmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        folder_id: str,
        model: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._folder_id = folder_id
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def embed_query(self, text: str) -> tuple[float, ...]:
        payload = {
            "modelUri": self._model,
            "text": text,
        }
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(self._embedding_url(), headers=self._headers(), json=payload)
            response.raise_for_status()

        data = response.json()
        embedding = data.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise RuntimeError("Yandex embedding response does not contain a non-empty embedding list")

        return tuple(float(value) for value in embedding)

    def _embedding_url(self) -> str:
        if self._base_url.endswith("/textEmbedding"):
            return self._base_url
        return f"{self._base_url}/textEmbedding"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "x-folder-id": self._folder_id,
        }
        headers["Authorization"] = self._auth_header(self._api_key)
        return headers

    @staticmethod
    def _auth_header(api_key: str) -> str:
        lowered = api_key.lower()
        if lowered.startswith("api-key ") or lowered.startswith("bearer "):
            return api_key
        return f"Api-Key {api_key}"
