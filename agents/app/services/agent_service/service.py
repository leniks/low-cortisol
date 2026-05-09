from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.contracts import PipelineEvent, UserRequest
from app.pipeline import AgentPipeline


@dataclass(frozen=True)
class AgentService:
    pipeline: AgentPipeline

    async def run_stream(self, request: UserRequest) -> AsyncIterator[PipelineEvent]:
        async for event in self.pipeline.run(request):
            yield event

