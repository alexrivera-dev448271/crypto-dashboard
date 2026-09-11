"""Market data providers.

One provider per upstream, all returning plain pandas DataFrames with a uniform
column layout so the analysis engines never care where the numbers came from:

    candle columns : o h l c v  (+ t for time-based series)
    index          : UTC timestamps (ms or datetime, provider-dependent, noted)

Providers are defensive: any upstream quirk (HTTP error, empty body, malformed
row) degrades to an empty DataFrame + warning rather than a 500, and each one
logs which source it used so the UI can show data provenance.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
import pandas as pd

log = logging.getLogger("cryptodash.data")


class DataError(RuntimeError):
    """Raised when a provider cannot deliver usable data at all."""


def _derive_futures_host(spot_base: str) -> str:
    """Map a Binance *spot* base URL to its derivatives (futures) host root.

    Spot and derivatives live on different hosts; hitting ``/fapi/*`` or
    ``/futures/data/*`` on the spot domain is region-blocked/403 in many
    locations, so we must target the dedicated futures host explicitly:

        https://api.binance.com     -> https://fapi.binance.com
        https://testnet.binance.vision -> https://testnet.binancefuture.com

    Falls back to the public fapi host when the mapping is not recognised.
    """
    from urllib.parse import urlparse

    host = (urlparse(spot_base).hostname or "").lower()
    if "binance" in host and ("testnet" in host or "vision" in host):
        return "https://testnet.binancefuture.com"
    if "fapi.binance.com" in host:  # already a futures root
        return spot_base.rstrip("/")
    return "https://fapi.binance.com"


def _df(rows: list[Any], columns: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(rows, columns=columns)
    for col in ("o", "h", "l", "c", "v"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["o", "h", "l", "c"])


# ── Binance (crypto spot + perpetual derivatives) ───────────────────────────
class BinanceProvider:
    INTERVALS = {"5m", "15m", "30m", "1h", "4h", "8h", "12h", "1d", "3d", "1w"}

    def __init__(self, httpx_client: httpx.AsyncClient, base_url: str,
                 futures_base: str | None = None) -> None:
        self._c = httpx_client
        self.base = base_url.rstrip("/")
        # Derivatives (funding / positioning stats) live on a *separate* host from
        # spot; the spot domain 403s them in many regions. Default-derive it so the
        # public API works out of the box, but allow an explicit override for
        # mirrors/proxies and testnets.
        self.futures = (futures_base or _derive_futures_host(base_url)).rstrip("/")

    async def candles(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        if interval not in self.INTERVALS:
            raise DataError(f"unsupported binance interval {interval!r}")
        try:
            r = await self._c.get(
                f"{self.base}/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": min(limit, 1000)},
            )
        except httpx.HTTPError as exc:   # timeouts / connection resets → degrade to DataError
            raise DataError(f"binance klines {symbol} unreachable ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise DataError(f"binance klines {symbol} -> HTTP {r.status_code}")
        # Kline row layout (after openTime): [open, high, low, close, volume,
        # closeTime, quoteVolume, trades, takerBuyBase, takerBuyQuote, ignore].
        rows = [[ts, *k[:5]] for ts, *k in r.json()]
        df = _df(rows, ["t_ms", "o", "h", "l", "c", "v"])
        if not df.empty:
            df["dt"] = pd.to_datetime(df["t_ms"], unit="ms", utc=True)
        return df

    async def ticker(self, symbol: str) -> dict[str, Any]:
        try:
            r = await self._c.get(f"{self.base}/api/v3/ticker/24hr", params={"symbol": symbol})
        except httpx.HTTPError as exc:   # timeouts / connection resets → degrade to DataError
            raise DataError(f"binance ticker {symbol} unreachable ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise DataError(f"binance ticker {symbol} -> HTTP {r.status_code}")
        d = r.json()
        return {"last": float(d["lastPrice"]), "change_pct_24h": float(d["priceChangePercent"])}

    # ── derivatives sentiment inputs (futures endpoints) ───────────────────
    async def funding_rate(self, symbol: str) -> float | None:
        try:
            r = await self._c.get(f"{self.futures}/fapi/v1/premiumIndex", params={"symbol": symbol})
            if r.status_code == 200:
                return float(r.json()["lastFundingRate"]) * 100.0
        except Exception as exc:  # noqa: BLE001 - optional signal, never fatal
            log.debug("funding rate failed for %s: %s", symbol, exc)
        return None

    async def global_long_short_ratio(self, symbol: str, period: str = "4h") -> float | None:
        """Global accounts long/short ratio (>1 = more longs)."""
        try:
            r = await self._c.get(
                f"{self.futures}/futures/data/globalLongShortAccountRatio",
                params={"symbol": symbol, "period": period, "limit": 1},
            )
            if r.status_code == 200:
                return float(r.json()[0]["longShortRatio"])
        except Exception as exc:  # noqa: BLE001
            log.debug("global long/short failed for %s: %s", symbol, exc)
        return None

    async def taker_buy_sell(self, symbol: str, period: str = "4h") -> float | None:
        """Taker buy/sell volume ratio (>1 = aggressive buying)."""
        try:
            r = await self._c.get(
                f"{self.futures}/futures/data/takerlongshortRatio",
                params={"symbol": symbol, "period": period, "limit": 1},
            )
            if r.status_code == 200:
                return float(r.json()[0]["buySellRatio"])
        except Exception as exc:  # noqa: BLE001
            log.debug("taker buy/sell failed for %s: %s", symbol, exc)
        return None


# ── Yahoo Finance (commodities & macro proxies, keyless) ────────────────────
class YahooProvider:
    INTERVAL_MAP = {"5m": "5m", "1h": "60m", "4h": "60m", "1d": "1d"}

    def __init__(self, httpx_client: httpx.AsyncClient) -> None:
        self._c = httpx_client

    async def candles(self, symbol: str, interval: str = "1d", limit: int = 500) -> pd.DataFrame:
        y_interval = self.INTERVAL_MAP.get(interval, "1d")
        # Yahoo's ``range`` param only accepts *named* values (1mo/3mo/6mo/1y...).
        # Integer days are silently ignored and the endpoint returns just the latest
        # bar — so we always request by epoch window (period1/period2) with headroom
        # for weekend/holiday gaps, then tail() to the requested count.
        bar_sec = {"60m": 3_600, "1d": 86_400}.get(y_interval, 86_400)
        headroom = 2.5 if y_interval == "60m" else 1.6   # extra calendar time for gaps
        span_sec = int(max(limit, 50) * bar_sec * headroom)
        p2 = int(time.time()) + 60                        # small future guard vs clock skew
        p1 = max(p2 - span_sec, p2 - int(8 * 365 * 86_400))   # cap lookback at ~8y

        r = await self._chart_with_retry(symbol, {"period1": p1, "period2": p2,
                                                  "interval": y_interval})

        res = (r.json().get("chart") or {}).get("result")
        if not res:
            return pd.DataFrame(columns=["dt", "o", "h", "l", "c", "v"])
        q = res[0].get("indicators", {}).get("quote")[0]
        ts = res[0].get("timestamp", [])
        rows = []
        for i, t in enumerate(ts):
            o, h, low, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
            if None in (o, h, low, c):  # Yahoo uses nulls for missing bars
                continue
            rows.append([t * 1000, o, h, low, c, v or 0.0])
        df = _df(rows, ["t_ms", "o", "h", "l", "c", "v"])
        if not df.empty:
            df["dt"] = pd.to_datetime(df["t_ms"], unit="ms", utc=True)
        return df.tail(limit).reset_index(drop=True)

    async def last_price(self, symbol: str) -> float | None:
        try:
            df = await self.candles(symbol, "1d", 5)
            if not df.empty:
                return float(df["c"].iloc[-1])
        except DataError as exc:
            log.debug("yahoo last price %s failed: %s", symbol, exc)
        return None

    async def _chart_with_retry(self, symbol: str, params: dict[str, Any], max_attempts: int = 3):
        """GET the Yahoo chart endpoint with a bounded backoff for rate limits / transient
        5xx (HTTP 429/502/503). Yahoo front-ends intermittently shed load; a short
        exponential pause usually clears it without hammering. Non-transient statuses and
        exhausted retries raise DataError so the caller can fall to the next source."""
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                r = await self._c.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                    params=params, timeout=httpx.Timeout(20.0),
                )
            except httpx.HTTPError as exc:   # timeouts / connection resets → transient
                last_exc = exc
                if attempt < max_attempts:
                    await asyncio.sleep(min(0.5 * (2 ** (attempt - 1)), 4.0))
                    continue
                raise DataError(f"yahoo {symbol} unreachable ({type(exc).__name__})") from exc

            if r.status_code == 200:
                return r
            last_exc = DataError(f"yahoo {symbol} -> HTTP {r.status_code}")
            # transient rate-limit / upstream blip → back off and retry once or twice
            if attempt < max_attempts and r.status_code in (429, 500, 502, 503):
                await asyncio.sleep(min(0.7 * (2 ** (attempt - 1)), 5.0))
                continue
            break
        raise last_exc if isinstance(last_exc, DataError) else DataError(f"yahoo {symbol} failed")


# ── Fear & Greed Index (alternative.me — keyless crypto sentiment) ─────────
class FearGreedProvider:
    def __init__(self, httpx_client: httpx.AsyncClient) -> None:
        self._c = httpx_client

    async def index(self, limit: int = 30) -> pd.DataFrame | None:
        """Returns columns [dt, value] newest-last; None on failure."""
        try:
            r = await self._c.get("https://api.alternative.me/fng/", params={"limit": limit})
            if r.status_code != 200:
                return None
            data = r.json().get("data", [])
            rows = [[int(d["timestamp"]) * 1000, float(d["value"])] for d in data]
            df = pd.DataFrame(rows, columns=["t_ms", "value"]).sort_values("t_ms").reset_index(drop=True)
            if not df.empty:
                df["dt"] = pd.to_datetime(df.pop("t_ms"), unit="ms", utc=True)
                return df
            return None
        except Exception as exc:  # noqa: BLE001 - optional signal
            log.debug("fear&greed failed: %s", exc)
            return None


# ── GoldPrice.dev (cross-checked spot prices — keyless gold hardening) ─────
class GoldPriceProvider:
    """Keyless cross-check for gold spot (XAU/USD). Used as a fallback / sanity
    signal so the gold asset never depends on a single upstream."""

    def __init__(self, httpx_client: httpx.AsyncClient) -> None:
        self._c = httpx_client

    async def last_price(self) -> float | None:
        try:
            r = await self._c.get(
                "https://api.goldprice.dev/v1/prices",
                params={"from": "XAU", "to": "USD"},
                timeout=httpx.Timeout(15.0),
            )
            if r.status_code != 200:
                return None
            for sym in (r.json().get("symbols") or []):
                if sym.get("symbol") == "XAU" and sym.get("quote_currency") == "USD":
                    p = float(sym["price"])
                    if not sym.get("is_stale", False) and 0 < p < 1e6:   # sane-range guard
                        return p
        except Exception as exc:  # noqa: BLE001 - optional signal, never fatal
            log.debug("goldprice.dev failed: %s", exc)
        return None


# ── FRED (M2 money supply etc. — user-supplied API key, optional) ──────────
class FredProvider:
    def __init__(self, httpx_client: httpx.AsyncClient, api_key: str | None) -> None:
        self._c = httpx_client
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key != "DEMO_KEY")

    # FRED rate-limits browser-like User-Agents (the request stalls until timeout)
    # and also chokes on an explicit Accept-Encoding override, but accepts the stock
    # client UA. Send *only* a minimal UA per-request so a shared client that sends
    # Yahoo's browser UA elsewhere still reaches FRED reliably.
    _FRED_UA = "python-httpx"

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._FRED_UA}


    async def series(self, series_id: str, limit: int = 260) -> pd.DataFrame | None:
        """Returns columns [dt, value]; None if unconfigured or failed."""
        if self.configured:
            df = await self._series_keyed(series_id, limit)
            if df is not None and len(df):
                return df
        # Fallback / no-key path: FRED's public fredgraph CSV export (no auth).
        return await self._series_csv(series_id, limit)

    async def _series_keyed(self, series_id: str, limit: int = 260) -> pd.DataFrame | None:
        """FRED observations API (requires the user's free FRED key)."""
        try:
            r = await self._c.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": series_id, "api_key": self.api_key,
                        "file_type": "json", "sort_order": "desc"},
                headers=self._headers(),
                timeout=httpx.Timeout(20.0),
            )
            if r.status_code != 200:
                log.warning("fred %s -> HTTP %s", series_id, r.status_code)
                return None
            obs = [o for o in r.json().get("data", []) if o.get("value") not in (".", None, "")]
            rows = [[pd.to_datetime(o["date"]).timestamp() * 1000, float(o["value"])] for o in obs[:limit]]
        except Exception as exc:  # noqa: BLE001 - optional series
            log.debug("fred %s failed: %s", series_id, exc)
            return None
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["t_ms", "value"]).sort_values("t_ms").reset_index(drop=True)
        df["dt"] = pd.to_datetime(df.pop("t_ms"), unit="ms", utc=True)
        return df

    async def _series_csv(self, series_id: str, limit: int = 260) -> pd.DataFrame | None:
        """FRED keyless CSV export (fredgraph.csv?id=<SID>). Works without a key.
        The endpoint serves full history for any public series and is stable;
        it is used whenever the keyed API is unavailable or unconfigured."""
        try:
            r = await self._c.get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv",
                params={"id": series_id},
                headers=self._headers(),
                timeout=httpx.Timeout(25.0),
            )
            if r.status_code != 200:
                log.debug("fred csv %s -> HTTP %s", series_id, r.status_code)
                return None
            lines = [ln for ln in (r.text or "").splitlines() if ln]
            rows: list[list[float]] = []
            for ln in lines[1:]:                       # skip "observation_date,<SID>" header
                d, v = ln.split(",")[:2]
                try:
                    ts = pd.to_datetime(d)             # naive date/time → UTC-anchored
                    val = float(v)                     # "." / "" raises -> skipped
                except (ValueError, TypeError):
                    continue                           # missing observation ("." ) or bad row
                rows.append([ts.timestamp() * 1000, val])
            if not rows:
                return None
            df = pd.DataFrame(rows, columns=["t_ms", "value"]).sort_values("t_ms").tail(limit)
            df = df.reset_index(drop=True)
            df["dt"] = pd.to_datetime(df.pop("t_ms"), unit="ms", utc=True)
            return df
        except Exception as exc:  # noqa: BLE001 - optional series
            log.debug("fred csv %s failed: %s", series_id, exc)
            return None
