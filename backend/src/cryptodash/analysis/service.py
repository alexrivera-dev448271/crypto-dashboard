"""Orchestrator: turns (symbol, timeframes) into a full recommendation payload.

Pipeline per request:
1. classify symbol → 'crypto' (Binance-served) or 'macro' (Yahoo-served)
2. fetch OHLCV for each requested timeframe + daily reference series (cached)
3. run engines — classic & ICT on every TF, sentiment once per symbol, macro context
4. aggregate into a composite verdict and persist engine/TF rows (tenant-scoped,
   RLS-protected) so the UI can show history

All data errors are collected and returned in ``data_errors`` rather than
aborting — a single flaky upstream must never sink an analysis that 3 other
sources could still support.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

import pandas as pd

from cryptodash.analysis.aggregator import TFSignal, composite
from cryptodash.analysis.classic import score as classic_score
from cryptodash.analysis.ict import score as ict_score
from cryptodash.analysis.macro import score as macro_score_fn
from cryptodash.analysis.sentiment import score as sentiment_score
from cryptodash.data.providers import (
    BinanceProvider, DataError, FearGreedProvider, FredProvider, YahooProvider,
)

log = logging.getLogger("cryptodash.service")


def _now_utc() -> datetime:
    return datetime.now(UTC)


class AnalysisService:
    def __init__(self, binance: BinanceProvider, yahoo: YahooProvider, fng: FearGreedProvider,
                 fred: FredProvider, fetcher) -> None:
        self.binance = binance
        self.yahoo = yahoo
        self.fng = fng
        self.fred = fred
        self.fetcher = fetcher

    # ── public API ────────────────────────────────────────────────────────
    async def analyse(self, *, owner_id: int, symbol: str, timeframes: list[str]) -> dict:
        t0 = time.monotonic()
        symbol_u = normalize_symbol(symbol)
        errors: list[str] = []

        # 1. classify + primary data
        kind = await self._classify(symbol_u, errors)
        candles_by_tf: dict[str, pd.DataFrame] = {}
        for tf in timeframes:
            try:
                df = await self.fetcher.candles(symbol_u, tf)
                if df is not None and len(df) >= 30:
                    candles_by_tf[tf] = df
                else:
                    errors.append(f"{symbol_u} {tf}: insufficient history")
            except DataError as exc:
                errors.append(f"{symbol_u} {tf}: {exc}")

        # daily reference for macro correlation (the pair itself)
        try:
            ref_daily = await self.fetcher.candles(symbol_u, "1d", limit=400) \
                if candles_by_tf else None
        except DataError as exc:
            errors.append(f"daily reference: {exc}")

        # 2. per-timeframe engines (classic + ICT)
        tf_signals: list[TFSignal] = []
        for tf, df in candles_by_tf.items():
            classic_res = classic_score(df)
            ict_res = ict_score(df)
            net = 0.5 * classic_res.score + 0.4 * ict_res.score   # TF-level net before macro blend
            direction = "long" if net >= 8 else "short" if net <= -8 else "neutral"
            tf_signals.append(TFSignal(
                interval=tf, direction=direction, score=float(net),
                engines={"classic": classic_res.to_dict(), "ict": ict_res.to_dict()},
            ))

        # 3. sentiment (independent crowd inputs; crypto-only for non-crypto pairs)
        fng_now: float | None = None
        funding: float | None = None
        lsr: float | None = None
        taker: float | None = None
        if kind == "crypto":
            try:
                fg_df = await self.fng.index(limit=60)
                fng_now = float(fg_df["value"].iloc[-1]) if fg_df is not None and len(fg_df) else None
            except Exception as exc:  # noqa: BLE001 - optional input
                errors.append(f"fear&greed: {exc}")
            perp = _perp_symbol(symbol_u)
            funding = await self.binance.funding_rate(perp)
            lsr = await self.binance.global_long_short_ratio(perp)
            taker = await self.binance.taker_buy_sell(perp)

        sentiment_res = sentiment_score(
            fear_greed=fng_now, funding_rate_pct=funding,
            long_short_ratio=lsr, taker_buy_sell_ratio=taker,
        )

        # 4. macro context vs the pair's daily series
        btc_daily = gold_daily = brent_daily = m2 = liquidity = None
        live_gold_spot = None
        try:
            bundle = await self.fetcher.macro_bundle()
            btc_daily, gold_daily, brent_daily, m2 = (
                bundle.get("bitcoin"), bundle.get("gold"), bundle.get("brent"), bundle.get("m2"))
            liquidity = bundle.get("liquidity")
            live_gold_spot = bundle.get("_live_gold_spot")
        except Exception as exc:  # noqa: BLE001 - context, never fatal
            errors.append(f"macro bundle: {exc}")

        macro_payload = None
        try:
            res = macro_score_fn(
                ref_daily=ref_daily if (ref_daily is not None and len(ref_daily) > 60) else None,
                pair_name=symbol_u,
                bitcoin=btc_daily, gold=gold_daily, brent=brent_daily, m2=m2,
                liquidity=liquidity,
            )
            macro_payload = res.to_dict()
            if live_gold_spot is not None:   # keyless spot cross-check for the UI panel
                macro_payload["live_gold_spot"] = round(float(live_gold_spot), 2)
        except Exception as exc:  # noqa: BLE001 - context, never fatal
            errors.append(f"macro score: {exc}")

        # 5. composite verdict (TF blend + macro vote)
        final = composite(
            tf_signals,
            macro_score=None if macro_payload is None else macro_payload.get("score"),
            macro_confidence=None if macro_payload is None else macro_payload.get("confidence"),
        )
        final.update({
            "symbol": symbol_u,
            "kind": kind,
            "generated_at": _now_utc().isoformat(),
            "timeframes_requested": timeframes,
            "data_errors": errors[:10],
        })

        # 6. persist tenant-scoped recommendation history
        try:
            await self._store(owner_id, symbol_u, tf_signals, sentiment_res, macro_payload, final)
        except Exception as exc:  # noqa: BLE001 - persistence must not break the response
            log.warning("failed to store recommendations for %s: %s", symbol_u, exc)

        return {
            "composite": final,
            "timeframes": [{"interval": s.interval, **s.engines} for s in tf_signals],
            "sentiment": sentiment_res.to_dict(),
            "macro": macro_payload or {"score": 0.0, "confidence": 0.1, "assets": [],
                                       "note": "macro context unavailable"},
            "fear_greed_now": fng_now,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }

    # ── internals ───────────────────────────────────────────────────────
    async def _classify(self, symbol_u: str, errors: list[str]) -> str:
        try:
            await self.binance.ticker(symbol_u if symbol_u.endswith("USDT") else f"{symbol_u}USDT")
            return "crypto"
        except DataError as exc:
            # not on Binance → treat as macro/yahoo asset; caller will surface data errors per TF
            log.debug("binance classify failed for %s (%s); assuming macro", symbol_u, exc)
            return "macro"
        except Exception as exc:  # noqa: BLE001 - classification must never 500 the request
            errors.append(f"classify: {type(exc).__name__}: {exc}")
            return "crypto"

    async def _store(self, owner_id: int, symbol_u: str, tf_signals: list[TFSignal],
                     sentiment_res, macro_payload: dict | None, final: dict) -> None:
        from cryptodash.db import db

        rows: list[tuple] = []
        for s in tf_signals:
            for eng_name in ("classic", "ict"):
                res = s.engines[eng_name]
                rows.append((owner_id, symbol_u, s.interval, eng_name, res["direction"],
                             res["score"], res["confidence"], _json(res)))
        if macro_payload is not None:
            mp = macro_payload
            direction = "long" if mp.get("score", 0) >= 8 else "short" if mp.get("score", 0) <= -8 else "neutral"
            rows.append((owner_id, symbol_u, "daily", "macro", direction,
                         float(mp.get("score", 0.0)), float(mp.get("confidence", 0.0)), _json({
                             k: mp[k] for k in ("direction", "score", "confidence", "factors") if k in mp})))

        rows.append((owner_id, symbol_u, "composite", "composite", final["direction"],
                     final["score"], final["confidence"], _json(final)))

        async with db.tenant(owner_id) as conn:
            await db.execute_values(
                conn,
                """INSERT INTO recommendations
                   (owner_id, symbol, interval, engine, direction, score, confidence, detail)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)""",
                rows,
            )


# ── helpers ────────────────────────────────────────────────────────────────
def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper().replace(" ", "").replace("-", "")
    return s or "BTCUSDT"


def _perp_symbol(symbol_u: str) -> str:
    s = symbol_u.replace("/", "")
    if not s.endswith("USDT"):
        s += "USDT"
    return s


def _json(obj: dict) -> str:
    return json.dumps(obj, default=str)
