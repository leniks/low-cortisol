from dataclasses import dataclass

from app.contracts import EnrichedQuery, ParquetCandidate
from app.integrations.vector_db import DatasetVectorRepository
from app.integrations.yandex_embeddings import YandexEmbeddingClient


@dataclass(frozen=True)
class PgVectorRagRetriever:
    embedder: YandexEmbeddingClient
    repository: DatasetVectorRepository
    top_k: int

    async def retrieve(self, query: EnrichedQuery) -> tuple[ParquetCandidate, ...]:
        embedding = await self.embedder.embed_query(query.enriched)
        return await self.repository.search(query, embedding, self.top_k)
