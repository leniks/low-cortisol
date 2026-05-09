from dataclasses import dataclass

from app.contracts import EnrichedQuery, ParquetCandidate
from app.models.parquet_reranker import ParquetRerankerModel


@dataclass(frozen=True)
class ParquetRerankingStep:
    model: ParquetRerankerModel

    async def run(
        self,
        query: EnrichedQuery,
        candidates: tuple[ParquetCandidate, ...],
    ) -> tuple[ParquetCandidate, ...]:
        return await self.model.rerank(query, candidates)

