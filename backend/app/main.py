from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router as api_router
from .api.ws import broadcast_loop
from .api.ws import router as ws_router
from .context import get_context

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_context()  # build the engine/DMX output eagerly on startup
    task = asyncio.create_task(broadcast_loop())
    yield
    task.cancel()
    get_context().shutdown()


app = FastAPI(title="DMX Controller", lifespan=lifespan)


@app.exception_handler(KeyError)
async def key_error_handler(request: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


app.include_router(api_router)
app.include_router(ws_router)

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
