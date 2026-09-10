"""In-memory sliding-window rate limiting.

Per (bucket key, limit) — O(1) amortized per check. Buckets expire after the
window so memory stays bounded for long-running processes. Single-process by
design (embedded-postgres deployment); swap this module for a Redis backend if
you ever scale horizontally without touching call sites.
"""
from __future__ import annotations

import time
from collections import deque


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = 0.0
        self.window_s = 60.0

    def check(self, key: str, limit: int, window_s: float | None = None) -> bool:
        """True if the request is allowed; False if the bucket is exhausted."""
        now = time.monotonic()
        window = window_s or self.window_s

        # opportunistic GC (every ~5s) so idle buckets don't accumulate
        if now - self._last_sweep > 5:
            cutoff = now - max(window, self.window_s) - 1.0
            stale = [k for k, d in self._hits.items() if not d or d[-1] < cutoff]
            for k in stale:
                self._hits.pop(k, None)
            self._last_sweep = now

        dq = self._hits.setdefault(key, deque())
        while dq and dq[0] <= now - window:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


rate_limiter = RateLimiter()
