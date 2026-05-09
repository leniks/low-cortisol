from dataclasses import dataclass

from app.contracts import PipelineResult, SqlReviewDraft, UserRequest
from app.models.main_agent import MainAgentModel
from app.services.backend_proxy import BackendProxyClient


@dataclass(frozen=True)
class MainCalculationsStep:
    main_agent: MainAgentModel
    backend_proxy: BackendProxyClient

    async def run(self, request: UserRequest, sql: SqlReviewDraft) -> PipelineResult:
        result = await self.main_agent.calculate(request, sql)
        await self.backend_proxy.publish_final_answer(request.conversation_id, result)
        return result

