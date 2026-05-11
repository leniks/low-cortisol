from io import StringIO
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, AsyncGenerator
import csv
import json
import re
from uuid import uuid4

from fastapi import APIRouter, Query, UploadFile, File, HTTPException, Header
from fastapi.responses import FileResponse, StreamingResponse

from app.dependencies import get_agent_service, get_artifact_store, get_dialog_store
from app.schemas.invoke import (
    ArtifactResponse,
    CheckpointResponse,
    DialogMessage,
    DialogStateRequest,
    InvokeRequest,
    InvokeResponse,
    PersistedChatState,
)
from app.utils.sse import to_sse_data

router = APIRouter(prefix="/invoke", tags=["invoke"])


def _chat_state_scope(client_id: str | None) -> str:
    if client_id and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", client_id):
        return f"client:{client_id}"
    return "default"


def _history_payload(conversation_id: str) -> list[dict[str, str]]:
    dialog_store = get_dialog_store()
    return [{"role": item.role, "content": item.content} for item in dialog_store.get(conversation_id)]


def _sse_event(event: str, data: dict[str, object]) -> str:
    # EventSource supports named events; keep payload JSON.
    return f"event: {event}\n{to_sse_data(data)}"


_PUBLIC_TRACE_TITLES = {
    "analysis": "Анализирую",
    "planning": "Планирую",
    "retrieval": "Ищу данные",
    "sql": "Проверяю данные",
    "calculation": "Считаю",
    "finalization": "Готовлю ответ",
    "clarification": "Уточняю",
}


def _public_trace_event(
    event: dict[str, object],
    emitted: set[tuple[str, str]],
) -> dict[str, object] | None:
    if event.get("visibility") == "detail":
        return None

    phase = str(event.get("phase") or "")
    if phase not in _PUBLIC_TRACE_TITLES:
        return None

    raw_status = str(event.get("status") or "")
    if raw_status == "running":
        status = "running"
    elif raw_status == "done":
        status = "done"
    else:
        return None

    key = (phase, status)
    if key in emitted:
        return None
    if status == "done" and phase != "finalization" and (phase, "running") not in emitted:
        return None
    emitted.add(key)

    return {
        "type": event.get("type") or "tool_call",
        "title": _PUBLIC_TRACE_TITLES[phase],
        "phase": phase,
        "status": status,
        "visibility": "summary",
    }


def _persist_chat_result(
    *,
    conversation_id: str,
    message: str,
    final_answer: str,
    replace_last: bool,
) -> None:
    dialog_store = get_dialog_store()
    if final_answer:
        if replace_last:
            dialog_store.replace_last_pair(conversation_id, message, final_answer)
        else:
            dialog_store.append_pair(conversation_id, message, final_answer)
        return

    if replace_last:
        return

    dialog = dialog_store.get(conversation_id)
    dialog.append(DialogMessage(role="user", content=message))
    dialog_store.set(conversation_id, dialog)


def _dialog_snapshot_after_result(
    *,
    conversation_id: str,
    message: str,
    final_answer: str,
    replace_last: bool,
) -> list[dict[str, str]]:
    dialog_store = get_dialog_store()
    dialog = dialog_store.get(conversation_id)
    if replace_last and len(dialog) >= 2 and dialog[-2].role == "user" and dialog[-1].role == "assistant":
        dialog = [
            *dialog[:-2],
            DialogMessage(role="user", content=message),
            DialogMessage(role="assistant", content=final_answer),
        ]
    elif final_answer:
        dialog = [
            *dialog,
            DialogMessage(role="user", content=message),
            DialogMessage(role="assistant", content=final_answer),
        ]
    elif not replace_last:
        dialog = [*dialog, DialogMessage(role="user", content=message)]
    return [{"role": item.role, "content": item.content} for item in dialog]


class RunArtifactCollector:
    def __init__(self, *, conversation_id: str, message: str) -> None:
        self._store = get_artifact_store()
        self.conversation_id = conversation_id
        self.run_id = self._store.create_run(
            conversation_id=conversation_id,
            user_message=message,
        )
        self._artifact_ids: list[str] = []
        self._evidence_packs: list[dict[str, Any]] = []
        self._plan_seen = False

    def capture_event(self, event: dict[str, object]) -> None:
        if event.get("type") == "artifact_source":
            artifact_type = str(event.get("artifact_type") or "")
            title = str(event.get("title") or artifact_type or "Артефакт")
            payload = event.get("payload")
            if artifact_type == "collection_plan" and isinstance(payload, dict) and not self._plan_seen:
                self._plan_seen = True
                self._add_json_artifact(
                    artifact_type="collection_plan",
                    title=title,
                    payload=payload,
                )
            elif artifact_type == "sql_evidence" and isinstance(payload, dict):
                self._evidence_packs.append(payload)
                self._add_json_artifact(
                    artifact_type="sql_evidence",
                    title=title,
                    payload=payload,
                )
            return

        if event.get("type") != "tool_result":
            return

        tool = str(event.get("tool") or "")
        if tool in {"submit_data_acquisition_plan", "request_evidence"}:
            return

        output = _tool_output(event)
        if not isinstance(output, dict):
            return

        if tool == "submit_data_acquisition_plan" and not self._plan_seen:
            self._plan_seen = True
            self._add_json_artifact(
                artifact_type="collection_plan",
                title="План получения данных",
                payload=output,
            )
            return

        if tool == "request_evidence":
            pack = output
            self._evidence_packs.append(pack)
            self._add_json_artifact(
                artifact_type="sql_evidence",
                title="SQL evidence pack",
                payload=pack,
            )

    def finalize(self, *, final_answer: str, dialog_snapshot: list[dict[str, str]]) -> str:
        dataset_artifacts = self._create_dataset_artifacts()
        script_artifact = self._create_script_artifact(bool(dataset_artifacts))
        final_artifact = self._add_json_artifact(
            artifact_type="final_answer",
            title="Финальный ответ",
            payload={"answer": final_answer},
        )
        artifacts = [
            *dataset_artifacts,
            *([script_artifact] if script_artifact is not None else []),
            final_artifact,
        ]
        linked_answer = _append_artifact_links(
            final_answer,
            artifacts=artifacts,
        )
        checkpoint = self._store.create_checkpoint(
            conversation_id=self.conversation_id,
            run_id=self.run_id,
            title="После финального ответа",
            payload={
                "dialog": _dialog_snapshot_with_answer(dialog_snapshot, linked_answer),
                "artifact_ids": list(self._artifact_ids),
            },
        )
        self._store.complete_run(self.run_id, status="completed")
        return _append_artifact_links(
            final_answer,
            artifacts=artifacts,
            checkpoint_id=checkpoint.id,
        )

    def complete(self, status: str) -> None:
        self._store.complete_run(self.run_id, status=status)

    def _add_json_artifact(
        self,
        *,
        artifact_type: str,
        title: str,
        payload: dict[str, Any],
    ):
        artifact = self._store.create_artifact(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            artifact_type=artifact_type,
            title=title,
            payload=payload,
            filename=f"{artifact_type}-{self.run_id[:8]}.json",
            content_type="application/json",
        )
        self._artifact_ids.append(artifact.id)
        return artifact

    def _create_dataset_artifacts(self):
        selected_tables = _select_answer_tables(self._evidence_packs)
        artifacts = []
        for index, (pack, check, rows) in enumerate(selected_tables, start=1):
            normalized_rows = _normalize_rows(rows, check.get("columns"))
            if not normalized_rows:
                continue

            csv_content = _rows_to_csv(normalized_rows)
            title = _dataset_artifact_title(index, check.get("purpose"))
            metadata = {
                "table_index": index,
                "row_count": len(normalized_rows),
                "columns": list(normalized_rows[0].keys()),
                "sources": pack.get("datasets_used") or [],
                "candidate_datasets": pack.get("candidate_datasets") or [],
                "sql": check.get("sql"),
                "purpose": check.get("purpose"),
                "limitations": pack.get("limitations") or [],
                "data_verdict": pack.get("data_verdict") or pack.get("reason") or "",
            }
            artifact = self._store.create_artifact(
                run_id=self.run_id,
                conversation_id=self.conversation_id,
                artifact_type="dataset_file",
                title=title,
                payload=metadata,
                content=csv_content,
                filename=f"dataset-{self.run_id[:8]}-{index}.csv",
                content_type="text/csv; charset=utf-8",
            )
            self._artifact_ids.append(artifact.id)
            artifacts.append(artifact)

            metadata_artifact = self._store.create_artifact(
                run_id=self.run_id,
                conversation_id=self.conversation_id,
                artifact_type="dataset_metadata",
                title=f"Метаданные: {title}",
                payload=metadata,
                filename=f"dataset-metadata-{self.run_id[:8]}-{index}.json",
                content_type="application/json",
            )
            self._artifact_ids.append(metadata_artifact.id)
        return artifacts

    def _create_script_artifact(self, has_dataset: bool):
        selected = _select_answer_rows(self._evidence_packs)
        if selected is None:
            return None

        pack, check, rows = selected
        script = _build_reproducible_script(
            sql=str(check.get("sql") or ""),
            rows=_normalize_rows(rows, check.get("columns")),
            parquet_paths=_extract_parquet_paths(pack),
            has_dataset=has_dataset,
        )
        artifact = self._store.create_artifact(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            artifact_type="build_script",
            title="Скрипт сборки датасета",
            payload={
                "sql": check.get("sql") or "",
                "parquet_paths": _extract_parquet_paths(pack),
                "fallback": "embedded_rows" if not _extract_parquet_paths(pack) else "duckdb_sql",
            },
            content=script,
            filename=f"build-dataset-{self.run_id[:8]}.py",
            content_type="text/x-python; charset=utf-8",
        )
        self._artifact_ids.append(artifact.id)
        return artifact


async def _stream_chat_answer(
    *,
    message: str,
    conversation_id: str,
    replace_last: bool,
) -> AsyncGenerator[str, None]:
    agent_service = get_agent_service()
    dialog_store = get_dialog_store()
    collector = RunArtifactCollector(conversation_id=conversation_id, message=message)

    # First meta event so frontend can persist conversation_id even for new chats.
    yield _sse_event("meta", {"conversation_id": conversation_id})

    final_parts: list[str] = []
    emitted_trace_events: set[tuple[str, str]] = set()
    history = _history_payload(conversation_id)
    async for event in agent_service.run_stream(
        message,
        conversation_id=conversation_id,
        history=history,
    ):
        collector.capture_event(event)
        event_type = str(event.get("type", ""))
        if event_type == "clarification":
            collector.complete("needs_clarification")
            yield _sse_event("clarification", event)
            yield _sse_event("done", {"ok": True, "needs_clarification": True})
            return
        if event_type != "final":
            if event_type in {"thought", "tool_call", "tool_result", "iteration"}:
                public_event = _public_trace_event(event, emitted_trace_events)
                if public_event:
                    yield _sse_event("trace", public_event)
            continue
        chunk = str(event.get("text", ""))
        if not chunk:
            continue
        final_parts.append(chunk)
        yield _sse_event("delta", {"text": chunk})

    final_answer = "".join(final_parts).strip()
    if final_answer:
        raw_final_answer = final_answer
        dialog_snapshot = _dialog_snapshot_after_result(
            conversation_id=conversation_id,
            message=message,
            final_answer=final_answer,
            replace_last=replace_last,
        )
        final_answer = collector.finalize(final_answer=final_answer, dialog_snapshot=dialog_snapshot)
        artifact_delta = final_answer[len(raw_final_answer) :]
        if artifact_delta:
            yield _sse_event("delta", {"text": artifact_delta})
    else:
        collector.complete("completed_empty")
    _persist_chat_result(
        conversation_id=conversation_id,
        message=message,
        final_answer=final_answer,
        replace_last=replace_last,
    )

    yield _sse_event("done", {"ok": True})


@router.post("", response_model=InvokeResponse)
async def invoke(body: InvokeRequest) -> InvokeResponse:
    agent_service = get_agent_service()
    dialog_store = get_dialog_store()

    conversation_id = dialog_store.ensure_conversation_id(body.conversation_id)
    collector = RunArtifactCollector(conversation_id=conversation_id, message=body.message)
    final_parts: list[str] = []
    history = _history_payload(conversation_id)
    async for event in agent_service.run_stream(body.message, conversation_id=conversation_id, history=history):
        collector.capture_event(event)
        if event.get("type") == "clarification":
            collector.complete("needs_clarification")
            clarification = event.get("clarification")
            answer = ""
            if isinstance(clarification, dict):
                answer = str(clarification.get("question") or "")
            return InvokeResponse(
                conversation_id=conversation_id,
                answer=answer,
                dialog=dialog_store.get(conversation_id),
            )
        if event.get("type") == "final":
            final_parts.append(str(event.get("text", "")))

    final_answer = "".join(final_parts).strip()
    if final_answer:
        dialog_snapshot = _dialog_snapshot_after_result(
            conversation_id=conversation_id,
            message=body.message,
            final_answer=final_answer,
            replace_last=False,
        )
        final_answer = collector.finalize(final_answer=final_answer, dialog_snapshot=dialog_snapshot)
    else:
        collector.complete("completed_empty")
    _persist_chat_result(
        conversation_id=conversation_id,
        message=body.message,
        final_answer=final_answer,
        replace_last=False,
    )
    dialog = dialog_store.get(conversation_id)

    return InvokeResponse(
        conversation_id=conversation_id,
        answer=final_answer,
        dialog=dialog,
    )


@router.post("/dialog", response_model=InvokeResponse)
async def set_dialog(body: DialogStateRequest) -> InvokeResponse:
    dialog_store = get_dialog_store()
    dialog_store.set(body.conversation_id, body.dialog)
    return InvokeResponse(
        conversation_id=body.conversation_id,
        answer=body.dialog[-1].content if body.dialog else "",
        dialog=dialog_store.get(body.conversation_id),
    )


@router.get("/sessions", response_model=PersistedChatState)
async def get_chat_sessions(x_client_id: str | None = Header(None)) -> PersistedChatState:
    dialog_store = get_dialog_store()
    return PersistedChatState.model_validate(dialog_store.get_chat_state(_chat_state_scope(x_client_id)))


@router.put("/sessions", response_model=PersistedChatState)
async def set_chat_sessions(
    body: PersistedChatState,
    x_client_id: str | None = Header(None),
) -> PersistedChatState:
    dialog_store = get_dialog_store()
    state = body.model_dump(mode="json", by_alias=True)
    dialog_store.set_chat_state(state, _chat_state_scope(x_client_id))
    return PersistedChatState.model_validate(state)


@router.post("/clarify", response_model=InvokeResponse)
async def clarify(body: InvokeRequest) -> InvokeResponse:
    agent_service = get_agent_service()
    dialog_store = get_dialog_store()

    conversation_id = dialog_store.ensure_conversation_id(body.conversation_id)
    collector = RunArtifactCollector(conversation_id=conversation_id, message=body.message)
    final_parts: list[str] = []
    history = _history_payload(conversation_id)
    async for event in agent_service.run_stream(body.message, conversation_id=conversation_id, history=history):
        collector.capture_event(event)
        if event.get("type") == "clarification":
            collector.complete("needs_clarification")
            clarification = event.get("clarification")
            answer = ""
            if isinstance(clarification, dict):
                answer = str(clarification.get("question") or "")
            return InvokeResponse(
                conversation_id=conversation_id,
                answer=answer,
                dialog=dialog_store.get(conversation_id),
            )
        if event.get("type") == "final":
            final_parts.append(str(event.get("text", "")))

    final_answer = "".join(final_parts).strip()
    if final_answer:
        dialog_snapshot = _dialog_snapshot_after_result(
            conversation_id=conversation_id,
            message=body.message,
            final_answer=final_answer,
            replace_last=True,
        )
        final_answer = collector.finalize(final_answer=final_answer, dialog_snapshot=dialog_snapshot)
    else:
        collector.complete("completed_empty")
    _persist_chat_result(
        conversation_id=conversation_id,
        message=body.message,
        final_answer=final_answer,
        replace_last=True,
    )
    dialog = dialog_store.get(conversation_id)

    return InvokeResponse(
        conversation_id=conversation_id,
        answer=final_answer,
        dialog=dialog,
    )


@router.get("/stream")
async def invoke_stream(
    message: str = Query(..., min_length=2),
    conversation_id: str | None = Query(None),
) -> StreamingResponse:
    dialog_store = get_dialog_store()
    cid = dialog_store.ensure_conversation_id(conversation_id)
    return StreamingResponse(
        _stream_chat_answer(message=message, conversation_id=cid, replace_last=False),
        media_type="text/event-stream",
    )


@router.get("/clarify/stream")
async def clarify_stream(
    message: str = Query(..., min_length=2),
    conversation_id: str | None = Query(None),
) -> StreamingResponse:
    dialog_store = get_dialog_store()
    cid = dialog_store.ensure_conversation_id(conversation_id)
    return StreamingResponse(
        _stream_chat_answer(message=message, conversation_id=cid, replace_last=True),
        media_type="text/event-stream",
    )


@router.post("/upload_from_agent")
async def upload_from_agent(file: UploadFile = File(...)):
    content = await file.read()
    text_content = content.decode("utf-8", errors="ignore")
    # Здесь можно сохранить файл или обработать
    # Для примера, просто возвращаем данные
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": len(content),
        "content": text_content[:10000],  # limit
    }


@router.get("/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(conversation_id: str = Query(..., min_length=1)) -> list[ArtifactResponse]:
    store = get_artifact_store()
    return [_artifact_response(artifact) for artifact in store.list_artifacts(conversation_id)]


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str):
    store = get_artifact_store()
    artifact = store.get_artifact(artifact_id)
    if artifact is None or not artifact.file_path:
        raise HTTPException(status_code=404, detail="Artifact not found")

    path = Path(artifact.file_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file not found")

    return FileResponse(
        path,
        media_type=artifact.content_type,
        filename=artifact.filename or path.name,
    )


@router.get("/checkpoints", response_model=list[CheckpointResponse])
async def list_checkpoints(conversation_id: str = Query(..., min_length=1)) -> list[CheckpointResponse]:
    store = get_artifact_store()
    return [_checkpoint_response(checkpoint) for checkpoint in store.list_checkpoints(conversation_id)]


@router.post("/checkpoints/{checkpoint_id}/rollback", response_model=InvokeResponse)
async def rollback_to_checkpoint(
    checkpoint_id: str,
    x_client_id: str | None = Header(None),
) -> InvokeResponse:
    store = get_artifact_store()
    checkpoint = store.get_checkpoint(checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Checkpoint not found")

    raw_dialog = checkpoint.payload.get("dialog")
    if not isinstance(raw_dialog, list):
        raise HTTPException(status_code=409, detail="Checkpoint does not contain a dialog snapshot")

    dialog: list[DialogMessage] = []
    for item in raw_dialog:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            dialog.append(DialogMessage(role=role, content=content))

    dialog_store = get_dialog_store()
    dialog_store.set(checkpoint.conversation_id, dialog)
    _update_chat_state_after_rollback(
        conversation_id=checkpoint.conversation_id,
        dialog=dialog,
        scope=_chat_state_scope(x_client_id),
    )
    store.mark_superseded_after_checkpoint(
        conversation_id=checkpoint.conversation_id,
        checkpoint_id=checkpoint.id,
    )
    return InvokeResponse(
        conversation_id=checkpoint.conversation_id,
        answer=dialog[-1].content if dialog else "",
        dialog=dialog_store.get(checkpoint.conversation_id),
    )


def _update_chat_state_after_rollback(
    *,
    conversation_id: str,
    dialog: list[DialogMessage],
    scope: str = "default",
) -> None:
    dialog_store = get_dialog_store()
    state = dialog_store.get_chat_state(scope)
    sessions = state.get("sessions")
    if not isinstance(sessions, list):
        return

    now = datetime.now(timezone.utc).isoformat()
    frontend_messages = [
        {
            "id": str(uuid4()),
            "role": message.role,
            "content": message.content,
            "createdAt": now,
        }
        for message in dialog
    ]

    changed = False
    for session in sessions:
        if not isinstance(session, dict) or session.get("conversationId") != conversation_id:
            continue
        session["messages"] = frontend_messages
        session["pendingClarification"] = None
        session["checkpoints"] = []
        session["updatedAt"] = now
        changed = True

    if changed:
        dialog_store.set_chat_state(state, scope)


def _tool_output(event: dict[str, object]) -> object:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    output = payload.get("output")
    if isinstance(output, dict) and isinstance(output.get("output"), dict):
        return output["output"]
    return output


def _select_answer_rows(evidence_packs: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[Any]] | None:
    for pack in evidence_packs:
        checks = pack.get("sql_checks")
        if not isinstance(checks, list):
            continue
        for check in checks:
            if not isinstance(check, dict):
                continue
            rows = check.get("rows")
            if isinstance(rows, list) and rows:
                return pack, check, rows
    return None


def _select_answer_tables(evidence_packs: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any], list[Any]]]:
    selected: list[tuple[dict[str, Any], dict[str, Any], list[Any]]] = []
    for pack in evidence_packs:
        checks = pack.get("sql_checks")
        if not isinstance(checks, list):
            continue
        for check in checks:
            if not isinstance(check, dict):
                continue
            rows = check.get("rows")
            if isinstance(rows, list) and rows:
                selected.append((pack, check, rows))
    return selected


def _dataset_artifact_title(index: int, purpose: object) -> str:
    purpose_text = str(purpose or "").strip()
    if purpose_text:
        return f"Таблица {index}: {purpose_text}"
    return f"Таблица {index}: CSV"


def _normalize_rows(rows: list[Any], columns: object) -> list[dict[str, Any]]:
    column_names = [str(item) for item in columns] if isinstance(columns, list) else []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append({str(key): value for key, value in row.items()})
            continue
        if isinstance(row, list | tuple):
            if column_names:
                normalized.append(
                    {
                        column_names[index] if index < len(column_names) else f"column_{index + 1}": value
                        for index, value in enumerate(row)
                    }
                )
            else:
                normalized.append({f"column_{index + 1}": value for index, value in enumerate(row)})
    return normalized


def _rows_to_csv(rows: list[dict[str, Any]]) -> str:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in columns})
    return buffer.getvalue()


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False) if isinstance(value, list | dict) else str(value)


def _extract_parquet_paths(pack: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for dataset in _as_dicts(pack.get("candidate_datasets")) + _as_dicts(pack.get("datasets_used")):
        metadata = dataset.get("metadata") if isinstance(dataset.get("metadata"), dict) else {}
        for key in ("parquet_uri", "parquet_path", "data_path", "path"):
            value = dataset.get(key) or metadata.get(key)
            if isinstance(value, str) and value and value not in paths:
                paths.append(value)
    return paths


def _as_dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _build_reproducible_script(
    *,
    sql: str,
    rows: list[dict[str, Any]],
    parquet_paths: list[str],
    has_dataset: bool,
) -> str:
    return f'''#!/usr/bin/env python3
"""Reproduce a MathMod DataAgent dataset artifact.

The preferred path is DuckDB over the source parquet files. If source paths are
not available in the evidence metadata, the script falls back to embedded rows.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

OUTPUT_CSV = Path("dataset.csv")
SQL = {json.dumps(sql, ensure_ascii=False)}
PARQUET_PATHS = {json.dumps(parquet_paths, ensure_ascii=False, indent=2)}
EMBEDDED_ROWS = {json.dumps(rows, ensure_ascii=False, indent=2, default=str)}


def write_rows(rows: list[dict[str, object]]) -> None:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    if SQL and PARQUET_PATHS:
        try:
            import duckdb

            connection = duckdb.connect(database=":memory:")
            if len(PARQUET_PATHS) == 1 and "parquet_data" in SQL:
                connection.read_parquet(PARQUET_PATHS[0]).create_view("parquet_data", replace=True)
            rows = [
                dict(zip([column[0] for column in cursor.description], row, strict=False))
                for cursor in [connection.execute(SQL)]
                for row in cursor.fetchall()
            ]
            write_rows(rows)
            return
        except Exception as exc:
            print(f"DuckDB reproduction failed, using embedded rows: {{exc}}")

    write_rows(EMBEDDED_ROWS)


if __name__ == "__main__":
    main()
'''


def _dialog_snapshot_with_answer(
    dialog_snapshot: list[dict[str, str]],
    answer: str,
) -> list[dict[str, str]]:
    if dialog_snapshot and dialog_snapshot[-1].get("role") == "assistant":
        return [*dialog_snapshot[:-1], {"role": "assistant", "content": answer}]
    return [*dialog_snapshot, {"role": "assistant", "content": answer}]


def _append_artifact_links(
    final_answer: str,
    *,
    artifacts: list[Any],
    checkpoint_id: str | None = None,
    checkpoint_id_placeholder: bool = False,
) -> str:
    lines = ["", "## Артефакты"]
    for artifact in artifacts:
        lines.append(f"- [{artifact.title}](/invoke/artifacts/{artifact.id}/download)")
    if checkpoint_id:
        lines.append(f"- [Откатиться к этому состоянию](rollback://checkpoint/{checkpoint_id})")
    elif checkpoint_id_placeholder:
        lines.append("- Checkpoint для отката будет создан после сохранения ответа.")
    return f"{final_answer.rstrip()}\n" + "\n".join(lines)


def _artifact_response(artifact: Any) -> ArtifactResponse:
    return ArtifactResponse(
        id=artifact.id,
        run_id=artifact.run_id,
        conversation_id=artifact.conversation_id,
        type=artifact.type,
        title=artifact.title,
        filename=artifact.filename,
        content_type=artifact.content_type,
        status=artifact.status,
        version=artifact.version,
        download_url=f"/invoke/artifacts/{artifact.id}/download",
    )


def _checkpoint_response(checkpoint: Any) -> CheckpointResponse:
    return CheckpointResponse(
        id=checkpoint.id,
        conversation_id=checkpoint.conversation_id,
        run_id=checkpoint.run_id,
        title=checkpoint.title,
    )
