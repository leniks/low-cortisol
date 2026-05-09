from typing import Protocol

from app.contracts import ArtifactRoute, PipelineResult, SqlReviewDraft, UserRequest


class MainAgentModel(Protocol):
    async def draft_sql(self, request: UserRequest, artifacts: ArtifactRoute) -> SqlReviewDraft:
        """Create SQL that an analyst can review before calculations."""

    async def calculate(self, request: UserRequest, sql: SqlReviewDraft) -> PipelineResult:
        """Run the main calculations and produce the final answer."""

