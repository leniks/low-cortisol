from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import re
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    run_id: str
    conversation_id: str
    type: str
    title: str
    payload: dict[str, Any] = field(default_factory=dict)
    file_path: str | None = None
    filename: str | None = None
    content_type: str = "application/json"
    status: str = "active"
    version: int = 1


@dataclass(frozen=True)
class CheckpointRecord:
    id: str
    conversation_id: str
    run_id: str
    title: str
    payload: dict[str, Any] = field(default_factory=dict)


class ArtifactStore(Protocol):
    def create_run(
        self,
        *,
        conversation_id: str,
        user_message: str,
        parent_checkpoint_id: str | None = None,
    ) -> str:
        """Create an auditable agent run."""

    def complete_run(self, run_id: str, *, status: str = "completed") -> None:
        """Mark run as completed, failed, needs_clarification, or superseded."""

    def create_artifact(
        self,
        *,
        run_id: str,
        conversation_id: str,
        artifact_type: str,
        title: str,
        payload: dict[str, Any] | None = None,
        content: str | bytes | None = None,
        filename: str | None = None,
        content_type: str = "application/json",
    ) -> ArtifactRecord:
        """Persist artifact metadata and optional downloadable content."""

    def list_artifacts(self, conversation_id: str) -> list[ArtifactRecord]:
        """Return artifacts for a conversation."""

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        """Return artifact metadata."""

    def create_checkpoint(
        self,
        *,
        conversation_id: str,
        run_id: str,
        title: str,
        payload: dict[str, Any],
    ) -> CheckpointRecord:
        """Persist a rollback checkpoint."""

    def list_checkpoints(self, conversation_id: str) -> list[CheckpointRecord]:
        """Return checkpoints for a conversation."""

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointRecord | None:
        """Return checkpoint metadata and payload."""

    def mark_superseded_after_checkpoint(self, *, conversation_id: str, checkpoint_id: str) -> None:
        """Mark runs and artifacts newer than checkpoint as superseded."""


class MemoryArtifactStore:
    def __init__(self, artifacts_dir: str) -> None:
        self._artifacts_dir = Path(artifacts_dir)
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, dict[str, Any]] = {}
        self._artifacts: dict[str, ArtifactRecord] = {}
        self._checkpoints: dict[str, CheckpointRecord] = {}

    def create_run(
        self,
        *,
        conversation_id: str,
        user_message: str,
        parent_checkpoint_id: str | None = None,
    ) -> str:
        run_id = str(uuid4())
        self._runs[run_id] = {
            "conversation_id": conversation_id,
            "user_message": user_message,
            "parent_checkpoint_id": parent_checkpoint_id,
            "status": "running",
            "order": len(self._runs),
        }
        return run_id

    def complete_run(self, run_id: str, *, status: str = "completed") -> None:
        if run_id in self._runs:
            self._runs[run_id]["status"] = status

    def create_artifact(
        self,
        *,
        run_id: str,
        conversation_id: str,
        artifact_type: str,
        title: str,
        payload: dict[str, Any] | None = None,
        content: str | bytes | None = None,
        filename: str | None = None,
        content_type: str = "application/json",
    ) -> ArtifactRecord:
        artifact_id = str(uuid4())
        path = self._write_content(artifact_id, content, filename, payload)
        record = ArtifactRecord(
            id=artifact_id,
            run_id=run_id,
            conversation_id=conversation_id,
            type=artifact_type,
            title=title,
            payload=payload or {},
            file_path=str(path) if path else None,
            filename=filename or (path.name if path else None),
            content_type=content_type,
        )
        self._artifacts[artifact_id] = record
        return record

    def list_artifacts(self, conversation_id: str) -> list[ArtifactRecord]:
        return [
            artifact
            for artifact in self._artifacts.values()
            if artifact.conversation_id == conversation_id
        ]

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        return self._artifacts.get(artifact_id)

    def create_checkpoint(
        self,
        *,
        conversation_id: str,
        run_id: str,
        title: str,
        payload: dict[str, Any],
    ) -> CheckpointRecord:
        checkpoint = CheckpointRecord(
            id=str(uuid4()),
            conversation_id=conversation_id,
            run_id=run_id,
            title=title,
            payload=payload,
        )
        self._checkpoints[checkpoint.id] = checkpoint
        return checkpoint

    def list_checkpoints(self, conversation_id: str) -> list[CheckpointRecord]:
        return [
            checkpoint
            for checkpoint in self._checkpoints.values()
            if checkpoint.conversation_id == conversation_id
        ]

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointRecord | None:
        return self._checkpoints.get(checkpoint_id)

    def mark_superseded_after_checkpoint(self, *, conversation_id: str, checkpoint_id: str) -> None:
        checkpoint = self._checkpoints.get(checkpoint_id)
        if not checkpoint:
            return
        checkpoint_order = self._runs.get(checkpoint.run_id, {}).get("order", -1)
        superseded_run_ids = {
            run_id
            for run_id, run in self._runs.items()
            if run.get("conversation_id") == conversation_id and int(run.get("order", -1)) > checkpoint_order
        }
        for run_id in superseded_run_ids:
            self._runs[run_id]["status"] = "superseded"
        for artifact_id, artifact in list(self._artifacts.items()):
            if artifact.run_id in superseded_run_ids:
                self._artifacts[artifact_id] = ArtifactRecord(
                    **{**artifact.__dict__, "status": "superseded"}
                )

    def _write_content(
        self,
        artifact_id: str,
        content: str | bytes | None,
        filename: str | None,
        payload: dict[str, Any] | None,
    ) -> Path | None:
        if content is None and payload is None:
            return None

        safe_name = _safe_filename(filename or f"{artifact_id}.json")
        path = self._artifacts_dir / f"{artifact_id}-{safe_name}"
        if content is None:
            content = json.dumps(payload or {}, ensure_ascii=False, indent=2)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path


class PostgresArtifactStore:
    def __init__(self, dsn: str, *, schema: str, artifacts_dir: str) -> None:
        self._dsn = dsn
        self._schema = _clean_identifier(schema)
        self._artifacts_dir = Path(artifacts_dir)
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def create_run(
        self,
        *,
        conversation_id: str,
        user_message: str,
        parent_checkpoint_id: str | None = None,
    ) -> str:
        run_id = str(uuid4())
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._schema}.runs
                        (id, conversation_id, user_message, parent_checkpoint_id, status)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (run_id, conversation_id, user_message, parent_checkpoint_id, "running"),
                )
        return run_id

    def complete_run(self, run_id: str, *, status: str = "completed") -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self._schema}.runs
                    SET status = %s, completed_at = now()
                    WHERE id = %s
                    """,
                    (status, run_id),
                )

    def create_artifact(
        self,
        *,
        run_id: str,
        conversation_id: str,
        artifact_type: str,
        title: str,
        payload: dict[str, Any] | None = None,
        content: str | bytes | None = None,
        filename: str | None = None,
        content_type: str = "application/json",
    ) -> ArtifactRecord:
        artifact_id = str(uuid4())
        path = self._write_content(artifact_id, content, filename, payload)
        filename = filename or (path.name if path else None)
        payload = payload or {}

        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._schema}.artifacts
                        (id, run_id, conversation_id, type, title, payload_json,
                         file_path, filename, content_type, status, version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        artifact_id,
                        run_id,
                        conversation_id,
                        artifact_type,
                        title,
                        self._jsonb(payload),
                        str(path) if path else None,
                        filename,
                        content_type,
                        "active",
                        1,
                    ),
                )

        return ArtifactRecord(
            id=artifact_id,
            run_id=run_id,
            conversation_id=conversation_id,
            type=artifact_type,
            title=title,
            payload=payload,
            file_path=str(path) if path else None,
            filename=filename,
            content_type=content_type,
        )

    def list_artifacts(self, conversation_id: str) -> list[ArtifactRecord]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, run_id, conversation_id, type, title, payload_json,
                           file_path, filename, content_type, status, version
                    FROM {self._schema}.artifacts
                    WHERE conversation_id = %s
                    ORDER BY created_at ASC
                    """,
                    (conversation_id,),
                )
                rows = cursor.fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, run_id, conversation_id, type, title, payload_json,
                           file_path, filename, content_type, status, version
                    FROM {self._schema}.artifacts
                    WHERE id = %s
                    """,
                    (artifact_id,),
                )
                row = cursor.fetchone()
        return self._artifact_from_row(row) if row else None

    def create_checkpoint(
        self,
        *,
        conversation_id: str,
        run_id: str,
        title: str,
        payload: dict[str, Any],
    ) -> CheckpointRecord:
        checkpoint_id = str(uuid4())
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._schema}.checkpoints
                        (id, conversation_id, run_id, title, payload_json)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (checkpoint_id, conversation_id, run_id, title, self._jsonb(payload)),
                )
        return CheckpointRecord(
            id=checkpoint_id,
            conversation_id=conversation_id,
            run_id=run_id,
            title=title,
            payload=payload,
        )

    def list_checkpoints(self, conversation_id: str) -> list[CheckpointRecord]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, conversation_id, run_id, title, payload_json
                    FROM {self._schema}.checkpoints
                    WHERE conversation_id = %s
                    ORDER BY created_at ASC
                    """,
                    (conversation_id,),
                )
                rows = cursor.fetchall()
        return [self._checkpoint_from_row(row) for row in rows]

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, conversation_id, run_id, title, payload_json
                    FROM {self._schema}.checkpoints
                    WHERE id = %s
                    """,
                    (checkpoint_id,),
                )
                row = cursor.fetchone()
        return self._checkpoint_from_row(row) if row else None

    def mark_superseded_after_checkpoint(self, *, conversation_id: str, checkpoint_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT created_at
                    FROM {self._schema}.checkpoints
                    WHERE id = %s AND conversation_id = %s
                    """,
                    (checkpoint_id, conversation_id),
                )
                row = cursor.fetchone()
                if not row:
                    return
                checkpoint_created_at = row[0]
                cursor.execute(
                    f"""
                    UPDATE {self._schema}.runs
                    SET status = 'superseded'
                    WHERE conversation_id = %s
                      AND created_at > %s
                    """,
                    (conversation_id, checkpoint_created_at),
                )
                cursor.execute(
                    f"""
                    UPDATE {self._schema}.artifacts
                    SET status = 'superseded'
                    WHERE conversation_id = %s
                      AND run_id IN (
                        SELECT id
                        FROM {self._schema}.runs
                        WHERE conversation_id = %s
                          AND status = 'superseded'
                      )
                    """,
                    (conversation_id, conversation_id),
                )

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema}")
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._schema}.runs (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        user_message TEXT NOT NULL,
                        parent_checkpoint_id TEXT,
                        status TEXT NOT NULL DEFAULT 'running',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        completed_at TIMESTAMPTZ
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS runs_conversation_idx
                    ON {self._schema}.runs (conversation_id, created_at)
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._schema}.artifacts (
                        id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        payload_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        file_path TEXT,
                        filename TEXT,
                        content_type TEXT NOT NULL DEFAULT 'application/json',
                        status TEXT NOT NULL DEFAULT 'active',
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS artifacts_conversation_idx
                    ON {self._schema}.artifacts (conversation_id, created_at)
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._schema}.checkpoints (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        payload_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS checkpoints_conversation_idx
                    ON {self._schema}.checkpoints (conversation_id, created_at)
                    """
                )

    def _connect(self):
        try:
            import psycopg
        except Exception as exc:
            raise RuntimeError("psycopg[binary] is required for Postgres artifact storage") from exc

        return psycopg.connect(self._dsn)

    @staticmethod
    def _jsonb(value: dict[str, Any]):
        try:
            from psycopg.types.json import Jsonb
        except Exception as exc:
            raise RuntimeError("psycopg[binary] is required for JSONB artifact storage") from exc
        return Jsonb(value)

    def _write_content(
        self,
        artifact_id: str,
        content: str | bytes | None,
        filename: str | None,
        payload: dict[str, Any] | None,
    ) -> Path | None:
        if content is None and payload is None:
            return None

        safe_name = _safe_filename(filename or f"{artifact_id}.json")
        path = self._artifacts_dir / f"{artifact_id}-{safe_name}"
        if content is None:
            content = json.dumps(payload or {}, ensure_ascii=False, indent=2)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def _artifact_from_row(row: Any) -> ArtifactRecord:
        payload = row[5] if isinstance(row[5], dict) else {}
        return ArtifactRecord(
            id=str(row[0]),
            run_id=str(row[1]),
            conversation_id=str(row[2]),
            type=str(row[3]),
            title=str(row[4]),
            payload=payload,
            file_path=str(row[6]) if row[6] else None,
            filename=str(row[7]) if row[7] else None,
            content_type=str(row[8] or "application/json"),
            status=str(row[9] or "active"),
            version=int(row[10] or 1),
        )

    @staticmethod
    def _checkpoint_from_row(row: Any) -> CheckpointRecord:
        payload = row[4] if isinstance(row[4], dict) else {}
        return CheckpointRecord(
            id=str(row[0]),
            conversation_id=str(row[1]),
            run_id=str(row[2]),
            title=str(row[3]),
            payload=payload,
        )


def _clean_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Postgres schema name must be a simple SQL identifier")
    return value


def _safe_filename(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return clean[:160] or "artifact.json"
