from typing import Literal

from pydantic import BaseModel, Field

from app.models.request_classifier.schemas import RagRouteDecision


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1)


class InvokeRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=2)
    history: list[ChatMessage] = Field(default_factory=list)
    route_decision: RagRouteDecision | None = None
