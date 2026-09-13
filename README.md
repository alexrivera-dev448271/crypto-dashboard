# CryptoDash — Multi-Timeframe Crypto Recommendation Dashboard

**Pick any trading pair → get a directional recommendation built from three independent
engines (classic technical analysis, ICT/SMC structure, market sentiment) across multiple
timeframes, plus the macro context every serious trader should check: US M2 money supply,
gold, Brent crude oil, and Bitcoin — blended into one conviction-weighted composite signal.**

CryptoDash is a self-contained, locally-run trading terminal. It ships as a single folder for
**Linux (x86_64)** or **Windows (10/11 x86_64)**: extract, double-click, log in — no compilers,
no Node.js, no database server to administer, no internet access needed at install time.

---

## What you get

| Panel | What it shows |
|---|---|
| **Pair picker** | Any Binance spot/USDT pair (BTC-USDT, ETH-USDT, SOL-USDT, …) with symbol search |
| **Composite signal card** | Conviction-weighted multi-timeframe recommendation: **LONG / SHORT / NEUTRAL** + score (−100…+100), alignment ratio across timeframes, per-engine vote breakdown |
| **Timeframe grid** | Independent signals per timeframe (5m → 1w), each with direction, score, and confidence — higher TFs weigh more |
| **Classic TA engine** | Trend/momentum/volatility/regime factors: EMA structure, RSI, MACD, ATR, ADX, volume regime… |
| **ICT / SMC engine** | Market-structure sweeps & breaks of structure, order blocks, liquidity grabs (equal highs/lows), fair-value gaps — with confluence notes |
| **Sentiment engine** | Fear & Greed index, perpetual funding rates, global long/short ratio, taker buy/sell flow |
| **Macro relations panel** | Your pair's behaviour vs **US M2 money supply (M2SL)**, **gold**, **Brent crude oil**, and **Bitcoin**: return correlations over 90d / 1y windows, per-asset context notes, sparkline trends — e.g. *risk-off: gold rising while BTC lags → defensive regime* |

The engines score independently on a −100…+100 scale; the aggregator blends them
(conviction-weighted by timeframe importance and engine confidence) rather than averaging
blindly, and reports an **alignment ratio** — the fraction of timeframes pointing with the net
signal — as an explicit confidence/accuracy proxy.

---

## Requirements

| | Linux package | Windows package |
|---|---|---|
| OS | x86_64 Linux (glibc ≥ 2.17; any recent distro: Ubuntu 20.04+, Debian 11+, Fedora, Arch, …) | Windows 10 or 11, x86_64 |
| CPU/RAM | Any modern x86_64 · ≥ 2 GB RAM free (embedded Postgres + analysis) | same |
| Disk | ~1.5–2 GB for the extracted package (+ a small data dir grows with usage) | ~1.5–2 GB |
| Pre-installed software | **None.** Python, pip, Node.js and PostgreSQL are bundled inside the package | same |
| Internet at install time | **Not required** — all wheels & binaries ship in the archive | same |
| Internet at run time | Only to fetch *live market data* (Binance/Yahoo/FRED). Without it the dashboard still runs on cached/stale series | same |

> No GPU needed. Analysis is CPU-only and completes in seconds per request.

---

## Installation

### Linux (x86_64)

```bash
# 1. Download the package, then extract anywhere writable:
tar xzf MT-Analysis-linux-x86_64.tar.gz -C ~/            # → ~/MT-Analysis-linux-x86_64/

# 2. Run it (first boot builds a private venv from bundled wheels — ~10–30 s):
cd ~/MT-Analysis-linux-x86_64
./cryptodash
```

Your browser will open `http://localhost:8400`. That's it.

### Windows (x86_64)

1. Copy `MT-Analysis-windows-amd64.zip` to a folder of your choice and **Extract All**.
2. Double-click **`Cryptodash.bat`** inside the extracted folder.
3. Wait for first boot (~30 s) — it builds its private environment from bundled wheels, then
   opens `http://localhost:8400` in your default browser automatically.

> The Windows package needs no installer and writes nothing outside its own folder except a
> tiny `.venv`, an auto-generated `.env`, and a local Postgres data dir — all inside the
> extracted directory. **Do not rename** the folder while it is running; you can move it when closed.

### First login

1. Click **Register** and create your account: an email address + a strong password
   (hashed server-side with Argon2id — never stored or transmitted in plain text).
2. Log in → open the dashboard, pick a pair, get your composite recommendation.

Everything — accounts, settings, cache, database — lives locally on your machine.

---

## Usage guide

### 1 · Pick your pair & timeframes

Top bar: search any **BASE-USDT** (or supported) Binance pair. Choose which timeframes to
analyse — defaults are **1h / 4h / 1d**; extend to the full range (`5m, 15m, 30m, 1h, 4h, 8h,
12h, 1d, 3d, 1w`) for deeper context.

### 2 · Read the composite signal

- **Direction** — LONG / SHORT / NEUTRAL from the conviction-weighted blend of all requested
  timeframes plus an independent macro-context vote.
- **Score (−100…+100)** — strength; |score| ≥ ~40 with high alignment is a strong setup,
  near-zero or low-alignment = stay flat.
- **Alignment ratio** — how many timeframes agree with the net direction. 1.0 = unanimous.
  Treat signals with low alignment as noise until structure confirms.
- **Vote breakdown** — per-timeframe and per-engine contributions so you can see *why*.

### 3 · Engine detail (drill-down)

Each timeframe card lists its three engine scores:

- **Classic** — trend (EMA stack/slope), momentum (RSI/MACD divergence), volatility regime
  (ATR %ile, ADX strength), volume confirmation. Neutral-regime chop is explicitly down-weighted.
- **ICT** — breaks of structure vs swings, order-block retracement entries, liquidity-pool
  sweeps (equal highs/lows) and fair-value gaps; outputs confluence notes, not just a number.
- **Sentiment** — Fear & Greed, funding-rate extremes (over-leverage), long/short ratio,
  taker-flow aggression.

### 4 · Macro relations panel (M2 / gold / Brent / Bitcoin)

For the selected pair you get:

- **Correlation windows** vs each reference asset over trailing **90-day and 1-year** daily-log-return samples.
- **US M2 money supply** — YoY growth rate of FRED `M2SL` vs its own baseline (expanding liquidity = historically bullish for risk assets; decelerating M2 flags de-risking).
- **Gold** — hedge-regime linkage: gold strength with crypto weakness → defensive/flight-to-safety regime.
- **Brent crude oil** — inflation channel: energy-driven CPI pressure feeds rate expectations and real-yield moves that hit hard assets like BTC.
- **Bitcoin** — relative-beta: the pair's beta to BTC (return correlation × momentum). Most alt-pairs trade as a *function of BTC*; the panel tells you whether your pair is currently leading, lagging, or decoupling from the market leader.

Each row carries a plain-English note explaining what that relationship means for position
bias right now, plus sparkline trend context. Data source chain: Binance → Yahoo (gold
LBMA/FRED `GC=F`/PAXG proxy), FRED keyless CSVs with an optional **FRED API key** you can add in
*Settings* to raise macro priority & freshness.

### 5 · Settings

- Add your **free FRED API key** (recommended for fresher M2/gold/Fed-balance-sheet series).
- Change password, manage sessions. Rate limiting and security headers are applied server-side
  by default — nothing to configure.

---

## Security model (what's inside the box)

CryptoDash is built so that running it locally does **not** trade off your security posture:

| Area | Implementation |
|---|---|
| **API keys & secrets** | Loaded only from environment; never in code or git. Your own API keys are stored **encrypted at rest (Fernet)** under a per-installation `MASTER_KEY`; first boot auto-generates both your session secret and master key locally. `.env` is created on disk, never shipped. |
| **Database** | Embedded PostgreSQL 18 with **row-level security** on every tenant table (per-user isolation enforced by the DB engine via a per-request GUC), a dedicated least-privilege `cryptodash_app` role separate from the bootstrap superuser, and **parameterized queries everywhere** — no f-string SQL. On Linux the socket is loopback-only inside your data dir; on Windows it listens on 127.0.0.1 only. |
| **Auth & sessions** | Server-side authentication with **Argon2id** password hashing + minimum-strength policy, signed & timed session cookies that are `HttpOnly`, `Secure` (in prod) and `SameSite=Lax`, plus sliding-window **rate limiting** on every route (auth routes stricter). |
| **External / web security** | Strict Content-Security-Policy, X-Frame-Options DENY, referrer & permissions policies, anti-bot/abuse headers; HTTPS 301 redirect in production (`FORCE_HTTPS=1` behind your TLS proxy); dependency supply-chain scanning via `make audit` (pip-audit + npm audit). |

Because it's local-first and loopback-only by default, a network attacker can't even reach the
API unless you deliberately expose the port.

---

## Architecture (for maintainers)

```
backend/src/cryptodash/
  app.py                FastAPI factory — lifespan, security headers, HTTPS redirect (prod)
  config.py             pydantic-settings; secrets from environment only
  api/                  routes: auth / analysis / market / settings (all rate-limited)
  db/                   embedded Postgres · RLS on owner_id · least-priv role · parameterized SQL
                        unix-socket transport (Linux/macOS) or loopback TCP (Windows) — one code path
  security/             Argon2id · itsdangerous sessions · Fernet vault · sliding-window rate limiter
  data/                 providers: Binance klines/funding/L-S-ratio, Yahoo fallback, FRED keyless CSV;
                        Postgres TTL cache with stale-fallback for monthly/weekly series
  analysis/             classic.py · ict.py · sentiment.py · macro.py · aggregator.py (conviction blend)
  jobs/                 APScheduler warming of hot series in the background
frontend/               React 18 + TypeScript + Vite — pair picker, composite card, TF grid,
                        sentiment bar, macro relation panel with sparklines, SVG candle chart
tests/                  security primitives · real-PG RLS isolation · engine pipelines · app smoke over HTTP
```

**Data sources & fallbacks (accuracy-focused):** crypto OHLCV from Binance klines → Yahoo
fallback; gold via FRED LBMA (`PGOLDAMUSDM`) / `GC=F` / PAXG proxy with live spot cross-check;
Brent via Yahoo `BZ=F` → EIA/FRED `DCOILBRENTEU` → WTI fallback; US M2 from FRED `M2SL`; Fed
liquidity from FRED `WALCL`. All cached in Postgres per-series-TTL; monthly/weekly series survive
upstream outages. Correlations use daily log returns aligned on UTC calendar day so cross-provider
timestamps join cleanly.

**Signal pipeline:** each requested timeframe → three independent engines scoring −100…+100 →
aggregator blends them conviction-weighted (higher TFs dominate; macro adds an independent context
vote) → composite direction + score + alignment ratio + full vote breakdown.

---

## Troubleshooting & FAQ

**"Port 8400 already in use / another instance is running"** — Stop the other instance or launch
with a different port: `./cryptodash --port 9000` (Linux) / set `BACKEND_PORT=9000` before running.

**First boot takes ~30 s and then nothing happens (Windows)** — A minimized console window is
running the server; check it for errors, or open `http://localhost:8400` manually.

**No market data / stale candles** — You need internet to reach Binance/Yahoo/FRED. Cached data
still serves while offline. Add a FRED key in Settings for freshest macro series.

**Windows SmartScreen warning on `.bat`/first-run** — This package is unsigned (local tooling).
Click *More info → Run anyway*. Nothing phones home at install time.

**Linux: `Permission denied` on `./cryptodash`** — `chmod +x cryptodash`. If you extracted from a
filesystem without exec bits, the launcher will still work via `bash cryptodash`.

**Reset everything (accounts, DB, cache)** — Stop the app and delete the `data/` folder inside the
package directory; next boot re-initializes the database.

**Where is my data?** — All inside the package dir: `.venv/` (private runtime), `.env`
(secrets), `data/postgres` (embedded DB + cache). Move/delete these to back up or wipe.

---

## License & disclaimer

This software is provided **as-is**, without warranty of any kind, and is for **educational and
informational purposes only**. It does not provide financial advice; signals are probabilistic
estimates derived from public market data, not guarantees of future performance. Cryptocurrency
trading involves substantial risk of loss. Do your own research and never trade more than you can
afford to lose.
