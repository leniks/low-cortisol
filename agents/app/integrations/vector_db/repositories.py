from typing import Protocol

from app.contracts import EnrichedQuery, ParquetCandidate


class DatasetVectorRepository(Protocol):
    async def search(self, query: EnrichedQuery, limit: int) -> tuple[ParquetCandidate, ...]:
        """Search pgvector dataset metadata and return parquet candidates."""

