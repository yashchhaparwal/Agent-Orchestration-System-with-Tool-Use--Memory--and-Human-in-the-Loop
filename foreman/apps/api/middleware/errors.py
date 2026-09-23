"""RFC 7807 problem details for every error the API returns."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

log = structlog.get_logger(__name__)


def _problem(status: int, title: str, detail: object) -> JSONResponse:
    return JSONResponse(
        {"type": "about:blank", "title": title, "status": status, "detail": detail},
        status_code=status,
        media_type="application/problem+json",
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        return _problem(exc.status_code, "Error", exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(422, "Validation failed", exc.errors())

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("api.unhandled", path=request.url.path, error=f"{type(exc).__name__}: {exc}")
        return _problem(500, "Internal error", "unexpected error; see server logs")
