"""Shared async HTTP client.

All outbound calls go through one httpx.AsyncClient with sensible timeouts so a
flaky upstream can't hang a request; callers add per-call timeout overrides for
the heavier endpoints (Yahoo chart, FRED). Retries are done at the provider level
(not here) because we want control over which failures are retryable.
"""
from __future__ import annotations

import httpx


def make_client(user_agent: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=8.0, read=15.0, write=8.0, pool=5.0),
        headers={"User-Agent": user_agent},
        follow_redirects=True,
    )
