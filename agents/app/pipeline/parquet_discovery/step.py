from dataclasses import dataclass

from app.contracts import EnrichedQuery, ParquetCandidate
from app.models.rag_retriever import RagRetrieverModel


@dataclass(frozen=True)
class ParquetDiscoveryStep:
    model: RagRetrieverModel

    async def run(self, query: EnrichedQuery) -> tuple[ParquetCandidate, ...]:
        return await self.model.retrieve(query)

