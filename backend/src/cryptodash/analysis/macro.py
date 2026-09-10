"""Macro context engine — links the selected pair to M2 money supply, gold,
Brent crude and Bitcoin (BTC is both a reference market and an asset of record
for crypto pairs).

Method
------
For each macro asset we compute:
* current level + 30-day change
* Pearson correlation between *daily log returns*, over trailing 90d and 1y windows
  (aligned on calendar days; short histories degrade gracefully)
and translate the combination into a directional bias with an explicit note:

* **Bitcoin beta** — when the pair is correlated to BTC, BTC's recent momentum
  transmits through that correlation (positive corr + falling BTC = headwind).
* **M2 money supply** — real liquidity regime. Year-over-year growth above its
  trailing average is a structural tailwind for risk assets; contraction is a
  headwind. The fastest-moving, best-studied macro input for crypto.
* **Gold** — safe-haven / inflation-hedge linkage: correlation sign × gold's own
  momentum indicates whether the current "flight" regime supports or opposes
  the asset.
* **Brent crude** — energy/inflation channel: significant positive correlation
  with the pair + rising oil = inflation trade supportive; large divergence is
  flagged as a structural break in the historical relationship.

The engine outputs values (for panels) *and* a transparent bias per asset so the
UI can render both the relation table and the resulting score.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cryptodash.analysis.classic import EngineResult, Factor, _clamp

log = logging.getLogger("cryptodash.analysis.macro")


@dataclass
class MacroAsset:
    name: str                     # 'bitcoin' | 'm2_usd' | 'gold' | 'brent_crude'
    series: pd.DataFrame          # columns [dt, value]
    current: float | None = None
    change_30d_pct: float | None = None
    corr_90d: float | None = None
    corr_1y: float | None = None
    extra: dict = field(default_factory=dict)   # e.g. m2 YoY growth


def _log_returns(s: pd.Series) -> pd.Series:
    return np.log(s).diff()


def _as_daily_series(df, value_col: str):
    """Normalize any series frame to one observation per UTC calendar day.

    Daily bars from different sources carry different intraday anchors (Binance
    klines at 00:00Z, Yahoo closes at exchange time, FRED monthly rows on the
    1st), so exact-timestamp joins silently yield zero overlap. Aligning on the
    UTC calendar day — last observation per day wins — is what makes cross-asset
    correlation robust across providers and timezones.
    """
    if df is None or len(df) < 2 or "dt" not in df.columns or value_col not in df.columns:
        return None
    ts = pd.to_datetime(pd.Series(df["dt"]), utc=True)          # tz-aware regardless of source
    vals = pd.to_numeric(df[value_col], errors="coerce")
    s = pd.Series(vals.to_numpy(), index=ts.dt.normalize())     # one bucket per UTC calendar day
    s = s.groupby(level=0).last().dropna()
    return s if len(s) else None


def _pearson_over(a: pd.Series, b: pd.Series, days: int) -> float | None:
    """Correlation of two aligned daily-return series over the trailing `days`.

    Note: column constancy is tested with per-column standard deviations below —
    NOT cross-row std at a single timestamp (that is ~0 for any pair whose
    returns coincide on one day, e.g. an asset correlated to itself)."""
    joined = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(joined) < 20:
        return None
    window = joined.tail(days).dropna()
    if len(window) < 20:
        return None
    x, y = window.iloc[:, 0].to_numpy(dtype=float), window.iloc[:, 1].to_numpy(dtype=float)
    if np.std(x) < 1e-12 or np.std(y) < 1e-12:
        return None
    r = float(np.corrcoef(x, y)[0, 1])
    return r if np.isfinite(r) else None


def _describe_asset(name: str, series: pd.DataFrame | None, ref_returns: pd.Series | None) -> MacroAsset:
    asset = MacroAsset(name=name, series=series if series is not None else pd.DataFrame(columns=["dt", "value"]))
    if series is None or series.empty or len(series) < 10:
        return asset

    s = asset.series
    vals = s["value"]
    last_dt = s["dt"].iloc[-1]
    asset.current = float(vals.iloc[-1])
    cutoff = last_dt - pd.Timedelta(days=30)
    past = vals[s["dt"] > cutoff]
    if len(past):
        base = float(past.iloc[0])
        if base:
            asset.change_30d_pct = (float(vals.iloc[-1]) / base - 1.0) * 100.0

    if ref_returns is not None and name != "m2_usd":
        daily = _as_daily_series(s, "value")
        if daily is not None and len(daily) >= 20:
            own = _log_returns(daily)
            joined_idx = ref_returns.index.intersection(own.index)
            if len(joined_idx):
                a_ref = ref_returns.loc[joined_idx]
                b_own = own.loc[joined_idx]
                asset.corr_90d = _pearson_over(a_ref, b_own, 90)
                asset.corr_1y = _pearson_over(a_ref, b_own, 365)

    if name == "m2_usd":
        # YoY growth vs trailing average (series resampled monthly here)
        m = s.set_index("dt")["value"].resample("ME").last().dropna()
        if len(m) >= 14:
            yoy = (m / m.shift(12) - 1.0).dropna() * 100.0
            asset.extra["yoy_growth_pct"] = float(yoy.iloc[-1]) if len(yoy) else None
            baseline = yoy.iloc[:-1].tail(60).mean() if len(yoy) > 2 else None
            asset.extra["baseline_yoy_pct"] = float(baseline) if baseline is not None and np.isfinite(baseline) else None
    return asset


def score(*, ref_daily: pd.DataFrame | None, pair_name: str,
          bitcoin: pd.DataFrame | None = None, gold: pd.DataFrame | None = None,
          brent: pd.DataFrame | None = None, m2: pd.DataFrame | None = None,
          liquidity: pd.DataFrame | None = None) -> EngineResult:
    """ref_daily: daily OHLC of the *selected* asset (or BTC for non-crypto)."""
    ref_daily_series = _as_daily_series(ref_daily, "c") if (ref_daily is not None and len(ref_daily) > 30) else None
    # log returns of the *daily* reference series (for return correlation)
    ref_returns = _log_returns(ref_daily_series) if ref_daily_series is not None and len(ref_daily_series) >= 20 else None

    assets: list[MacroAsset] = []
    assets.append(_describe_asset("bitcoin", bitcoin, ref_returns))       # idx 0
    assets.append(_describe_asset("gold", gold, ref_returns))             # idx 1
    assets.append(_describe_asset("brent_crude", brent, ref_returns))     # idx 2
    assets.append(_describe_asset("m2_usd", m2, None))                    # idx 3
    if liquidity is not None and len(liquidity):
        liq = _describe_asset("fed_assets", liquidity, None)             # idx 4 (optional)
        _liquidity_regime(liq)
        assets.append(liq)

    factors: list[Factor] = []
    weights: dict[str, float] = {}
    bias: dict[str, float] = {}
    # every asset gets a default note so a missing-data path can never KeyError
    notes: dict[str, str] = {k: "no data available for this period"
                             for k in ("bitcoin", "m2_usd", "gold", "brent_crude")}

    # ── Bitcoin beta ────────────────────────────────────────────────────────
    btc = assets[0]
    if btc.current and btc.corr_1y is not None and abs(btc.corr_1y) > 0.35 and btc.change_30d_pct is not None:
        transmission = _clamp((btc.change_30d_pct / 25.0))          # ±25%/mo maps to full bias
        strength = _clamp(abs(btc.corr_1y), 0.35, 1.0)
        b = float(np.sign(transmission * btc.corr_1y) * min(1.0, abs(transmission)) * (0.4 + 0.6 * strength))
        bias["bitcoin"] = _clamp(b, -1, 1)
        weights["bitcoin"] = 0.35
        notes["bitcoin"] = (f"corr with BTC {btc.corr_1y:+.2f} (1y), BTC {btc.change_30d_pct:+.1f}% in 30d — "
                            f"{'transmitting strength' if bias['bitcoin'] > 0 else 'transmitting weakness'}")
    elif btc.current:
        notes["bitcoin"] = "no stable correlation with BTC (or insufficient history) — treated as independent driver"
    factors.append(Factor("bitcoin", round(btc.change_30d_pct, 2) if btc.change_30d_pct is not None else None,
                          bias.get("bitcoin", 0.0), notes["bitcoin"]))

    # ── M2 money supply (liquidity regime) ────────────────────────────────
    m2 = assets[3]
    yoy = m2.extra.get("yoy_growth_pct")
    baseline = m2.extra.get("baseline_yoy_pct")
    if yoy is not None:
        spread = yoy - (baseline if baseline is not None else 6.0)   # vs its own history (~6% typical)
        b = _clamp(spread / 8.0, -1, 1)                              # ±8pp of baseline → full bias
        bias["m2_usd"] = float(b)
        weights["m2_usd"] = 0.30
        base_str = f"{baseline:.1f}%" if baseline is not None else "n/a"
        notes["m2_usd"] = (f"US M2 YoY {yoy:+.1f}% vs baseline {base_str} — "
                           f"{'expanding liquidity tailwind' if b > 0.15 else 'contracting liquidity headwind' if b < -0.15 else 'neutral liquidity regime'}")
    elif m2.current:
        notes["m2_usd"] = "M2 history too short to compute YoY growth"
    factors.append(Factor(
        "m2_money_supply",
        round(yoy, 2) if yoy is not None else (round(float(m2.current) / 1e6, 0) if m2.current and m2.name == "m2_usd" and float(m2.current) > 1e9 else None),
        bias.get("m2_usd", 0.0), notes["m2_usd"]))

    # ── Gold (safe-haven/inflation linkage) ───────────────────────────────
    gold = assets[1]
    if gold.current and gold.corr_1y is not None and abs(gold.corr_1y) > 0.35 and gold.change_30d_pct is not None:
        transmission = _clamp(gold.change_30d_pct / 8.0)            # ±8%/mo full scale (gold moves slower)
        strength = _clamp(abs(gold.corr_1y), 0.35, 1.0)
        b = float(np.sign(transmission * gold.corr_1y) * min(1.0, abs(transmission)) * (0.4 + 0.6 * strength))
        bias["gold"] = _clamp(b, -1, 1)
        weights["gold"] = 0.18
        notes["gold"] = (f"corr with gold {gold.corr_1y:+.2f} (1y), gold {gold.change_30d_pct:+.1f}% in 30d — "
                         f"hedge-regime {'supportive' if bias['gold'] > 0 else 'opposing'}")
    elif gold.current:
        notes["gold"] = "no stable gold correlation over the trailing year"
    factors.append(Factor("gold", round(gold.change_30d_pct, 2) if gold.change_30d_pct is not None else None,
                          bias.get("gold", 0.0), notes["gold"]))

    # ── Brent crude (energy/inflation channel) ────────────────────────────
    brent = assets[2]
    if brent.current and brent.corr_1y is not None and abs(brent.corr_1y) > 0.35 and brent.change_30d_pct is not None:
        transmission = _clamp(brent.change_30d_pct / 12.0)
        strength = _clamp(abs(brent.corr_1y), 0.35, 1.0)
        b = float(np.sign(transmission * brent.corr_1y) * min(1.0, abs(transmission)) * (0.4 + 0.6 * strength))
        bias["brent_crude"] = _clamp(b, -1, 1)
        weights["brent_crude"] = 0.17
        notes["brent_crude"] = (f"corr with Brent {brent.corr_1y:+.2f} (1y), Brent {brent.change_30d_pct:+.1f}% in 30d — "
                                f"inflation channel {'supportive' if bias['brent_crude'] > 0 else 'opposing'}")
    elif brent.current:
        notes["brent_crude"] = "no stable Brent correlation over the trailing year"
    factors.append(Factor("brent_crude", round(brent.change_30d_pct, 2) if brent.change_30d_pct is not None else None,
                          bias.get("brent_crude", 0.0), notes["brent_crude"]))

    # ── Central-bank liquidity (Fed balance sheet / WALCL) ────────────────
    if assets:  # liquidity only present when FRED is configured and returned data
        liq = next((a for a in assets if a.name == "fed_assets"), None)
        if liq is not None:
            yoy_l = liq.extra.get("yoy_growth_pct")
            base_l = liq.extra.get("baseline_yoy_pct")
            notes.setdefault("fed_assets", "no Fed balance-sheet (WALCL) data available")
            if yoy_l is not None:
                spread_l = yoy_l - (base_l if base_l is not None else 5.0)
                b_l = _clamp(spread_l / 8.0, -1, 1)
                bias["fed_assets"] = float(b_l)
                weights["fed_assets"] = 0.15
                base_str = f"{base_l:.1f}%" if base_l is not None else "n/a"
                notes["fed_assets"] = (f"Fed balance sheet YoY {yoy_l:+.1f}% vs baseline "
                                       f"{base_str} — "
                                       f"{'expanding' if b_l > 0.15 else 'contracting' if b_l < -0.15 else 'flat'} central-bank liquidity")
            elif liq.current:
                notes["fed_assets"] = "Fed balance-sheet history too short for YoY"
            factors.append(Factor("fed_liquidity", round(yoy_l, 2) if yoy_l is not None else None,
                                  bias.get("fed_assets", 0.0), notes["fed_assets"]))

    # ── weighted aggregate ─────────────────────────────────────────────────
    if weights:
        total_w = sum(weights.values())
        raw = sum(bias[k] * weights[k] for k in weights) / total_w
    else:
        raw = 0.0
    score_val = float(_clamp(raw, -1, 1)) * 100.0
    direction = "long" if score_val >= 8 else "short" if score_val <= -8 else "neutral"

    covered_keys = ["bitcoin", "m2_usd", "gold", "brent_crude"] + (["fed_assets"] if len(assets) > 4 else [])
    coverage = sum(1 for k in covered_keys if bias.get(k) is not None or notes.get(k, "").startswith("no stable")) / len(covered_keys)
    confidence = float(_clamp(0.5 * coverage + 0.5 * min(1.0, abs(score_val) / 50.0)))

    detail_assets = [
        {
            "name": a.name,
            "current": a.current,
            "change_30d_pct": round(a.change_30d_pct, 2) if a.change_30d_pct is not None else None,
            "corr_90d": round(a.corr_90d, 3) if a.corr_90d is not None else None,
            "corr_1y": round(a.corr_1y, 3) if a.corr_1y is not None else None,
            "extra": {k: (round(vv, 2) if isinstance(vv, float) else vv) for k, vv in a.extra.items()},
            # sparkline: last ~90 points downsampled to <=64 values for cheap UI
            "spark": _spark(a.series),
        }
        for a in assets
    ]

    res = EngineResult("macro", direction, score_val, confidence, factors)
    # Attach the per-asset presentation block so the UI can render levels, 30d
    # changes, correlations and sparklines (merged into to_dict() output).
    res._extra = {"assets": detail_assets}          # type: ignore[attr-defined]
    return res


def set_index_close(df: pd.DataFrame) -> pd.Series:
    idx = df["dt"] if "dt" in df.columns else pd.to_datetime(df["t_ms"], unit="ms")
    return df.set_index(idx)["c"]


def _liquidity_regime(asset: MacroAsset) -> None:
    """Compute BIS Global Liquidity Index YoY growth vs its trailing baseline (same
    method as M2). Stored in asset.extra for the factor block."""
    if asset.current is None or "dt" not in asset.series.columns:
        return
    m = asset.series.set_index("dt")["value"].resample("ME").last().dropna()
    if len(m) >= 14:
        yoy = (m / m.shift(12) - 1.0).dropna() * 100.0
        asset.extra["yoy_growth_pct"] = float(yoy.iloc[-1]) if len(yoy) else None
        baseline = yoy.iloc[:-1].tail(60).mean() if len(yoy) > 2 else None
        asset.extra["baseline_yoy_pct"] = float(baseline) if baseline is not None and np.isfinite(baseline) else None


def _spark(series: pd.DataFrame | None, n_points: int = 64) -> list[float] | None:
    """Downsample a price series to <=n_points floats for a cheap UI sparkline."""
    if series is None or "value" not in series.columns or len(series) == 0:
        return None
    v = series["value"].tail(90).to_numpy(dtype=float)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return None
    if len(v) <= n_points:
        return [float(x) for x in v]
    step = max(1, len(v) // (n_points - 1))          # leave room to always append the last value
    out = [float(x) for x in v[::step]]
    if out[-1] != float(v[-1]):                       # guarantee the sparkline ends at "now"
        out.append(float(v[-1]))
    return out[: n_points + 1]
