from typing import AsyncGenerator

from fastapi import APIRouter, Query, UploadFile, File
from fastapi.responses import StreamingResponse

from app.dependencies import get_agent_service, get_dialog_store
from app.schemas.invoke import InvokeRequest, InvokeResponse
from app.utils.sse import to_sse_data

router = APIRouter(prefix="/invoke", tags=["invoke"])


def _history_payload(conversation_id: str) -> list[dict[str, str]]:
    dialog_store = get_dialog_store()
    return [{"role": item.role, "content": item.content} for item in dialog_store.get(conversation_id)]


def _sse_event(event: str, data: dict[str, object]) -> str:
    # EventSource supports named events; keep payload JSON.
    return f"event: {event}\n{to_sse_data(data)}"


async def _stream_chat_answer(
    *,
    message: str,
    conversation_id: str,
    replace_last: bool,
) -> AsyncGenerator[str, None]:
    agent_service = get_agent_service()
    dialog_store = get_dialog_store()

    # First meta event so frontend can persist conversation_id even for new chats.
    yield _sse_event("meta", {"conversation_id": conversation_id})

    final_parts: list[str] = []
    history = _history_payload(conversation_id)
    async for event in agent_service.run_stream(message, conversation_id=conversation_id, history=history):
        if event.get("type") != "final":
            continue
        chunk = str(event.get("text", ""))
        if not chunk:
            continue
        final_parts.append(chunk)
        yield _sse_event("delta", {"text": chunk})

    final_answer = "".join(final_parts).strip()
    if replace_last:
        dialog_store.replace_last_pair(conversation_id, message, final_answer)
    else:
        dialog_store.append_pair(conversation_id, message, final_answer)

    yield _sse_event("done", {"ok": True})


@router.post("", response_model=InvokeResponse)
async def invoke(body: InvokeRequest) -> InvokeResponse:
    agent_service = get_agent_service()
    dialog_store = get_dialog_store()

    conversation_id = dialog_store.ensure_conversation_id(body.conversation_id)
    final_parts: list[str] = []
    history = _history_payload(conversation_id)
    async for event in agent_service.run_stream(body.message, conversation_id=conversation_id, history=history):
        if event.get("type") == "final":
            final_parts.append(str(event.get("text", "")))

    final_answer = "".join(final_parts).strip()
    dialog = dialog_store.append_pair(conversation_id, body.message, final_answer)

    return InvokeResponse(
        conversation_id=conversation_id,
        answer=final_answer,
        dialog=dialog,
    )


@router.post("/clarify", response_model=InvokeResponse)
async def clarify(body: InvokeRequest) -> InvokeResponse:
    agent_service = get_agent_service()
    dialog_store = get_dialog_store()

    conversation_id = dialog_store.ensure_conversation_id(body.conversation_id)
    final_parts: list[str] = []
    history = _history_payload(conversation_id)
    async for event in agent_service.run_stream(body.message, conversation_id=conversation_id, history=history):
        if event.get("type") == "final":
            final_parts.append(str(event.get("text", "")))

    final_answer = "".join(final_parts).strip()
    dialog = dialog_store.replace_last_pair(conversation_id, body.message, final_answer)

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
