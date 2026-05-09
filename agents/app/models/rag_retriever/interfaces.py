from typing import Protocol

from app.contracts import EnrichedQuery, ParquetCandidate


class RagRetrieverModel(Protocol):
    async def retrieve(self, query: EnrichedQuery) -> tuple[ParquetCandidate, ...]:
        """Find candidate parquet files through vector search metadata."""

