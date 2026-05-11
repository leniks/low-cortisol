from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any
from typing import Protocol
from uuid import uuid4

from app.schemas.invoke import DialogMessage


EMPTY_CHAT_STATE: dict[str, object] = {"version": 1, "activeSessionId": None, "sessions": []}
DEFAULT_CHAT_STATE_SCOPE = "default"


class DialogStore(Protocol):
    def ensure_conversation_id(self, conversation_id: str | None) -> str:
        """Return an existing conversation id or create a new conversation."""

    def get(self, conversation_id: str) -> list[DialogMessage]:
        """Return ordered dialog messages."""

    def set(self, conversation_id: str, dialog: list[DialogMessage]) -> None:
        """Replace the full dialog state."""

    def append_pair(self, conversation_id: str, user_text: str, assistant_text: str) -> list[DialogMessage]:
        """Append user/assistant messages."""

    def replace_last_pair(self, conversation_id: str, user_text: str, assistant_text: str) -> list[DialogMessage]:
        """Replace the last user/assistant pair, or append if the tail is not a pair."""

    def get_chat_state(self, scope: str = DEFAULT_CHAT_STATE_SCOPE) -> dict[str, Any]:
        """Return persisted frontend chat state."""

    def set_chat_state(self, state: dict[str, Any], scope: str = DEFAULT_CHAT_STATE_SCOPE) -> None:
        """Persist frontend chat state."""


@dataclass
class MemoryDialogStore:
    _dialogs: dict[str, list[DialogMessage]] = field(default_factory=dict)
    _chat_states: dict[str, dict[str, Any]] = field(default_factory=dict)

    def ensure_conversation_id(self, conversation_id: str | None) -> str:
        if conversation_id:
            self._dialogs.setdefault(conversation_id, [])
            return conversation_id
        conversation_id = str(uuid4())
        self._dialogs[conversation_id] = []
        return conversation_id

    def get(self, conversation_id: str) -> list[DialogMessage]:
        return list(self._dialogs.get(conversation_id, []))

    def set(self, conversation_id: str, dialog: list[DialogMessage]) -> None:
        self._dialogs[conversation_id] = list(dialog)

    def append_pair(self, conversation_id: str, user_text: str, assistant_text: str) -> list[DialogMessage]:
        dialog = self.get(conversation_id)
        dialog.append(DialogMessage(role="user", content=user_text))
        dialog.append(DialogMessage(role="assistant", content=assistant_text))
        self.set(conversation_id, dialog)
        return dialog

    def replace_last_pair(self, conversation_id: str, user_text: str, assistant_text: str) -> list[DialogMessage]:
        dialog = self.get(conversation_id)
        if len(dialog) >= 2 and dialog[-2].role == "user" and dialog[-1].role == "assistant":
            dialog[-2] = DialogMessage(role="user", content=user_text)
            dialog[-1] = DialogMessage(role="assistant", content=assistant_text)
            self.set(conversation_id, dialog)
            return dialog
        return self.append_pair(conversation_id, user_text, assistant_text)

    def get_chat_state(self, scope: str = DEFAULT_CHAT_STATE_SCOPE) -> dict[str, Any]:
        return dict(self._chat_states.get(scope, EMPTY_CHAT_STATE))

    def set_chat_state(self, state: dict[str, Any], scope: str = DEFAULT_CHAT_STATE_SCOPE) -> None:
        self._chat_states[scope] = dict(state)


class PostgresDialogStore:
    def __init__(self, dsn: str, *, schema: str = "chat_history") -> None:
        self._dsn = dsn
        self._schema = _clean_identifier(schema)
        self._ensure_schema()

    def ensure_conversation_id(self, conversation_id: str | None) -> str:
        conversation_id = conversation_id or str(uuid4())
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._schema}.conversations (id)
                    VALUES (%s)
                    ON CONFLICT (id) DO UPDATE SET updated_at = now()
                    """,
                    (conversation_id,),
                )
        return conversation_id

    def get(self, conversation_id: str) -> list[DialogMessage]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT role, content
                    FROM {self._schema}.messages
                    WHERE conversation_id = %s
                    ORDER BY position ASC, id ASC
                    """,
                    (conversation_id,),
                )
                rows = cursor.fetchall()
        return [DialogMessage(role=str(row[0]), content=str(row[1])) for row in rows]

    def set(self, conversation_id: str, dialog: list[DialogMessage]) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                self._upsert_conversation(cursor, conversation_id)
                cursor.execute(
                    f"DELETE FROM {self._schema}.messages WHERE conversation_id = %s",
                    (conversation_id,),
                )
                for position, message in enumerate(dialog):
                    cursor.execute(
                        f"""
                        INSERT INTO {self._schema}.messages (conversation_id, position, role, content)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (conversation_id, position, message.role, message.content),
                    )
                self._touch_conversation(cursor, conversation_id)

    def append_pair(self, conversation_id: str, user_text: str, assistant_text: str) -> list[DialogMessage]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                self._upsert_conversation(cursor, conversation_id)
                self._lock_conversation(cursor, conversation_id)
                next_position = self._next_position(cursor, conversation_id)
                for offset, message in enumerate(
                    (
                        DialogMessage(role="user", content=user_text),
                        DialogMessage(role="assistant", content=assistant_text),
                    )
                ):
                    cursor.execute(
                        f"""
                        INSERT INTO {self._schema}.messages (conversation_id, position, role, content)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (conversation_id, next_position + offset, message.role, message.content),
                    )
                self._touch_conversation(cursor, conversation_id)
        return self.get(conversation_id)

    def replace_last_pair(self, conversation_id: str, user_text: str, assistant_text: str) -> list[DialogMessage]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                self._upsert_conversation(cursor, conversation_id)
                self._lock_conversation(cursor, conversation_id)
                cursor.execute(
                    f"""
                    SELECT id, role
                    FROM {self._schema}.messages
                    WHERE conversation_id = %s
                    ORDER BY position DESC, id DESC
                    LIMIT 2
                    """,
                    (conversation_id,),
                )
                tail = cursor.fetchall()
                if len(tail) == 2 and str(tail[0][1]) == "assistant" and str(tail[1][1]) == "user":
                    cursor.execute(
                        f"UPDATE {self._schema}.messages SET content = %s WHERE id = %s",
                        (assistant_text, tail[0][0]),
                    )
                    cursor.execute(
                        f"UPDATE {self._schema}.messages SET content = %s WHERE id = %s",
                        (user_text, tail[1][0]),
                    )
                    self._touch_conversation(cursor, conversation_id)
                    should_append = False
                else:
                    should_append = True

        if should_append:
            return self.append_pair(conversation_id, user_text, assistant_text)
        return self.get(conversation_id)

    def get_chat_state(self, scope: str = DEFAULT_CHAT_STATE_SCOPE) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT payload FROM {self._schema}.app_state WHERE scope = %s",
                    (scope,),
                )
                row = cursor.fetchone()
        if not row:
            return dict(EMPTY_CHAT_STATE)
        payload = row[0]
        if isinstance(payload, dict):
            return payload
        return dict(EMPTY_CHAT_STATE)

    def set_chat_state(self, state: dict[str, Any], scope: str = DEFAULT_CHAT_STATE_SCOPE) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                try:
                    from psycopg.types.json import Jsonb
                except Exception as exc:  # pragma: no cover - depends on runtime image
                    raise RuntimeError("psycopg[binary] is required for Postgres chat state storage") from exc

                cursor.execute(
                    f"""
                    INSERT INTO {self._schema}.app_state (scope, payload)
                    VALUES (%s, %s)
                    ON CONFLICT (scope) DO UPDATE
                    SET payload = EXCLUDED.payload,
                        updated_at = now()
                    """,
                    (scope, Jsonb(state)),
                )

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema}")
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._schema}.conversations (
                        id TEXT PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._schema}.messages (
                        id BIGSERIAL PRIMARY KEY,
                        conversation_id TEXT NOT NULL REFERENCES {self._schema}.conversations(id) ON DELETE CASCADE,
                        position INTEGER NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                        content TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        UNIQUE (conversation_id, position)
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS messages_conversation_order_idx
                    ON {self._schema}.messages (conversation_id, position, id)
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._schema}.app_state (
                        scope TEXT PRIMARY KEY,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )

    def _connect(self):
        try:
            import psycopg
        except Exception as exc:  # pragma: no cover - depends on runtime image
            raise RuntimeError("psycopg[binary] is required for Postgres dialog storage") from exc

        return psycopg.connect(self._dsn)

    def _upsert_conversation(self, cursor, conversation_id: str) -> None:
        cursor.execute(
            f"""
            INSERT INTO {self._schema}.conversations (id)
            VALUES (%s)
            ON CONFLICT (id) DO NOTHING
            """,
            (conversation_id,),
        )

    def _lock_conversation(self, cursor, conversation_id: str) -> None:
        cursor.execute(
            f"SELECT id FROM {self._schema}.conversations WHERE id = %s FOR UPDATE",
            (conversation_id,),
        )

    def _touch_conversation(self, cursor, conversation_id: str) -> None:
        cursor.execute(
            f"UPDATE {self._schema}.conversations SET updated_at = now() WHERE id = %s",
            (conversation_id,),
        )

    def _next_position(self, cursor, conversation_id: str) -> int:
        cursor.execute(
            f"SELECT COALESCE(MAX(position), -1) + 1 FROM {self._schema}.messages WHERE conversation_id = %s",
            (conversation_id,),
        )
        return int(cursor.fetchone()[0])


def _clean_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Postgres schema name must be a simple SQL identifier")
    return value
