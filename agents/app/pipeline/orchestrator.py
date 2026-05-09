from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.contracts import PipelineEvent, UserRequest
from app.pipeline.artifact_routing import ArtifactRoutingStep
from app.pipeline.main_calculations import MainCalculationsStep
from app.pipeline.parquet_discovery import ParquetDiscoveryStep
from app.pipeline.parquet_reranking import ParquetRerankingStep
from app.pipeline.query_enrichment import QueryEnrichmentStep
from app.pipeline.sql_review import SqlReviewStep


@dataclass(frozen=True)
class AgentPipeline:
    query_enrichment: QueryEnrichmentStep
    parquet_discovery: ParquetDiscoveryStep
    parquet_reranking: ParquetRerankingStep
    artifact_routing: ArtifactRoutingStep
    sql_review: SqlReviewStep
    main_calculations: MainCalculationsStep

    async def run(self, request: UserRequest) -> AsyncIterator[PipelineEvent]:
        enriched = await self.query_enrichment.run(request)
        yield PipelineEvent(type="thought", text="Query enriched", payload={"query": enriched.enriched})

        candidates = await self.parquet_discovery.run(enriched)
        yield PipelineEvent(type="tool_result", text="Parquet candidates found", payload={"count": len(candidates)})

        parquets = await self.parquet_reranking.run(enriched, candidates)
        yield PipelineEvent(type="tool_result", text="Parquets reranked", payload={"count": len(parquets)})

        artifacts = await self.artifact_routing.run(parquets)
        yield PipelineEvent(type="tool_result", text="Artifacts routed", files=artifacts.readable_files)

        sql = await self.sql_review.run(request, artifacts)
        yield PipelineEvent(type="tool_call", text="SQL draft prepared", tool="sql_review", payload={"sql": sql.sql})

        result = await self.main_calculations.run(request, sql)
        yield PipelineEvent(type="final", text=result.answer, files=result.files, payload=result.metadata)

