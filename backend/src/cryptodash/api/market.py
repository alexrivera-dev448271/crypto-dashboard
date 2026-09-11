"""Market data endpoints (public read surface; auth required for consistency).

These expose the *cache* only — cheap, cached numbers for charts and panels.
Full analysis is /api/analysis/recommend.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Query, Request

from cryptodash.analysis.service import normalize_symbol
from cryptodash.api.deps import require_user
from cryptodash.api.errors import BadRequest
from cryptodash.data.providers import DataError

log = logging.getLogger("cryptodash.api.market")

router = APIRouter()

INTERVALS = {"5m", "1h", "4h", "1d"}


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Serialize OHLCV rows as plain JSON-ready dicts.

    NOTE: do NOT use itertuples() here — a non-identifier column name (e.g. a
    residual 'class' from an enriched frame) gets renamed to _1, which shifts
    every later field one position when mapped by attribute name.
    """
    if df is None or df.empty:
        return []
    out = []
    for row in df.tail(600).to_dict("records"):
        rec = {k: (None if v is None else v) for k, v in row.items() if k in _OHLCV_KEYS}
        ts = row.get("dt") or row.get("t_ms")
        if ts is not None:
            rec["ts"] = pd.Timestamp(ts).isoformat() if not isinstance(ts, str) else ts
        out.append(rec)
    return out


_OHLCV_KEYS = ("t_ms", "o", "h", "l", "c", "v")


@router.get("/candles")
async def candles(request: Request, user=Depends(require_user),
                  symbol: str = Query(min_length=1, max_length=32),
                  interval: str = Query(default="1h"), limit: int = Query(default=200, le=600)) -> dict:
    if interval not in INTERVALS:
        raise BadRequest(f"interval must be one of {sorted(INTERVALS)}")
    sym = normalize_symbol(symbol)
    if not sym:
        raise BadRequest("symbol is required")
    service = request.app.state.service
    try:
        df = await service.fetcher.candles(sym, interval, limit)
    except DataError as exc:
        from cryptodash.api.errors import UpstreamError

        raise UpstreamError(str(exc)) from exc
    return {"symbol": sym, "interval": interval, "candles": _df_to_records(df)}


@router.get("/macro")
async def macro(request: Request, user=Depends(require_user)) -> dict:
    """Macro bundle (BTC / gold / Brent / M2) — the relations panel data."""
    service = request.app.state.service
    bundle = await service.fetcher.macro_bundle()

    out: dict[str, Any] = {}
    for name in ("bitcoin", "gold", "brent", "m2"):
        df = bundle.get(name)
        if df is None or df.empty:
            out[name] = None
            continue
        values = [round(float(v), 4) for v in df["value"].tolist()]
        dts = [d.isoformat() for d in df["dt"]]
        out[name] = {
            "current": values[-1],
            "change_30d_pct": None,   # computed by macro engine; kept light here
            "history": list(zip(dts, values))[-90:],
        }
    return out
