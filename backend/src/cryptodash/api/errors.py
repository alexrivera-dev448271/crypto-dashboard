"""Consistent error handling for the /api surface.

Everything raises typed exceptions; handlers translate them into stable JSON
shapes so the frontend can branch on status codes without parsing strings.
HTML error pages for non-API paths are registered in app.py instead.
"""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("cryptodash.api")


class ApiError(Exception):
    status_code = 400
    detail = "request error"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.__class__.detail


class BadRequest(ApiError):
    status_code = 400


class Unauthorized(ApiError):
    status_code = 401


class Forbidden(ApiError):
    status_code = 403


class NotFound(ApiError):
    status_code = 404


class Conflict(ApiError):
    status_code = 409


class RateLimited(ApiError):
    status_code = 429
    detail = "rate limit exceeded — slow down"


class UpstreamError(ApiError):
    status_code = 502
    detail = "upstream data source error"


_handlers: dict[type, object] = {}


def _make_handler(exc_type: type[ApiError]):
    async def handler(request: Request, exc) -> JSONResponse:
        log.debug("api %s on %s: %s", exc.__class__.__name__, request.url.path, exc.detail)
        return JSONResponse({"detail": exc.detail}, status_code=exc_type.status_code)

    handler.__name__ = f"handle_{exc_type.__name__}"
    _handlers[exc_type] = handler


for _t in (ApiError, BadRequest, Unauthorized, Forbidden, NotFound, Conflict, RateLimited, UpstreamError):
    _make_handler(_t)


async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled exception on %s", request.url.path)
    return JSONResponse({"detail": "internal error"}, status_code=500)


error_handlers = {**_handlers}
