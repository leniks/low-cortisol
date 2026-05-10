from typing import Literal

from pydantic import BaseModel, Field


class ClarificationOption(BaseModel):
    label: str = Field(min_length=1)
    value: str = Field(min_length=1)


class ClarificationResult(BaseModel):
    is_complete: bool
    question: str | None = None
    missing_fields: tuple[Literal["period", "geography", "metric", "other"], ...] = ()
    options: tuple[ClarificationOption, ...] = ()
    reason: str = Field(min_length=1)

