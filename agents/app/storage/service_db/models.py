from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class SessionRecord:
    id: str
    created_at: datetime
    updated_at: datetime
    title: str | None = None


@dataclass(frozen=True)
class SessionMessageRecord:
    id: str
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime

