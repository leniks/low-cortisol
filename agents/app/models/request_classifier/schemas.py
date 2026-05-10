from typing import Literal

from pydantic import BaseModel, Field


class RagRouteDecision(BaseModel):
    decision: Literal["needs_rag", "use_existing_context", "no_data_needed"]
    needs_rag: bool
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

