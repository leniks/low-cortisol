from fastapi import FastAPI

from app.api.routes.invoke import router as invoke_router


app = FastAPI(title="MathMod Agent Service", version="0.1.0")
app.include_router(invoke_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "MathMod Agent Service"}

