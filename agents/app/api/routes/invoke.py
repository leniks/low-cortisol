from collections.abc import AsyncGenerator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.dependencies import get_main_agent_chat_service
from app.schemas.invoke import InvokeRequest
from app.utils.sse import to_sse_data

router = APIRouter(prefix="/invoke", tags=["invoke"])


def _sse_data(data: dict[str, object]) -> str:
    return to_sse_data(data)


async def _stream_main_agent_answer(body: InvokeRequest) -> AsyncGenerator[str, None]:
    service = get_main_agent_chat_service()

    try:
        async for chunk in service.run_stream(
            message=body.message,
            history=body.history,
            conversation_id=body.conversation_id,
        ):
            yield _sse_data({"type": "final", "text": chunk})
    except Exception as exc:
        yield _sse_data({"type": "final", "text": f"Agent service error: {exc}"})


@router.post("/stream")
async def invoke_stream_post(body: InvokeRequest) -> StreamingResponse:
    return StreamingResponse(_stream_main_agent_answer(body), media_type="text/event-stream")


@router.get("/stream")
async def invoke_stream_get(
    message: str = Query(..., min_length=2),
    conversation_id: str | None = Query(None),
) -> StreamingResponse:
    body = InvokeRequest(message=message, conversation_id=conversation_id)
    return StreamingResponse(_stream_main_agent_answer(body), media_type="text/event-stream")

