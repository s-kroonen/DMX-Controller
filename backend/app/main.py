from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from .api.routes import router as api_router
from .api.sound_routes import router as sound_router
from .api.ws import broadcast_loop
from .api.ws import router as ws_router
from .context import get_context
from .paths import bundle_root

FRONTEND_DIR = bundle_root() / "frontend"


class NoCacheStaticFiles(StaticFiles):
    """Plain StaticFiles lets browsers cache the JS modules from disk
    indefinitely with no explicit Cache-Control header, which has bitten
    us before: pull a frontend change, reload, and the browser silently
    keeps serving the old file (old behavior with none of the fix) until
    a hard refresh. This is a single-venue local app, not something
    served at scale, so trading away that caching for "changes always
    take effect on a normal reload" is the right call. `no-cache` still
    lets the browser keep a copy but forces a revalidation request (via
    ETag/Last-Modified) every time -- cheap on localhost, and correct.
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


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
app.include_router(sound_router)
app.include_router(ws_router)

if FRONTEND_DIR.exists():
    app.mount("/", NoCacheStaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
