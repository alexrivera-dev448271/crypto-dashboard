"""Seed synthetic market data into the embedded Postgres cache tables.

Purpose: let ``POST /api/analysis/recommend`` be exercised end-to-end while the
machine is offline (no upstream reachability). The fetcher's TTL cache serves
these rows, so every engine — classic TA, ICT, sentiment, macro, composite and
persistence — runs on its real code path; only the numbers are synthetic.

The fixture is deterministic (fixed RNG seed) for reproducible verification:
one shared "risk-market" shock drives BTC / gold / Brent so the correlation
engine produces meaningful (non-degenerate) results, M2 grows ~+7%/yr and BIS
liquidity is roughly flat.

Usage:  .venv/bin/python scripts/seed_offline_fixture.py
Idempotent: wipes BTCUSDT candles + all macro_series first. Works whether or not
the server is running (shared unix socket).
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))

import numpy as np      # noqa: E402
import psycopg         # noqa: E402

from cryptodash.config import get_settings   # noqa: E402

SEED = 20260910
BTC_END_PRICE = 118_431.0          # synthetic "now" price for the fixture


def _anchor(logrets: np.ndarray, end_price: float) -> np.ndarray:
    """Scale a log-return walk so its terminal price equals ``end_price``."""
    path = np.exp(np.cumsum(logrets))
    shift = np.log(end_price / float(path[-1])) / max(1, len(logrets))
    return np.exp(np.cumsum(logrets + shift))


def _candles(symbol: str, interval: str, n: int, end_ts: datetime, closes: np.ndarray, vol_mult: float) -> list[tuple]:
    step = {"5m": 300, "1h": 3_600, "4h": 14_400, "1d": 86_400}[interval]
    tsms = [int((end_ts - timedelta(seconds=step * (n - 1 - i))).timestamp() * 1000) for i in range(n)]
    o = closes * (1 + np.random.default_rng(SEED).normal(0, 4e-5, n))
    wick = np.abs(np.random.default_rng(SEED + 7).normal(0, 0.35, n)) * vol_mult
    h = np.maximum(o, closes) * (1 + wick)
    low = np.minimum(o, closes) * (1 - wick)
    vbase = {"5m": 90.0, "1h": 2_600.0, "4h": 10_500.0, "1d": 42_000.0}[interval]
    v = np.random.default_rng(SEED + 13).gamma(2.0, 1.0, n) * (vbase / 2.0)
    return [(symbol, interval, tms, round(float(o[i]), 8), round(float(h[i]), 8),
             round(float(low[i]), 8), round(float(closes[i]), 8), float(v[i])) for i, tms in enumerate(tsms)]


def _daily(name: str, n_days: int, end_ts: datetime, closes: np.ndarray) -> list[tuple]:
    out = []
    for i in range(n_days):
        dts = (end_ts - timedelta(days=n_days - 1 - i)).replace(hour=0, minute=0, second=0, microsecond=0)
        out.append((name, dts.isoformat(), float(closes[i])))
    return out


def _monthly(name: str, n_months: int, end_ts: datetime, yoy_pct: float) -> list[tuple]:
    """Monthly observations with average YoY growth ~``yoy_pct`` (level anchored arbitrarily)."""
    rng = np.random.default_rng(SEED + 101)
    steps = [float(yoy_pct / 100.0)] * n_months
    for i in range(n_months):
        steps[i] += float(rng.normal(0, 0.04 if (i % 3 == 2 and i < n_months - 1) else 0.0))
    d = (end_ts - timedelta(days=31 * n_months)).replace(day=28, hour=0, minute=0, second=0, microsecond=0)
    out, lvl = [], 1.0
    for i in range(n_months):
        lvl *= 1 + steps[i]
        out.append((name, d.isoformat(), float(lvl)))
        d = (d.replace(day=28) + timedelta(days=31)).replace(day=28)
    return out


def main() -> int:
    rng = np.random.default_rng(SEED)
    end_ts = datetime.now(UTC).replace(microsecond=0)

    # One shared daily "risk market" shock (drives cross-asset correlation) + BTC's own.
    common_daily = rng.normal(0.0, 0.018, 430)
    btc_idio = rng.normal(0.0, 0.006, 430)

    # ── pair candles: BTCUSDT on the four dashboard timeframes ────────────
    n_days, tf_bars = 430, {"5m": 700, "1h": 900, "4h": 800}
    btc_daily_closes = _anchor(common_daily * 0.9 + btc_idio, BTC_END_PRICE)

    def intraday(n: int, step: str) -> np.ndarray:
        """Resample the *daily* walk into `n` bars: each bar inherits its day's
        shock at reduced magnitude (plus per-bar noise), then anchors to end price."""
        step_s = {"5m": 300, "1h": 3_600, "4h": 14_400}[step]
        bars_per_day = max(1.0, 86_400 / step_s)
        day_idx = np.minimum((np.arange(n) // bars_per_day).astype(int), len(btc_daily_closes) - 2)
        scale = 1.0 / np.sqrt(bars_per_day)                      # intraday vol << daily vol
        base = (common_daily[day_idx] * 0.9 + btc_idio[day_idx]) * scale
        noise = rng.normal(0.0, 0.5e-4 if step == "1h" else 2e-4, n)
        return _anchor(base + noise, BTC_END_PRICE)

    candles: list[tuple] = []
    for tf in ("5m", "1h", "4h"):
        closes = intraday(tf_bars[tf], tf)
        vol_mult = {"5m": 0.0025, "1h": 0.008, "4h": 0.016}[tf]
        candles += _candles("BTCUSDT", tf, tf_bars[tf], end_ts, closes, vol_mult)
    candles += _candles("BTCUSDT", "1d", n_days, end_ts, btc_daily_closes, 0.02)

    # ── macro series (shared cache tables) ────────────────────────────────
    gold = _anchor(common_daily * 0.55 + rng.normal(0, 0.006, n_days), 3_521.4)
    brent = _anchor(common_daily * 0.40 + rng.normal(0, 0.011, n_days), 69.8)

    macro: list[tuple] = []
    macro += _daily("bitcoin", n_days, end_ts, btc_daily_closes)   # mirrors the pair's own daily feed
    macro += _daily("gold", n_days, end_ts, gold)
    macro += _daily("brent", n_days, end_ts, brent)
    macro += _monthly("m2", 220, end_ts, 7.4)         # M2 expanding → tailwind regime
    macro += _monthly("liquidity", 220, end_ts, 1.8)  # BIS liquidity roughly flat

    with psycopg.connect(host=str(get_settings().pgdata_dir), dbname="cryptodash", user="postgres") as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM candles WHERE symbol='BTCUSDT'")
        cur.execute("DELETE FROM macro_series")
        cur.executemany(
            "INSERT INTO candles(symbol, interval, ts_ms, o, h, l, c, v)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (symbol, interval, ts_ms) DO NOTHING", candles)
        cur.executemany(
            "INSERT INTO macro_series(series, ts, value) VALUES (%s::text, %s::timestamptz, %s)"
            " ON CONFLICT (series, ts) DO NOTHING", macro)
        conn.commit()

    print(f"seeded candles={len(candles)}  macro_rows={len(macro)}  end_ts={end_ts.isoformat()}  seed={SEED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
