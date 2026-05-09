from typing import Protocol

from app.contracts import ArtifactRoute, ParquetCandidate


class LlmServiceClient(Protocol):
    async def prepare_artifacts(self, parquets: tuple[ParquetCandidate, ...]) -> ArtifactRoute:
        """Resolve readable S3 files and parquet inputs for selected datasets."""

    async def publish_parquets(self, parquets: tuple[ParquetCandidate, ...]) -> None:
        """Send parquet inputs to the LLM service."""

