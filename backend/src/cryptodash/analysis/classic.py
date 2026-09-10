"""Classic technical analysis engine.

Produces a directional score in [-100, +100] with per-factor transparency:
each factor reports its value, bias (−1..+1) and a one-line note so the UI can
explain *why* the engine leans long/short. Weights are fixed but centralised —
tune them here only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cryptodash.analysis.indicators import (
    atr, bollinger, ema, macd, rolling_return_pct, rsi, stoch_rsi,
)

log = logging.getLogger("cryptodash.analysis.classic")


@dataclass
class Factor:
    name: str
    value: float | None
    bias: float            # -1 (strong short) .. +1 (strong long); 0 neutral/no data
    note: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "value": round(float(self.value), 6) if self.value is not None else None,
                "bias": round(float(self.bias), 3), "note": self.note}


@dataclass
class EngineResult:
    engine: str
    direction: str                 # 'long' | 'short' | 'neutral'
    score: float                   # -100..+100
    confidence: float               # 0..1 (data coverage & agreement)
    factors: list[Factor] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {
            "engine": self.engine,
            "direction": self.direction,
            "score": round(float(self.score), 2),
            "confidence": round(float(self.confidence), 3),
            "factors": [f.to_dict() for f in self.factors],
        }
        # Engines may attach extra presentation payloads (e.g. macro's per-asset
        # block) as plain attributes; merge them without shadowing core fields.
        extra = getattr(self, "_extra", None)
        if isinstance(extra, dict):
            for k, v in extra.items():
                out.setdefault(k, v)
        return out


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score(df: pd.DataFrame) -> EngineResult:
    """Classic TA for one timeframe of OHLCV data (needs >= ~80 bars)."""
    factors: list[Factor] = []
    if df is None or len(df) < 60:
        return EngineResult("classic", "neutral", 0.0, 0.1,
                            [Factor("coverage", len(df) if df is not None else 0, 0.0, "not enough history")])

    c = df["c"]
    last = float(c.iloc[-1])

    # ── trend (weight .45) ────────────────────────────────────────────────
    e20, e50 = ema(c, 20), ema(c, 50)
    e200 = ema(c, 200) if len(df) >= 210 else None
    w_trend = 0.45
    trend_bias = 0.0

    if e200 is not None:
        pos_ema200 = _clamp((last / float(e200.iloc[-1]) - 1.0) * 8, -1, 1)          # distance above/below EMA200
        ema_order = np.sign(float(e50.iloc[-1]) - float(e200.iloc[-1]))               # golden/death cross state
        slope_ema200 = _clamp((float(e200.iloc[-1]) / float(e200.iloc[-20]) - 1.0) * 40, -1, 1)
        trend_bias = 0.5 * pos_ema200 + 0.3 * ema_order + 0.2 * slope_ema200
        note = f"price {'above' if last > float(e200.iloc[-1]) else 'below'} EMA200; " \
               f"{'golden-cross regime' if e50.iloc[-1] > e200.iloc[-1] else 'death-cross regime'}"
        factors.append(Factor("ema_structure", last, trend_bias, note))
    elif len(df) >= 60:
        pos_ema50 = _clamp((last / float(e50.iloc[-1]) - 1.0) * 8, -1, 1)
        ema_order = np.sign(float(e20.iloc[-1]) - float(e50.iloc[-1])) if not pd.isna(e50.iloc[-1]) else 0.0
        trend_bias = 0.6 * pos_ema50 + 0.4 * ema_order
        factors.append(Factor("ema_structure", last, trend_bias, "EMA20/50 regime (no EMA200 history yet)"))

    # ── momentum (weight .35) ─────────────────────────────────────────────
    w_mom = 0.35
    rsi14 = float(rsi(c).iloc[-1])
    if rsi14 >= 78:
        bias_rsi, note_rsi = _clamp(-(rsi14 - 78) / 22 * 1.2), "RSI overbought — exhaustion risk"
    elif rsi14 <= 22:
        bias_rsi, note_rsi = _clamp((22 - rsi14) / 22 * 1.2), "RSI oversold — bounce potential"
    else:
        # mid-range: RSI direction itself is informative (>50 = buyers in control)
        bias_rsi, note_rsi = _clamp((rsi14 - 50) / 30 * 0.6), f"RSI {rsi14:.0f} (buyers/sellers: {'bulls' if rsi14 > 50 else 'bears'})"
    factors.append(Factor("rsi_14", rsi14, bias_rsi, note_rsi))

    _, macd_sig, macd_hist = macd(c)
    h_now, h_prev = float(macd_hist.iloc[-1]), float(macd_hist.iloc[-2])
    if pd.isna(h_now):
        bias_macd = 0.0
    else:
        sign = np.sign(h_now)
        fresh_cross = np.sign(h_now - h_prev) * (h_now * h_prev < 0)
        bias_macd = _clamp(sign * (0.5 + abs(h_now / max(last, 1e-9)) * 40) + fresh_cross * 0.3, -1, 1)
    factors.append(Factor("macd_hist", h_now if not pd.isna(h_now) else None, bias_macd,
                          "MACD histogram rising/falling; fresh crossover adds conviction"))

    sr = float(stoch_rsi(c).iloc[-1])
    bias_sr = _clamp((sr - 50) / 30 * 0.8 if 20 <= sr <= 80 else (0.6 if sr > 90 else -0.6), -1, 1)
    factors.append(Factor("stoch_rsi", sr, bias_sr, "Stochastic RSI position"))

    mom_bias = _clamp(0.4 * bias_rsi + 0.35 * bias_macd + 0.25 * bias_sr)

    # ── volatility & volume (weight .20) ──────────────────────────────────
    w_vol = 0.20
    lower, mid_b, upper = bollinger(c)
    bb_pos = _clamp((last - float(mid_b.iloc[-1])) / max(float(upper.iloc[-1]) - float(mid_b.iloc[-1]), 1e-9), -1, 1) \
        if not pd.isna(upper.iloc[-1]) else 0.0
    vol_bias = _clamp(-0.35 * bb_pos + 0.2 * (np.sign(bb_pos)))   # extended moves fade; momentum confirms direction
    atr_pct = float(atr(df).iloc[-1] / last) * 100 if not pd.isna(atr(df).iloc[-1]) else None
    factors.append(Factor("bollinger_pos", round(float(bb_pos), 3), vol_bias,
                          f"BB position {bb_pos:+.2f}; ATR {atr_pct:.1f}% of price" if atr_pct is not None else "Bollinger position"))

    v_mean = float(df["v"].tail(20).mean()) or 1e-9
    v_last = float(df["v"].iloc[-1])
    vol_ratio = _clamp((v_last / v_mean - 1.0) * 2, -1, 1)         # volume expansion vs recent norm
    ret_5 = rolling_return_pct(c, 5).dropna()
    direction_now = np.sign(float(ret_5.iloc[-1])) if len(ret_5) else 0.0
    factors.append(Factor("volume_confirm", round(v_last / v_mean, 2), _clamp(direction_now * max(abs(vol_ratio), 0.3)),
                          f"last bar volume {v_last/v_mean:.1f}x the 20-bar average"))

    vol_vol_bias = _clamp(0.6 * vol_bias + 0.4 * factors[-1].bias)

    # ── aggregate ─────────────────────────────────────────────────────────
    raw = w_trend * trend_bias + w_mom * mom_bias + w_vol * vol_vol_bias
    score_val = float(_clamp(raw, -1, 1)) * 100.0

    direction = "neutral"
    if score_val >= 8:
        direction = "long"
    elif score_val <= -8:
        direction = "short"

    # confidence from data coverage + factor agreement
    coverage = min(1.0, len(df) / 250.0)
    confidence = _clamp(0.4 * coverage + 0.6 * min(1.0, abs(score_val) / 50.0))

    return EngineResult("classic", direction, score_val, float(confidence), factors)
