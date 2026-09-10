"""End-to-end style tests for CryptoDash.

Covers: unit-level (security primitives, indicators), integration (embedded
postgres + RLS isolation via real queries, provider→engine pipeline with
synthetic data, full FastAPI app over HTTP using TestClient).

Run:  .venv/bin/pytest -q          (from project root)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def _synth_candles(n=750, start_price=30_000.0, seed=7) -> pd.DataFrame:
    """Deterministic trending + cyclical OHLCV frame for pipeline tests."""
    rng = np.random.default_rng(seed)
    drift = 0.0012 * n
    noise = rng.normal(0, 90, n).cumsum()
    cycle = 500 * np.sin(np.linspace(0, 6 * np.pi, n))
    close = start_price + np.linspace(0, drift, n) + noise + cycle

    o = close + rng.normal(0, 25, n)
    h = np.maximum(o, close) + abs(rng.normal(0, 40, n))
    l = np.minimum(o, close) - abs(rng.normal(0, 40, n))
    v = rng.uniform(1e6, 3e7, n)

    t_ms = (pd.Timestamp("2025-09-08", tz="UTC") - pd.Timedelta(days=40)).value // 10**6
    return pd.DataFrame({"t_ms": [t_ms + i * 3_600_000 for i in range(n)],
                         "o": o, "h": h, "l": l, "c": close, "v": v})
