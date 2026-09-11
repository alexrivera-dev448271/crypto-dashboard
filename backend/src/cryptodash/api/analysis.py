"""Analysis endpoints: recommendation generation + history."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from cryptodash.analysis.service import normalize_symbol
from cryptodash.api.deps import require_user
from cryptodash.api.errors import BadRequest
from cryptodash.config import get_settings
from cryptodash.db import db
from cryptodash.security.rate_limit import rate_limiter

log = logging.getLogger("cryptodash.api.analysis")

router = APIRouter()

VALID_INTERVALS = {"5m", "1h", "4h", "1d"}
DEFAULT_INTERVALS = ["1h", "4h", "1d"]


class RecommendIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    timeframes: list[str] | None = Field(default=None, max_length=8)

    def normalized(self) -> tuple[str, list[str]]:
        sym = normalize_symbol(self.symbol or "")
        if not sym:
            raise BadRequest("symbol is required")
        tfs = [t.strip() for t in (self.timeframes or DEFAULT_INTERVALS) if t and t.strip()]
        invalid = sorted({t for t in tfs if t not in VALID_INTERVALS})
        if invalid or not tfs:
            raise BadRequest(f"timeframes must be a subset of {sorted(VALID_INTERVALS)}")
        return sym, sorted(set(tfs), key=lambda x: list(VALID_INTERVALS).index(x))


@router.post("/recommend", response_model=None)
async def recommend(request: Request, body: RecommendIn, user=Depends(require_user)):
    """Run the full multi-engine / multi-timeframe analysis for one pair.

    Heavy endpoint → strict per-user rate limit + optional FRED key override
    (user-supplied key takes precedence over the server-wide setting).
    """
    symbol_u, tfs = body.normalized()
    s = get_settings()
    user_key = f"analysis:user:{user['id']}"
    if not rate_limiter.check(user_key, s.rl_analysis_per_min):
        from cryptodash.api.errors import RateLimited

        raise RateLimited(f"rate limit of {s.rl_analysis_per_min} analyses/min exceeded")

    service = request.app.state.service

    # per-user FRED key override (stored encrypted; never returned to the client)
    fred_key: str | None = None
    try:
        async with db.tenant(user["id"]) as conn:
            row = await db.fetch_one(conn, "SELECT ciphertext FROM user_secrets WHERE kind='fred_api_key'")
        if row is not None:
            from cryptodash.security.secrets import SecretVault

            vault = SecretVault(s.master_key)
            fred_key = vault.decrypt(row["ciphertext"]) or None
    except Exception as exc:  # noqa: BLE001 - bad key should degrade, not block analysis
        log.warning("failed to decrypt user FRED key: %s", exc)

    if fred_key:
        service.fred.api_key = fred_key   # single-process: per-request override is safe
    try:
        return await service.analyse(owner_id=user["id"], symbol=symbol_u, timeframes=tfs)
    finally:
        service.fred.api_key = s.fred_api_key  # restore server-wide default


@router.get("/history")
async def history(request: Request, user=Depends(require_user),
                  symbol: str | None = Query(default=None), limit: int = Query(default=50, le=200)) -> dict:
    """Persisted recommendation rows for this user (RLS-scoped server-side)."""
    params: list[object] = [user["id"]]
    where = "owner_id=%s"
    if symbol and (sym_f := normalize_symbol(symbol)):
        where += " AND symbol=%s"
        params.append(sym_f)

    async with db.tenant(user["id"]) as conn:
        rows = await db.fetch_all(
            conn,
            f"""SELECT id, symbol, interval, engine, direction, score, confidence, created_at, detail
                FROM recommendations WHERE {where}
                ORDER BY created_at DESC LIMIT %s""",
            (*params, limit),
        )

    return {"items": rows}


@router.get("/symbols")
async def symbols() -> dict:
    """Known pair universe (spot USDT pairs + common majors)."""
    presets = {
        "majors": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"],
        "high-liquidity": ["ADAUSDT", "DOGEUSDT", "LTCUSDT", "AVAXUSDT", "DOTUSDT",
                           "LINKUSDT", "NEARUSDT", "TRXUSDT", "BCHUSDT", "ATOMUSDT"],
        "macro_refs": {"gold": "GC=F", "brent": "BZ=F", "wti": "CL=F", "m2_usd": "M2SL"},
    }
    return {"presets": presets, "intervals": sorted(VALID_INTERVALS), "defaults": DEFAULT_INTERVALS}
