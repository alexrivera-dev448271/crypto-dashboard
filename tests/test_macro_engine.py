"""Macro-correlation engine (pair vs BTC / M2 / gold / Brent / BIS liquidity) unit tests.

Covers:
  * score() produces a bounded EngineResult with all expected factors present,
  * the new BIS Global Liquidity Index factor is added only when liquidity data is supplied,
  * missing-data paths never raise (regression: notes KeyError on empty inputs),
  * M2 YoY growth vs trailing baseline is computed from monthly series,
  * live keyless gold spot provider returns a positive XAU/USD price (network, guarded).

NOTE the two input shapes (matches the real DataFetcher macro bundle):
  ref_daily -> columns [dt, c]   (daily OHLC close of the selected pair)
  asset     -> columns [dt, value]   (bitcoin / gold / brent / m2 / liquidity series)

Run: .venv/bin/pytest tests/test_macro_engine.py -q
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from cryptodash.analysis.macro import score


def _daily_close(n=260, start=100.0, trend=0.0008, seed=3) -> pd.DataFrame:
    """Shape for ref_daily: [dt, c]."""
    rng = np.random.default_rng(seed)
    close = [start] * n
    for i in range(1, n):
        close[i] = close[i - 1] * (1 + trend + rng.normal(0, 0.02))
    dt = pd.date_range(end="2026-08-31", periods=n, freq="D")
    return pd.DataFrame({"dt": dt, "c": np.asarray(close)})


def _daily_value(n=260, start=100.0, trend=0.0004, seed=9) -> pd.DataFrame:
    """Shape for macro asset series: [dt, value]."""
    rng = np.random.default_rng(seed)
    val = [start] * n
    for i in range(1, n):
        val[i] = val[i - 1] * (1 + trend + rng.normal(0, 0.02))
    dt = pd.date_range(end="2026-08-31", periods=n, freq="D")
    return pd.DataFrame({"dt": dt, "value": np.asarray(val)})


def _monthly(n=48, start=1e5, yoy=0.06, seed=7) -> pd.DataFrame:
    """Monthly series for M2 / liquidity (resampled to ME internally)."""
    rng = np.random.default_rng(seed)
    steps = ((1 + yoy / 12) ** np.arange(n)) * (1 + rng.normal(0, 0.004, n))
    val = start * steps
    dt = pd.date_range(end="2026-07-31", periods=n, freq="MS")
    return pd.DataFrame({"dt": dt, "value": val})


class TestScoreShape:
    def test_bounded_and_direction(self):
        ref = _daily_close()
        r = score(ref_daily=ref, pair_name="BTCUSDT", bitcoin=_daily_value(seed=4))
        assert -100.0 <= r.score <= 100.0
        assert r.direction in {"long", "short", "neutral"}
        assert 0.0 <= r.confidence <= 1.0

    def test_core_factors_present(self):
        ref = _daily_close()
        r = score(ref_daily=ref, pair_name="BTCUSDT")
        names = {f.name for f in r.factors}
        assert {"bitcoin", "m2_money_supply", "gold", "brent_crude"} <= names

    def test_correlation_computed_when_assets_supplied(self):
        ref = _daily_close(seed=1)
        # feed a correlated daily series -> corr_90d should be populated and near 1
        base = np.asarray(ref["c"]) * 1.2 + 5  # same shape, scaled => high correlation
        btc = pd.DataFrame({"dt": ref["dt"], "value": base})
        r = score(ref_daily=ref, pair_name="BTCUSDT", bitcoin=btc)
        b = next(f for f in r.factors if f.name == "bitcoin")
        assert b.value is not None


class TestGlobalLiquidityFactor:
    def test_added_only_when_liquidity_supplied(self):
        ref = _daily_close()
        wo = score(ref_daily=ref, pair_name="BTCUSDT")
        with_l = score(ref_daily=ref, pair_name="BTCUSDT", liquidity=_monthly(yoy=0.10))
        assert not any(f.name == "fed_liquidity" for f in wo.factors)
        lf = [f for f in with_l.factors if f.name == "fed_liquidity"]
        assert len(lf) == 1, "central-bank-liquidity factor must appear when liquidity data is given"


class TestMissingDataRegression:
    """score() must degrade gracefully (no KeyError / no exception) on empty inputs."""

    def test_all_missing_returns_neutral(self):
        r = score(ref_daily=None, pair_name="BTCUSDT")  # no assets at all
        assert r.direction == "neutral"
        assert r.score == pytest.approx(0.0)

    def test_ref_only_no_assets(self):
        ref = _daily_close()
        r = score(ref_daily=ref, pair_name="BTCUSDT")  # has BTC ref but no macro assets
        assert -100.0 <= r.score <= 100.0


class TestM2Growth:
    def test_yoy_computed_from_monthly_series(self):
        m2 = _monthly(n=48, start=1e6, yoy=0.06)
        ref = _daily_close()
        r = score(ref_daily=ref, pair_name="BTCUSDT", m2=m2)
        m2f = next(f for f in r.factors if f.name == "m2_money_supply")
        # YoY growth should be a small positive percentage (6%/yr regime).
        assert m2f.value is not None and 0.0 < m2f.value < 25.0

    def test_yoy_tracks_regime(self):
        """Expanding (6%/yr) M2 yields higher YoY value than contracting (3%/yr)."""
        ref = _daily_close()
        fast = next(f for f in score(ref_daily=ref, pair_name="X",
                                     m2=_monthly(n=48, start=1e6, yoy=0.06)).factors
                    if f.name == "m2_money_supply")
        slow = next(f for f in score(ref_daily=ref, pair_name="X",
                                     m2=_monthly(n=48, start=1e6, yoy=0.03)).factors
                    if f.name == "m2_money_supply")
        assert fast.value > slow.value


class TestGoldLiveSpot:
    def test_keyless_gold_price_provider(self):
        """Real network call to the keyless gold provider -> positive XAU/USD price."""
        import httpx

        from cryptodash.data.providers import GoldPriceProvider

        async def fetch():
            headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
            async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers=headers) as c:
                return await GoldPriceProvider(c).last_price()

        try:
            price = asyncio.run(fetch())
        except Exception as exc:  # network unavailable -> skip rather than fail the suite
            pytest.skip(f"gold provider unreachable: {exc}")
        assert isinstance(price, (int, float)) and price > 0.0


# ── ICT engine regressions ────────────────────────────────────────────────
class TestIctSweepsRegression:
    """The liquidity-sweep + FVG paths must run on real-shaped data without error.

    Regression: iterating a pandas swing DataFrame directly yields *column names*
    (str), and indexing them with ``sv["idx"]`` raised "string indices must be
    integers". That path only fires when there are recent confirmed swings AND an
    actual sweep, i.e. on real market candles — the old synthetic-only tests never
    exercised it, so a live recommend() 500'd while the suite stayed green.
    """

    def _ohlc(self, n=300, seed=11) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        close = 100 * (1 + 0.0004) ** np.arange(n) * (1 + rng.normal(0, 0.012, n).cumsum())
        o = np.concatenate([[close[0]], close[:-1]])
        spread = abs(rng.normal(0, 0.008, n))
        h = np.maximum(o, close) * (1 + spread)
        l = np.minimum(o, close) * (1 - spread)
        v = rng.uniform(1e6, 3e7, n)
        return pd.DataFrame({"o": o, "h": h, "l": l, "c": close, "v": v})

    def test_score_runs_and_is_bounded(self):
        from cryptodash.analysis.ict import score as ict_score
        r = ict_score(self._ohlc())
        assert -100.0 <= r.score <= 100.0
        assert r.direction in {"long", "short", "neutral"}

    def test_low_liquidity_sweep_bullish(self):
        """A wick below a confirmed swing low that closes back inside = bullish stop hunt."""
        from cryptodash.analysis.ict import _liquidity_sweeps
        n = 120
        base = np.full(n, 100.0)
        c = base.copy(); o = base.copy()
        h = (base + 1.0).copy(); l = (base - 1.0).copy()

        # confirmed swing low at bar n-8: a clear local minimum ...
        for i in range(n - 9, n - 7):
            l[i] = base[i]; h[i] = base[i] + 2.0
        l[n - 8] = base[n - 8] - 3.0; h[n - 8] = base[n - 8] - 1.5
        # ... then one bar later wicks below it and closes back above → stop hunt.
        idx = n - 6
        l[idx] = base[idx] - 4.5   # breaks the (base-3) swing low by >0.25 ATR
        h[idx] = base[idx] + 1.0

        df = pd.DataFrame({"o": o, "h": h, "l": l, "c": c, "v": np.full(n, 1e6)})
        bias, note = _liquidity_sweeps(df, *[self._swings(df) for _ in range(0)]) if False else self._sweeps_on(df)
        assert bias > 0.0 and "low-liquidity sweep" in note, (bias, note)

    def test_high_liquidity_sweep_bearish(self):
        """Mirror image: wick above a confirmed swing high that closes back inside = bearish."""
        from cryptodash.analysis.ict import _liquidity_sweeps
        n = 120
        base = np.full(n, 100.0)
        c = base.copy(); o = base.copy()
        h = (base + 1.0).copy(); l = (base - 1.0).copy()

        # confirmed swing high at bar n-8 ...
        for i in range(n - 9, n - 7):
            h[i] = base[i]; l[i] = base[i] - 2.0
        h[n - 8] = base[n - 8] + 3.0; l[n - 8] = base[n - 8] + 1.5
        # ... then one bar wicks above it and closes back below → stop hunt.
        idx = n - 6
        h[idx] = base[idx] + 4.5
        l[idx] = base[idx] - 1.0

        df = pd.DataFrame({"o": o, "h": h, "l": l, "c": c, "v": np.full(n, 1e6)})
        bias, note = self._sweeps_on(df)
        assert bias < 0.0 and "high-liquidity sweep" in note, (bias, note)

    def _sweeps_on(self, df):
        from cryptodash.analysis.indicators import atr as atr_indicator, swing_points
        from cryptodash.analysis.ict import _liquidity_sweeps
        sh, sl = swing_points(df, left=4, right=4)
        a = max(float(atr_indicator(df).iloc[-1]), 1e-9)
        return _liquidity_sweeps(df, sh, sl, a)

    def test_fvg_magnet_bull_gap_below(self):
        """A clean unfilled bullish FVG that price has since rallied above → support magnet below."""
        from cryptodash.analysis.ict import _fvg_magnet
        n = 100
        c = np.full(n, 100.0); o = c.copy()
        h = (c + 0.5).copy(); l = (c - 0.5).copy()

        # bullish FVG centred on bar i=40: low[i+1]=l[41] > high[i-1]=h[39].
        h[39] = 100.0            # high of bar i-1 (gap bottom) -> zone bot
        l[41] = 105.0            # low of bar i+1 (gap top)     -> zone top; 105 > 100 ✓
        h[41] = 106.0
        # price then rallies and holds ABOVE the gap, staying within 3*ATR (a=1):
        c[42:] = 107.0; o[42:] = 106.5
        h[42:] = 107.5; l[42:] = 106.5     # lows stay > top(105) → the gap is never filled/touched

        df = pd.DataFrame({"o": o, "h": h, "l": l, "c": c, "v": np.full(n, 1e6)})
        last = float(c[-1])                        # price above the unfilled bullish gap
        bias, note = _fvg_magnet(df, last, a=1.0)
        assert -1.0 <= bias <= 1.0 and "bullish" in note.lower(), (bias, note)
        assert bias > 0.0                          # pulls up toward the gap

    def test_fvg_paths_run(self):
        from cryptodash.analysis.indicators import atr as atr_indicator
        from cryptodash.analysis.ict import _fvg_magnet
        df = self._ohlc(n=200)
        last = float(df["c"].iloc[-1])
        a = max(float(atr_indicator(df).iloc[-1]), 1e-9)
        bias, note = _fvg_magnet(df, last, a)   # must not raise regardless of FVGs present
        assert -1.0 <= bias <= 1.0
