"""Static API key on every request except /health (Architecture.md §11)."""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


class ApiKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in PUBLIC_PATHS or not self._api_key:
            return await call_next(request)
        supplied = request.headers.get("x-api-key", "")
        if not hmac.compare_digest(supplied, self._api_key):
            return JSONResponse(
                {
                    "type": "about:blank",
                    "title": "Unauthorized",
                    "status": 401,
                    "detail": "missing or invalid X-API-Key",
                },
                status_code=401,
                media_type="application/problem+json",
            )
        return await call_next(request)
