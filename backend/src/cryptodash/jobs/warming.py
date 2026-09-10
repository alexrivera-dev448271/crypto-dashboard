"""Background job: keep hot pairs' cache warm so first analyses are fast."""
from __future__ import annotations

import logging
import random

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger("cryptodash.jobs.warming")

HOT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


async def warm_once(app) -> None:
    """Refresh candles for hot symbols across the default intervals (best effort)."""
    service = getattr(app.state, "service", None)
    if service is None:
        return
    for symbol in HOT_SYMBOLS:
        for interval in ("1h", "4h", "1d"):
            try:
                await service.fetcher.candles(symbol, interval, limit=250)
            except Exception as exc:  # noqa: BLE001 - warming must never raise
                log.debug("warming %s %s failed: %s", symbol, interval, exc)


def start_warming(app):
    """Start the scheduler; returns a stop callable (None if not started)."""

    async def _warm():
        try:
            await warm_once(app)
        except Exception as exc:  # noqa: BLE001
            log.warning("warming pass failed: %s", exc)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(_warm, trigger=IntervalTrigger(minutes=55), id="warm_candles", max_instances=1)
    # jitter the first run so concurrent instances (or tests) don't stampede upstreams
    scheduler.add_job(lambda: None if False else _jittered_first_warm(app), id="first_warm")
    try:
        scheduler.start()
    except Exception as exc:  # noqa: BLE001 - warming is optional
        log.warning("could not start warming scheduler: %s", exc)
        return None

    async def stop():
        with __import__("contextlib").suppress(Exception):
            if scheduler.running:
                scheduler.shutdown(wait=False)

    return stop


async def _jittered_first_warm(app):
    import asyncio

    await asyncio.sleep(random.uniform(2, 10))
    await warm_once(app)
