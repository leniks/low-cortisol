from typing import Protocol

from app.contracts import HumanReadableFile, PipelineResult, SqlReviewDraft


class BackendProxyClient(Protocol):
    async def publish_readable_files(self, files: tuple[HumanReadableFile, ...]) -> None:
        """Send human-readable S3 files to the backend proxy."""

    async def publish_sql_review(self, conversation_id: str | None, sql: SqlReviewDraft) -> None:
        """Send SQL draft to the backend proxy for analyst review."""

    async def publish_final_answer(self, conversation_id: str | None, result: PipelineResult) -> None:
        """Send final answer and calculation artifacts to the backend proxy."""

