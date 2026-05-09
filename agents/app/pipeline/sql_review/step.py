from dataclasses import dataclass

from app.contracts import ArtifactRoute, SqlReviewDraft, UserRequest
from app.models.main_agent import MainAgentModel
from app.services.backend_proxy import BackendProxyClient


@dataclass(frozen=True)
class SqlReviewStep:
    main_agent: MainAgentModel
    backend_proxy: BackendProxyClient

    async def run(self, request: UserRequest, artifacts: ArtifactRoute) -> SqlReviewDraft:
        sql = await self.main_agent.draft_sql(request, artifacts)
        await self.backend_proxy.publish_sql_review(request.conversation_id, sql)
        return sql

