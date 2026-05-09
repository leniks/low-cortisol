from typing import Protocol

from app.contracts import EnrichedQuery, UserRequest


class QueryEnricherModel(Protocol):
    async def enrich(self, request: UserRequest) -> EnrichedQuery:
        """Expand the user query and detect missing clarification points."""

