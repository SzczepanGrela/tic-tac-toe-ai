"""Browser policy shared by the HTTP middleware and release smoke check."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


SECURITY_HEADERS = {
    "Content-Security-Policy": "; ".join((
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self'",
        "font-src 'self'",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    )),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
_ENCODED_HEADERS = [(name.lower().encode("ascii"), value.encode("ascii"))
                    for name, value in SECURITY_HEADERS.items()]
_HEADER_NAMES = {name for name, _ in _ENCODED_HEADERS}


class SecurityHeadersMiddleware:
    """Only alter response headers; forward streaming bodies/disconnects intact."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (name, value) for name, value in message.get("headers", [])
                    if name.lower() not in _HEADER_NAMES
                ] + _ENCODED_HEADERS
            await send(message)

        await self.app(scope, receive, send_with_headers)
