"""Vectorized technical-analysis primitives (pandas/numpy).

Kept dependency-light on purpose: every indicator is a pure function of a
DataFrame with columns ``o h l c v`` so engines stay testable in isolation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    hist = line - sig
    return line, sig, hist


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["c"].shift(1)
    tr = pd.concat(
        [(df["h"] - df["l"]), (df["h"] - prev_close).abs(), (df["l"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def stoch_rsi(close: pd.Series, rsi_n: int = 14, stoch_n: int = 14, smooth_k: int = 3):
    r = rsi(close, rsi_n)
    lo = r.rolling(stoch_n, min_periods=stoch_n).min()
    hi = r.rolling(stoch_n, min_periods=stoch_n).max()
    raw = (r - lo) / (hi - lo).replace(0.0, np.nan)
    k_line = raw.rolling(smooth_k, min_periods=1).mean()
    return (k_line * 100).fillna(50.0)


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["c"].diff()).fillna(0.0)
    return (direction * df["v"]).cumsum()


def swing_points(df: pd.DataFrame, left: int = 4, right: int = 4):
    """Classic pivot swings: bar i is a swing high if it's the max of [i-left, i+right].

    Returns two DataFrames (highs, lows), each with columns ``idx`` (position in df)
    and price. A point only becomes "confirmed" ``right`` bars later — engines must
    account for that lag when reading recent structure.
    """
    highs: list[dict] = []
    lows: list[dict] = []
    (h, lo) = df["h"].to_numpy(), df["l"].to_numpy()
    n = len(df)
    for i in range(left, n - right):
        window_h = h[i - left : i + right + 1]
        if h[i] == window_h.max():
            highs.append({"idx": i, "price": float(h[i])})
        window_lo = lo[i - left : i + right + 1]
        if lo[i] == window_lo.min():
            lows.append({"idx": i, "price": float(lo[i])})

    # always return frames with the documented columns even when empty — engines
    # filter these by `idx`, and a columnless empty frame would KeyError downstream.
    cols = {"idx": "int64", "price": "float64"}
    return (pd.DataFrame(highs, columns=list(cols)).astype(cols) if highs else pd.DataFrame(columns=list(cols)),
            pd.DataFrame(lows, columns=list(cols)).astype(cols) if lows else pd.DataFrame(columns=list(cols)))


def rolling_return_pct(close: pd.Series, n: int) -> pd.Series:
    """Trailing % change over the last *n* bars at each point."""
    base = close.shift(n)
    return (close / base - 1.0) * 100.0
