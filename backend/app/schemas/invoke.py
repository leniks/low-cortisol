from typing import Literal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DialogMessage(BaseModel):
    role: Literal["user", "assistant"] = "user"
    content: str = Field(min_length=1)


class InvokeRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=2)


class ClarificationOption(BaseModel):
    label: str = Field(min_length=1)
    value: str = Field(min_length=1)


ClarificationField = Literal["period", "geography", "metric", "formula", "other"]


class ClarificationStep(BaseModel):
    field: ClarificationField
    question: str | None = None
    reason: str = ""
    options: list[ClarificationOption] = Field(default_factory=list)


class ClarificationResult(BaseModel):
    is_complete: bool
    question: str | None = None
    missing_fields: list[ClarificationField] = Field(default_factory=list)
    options: list[ClarificationOption] = Field(default_factory=list)
    steps: list[ClarificationStep] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class DialogStateRequest(BaseModel):
    conversation_id: str
    dialog: list[DialogMessage]


class PersistedChatState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: Literal[1] = 1
    active_session_id: str | None = Field(None, alias="activeSessionId")
    sessions: list[dict[str, Any]] = Field(default_factory=list)


class FileData(BaseModel):
    filename: str
    content_type: str
    size: int
    content: str


class ArtifactResponse(BaseModel):
    id: str
    run_id: str
    conversation_id: str
    type: str
    title: str
    filename: str | None = None
    content_type: str
    status: str
    version: int
    download_url: str


class CheckpointResponse(BaseModel):
    id: str
    conversation_id: str
    run_id: str
    title: str


class InvokeEvent(BaseModel):
    type: Literal["thought", "tool_call", "tool_result", "clarification", "final"]
    text: str = ""
    tool: str | None = None
    payload: dict | None = None
    clarification: ClarificationResult | None = None
    files: list[FileData] | None = None


class InvokeResponse(BaseModel):
    conversation_id: str | None = None
    answer: str
    dialog: list[DialogMessage]
