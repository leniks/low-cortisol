from dataclasses import dataclass

from app.contracts import ArtifactRoute, ParquetCandidate
from app.services.backend_proxy import BackendProxyClient
from app.services.llm_service import LlmServiceClient


@dataclass(frozen=True)
class ArtifactRoutingStep:
    backend_proxy: BackendProxyClient
    llm_service: LlmServiceClient

    async def run(self, parquets: tuple[ParquetCandidate, ...]) -> ArtifactRoute:
        route = await self.llm_service.prepare_artifacts(parquets)
        await self.backend_proxy.publish_readable_files(route.readable_files)
        await self.llm_service.publish_parquets(route.parquets)
        return route

