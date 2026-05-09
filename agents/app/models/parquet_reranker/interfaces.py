from typing import Protocol

from app.contracts import EnrichedQuery, ParquetCandidate


class ParquetRerankerModel(Protocol):
    async def rerank(
        self,
        query: EnrichedQuery,
        candidates: tuple[ParquetCandidate, ...],
    ) -> tuple[ParquetCandidate, ...]:
        """Remove irrelevant parquet files and sort the useful ones."""

