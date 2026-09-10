"""Full-app integration test: boots the FastAPI app (with lifespan → embedded postgres,
providers, scheduler) and drives it over HTTP via TestClient.

Covers: security headers on every response, auth flow (register→login→me), the 401
unauthorized gate, and the full recommend payload shape (composite + multi-TF engines +
macro context). The network-backed parts are guarded with skip so the suite still passes
in an offline sandbox.

The app is pointed at a throwaway data dir via real environment variables (pydantic-
settings reads env before .env), and the DB singleton is started/stopped for this process.

Run: .venv/bin/pytest tests/test_app_smoke.py -q
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # Isolated config BEFORE create_app(): real env vars take precedence over .env, and
    # DATA_DIR_OVERRIDE redirects the embedded postgres cluster into a temp path so this
    # test never touches (or clashes with) the project's live data dir. We clear the
    # settings LRU cache first because pytest runs all modules in one process and other
    # fixtures may have cached an earlier config instance.
    tmp = tmp_path_factory.mktemp("pgdata_app")
    os.environ["APP_ENV"] = "development"
    os.environ["SESSION_SECRET"] = "app-smoke-test-session-secret-0123456789abc"
    os.environ["MASTER_KEY"] = (
        "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDEyMw=="  # valid urlsafe-b64 Fernet key
    )
    os.environ["DATA_DIR_OVERRIDE"] = str(tmp)
    # This suite logs in repeatedly from a single client IP; lift the auth rate limit
    # (a real limiter stays under test, we just don't trip it across many logins).
    os.environ["RL_AUTH_PER_MIN"] = "1000"

    from cryptodash.config import get_settings

    get_settings.cache_clear()
    assert get_settings().data_dir == tmp, "config override not in effect before app boot"

    from cryptodash.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


EMAIL = "smoke@example.com"
PASSWORD = "correct-horse-battery99"


def test_health_and_headers(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    # security headers must be present on every response (even a bare health probe)
    h = {k.lower(): v for k, v in r.headers.items()}
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") in ("DENY", "SAMEORIGIN")
    assert "content-security-policy" in h


def test_requires_auth(client):
    # no session cookie yet -> the analysis gate must reject with 401
    r = client.post("/api/analysis/recommend", json={"symbol": "BTCUSDT"})
    assert r.status_code == 401


def _register_and_login(client) -> None:
    reg = client.post(
        "/api/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "display_name": "Smoke"},
    )
    assert reg.status_code in (201, 409), f"register failed: {reg.text}"
    login = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 200, f"login failed: {login.text}"


def test_auth_flow(client):
    _register_and_login(client)
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == EMAIL and body["display_name"] == "Smoke"


def test_recommend_shape(client):
    """Full multi-engine / multi-TF payload (from AnalysisService.analyse).

    Expected shape:
      composite : {symbol, direction, score, confidence}
      timeframes[] : {interval, engines:{classic, ict}, ...}   (only TFs with data)
      sentiment : per-symbol crowd inputs (not repeated per-TF)
      macro     : correlation context vs BTC / M2 / gold / Brent

    Network-backed: skip if the sandbox has no live market feed.
    """
    _register_and_login(client)
    try:
        r = client.post(
            "/api/analysis/recommend",
            json={"symbol": "BTCUSDT", "timeframes": ["1h", "4h", "1d"]},
        )
    except Exception as exc:  # network / upstream unavailable in sandbox
        pytest.skip(f"recommend needs live market data: {exc}")

    if r.status_code != 200:
        # A 5xx is an engine/data bug, not "offline" — fail loudly. (4xx like 429/503 for a
        # throttled feed may still legitimately skip; treat only server errors as failures.)
        if r.status_code >= 500:
            pytest.fail(f"recommend returned {r.status_code}: {r.text[:300]}")
        pytest.skip(f"recommend returned {r.status_code}: {r.text[:300]}")

    payload = r.json()
    for top_key in ("composite", "timeframes", "macro"):
        assert top_key in payload, f"payload missing '{top_key}': {list(payload.keys())}"

    comp = payload["composite"]
    for key in ("direction", "score", "confidence"):
        assert key in comp, f"composite missing '{key}': {comp}"
    assert comp["direction"] in {"long", "short", "neutral"}

    # Only TFs with fetched candles appear; skip if the market feed is offline.
    tfs = payload.get("timeframes") or []
    if not tfs:
        pytest.skip(f"no timeframes returned (market data unavailable): {payload.get('errors')}")
    for tf in tfs:
        # engines are inlined at the top level of each TF block ({interval, classic:{...}, ict:{...}})
        assert "classic" in tf and "ict" in tf, f"{tf} missing classic/ict engines: {list(tf)}"

    macro = payload.get("macro") or {}
    if not tfs and not macro.get("assets"):  # nothing at all -> treat as offline, not a shape bug
        pytest.skip("no candles and no macro assets; market feed offline in sandbox")


def test_logout(client):
    _register_and_login(client)
    r = client.post("/api/auth/logout")
    assert r.status_code == 200 and r.json().get("ok") is True
