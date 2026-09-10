"""FastAPI dependencies: session resolution, auth requirement, rate limiting."""
from __future__ import annotations

import logging

from fastapi import Request

from cryptodash.config import get_settings
from cryptodash.db import db
from cryptodash.security.auth import SessionService
from cryptodash.security.rate_limit import rate_limiter

log = logging.getLogger("cryptodash.api.deps")

SESSION_COOKIE = "cd_session"


def client_ip(request: Request) -> str:
    """Best-effort caller identity for per-IP rate limit buckets."""
    if get_settings().is_production and settings_has_proxy():
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def settings_has_proxy() -> bool:
    # production deployments behind a TLS terminator are expected to set X-Forwarded-For
    return get_settings().is_production


def _bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


async def get_current_user(request: Request, user_id: int | None = None) -> dict:
    """Resolve session (cookie or Bearer) → active user row dict; {} when absent."""
    service: SessionService = request.app.state.session
    token = request.cookies.get(SESSION_COOKIE) or _bearer_token(request)

    uid = user_id if user_id is not None else (service.resolve(token) if token else None)
    if not uid:
        return {}

    async with db.shared() as conn:
        row = await db.fetch_one(conn, "SELECT id, email, display_name FROM users WHERE id=%s AND is_active", (uid,))
    user = row or {}
    if user:
        request.state.user_id = uid
    return user


async def require_user(request: Request) -> dict:
    user = await get_current_user(request)
    from cryptodash.api.errors import Unauthorized

    if not user:
        raise Unauthorized("authentication required")
    request.state.user = user
    return user


def rate_limited(limit: int | None = None, window_s: float | None = None):
    """Dependency factory: per-IP sliding-window limiter (key derived at call)."""

    def _dep(request: Request) -> None:
        s = get_settings()
        key = f"api:{client_ip(request)}"
        if not rate_limiter.check(key, limit or s.rl_api_per_min, window_s=window_s):
            from cryptodash.api.errors import RateLimited

            raise RateLimited(f"rate limit of {limit or s.rl_api_per_min} requests/min exceeded")

    return _dep


def auth_attempts_limited():
    """Stricter limiter for login/register (per user+IP)."""

    def _dep(request: Request) -> None:
        s = get_settings()
        key = f"auth:{client_ip(request)}"
        if not rate_limiter.check(key, s.rl_auth_per_min):
            from cryptodash.api.errors import RateLimited

            raise RateLimited(f"too many auth attempts — retry in a minute")

    return _dep
