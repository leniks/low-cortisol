from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.routes.invoke import router as invoke_router

app = FastAPI(title="MathMod DataAgent API", version="0.1.0")
app.include_router(invoke_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(Path("static/index.html"))