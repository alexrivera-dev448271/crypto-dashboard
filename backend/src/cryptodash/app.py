"""FastAPI application factory.

Wires: lifespan (embedded postgres + providers + scheduler) → security
middleware (headers / HTTPS redirect) → exception handlers (clean JSON errors,
styled 404/500 pages for the UI shell) → API routers → static frontend mount.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from cryptodash.analysis.service import AnalysisService
from cryptodash.config import get_settings
from cryptodash.data.fetcher import DataFetcher
from cryptodash.data.http_client import make_client
from cryptodash.data.providers import (
    BinanceProvider, FearGreedProvider, FredProvider, GoldPriceProvider, YahooProvider,
)
from cryptodash.db import db
from cryptodash.security.auth import SessionService

log = logging.getLogger("cryptodash.app")


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ── database (embedded postgres, schema bootstrap) ───────────────
        db.start(settings.pgdata_dir)
        app.state.db = db

        http_client = make_client(settings.yahoo_user_agent)
        app.state.http = http_client

        binance = BinanceProvider(http_client, settings.binance_base)
        yahoo = YahooProvider(http_client)
        fng = FearGreedProvider(http_client)
        fred = FredProvider(http_client, settings.fred_api_key)
        gold_price = GoldPriceProvider(http_client)   # keyless live XAU/USD cross-check
        fetcher = DataFetcher(binance, yahoo, fred, gold_price=gold_price)
        app.state.service = AnalysisService(binance, yahoo, fng, fred, fetcher)
        app.state.session = SessionService(settings.session_secret)

        # ── background candle warming (keeps cache hot; best effort) ─────
        from cryptodash.jobs.warming import start_warming

        stop_warming = start_warming(app)   # sync factory: returns a stop callable or None

        log.info("CryptoDash ready (%s mode)", settings.app_env)
        try:
            yield
        finally:
            if stop_warming is not None:
                with suppress(Exception):  # async factory; lifespan teardown runs inside the loop
                    await stop_warming()
            with suppress(Exception):  # async client needs an await; lifespan shutdown runs inside the loop
                await http_client.aclose()
            db.stop()
            log.info("shutdown complete")

    app = FastAPI(title="CryptoDash", version="0.1.0", lifespan=lifespan)

    # ── security middleware (order: outermost first) ──────────────────────
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        h[
            "Content-Security-Policy"
        ] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if settings.is_production and settings.force_https:
            h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    @app.middleware("http")
    async def https_redirect(request: Request, call_next):
        # When serving behind a TLS terminator, refuse plain-HTTP API access.
        if settings.is_production and settings.force_https and request.url.scheme != "https":
            url = request.url.replace(scheme="https")
            return JSONResponse({"detail": "HTTPS required", "redirect": str(url)}, status_code=426)
        return await call_next(request)

    # ── exception handlers: clean, consistent errors ──────────────────────
    from cryptodash.api.errors import error_handlers

    for exc_type, handler in error_handlers.items():
        app.add_exception_handler(exc_type, handler)

    # ── API routers (before the static mount so /api always wins) ────────
    from cryptodash.api.analysis import router as analysis_router
    from cryptodash.api.auth import router as auth_router
    from cryptodash.api.market import router as market_router
    from cryptodash.api.settings_api import router as settings_router

    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    app.include_router(analysis_router, prefix="/api/analysis", tags=["analysis"])
    app.include_router(market_router, prefix="/api/market", tags=["market"])
    app.include_router(settings_router, prefix="/api/settings", tags=["settings"])

    @app.get("/api/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    # ── static frontend (built SPA) + branded error pages ─────────────────
    dist = settings.frontend_dist
    if dist is not None:
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")
    else:

        @app.get("/{full_path:path}", include_in_schema=False)
        async def no_spa(full_path: str):
            return _missing_frontend_response()

    # branded 404 for unknown /api routes (FastAPI default would be JSON)
    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        return HTMLResponse(_ERROR_PAGE, status_code=404)

    @app.exception_handler(500)
    async def server_error(request: Request, exc):
        log.exception("unhandled 500 on %s", request.url.path)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "internal error"}, status_code=500)
        return HTMLResponse(_ERROR_PAGE, status_code=500)

    return app


def _missing_frontend_response():
    return HTMLResponse(
        """<!doctype html><meta charset="utf-8"><title>CryptoDash</title>
        <body style="font-family:system-ui;background:#0b0e14;color:#dfe3ee;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="max-width:26rem;text-align:center"><h2>CryptoDash backend is running</h2>
        <p>The frontend build is missing. Run <code>npm ci && npm run build</code> in
        <b>frontend/</b> (or use the dev server) to serve the UI.</p></div></body>""",
    )


_ERROR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>CryptoDash — not found</title></head>
<body style="font-family:system-ui;background:#0b0e14;color:#dfe3ee;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="max-width:28rem;text-align:center"><h1 style="margin-bottom:.25em">CryptoDash</h1>
<h2 style="color:#f6c453;font-weight:500">Page not found</h2>
<p>The page you requested doesn't exist. <a href="/" style="color:#7aa2ff">Go to the dashboard</a>.</p></div>
</body></html>"""


# re-export for tests
session_service_factory = SessionService
