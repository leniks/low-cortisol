from pydantic import BaseModel, Field


class RerankDecision(BaseModel):
    dataset_id: str = Field(min_length=1)
    keep: bool
    relevance: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


class RerankResult(BaseModel):
    items: tuple[RerankDecision, ...] = ()
