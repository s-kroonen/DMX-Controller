"""A small access PIN for LAN clients.

The server listens on every interface (the packaged app always does), so anyone on the network can
otherwise run the lights. When a PIN is set, requests to /api and /ws from another machine need a
cookie obtained by POST /api/auth/login. Requests from this machine itself (the desktop window,
localhost) are never asked: the PIN guards the network, it isn't a user system. Static files stay
open so the login prompt can load. No PIN set = no check, exactly as before.

The PIN lives in settings.json as a salted hash; the cookie value is derived from that hash, so
changing the PIN logs every other device out and a restart keeps everyone logged in.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

COOKIE = "dmx_auth"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
MAX_FAILURES = 5
LOCKOUT_S = 30.0

_failures: dict[str, list[float]] = {}   # client host -> times of recent bad tries


def valid_pin_format(pin: str) -> bool:
    return pin.isdigit() and 4 <= len(pin) <= 8


def hash_pin(pin: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    return f"{salt}${hashlib.sha256((salt + pin).encode()).hexdigest()}"


def pin_matches(pin: str, stored: str) -> bool:
    salt, _, _ = stored.partition("$")
    return hmac.compare_digest(hash_pin(pin, salt), stored)


def token_for(stored: str) -> str:
    return hashlib.sha256(("dmx-auth|" + stored).encode()).hexdigest()


def is_loopback(host: str | None) -> bool:
    return host in LOOPBACK


def is_authorized(stored: str | None, host: str | None, cookie: str | None) -> bool:
    if not stored or is_loopback(host):
        return True
    return bool(cookie) and hmac.compare_digest(cookie, token_for(stored))


def locked_out(host: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    recent = [t for t in _failures.get(host, []) if now - t < LOCKOUT_S]
    _failures[host] = recent
    return len(recent) >= MAX_FAILURES


def record_failure(host: str, now: float | None = None) -> None:
    _failures.setdefault(host, []).append(time.monotonic() if now is None else now)


def clear_failures(host: str) -> None:
    _failures.pop(host, None)


class PinMiddleware:
    """Pure ASGI, so it covers the websocket as well as plain requests."""

    def __init__(self, app, get_stored_pin):
        self.app = app
        self.get_stored_pin = get_stored_pin

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        guarded = (path == "/ws" or path.startswith("/api")) and not path.startswith("/api/auth/")
        if not guarded:
            return await self.app(scope, receive, send)

        host = (scope.get("client") or (None, 0))[0]
        cookie = None
        for name, value in scope.get("headers", []):
            if name == b"cookie":
                for part in value.decode("latin-1").split(";"):
                    key, _, val = part.strip().partition("=")
                    if key == COOKIE:
                        cookie = val
        if is_authorized(self.get_stored_pin(), host, cookie):
            return await self.app(scope, receive, send)

        if scope["type"] == "websocket":
            await receive()                       # the connect message
            return await send({"type": "websocket.close", "code": 4401})
        body = b'{"detail":"pin required"}'
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
