"""Sentiment / positioning engine.

Combines independent crowd-sentiment inputs into a single score where *extremes
fade*: eutrophication of greed is bearish, capitulation is bullish — while mild
alignment adds directional momentum.

Inputs (each optional; missing ones degrade gracefully):
* Fear & Greed Index (0–100) — crowd mood
* Perpetual funding rate (%) — leveraged-crowd cost of bias (>0 = longs paying)
* Global long/short account ratio — retail positioning
* Taker buy/sell volume ratio — aggressive order flow

Confidence scales with how many independent inputs are actually present.
"""
from __future__ import annotations

import logging
import math

from cryptodash.analysis.classic import EngineResult, Factor, _clamp

log = logging.getLogger("cryptodash.analysis.sentiment")


def score(*, fear_greed: float | None = None, funding_rate_pct: float | None = None,
          long_short_ratio: float | None = None, taker_buy_sell_ratio: float | None = None) -> EngineResult:
    factors: list[Factor] = []

    # ── Fear & Greed (contrarian at the extremes) ───────────────────────────
    if fear_greed is not None and math.isfinite(fear_greed):
        if fear_greed >= 80:
            bias, note = -_clamp((fear_greed - 80) / 20 * 1.3), "euphoric — crowded-long risk"
        elif fear_greed <= 20:
            bias, note = _clamp((20 - fear_greed) / 20 * 1.3), "capitulation — contrarian long fuel"
        else:
            # moderate zones: sentiment still has mild directional value
            bias = _clamp((fear_greed - 50) / 45 * 0.5)
            note = f"F&G {fear_greed:.0f} ({'greed' if fear_greed >= 50 else 'fear'})"
        factors.append(Factor("fear_greed", round(fear_greed, 1), bias, note))

    # ── Funding rate (contrarian at extremes; sign = crowd leveraged side) ─
    if funding_rate_pct is not None and math.isfinite(funding_rate_pct):
        extreme_long = _clamp(max(0.0, funding_rate_pct - 0.05) / 0.30)     # >+5% APY-ish → overheated longs
        extreme_short = _clamp(max(0.0, -funding_rate_pct + 0.05) / 0.30)   # deep negative → shorts crowded
        bias = -(0.6 * extreme_long + 0.4 * math.copysign(1, funding_rate_pct or 1e-9) * min(abs(funding_rate_pct), 0.05) / 0.05 * 0.2) \
            - (-extreme_short * 0.8 if extreme_short > 0 else 0.0)
        bias = _clamp(bias, -1, 1)
        note = f"funding {funding_rate_pct:+.4f}% — {'longs paying (crowded)' if funding_rate_pct > 0.02 else 'shorts paying' if funding_rate_pct < -0.02 else 'neutral'}"
        factors.append(Factor("funding_rate", round(funding_rate_pct, 5), bias, note))

    # ── Retail positioning (contrarian mirror) ─────────────────────────────
    if long_short_ratio is not None and math.isfinite(long_short_ratio):
        excess = _clamp((long_short_ratio - 1.0) * 3, -1, 1)   # >1 means more longs than shorts
        bias = -excess * 0.8
        note = f"global long/short {long_short_ratio:.2f} — {'retail over-leveraged long' if excess > 0.3 else 'over-leveraged short' if excess < -0.3 else 'balanced'}"
        factors.append(Factor("long_short_ratio", round(long_short_ratio, 3), bias, note))

    # ── Aggressive flow (taker ratio) — directional, not contrarian ────────
    if taker_buy_sell_ratio is not None and math.isfinite(taker_buy_sell_ratio):
        excess = _clamp((taker_buy_sell_ratio - 1.0) * 4, -1, 1)
        bias = excess
        note = f"taker buy/sell {taker_buy_sell_ratio:.2f} — {'aggressive buying' if excess > 0 else 'aggressive selling'}"
        factors.append(Factor("taker_flow", round(taker_buy_sell_ratio, 3), bias, note))

    # ── aggregate (equal weight over present inputs) ───────────────────────
    present = [f for f in factors if f.value is not None]
    if not present:
        return EngineResult("sentiment", "neutral", 0.0, 0.1,
                            [Factor("coverage", 0, 0.0, "no sentiment inputs available (symbol may lack futures data)")])

    raw = sum(f.bias for f in present) / len(present)
    score_val = float(_clamp(raw, -1, 1)) * 100.0
    direction = "long" if score_val >= 8 else "short" if score_val <= -8 else "neutral"

    coverage = min(1.0, len(present) / 3.0)          # 3+ independent inputs = full confidence credit
    agreement = abs(raw)
    confidence = float(_clamp(0.5 * coverage + 0.5 * agreement))
    return EngineResult("sentiment", direction, score_val, confidence, factors)
