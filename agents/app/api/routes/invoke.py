from collections.abc import AsyncGenerator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.dependencies import get_main_agent_chat_service, get_request_clarifier, get_request_classifier
from app.models.request_clarifier import ClarificationResult
from app.models.request_classifier import RagRouteDecision
from app.schemas.invoke import InvokeRequest
from app.utils.sse import to_sse_data

router = APIRouter(prefix="/invoke", tags=["invoke"])


def _sse_data(data: dict[str, object]) -> str:
    return to_sse_data(data)


async def _stream_main_agent_answer(body: InvokeRequest) -> AsyncGenerator[str, None]:
    service = get_main_agent_chat_service()

    try:
        async for event in service.run_stream(
            message=body.message,
            history=body.history,
            conversation_id=body.conversation_id,
            route_decision=body.route_decision,
        ):
            yield _sse_data(event)
    except Exception as exc:
        yield _sse_data({"type": "final", "text": f"Agent service error: {exc}"})


@router.post("/stream")
async def invoke_stream_post(body: InvokeRequest) -> StreamingResponse:
    return StreamingResponse(_stream_main_agent_answer(body), media_type="text/event-stream")


@router.post("/classify", response_model=RagRouteDecision)
async def classify(body: InvokeRequest) -> RagRouteDecision:
    classifier = get_request_classifier()
    history = [item.model_dump() for item in body.history]
    return await classifier.classify(message=body.message, history=history)


@router.post("/clarify-missing", response_model=ClarificationResult)
async def clarify_missing(body: InvokeRequest) -> ClarificationResult:
    clarifier = get_request_clarifier()
    history = [item.model_dump() for item in body.history]
    return await clarifier.clarify(message=body.message, history=history)


@router.get("/stream")
async def invoke_stream_get(
    message: str = Query(..., min_length=2),
    conversation_id: str | None = Query(None),
) -> StreamingResponse:
    body = InvokeRequest(message=message, conversation_id=conversation_id)
    return StreamingResponse(_stream_main_agent_answer(body), media_type="text/event-stream")
