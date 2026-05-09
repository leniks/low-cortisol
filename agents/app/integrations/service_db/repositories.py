from typing import Protocol

from app.storage.service_db import SessionMessageRecord, SessionRecord


class SessionRepository(Protocol):
    async def get(self, session_id: str) -> SessionRecord | None:
        """Read a chat session."""

    async def save(self, session: SessionRecord) -> None:
        """Create or update a chat session."""


class SessionMessageRepository(Protocol):
    async def list_by_session(self, session_id: str) -> tuple[SessionMessageRecord, ...]:
        """Read messages for a session."""

    async def append(self, message: SessionMessageRecord) -> None:
        """Append a message to a session."""

