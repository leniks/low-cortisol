from typing import Literal

from pydantic import BaseModel, Field


class DialogMessage(BaseModel):
    role: Literal["user", "assistant"] = "user"
    content: str = Field(min_length=1)


class InvokeRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=2)


class RagRouteDecision(BaseModel):
    decision: Literal["needs_rag", "use_existing_context", "no_data_needed"]
    needs_rag: bool
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ClarificationOption(BaseModel):
    label: str = Field(min_length=1)
    value: str = Field(min_length=1)


class ClarificationResult(BaseModel):
    is_complete: bool
    question: str | None = None
    missing_fields: list[Literal["period", "geography", "metric", "other"]] = Field(default_factory=list)
    options: list[ClarificationOption] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class ClassifyRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=2)


class DialogStateRequest(BaseModel):
    conversation_id: str
    dialog: list[DialogMessage]


class FileData(BaseModel):
    filename: str
    content_type: str
    size: int
    content: str


class InvokeEvent(BaseModel):
    type: Literal["thought", "tool_call", "tool_result", "final"]
    text: str
    tool: str | None = None
    payload: dict | None = None
    files: list[FileData] | None = None


class InvokeResponse(BaseModel):
    conversation_id: str | None = None
    answer: str
    dialog: list[DialogMessage]
