"""WebSocket endpoint broadcasting live engine state to every connected
client at a fixed rate, so the RGB/strobe/pan-tilt overlay panels and the
3D scene all stay in sync -- including when an animation is running and
changing values with no direct user action to trigger a REST response.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..context import get_context

router = APIRouter()

_clients: set[WebSocket] = set()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    try:
        while True:
            # Clients don't need to send anything for the broadcast loop to
            # work; recv() just lets us detect disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)


async def broadcast_loop(hz: float = 10.0) -> None:
    period = 1.0 / hz
    while True:
        await asyncio.sleep(period)
        if not _clients:
            continue
        ctx = get_context()
        payload = json.dumps(ctx.snapshot())
        dead = []
        for client in list(_clients):
            try:
                await client.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(client)
        for client in dead:
            _clients.discard(client)
