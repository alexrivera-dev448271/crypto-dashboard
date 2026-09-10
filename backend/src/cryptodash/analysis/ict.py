"""ICT / smart-money-concept engine.

Detects the classic institutional footprints on OHLCV:

* **Market structure** – sequence of confirmed swing highs/lows (HHL vs LHL).
* **Order blocks**     – last opposing candle before a strong impulse that broke
                         structure; price returning into them = high-probability
                         reaction zone.
* **Fair value gaps**  – three-candle imbalances left behind by fast moves; the
                         nearest unfilled one acts as a magnet (continuation bias).
* **Liquidity sweeps** – wicks that take out a prior swing and close back inside:
                         stop-hunt, often marks an exhaustion / reversal.
* **Premium/discount** – price position within the dealing range; buys are taken
                         in discount, sells in premium (relative to structure).

Every factor is transparent (value + bias + note) so the UI can explain signals.
Swing points only become *confirmed* after `right` bars — we report that lag as a
confidence haircut rather than pretending recent pivots are certain.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from cryptodash.analysis.classic import EngineResult, Factor, _clamp
from cryptodash.analysis.indicators import atr as atr_indicator, swing_points

log = logging.getLogger("cryptodash.analysis.ict")


def score(df: pd.DataFrame) -> EngineResult:
    factors: list[Factor] = []
    if df is None or len(df) < 80:
        return EngineResult("ict", "neutral", 0.0, 0.1, [Factor("coverage", 0, 0.0, "not enough bars for ICT structure")])

    c = df["c"]
    last = float(c.iloc[-1])
    atr_s = atr_indicator(df)
    a = max(float(atr_s.iloc[-1]), 1e-9)

    swings_h, swings_l = swing_points(df, left=4, right=4)
    recent_confirmed = len(swings_h) + len(swings_l) >= 4   # some lag: latest pivots are unconfirmed
    if not recent_confirmed:
        factors.append(Factor("structure", None, 0.0, "too few confirmed swings yet"))

    structure_bias = _market_structure(df, swings_h, swings_l)
    ob_long, ob_short = _order_block_proximity(df, a)
    fvg_bias, fvg_note = _fvg_magnet(df, last, a)
    sweep_bias, sweep_note = _liquidity_sweeps(df, swings_h, swings_l, a)
    pd_pos, pd_note = _premium_discount(df, swings_h, swings_l, structure_bias)

    if recent_confirmed:
        factors.append(Factor(
            "market_structure", round(structure_bias, 2), structure_bias,
            f"{'higher highs/lows (bullish)' if structure_bias > 0.15 else 'lower highs/lows (bearish)' if structure_bias < -0.15 else 'mixed/choppy'}; {len(swings_h)} swing highs, {len(swings_l)} swing lows",
        ))

    factors.append(Factor("order_block_bull", ob_long["value"], ob_long["bias"], ob_long["note"]))
    factors.append(Factor("order_block_bear", ob_short["value"], ob_short["bias"], ob_short["note"]))
    if fvg_note:
        factors.append(Factor("fvg_magnet", round(fvg_bias, 2), fvg_bias, fvg_note))
    if sweep_note:
        factors.append(Factor("liquidity_sweep", round(sweep_bias, 2), sweep_bias, sweep_note))

    # aggregate with structure as the anchor (ICT is context-first)
    raw = (0.40 * structure_bias
           + 0.18 * ob_long["bias"] + 0.18 * ob_short["bias"]
           + 0.12 * fvg_bias
           + 0.12 * sweep_bias)
    # premium/discount acts as a filter/modifier, not an independent vote:
    modifier = _clamp(1.0 - 0.25 * abs(pd_pos) if pd_note else 1.0)
    score_val = float(_clamp(raw, -1, 1)) * 100.0 * modifier

    direction = "neutral"
    if score_val >= 8:
        direction = "long"
    elif score_val <= -8:
        direction = "short"

    coverage = min(1.0, len(df) / 250.0) * (0.75 if not recent_confirmed else 1.0)
    confidence = float(_clamp(0.35 * coverage + 0.65 * min(1.0, abs(score_val) / 50.0)))

    return EngineResult("ict", direction, score_val, confidence, factors)


# ── individual concept detectors (each returns transparent values) ─────────
def _market_structure(df: pd.DataFrame, swings_h: pd.DataFrame, swings_l: pd.DataFrame) -> float:
    """+1 fully bullish sequence … −1 fully bearish; 0 = chop/insufficient."""
    if len(swings_h) < 2 or len(swings_l) < 2:
        return 0.0
    last_5h = swings_h.tail(5)["price"].tolist()
    last_5l = swings_l.tail(5)["price"].tolist()
    hh = sum(b > a for a, b in zip(last_5h[-4:], last_5h[-3:])) if len(last_5h) >= 2 else 0
    lh = sum(b < a for a, b in zip(last_5h[-4:], last_5h[-3:])) if len(last_5h) >= 2 else 0
    hl = sum(b > a for a, b in zip(last_5l[-4:], last_5l[-3:])) if len(last_5l) >= 2 else 0
    ll = sum(b < a for a, b in zip(last_5l[-4:], last_5l[-3:])) if len(last_5l) >= 2 else 0
    total = max(1, hh + lh + hl + ll)
    return (hh + hl - lh - ll) / total * 2.0


def _order_block_proximity(df: pd.DataFrame, a: float):
    """Find the most recent bullish & bearish order blocks and how close price is."""

    def find(direction: int) -> dict | None:  # direction +1 = bullish OB (buy zone below), −1 = bearish OB (sell zone above)
        o, ccl = df["o"].to_numpy(), df["c"].to_numpy()
        h_arr, l_arr = df["h"].to_numpy(), df["l"].to_numpy()
        n = len(df)
        for i in range(n - 2, max(0, n - 160), -1):
            body_dir = np.sign(ccl[i] - o[i])
            if direction > 0 and body_dir < 0:          # bearish candle that then impulsed up
                broke_up = bool((ccl[i + 1 : min(i + 4, n)] > h_arr[i]).any())
                if broke_up:
                    # demand zone spans the last bearish candle (open is its ceiling)
                    return {"top": float(o[i]), "bottom": float(l_arr[i]), "idx": i}
            elif direction < 0 and body_dir > 0:        # bullish candle that then impulsed down
                broke_down = bool((ccl[i + 1 : min(i + 4, n)] < l_arr[i]).any())
                if broke_down:
                    # supply zone spans the last bullish candle (open is its floor)
                    return {"top": float(h_arr[i]), "bottom": float(o[i]), "idx": i}
        return None

    last = df["c"].iloc[-1]
    bull, bear = find(+1), find(-1)
    res_bull, res_bear = {"value": None, "bias": 0.0, "note": ""}, {"value": None, "bias": 0.0, "note": ""}

    if bull and bull["top"] < last:  # price above the zone (already used) → only matters on pullback
        dist = (last - bull["top"]) / a
        if dist <= 1.5:
            res_bull = {"value": round(bull["top"], 6), "bias": _clamp(0.9 - dist * 0.4),
                        "note": f"price within {dist:.1f} ATR of bullish order block [{bull['bottom']:.2f}–{bull['top']:.2f}]"}
        else:
            res_bull["note"] = "no active bullish OB nearby"
    elif bull and last <= bull["top"]:  # inside / below the zone right now
        res_bull = {"value": round(bull["bottom"], 6), "bias": 0.7,
                    "note": f"price INSIDE bullish order block [{bull['bottom']:.2f}–{bull['top']:.2f}] — demand zone active"}

    if bear and bear["bottom"] > last:
        dist = (bear["bottom"] - last) / a
        if dist <= 1.5:
            res_bear = {"value": round(bear["bottom"], 6), "bias": _clamp(-(0.9 - dist * 0.4)),
                        "note": f"price within {dist:.1f} ATR of bearish order block [{bear['bottom']:.2f}–{bear['top']:.2f}]"}
        else:
            res_bear["note"] = "no active bearish OB nearby"
    elif bear and last >= bear["bottom"]:
        res_bear = {"value": round(bear["top"], 6), "bias": -0.7,
                    "note": f"price INSIDE bearish order block [{bear['bottom']:.2f}–{bear['top']:.2f}] — supply zone active"}

    return res_bull, res_bear


def _fvg_magnet(df: pd.DataFrame, last: float, a: float) -> tuple[float, str]:
    """Nearest unfilled FVG within 3 ATR of price → continuation bias in the gap's direction."""
    h_arr, l_arr = df["h"].to_numpy(), df["l"].to_numpy()
    n = len(df)
    lookback = min(n - 2, 150)

    bull_fvgs: list[tuple[int, float, float]] = []  # (idx, zone_top, zone_bot), gap up
    bear_fvgs: list[tuple[int, float, float]] = []  # (idx, zone_top, zone_bot), gap down
    for i in range(n - lookback, n - 2):
        if l_arr[i + 1] > h_arr[i - 1]:            # bullish FVG centred on bar i
            bull_fvgs.append((i, float(l_arr[i + 1]), float(h_arr[i - 1])))
        elif h_arr[i + 1] < l_arr[i - 1]:          # bearish FVG
            bear_fvgs.append((i, float(l_arr[i - 1]), float(h_arr[i + 1])))

    def unfilled_below(zones: list[tuple[int, float, float]]) -> tuple[float, float] | None:
        for idx, top, bot in reversed(zones):
            if top >= last:
                continue                            # zone is at/above price — not a "below" magnet
            touched = bool((l_arr[idx + 2 :] <= top).any())   # later wick already into the gap
            if not touched and last - top <= 3 * a:
                return (top, bot)
        return None

    def unfilled_above(zones: list[tuple[int, float, float]]) -> tuple[float, float] | None:
        for idx, top, bot in reversed(zones):
            if bot <= last:
                continue                            # zone is at/below price — not an "above" magnet
            touched = bool((h_arr[idx + 2 :] >= bot).any())
            if not touched and bot - last <= 3 * a:
                return (top, bot)
        return None

    ub, ua = unfilled_below(bull_fvgs), unfilled_above(bear_fvgs)
    if ub and ua:
        d_bull, d_bear = last - ub[0], ua[1] - last   # distances to the nearer edge of each zone
        # closer magnet dominates: bear-zone-above wins → price tends up; bull-gap-below wins → down.
        bias = float(_clamp(0.6 * (2.0 * d_bull / max(d_bull + d_bear, 1e-9) - 1.0), -1, 1))
        return bias, f"contending FVGs: bullish [{ub[1]:.2f}–{ub[0]:.2f}] below vs bearish [{ua[1]:.2f}–{ua[0]:.2f}] above — net {'bull' if bias > 0 else 'bear'} pull"
    if ub:
        return float(_clamp(0.8 * (1 - (last - ub[0]) / (3 * a)))), f"unfilled bullish FVG [{ub[1]:.2f}–{ub[0]:.2f}] below — magnet for continuation up"
    if ua:
        return float(-_clamp(0.8 * (1 - (ua[1] - last) / (3 * a)))), f"unfilled bearish FVG [{ua[1]:.2f}–{ua[0]:.2f}] above — magnet for continuation down"
    return 0.0, ""


def _liquidity_sweeps(df: pd.DataFrame, swings_h: pd.DataFrame, swings_l: pd.DataFrame, a: float) -> tuple[float, str]:
    """Wick that breaks a confirmed swing and closes back inside → stop hunt."""
    n = len(df)
    c_arr, h_arr, l_arr = df["c"].to_numpy(), df["h"].to_numpy(), df["l"].to_numpy()

    def recent_swings(s: pd.DataFrame, max_age: int = 40) -> pd.Series:
        cutoff = n - max_age
        return s[s["idx"] >= cutoff]

    bias = 0.0
    notes: list[str] = []
    for sv in recent_swings(swings_l).to_dict("records"):
        idx, level = int(sv["idx"]), float(sv["price"])
        later = slice(idx + 1, n)
        wick_below = l_arr[later].min() < level - 0.25 * a if (l_arr[later] < level).any() else False
        close_back = bool((c_arr[later] > level).any())
        if wick_below and close_back:
            bias += 1.0
            notes.append(f"low-liquidity sweep at {level:.2f} (stop hunt → bullish)")
    for sv in recent_swings(swings_h).to_dict("records"):
        idx, level = int(sv["idx"]), float(sv["price"])
        later = slice(idx + 1, n)
        wick_above = h_arr[later].max() > level + 0.25 * a if (h_arr[later] > level).any() else False
        close_back = bool((c_arr[later] < level).any())
        if wick_above and close_back:
            bias -= 1.0
            notes.append(f"high-liquidity sweep at {level:.2f} (stop hunt → bearish)")

    return float(_clamp(bias, -1, 1)), "; ".join(notes)


def _premium_discount(df: pd.DataFrame, swings_h: pd.DataFrame, swings_l: pd.DataFrame, structure_bias: float):
    """Position of price within the recent dealing range (0=bottom, 1=top)."""
    n = len(df)
    window = min(120, n - 5)
    rng_hi = max(float(df["h"].tail(window).max()),
                 float(swings_h["price"].max()) if not swings_h.empty else df["h"].tail(window).max())
    rng_lo = min(float(df["l"].tail(window).min()),
                 float(swings_l["price"].min()) if not swings_l.empty else df["l"].tail(window).min())
    span = rng_hi - rng_lo
    if span <= 1e-9:
        return 0.5, ""
    last = float(df["c"].iloc[-1])
    pos = (last - rng_lo) / span
    note = f"price at {pos*100:.0f}% of the [{rng_lo:.2f}–{rng_hi:.2f}] dealing range (" \
           f"{'discount' if pos < 0.4 else 'premium' if pos > 0.6 else 'mid-range'})"
    return float(pos), note
