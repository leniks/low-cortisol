from dataclasses import dataclass

from app.contracts import EnrichedQuery, UserRequest
from app.models.query_enricher import QueryEnricherModel


@dataclass(frozen=True)
class QueryEnrichmentStep:
    model: QueryEnricherModel

    async def run(self, request: UserRequest) -> EnrichedQuery:
        return await self.model.enrich(request)

