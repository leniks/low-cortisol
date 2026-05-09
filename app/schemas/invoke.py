from typing import Literal

from pydantic import BaseModel, Field


class DialogMessage(BaseModel):
    role: Literal["user", "assistant"] = "user"
    content: str = Field(min_length=1)


class InvokeRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=2)


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
