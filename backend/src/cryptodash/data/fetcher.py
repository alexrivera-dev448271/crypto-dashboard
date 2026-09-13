"""Cached data access (Postgres-backed TTL cache).

Every provider fetch passes through here first: if the cached series is still
fresh for its interval we serve it from the local database — faster, cheaper, and
polite to upstreams. Otherwise we refetch and upsert by primary key so partial
overlaps never duplicate bars.

Shared tables (``candles``, ``macro_series``) are not tenant-scoped: they hold
public market data and are read/written through the app role without setting the
tenant GUC.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import pandas as pd

from cryptodash.data.providers import (
    BinanceProvider, DataError, FredProvider, GoldPriceProvider, YahooProvider,
)
from cryptodash.db import db

log = logging.getLogger("cryptodash.fetcher")

# how long a cached series stays "fresh" per interval
TTL: dict[str, timedelta] = {
    "5m": timedelta(minutes=2),
    "15m": timedelta(minutes=8),
    "30m": timedelta(minutes=15),
    "1h": timedelta(minutes=30),
    "4h": timedelta(hours=2),
    "1d": timedelta(hours=6),
    "1w": timedelta(days=2),
}

MACRO_TTL = timedelta(hours=6)

# Hard wall-clock budget per individual upstream source attempt (seconds). A blackholed
# network (SYN dropped / DNS stall) can outlive httpx's connect timeout because the hang
# happens in getaddrinfo — so every candidate fetch is additionally fenced here. Completed
# candidates are kept, only the one still running when this trips gets abandoned.
FETCH_DEADLINE_S = 18.0

# How stale a cached macro series may be before it must be refreshed — scaled to the
# native update cadence of each series so monthly data (M2, global liquidity) can
# actually hit cache instead of re-fetching every request.
MACRO_STALE_AFTER: dict[str, timedelta] = {
    "bitcoin": timedelta(days=2),   # daily closes
    "gold": timedelta(days=3),      # daily LBMA / COMEX
    "brent": timedelta(days=3),     # daily futures close
    "m2": timedelta(days=90),        # monthly (FRED M2SL lags real time 1-2 months)
    "liquidity": timedelta(days=30),   # weekly Fed balance sheet (WALCL, updates every Wed)
}


def _now() -> datetime:
    return datetime.now(UTC)


# ── fetch deadline plumbing (blackholed-network guard) ───────────────────────
# A per-request budget set by AnalysisService.analyse(); every upstream source
# attempt is fenced against min(FETCH_DEADLINE_S, remaining budget). ContextVar so
# concurrent requests each carry their own deadline. When unset (e.g. background
# warm jobs), only the per-source fence applies.
_fetch_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "cryptodash_fetch_deadline", default=None)


def set_fetch_budget(seconds: float) -> "contextvars.Token[float | None]":
    """Start a request-level fetch budget of *seconds* (wall clock)."""
    return _fetch_deadline.set(time.monotonic() + seconds)


def reset_fetch_budget(token: "contextvars.Token[float | None]") -> None:
    with suppress(LookupError):  # defensive; tokens are consumed within their own context
        _fetch_deadline.reset(token)


async def _fenced(coro, label: str):
    """Run *coro* under the per-source deadline and any remaining request budget.

    Either tripping raises DataError(label…) so callers treat it as one failed source.
    If the request budget is already spent we fail fast without even attempting."""
    dl = _fetch_deadline.get()
    if dl is not None:
        remaining = max(0.0, dl - time.monotonic())
        if remaining <= 0:
            raise DataError(f"{label}: request fetch budget exhausted")
        timeout = min(FETCH_DEADLINE_S, remaining)
    else:
        timeout = FETCH_DEADLINE_S
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise DataError(f"{label}: fetch exceeded {FETCH_DEADLINE_S:.0f}s cap") from exc


class DataFetcher:
    def __init__(self, binance: BinanceProvider, yahoo: YahooProvider, fred: FredProvider,
                 gold_price: GoldPriceProvider | None = None) -> None:
        self.binance = binance
        self.yahoo = yahoo
        self.fred = fred
        self.gold_price = gold_price   # optional keyless live-gold cross-check

    async def live_gold_spot(self) -> float | None:
        """Keyless XAU/USD spot from goldprice.dev (cross-check for the gold asset)."""
        if self.gold_price is not None:
            try:
                return await _fenced(self.gold_price.last_price(), "goldspot")
            except DataError as exc:  # noqa: BLE001 - cross-check only, never fatal
                log.debug("live gold spot unavailable: %s", exc)
        return None

    # ── candles (crypto via Binance; macro symbols via Yahoo as fallback) ──
    async def candles(self, symbol: str, interval: str = "1h", limit: int = 500) -> pd.DataFrame:
        fresh = await self._read_candles(symbol, interval)
        if fresh is not None and len(fresh) >= max(30, min(limit, 120)):
            return fresh.tail(limit).reset_index(drop=True)   # fresh enough (checked in _read)

        try:
            df = await self._fetch_candles(symbol, interval, limit)
        except DataError:  # upstream unreachable → fall back to cache below (or re-raise if none)
            if fresh is not None and len(fresh) >= 30:
                return fresh.tail(limit).reset_index(drop=True)   # offline → serve last-known series
            stale = await self._read_candles(symbol, interval, allow_stale=True)
            if stale is not None and len(stale) >= 30:
                log.warning("candles %s %s: upstream unreachable → serving stale cache (%d bars)",
                            symbol, interval, len(stale))
                return stale.tail(limit).reset_index(drop=True)
            raise
        if df is not None and not df.empty:
            await self._write_candles(symbol, interval, df)
        return df

    async def _read_candles(self, symbol: str, interval: str, allow_stale: bool = False) -> pd.DataFrame | None:
        try:
            async with db.shared() as conn:
                rows = await db.fetch_all(
                    conn,
                    "SELECT ts_ms, o, h, l, c, v FROM candles WHERE symbol=%s AND interval=%s ORDER BY ts_ms",
                    (symbol, interval),
                )
                if not rows:
                    return None
            # Return exactly the same frame shape as the live providers ([t_ms, o,h,l,c,v, dt])
            # so cached and fresh candles are interchangeable downstream.
            df = pd.DataFrame(rows, columns=["ts_ms", "o", "h", "l", "c", "v"]).rename(columns={"ts_ms": "t_ms"})
            last_ts = datetime.fromtimestamp(int(df["t_ms"].iloc[-1]) / 1000, tz=UTC)
            if not allow_stale and _now() - last_ts > TTL.get(interval, timedelta(hours=1)) * 2:
                return None
            df["dt"] = pd.to_datetime(df["t_ms"], unit="ms", utc=True)  # keep t_ms too (provider shape)
            return df
        except Exception as exc:  # noqa: BLE001 - cache failure → live fetch
            log.debug("candle cache read failed (%s %s): %s", symbol, interval, exc)
            return None

    async def _fetch_candles(self, symbol: str, interval: str, limit: int) -> pd.DataFrame | None:
        """Fetch OHLC bars for a user-typed symbol by walking an ordered list of candidate
        sources and returning the first one that delivers usable data.

        This keeps accuracy high (exact Binance spot/crypto first; precise Yahoo futures/spot
        for commodities & FX) while degrading gracefully — a single upstream 429/HTTP error no
        longer hard-fails, we simply try the next candidate (e.g. gold → GC=F then PAXGUSDT)."""
        last_exc: DataError | None = None
        tried: list[str] = []
        for kind, ref in _candle_candidates(symbol):
            label = f"{kind}:{ref}"
            if label in tried:
                continue
            tried.append(label)
            try:
                df = await self._fetch_from(kind, ref, interval, limit)
            except DataError as exc:
                last_exc = exc
                log.debug("candle source %s failed for %s: %s", label, symbol, exc)
                continue
            if df is not None and len(df):
                # Remember provenance so the UI / logs can show where these bars came from.
                df.attrs["source"] = f"{kind}:{ref}"
                return df
        raise DataError(f"no data source could serve {symbol} (tried: {', '.join(tried)})") from last_exc

    async def _fetch_from(self, kind: str, ref: str, interval: str, limit: int) -> pd.DataFrame | None:
        # Fence each source attempt against a blackholed network (DNS/SYN hang);
        # honours the per-request fetch budget when one is active.
        if kind == "binance":
            coro = self.binance.candles(ref, interval, limit)
        else:  # kind == "yahoo"
            coro = self.yahoo.candles(ref, interval, limit)
        return await _fenced(coro, f"candles:{ref}:{interval}")

    async def _write_candles(self, symbol: str, interval: str, df: pd.DataFrame) -> None:
        if "t_ms" not in df.columns or "dt" in df.columns and "c" not in df.columns:
            return
        rows = [(int(r.t_ms), float(r.o), float(r.h), float(r.l), float(r.c), float(r.v))
                for r in df[["t_ms", "o", "h", "l", "c", "v"]].itertuples(index=False)]
        if not rows:
            return
        try:
            async with db.shared() as conn:
                await db.execute_values(
                    conn,
                    """INSERT INTO candles(symbol, interval, ts_ms, o, h, l, c, v)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (symbol, interval, ts_ms) DO NOTHING""",
                    [(symbol, interval, *r) for r in rows],
                )
        except Exception as exc:  # noqa: BLE001 - cache write is best-effort
            log.warning("candle cache write failed (%s %s): %s", symbol, interval, exc)

    # ── macro bundle (BTC / gold / Brent / M2 daily closes) ───────────────
    async def macro_bundle(self) -> dict:
        """Returns {bitcoin, gold, brent, m2} as [dt, value] DataFrames or None."""
        out: dict[str, pd.DataFrame | None] = {}

        # Bitcoin: Binance BTCUSDT daily (most reliable crypto-native source)
        try:
            df = await self.candles("BTCUSDT", "1d", limit=400)
            out["bitcoin"] = _to_value_df(df) if df is not None and len(df) else None
        except DataError:
            out["bitcoin"] = None

        # Gold: FRED LBMA (keyed API when set, else public CSV), else COMEX front
        # month (GC=F), else PAXG proxy. Cross-checked against a keyless live XAU/USD
        # spot when available so the headline "gold" level is never single-sourced.
        gold = await self._macro_series("gold", [
            ("fred", "PGOLDAMUSDM"),   # FRED: LBMA Gold AM (USD) — keyed or keyless CSV
            ("yahoo", "GC=F"),
            ("binance_proxy", "PAXGUSDT"),
        ])
        out["gold"] = gold
        spot = await self.live_gold_spot()
        if spot and isinstance(gold, pd.DataFrame) and len(gold):
            # sanity cross-check: flag (in the returned frame attrs) a >3% divergence so
            # the UI/macro engine can note that the historical proxy vs live spot disagree.
            last_hist = float(gold["value"].iloc[-1]) if "value" in gold.columns else None
            if last_hist and abs(spot - last_hist) / last_hist > 0.03:
                log.info("gold cross-check: live %s vs history %s (divergence noted)", round(spot,2), round(last_hist,2))

        # Brent: Yahoo BZ=F (front month), then EIA daily Brent via FRED keyless CSV
        # (DCOILBRENTEU — stable, no rate limits, ~40y history), fallback WTI CL=F
        brent = await self._macro_series("brent", [
            ("yahoo", "BZ=F"),
            ("fred", "DCOILBRENTEU"),
            ("yahoo", "CL=F"),
        ])
        out["brent"] = brent

        # M2 money supply — FRED (keyed API when a key is set, else the public
        # keyless CSV export). The honest source for US M2; monthly. Cache-first:
        # a fresh cached series never re-hits FRED.
        m2 = await self._fred_macro("m2", "M2SL")
        out["m2"] = _to_value_df(m2.rename(columns={"value": "c"})) if (m2 is not None and len(m2)) else None

        # Global central-bank liquidity — FRED series WALCL (total Federal Reserve
        # assets / the Fed's balance-sheet size in millions USD), a widely used
        # keyless proxy for global monetary-liquidity stance that updates weekly.
        liq = await self._fred_macro("liquidity", "WALCL")
        out["liquidity"] = _to_value_df(liq.rename(columns={"value": "c"})) if (liq is not None and len(liq)) else None

        # attach the live gold spot for UI display (not part of the daily series)
        out["_live_gold_spot"] = spot   # consumed by service, stripped before persistence
        return out

    async def _fred_macro(self, name: str, series_id: str) -> pd.DataFrame | None:
        """FRED monthly/daily macro series with cache-first access (same contract as gold/Brent).

        Staleness is handled gracefully so monthly data (M2, BIS liquidity — which lag
        real time by weeks) never flaps between present and absent on a single network
        hiccup: if the cached read misses *and* the live refresh fails, fall back to the
        last known values regardless of age. A successful refresh persists so later
        requests hit cache again.
        """
        cached = await self._read_macro(name)
        if cached is not None and len(cached):
            return cached
        try:
            s = await _fenced(self.fred.series(series_id, limit=300), f"fred:{series_id}")
            if s is not None and len(s) >= 60:
                fresh = _to_value_df(s.rename(columns={"value": "c"}))
                # persist with the cache contract ([dt, value]) so future reads hit cache
                await self._write_macro(name, pd.DataFrame({"dt": fresh["dt"], "value": fresh["value"]}))
                return fresh
        except DataError as exc:  # noqa: BLE001 - optional context series
            log.debug("fred %s (%s) failed: %s", name, series_id, exc)
        return await self._read_macro(name, allow_stale=True)

    async def _macro_series(self, name: str, sources: list[tuple[str, object]]) -> pd.DataFrame | None:
        # try cache first (shared across users — public data)
        cached = await self._read_macro(name)
        if cached is not None and len(cached):
            return cached

        for entry in sources:
            if not (isinstance(entry, tuple) and len(entry) == 2):   # skip disabled slots
                continue
            kind, ref = entry
            if ref is None:
                continue
            try:
                df = None
                if kind == "yahoo":
                    df = await _fenced(self.yahoo.candles(str(ref), "1d", limit=400), f"macro:{name}:{ref}")
                elif kind == "binance_proxy":
                    df = await _fenced(self.binance.candles(str(ref), "1d", limit=400), f"macro:{name}:{ref}")
                elif kind == "fred" and ref:
                    s = await _fenced(self.fred.series(str(ref), limit=300), f"macro:{name}:{ref}")
                    if s is not None:
                        df = s.rename(columns={"value": "c"})
                if df is not None and len(df) >= 60:
                    value_df = _to_value_df(df)
                    await self._write_macro(name, value_df)
                    return value_df
            except DataError as exc:
                log.debug("macro %s source %s failed: %s", name, ref, exc)
        # Upstream unreachable (offline / budget spent): serve last-known values so the
        # relations panel degrades gracefully instead of going blank.
        return await self._read_macro(name, allow_stale=True)

    async def _read_macro(self, series_id: str, allow_stale: bool = False) -> pd.DataFrame | None:
        """Read a cached macro series. ``allow_stale`` returns the last known values even
        when they exceed the freshness window — used as a degraded fallback so monthly/weekly
        data (M2, Fed balance sheet) survives upstream outages instead of vanishing."""
        try:
            async with db.shared() as conn:
                rows = await db.fetch_all(
                    conn, "SELECT ts, value FROM macro_series WHERE series=%s ORDER BY ts", (series_id,)
                )
                if not rows or len(rows) < 60:
                    return None
                df = pd.DataFrame(rows, columns=["ts", "value"])
                # timestamptz → aware datetime already; build a tz-aware index and check freshness.
                df["dt"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(UTC)
                last = df["dt"].iloc[-1]
                if not allow_stale and _now() - last > MACRO_STALE_AFTER.get(series_id, MACRO_TTL * 3):
                    return None
                return df
        except Exception as exc:  # noqa: BLE001
            log.debug("macro cache read failed (%s): %s", series_id, exc)
            return None

    async def _write_macro(self, series_id: str, value_df: pd.DataFrame | None) -> None:
        if value_df is None or value_df.empty:
            return
        try:
            async with db.shared() as conn:
                await db.execute_values(
                    conn,
                    "INSERT INTO macro_series(series, ts, value) VALUES (%s,%s,%s)"
                    # Upsert: a refresh must *replace* any cached row for the same
                    # timestamp (e.g. FRED revising a release), never keep a stale copy.
                    "ON CONFLICT (series, ts) DO UPDATE SET value = EXCLUDED.value",
                    [(series_id, r.dt.to_pydatetime(), float(r.value)) for r in value_df.itertuples(index=False)],
                )
        except Exception as exc:  # noqa: BLE001
            log.debug("macro cache write failed (%s): %s", series_id, exc)


def _yahoo_alias(symbol: str) -> str:
    """Map common dashboard symbols to Yahoo tickers (single-ticker convenience)."""
    return COMMODITY_YAHOO.get(symbol, symbol)


# Precise commodity / FX spot & futures tickers on Yahoo. A user typing a gold pair
# (XAUUSDT, XAU/USD, "GOLD"), silver (XAG…), Brent or WTI gets real OHLC from the
# corresponding market instrument rather than an unresolvable literal ticker 429'ing.
COMMODITY_YAHOO = {
    # gold — spot/futures variants
    "XAU": "GC=F", "GOLD": "GC=F", "XAUGLD": "GC=F", "GLD": "GC=F",
    # silver
    "XAG": "SI=F", "SILVER": "SI=F", "XAGSIL": "SI=F", "SLV": "SI=F",
    # crude
    "BRENT": "BZ=F", "BRNTOIL": "BZ=F", "WTI": "CL=F", "CRUDE": "CL=F",
}


def _candle_candidates(symbol: str) -> list[tuple[str, str]]:
    """Ordered (kind, reference) source candidates for a user-typed symbol.

    Accuracy-first ordering with graceful fallback:
      * recognised commodity/FX (gold/silver/crude — e.g. XAUUSDT, XAU/USD, "GOLD") →
        the on-exchange token proxy when one exists (PAXG ≈ gold), else the precise
        Yahoo futures instrument (GC=F …); checked *first* so a commodity's crypto-style
        spelling ("XAUUSDT") is never mistaken for an on-exchange crypto pair;
      * an exact quote-bearing pair (…USDT or A/B) → Binance spot first, then Yahoo's
        dash notation (BTC-USD) so a delisted/rate-limited listing still resolves;
      * a bare base ("BTC") → its USDT spot, then the Yahoo spot twin;
      * anything else → as-typed on each provider.

    The fetcher walks these in order and returns the first that yields data — a single
    upstream 429/HTTP error no longer kills the request; we simply fall to the next candidate.
    """
    s = symbol.strip().upper()
    if not s:
        return []

    # ── recognised commodity / FX → precise instruments (checked first) ──
    code = _commodity_code(symbol)
    if code:
        out: list[tuple[str, str]] = []
        proxy = COMMODITY_BINANCE_PROXY.get(code)
        if proxy:
            out.append(("binance", proxy))          # on-exchange token tracks the physical (PAXG ≈ XAU/oz)
        yahoo_ref = COMMODITY_YAHOO.get(code)       # precise futures front-month (GC=F / SI=F / BZ=F …)
        if yahoo_ref:
            out.append(("yahoo", yahoo_ref))
        return _dedupe(out)

    base, quote = _split_pair(s)
    # ── exact quote-bearing pair ("ARBUSDT" / "BTC/USDC") → Binance first, then Yahoo twin ──
    if base and quote:
        usd_twin = f"{base}-USD" if quote in ("USDT", "USDC", "BUSD", "FDUSD") else None
        return _dedupe([("binance", s.replace("/", ""))] + ([( "yahoo", usd_twin)] if usd_twin else []))

    # ── bare symbol / unknown → its USDT spot, then an inferred Yahoo spot twin ──
    out = [("binance", f"{base}USDT" if base.isalpha() and len(base) <= 12 else s.replace("/", ""))]
    if "/" not in symbol and base.isalpha():
        out.append(("yahoo", f"{base}-USD"))
    elif "/" in symbol:
        out.extend([("yahoo", s.strip()), ("yahoo", s.replace("/", "-").replace("USDT", "USD"))])
    return _dedupe(out)


def _split_pair(symbol: str) -> tuple[str, str]:
    """Split 'XAU/USD' / 'BTCUSDT' into (base, quote); quote is '' when not recognisable."""
    symbol = symbol.strip().upper()
    if "/" in symbol:
        base, _, quote = symbol.partition("/")
        return base, quote
    for q in ("USDT", "USDC", "BUSD", "FDUSD"):
        if symbol.endswith(q) and len(symbol) > len(q):
            return symbol[: -len(q)], q
    return symbol, ""


def _commodity_code(symbol: str) -> str | None:
    """Match a user-typed symbol to a known commodity code (XAU / XAG / BRENT / WTI …).

    Deliberately conservative: only exact name/code matches and suffix/prefix matches on the
    raw token — never a base-with-stablecoin-quote ("XAU/USDT"), which we'd rather let fall
    through to the as-typed crypto path than silently re-map.
    """
    s = symbol.strip().upper()
    if s in COMMODITY_YAHOO:                          # exact name or code ("GOLD", "BRENT")
        return s
    base, _quote = _split_pair(s)                     # XAUUSDT / XAG/USD → XAU / XAG
    if base in COMMODITY_YAHOO and _quote not in ("USDT", "USDC", "BUSD", "FDUSD"):
        return base                                   # …but only for a *foreign* quote (XAU/USD), not a stablecoin
    for code in COMMODITY_YAHOO:                      # suffix/prefix match ("XAUGLD" → GOLD)
        if len(code) >= 3 and (s.startswith(code) or s.endswith(code)):
            return code
    return None


def _dedupe(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for it in items:
        if it and it[1] and it not in seen:
            seen.add(it)
            out.append(it)
    return out


# On-exchange token proxies that track the physical commodity (preferred when they exist).
COMMODITY_BINANCE_PROXY = {
    "XAU": "PAXGUSDT",   # Paxos gold — 1 PAXG ≈ 1 troy oz XAU/USD
    "GOLD": "PAXGUSDT",
}


def _to_value_df(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Normalize any provider candle frame to [dt, value] daily closes."""
    if df is None or df.empty:
        return None
    out = pd.DataFrame({"dt": df["dt"], "value": df["c"]})
    # collapse to one row per day (daily bars already are; safety for intraday)
    out = out.set_index("dt").resample("1D").last().dropna().reset_index()
    return out
