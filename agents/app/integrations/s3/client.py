from typing import Protocol

from app.contracts import HumanReadableFile, ParquetCandidate


class S3Client(Protocol):
    async def get_readable_files(self, parquets: tuple[ParquetCandidate, ...]) -> tuple[HumanReadableFile, ...]:
        """Load human-readable file metadata related to selected parquets."""

    async def get_parquet_uri(self, dataset_id: str) -> str:
        """Resolve a parquet object URI by dataset id."""

