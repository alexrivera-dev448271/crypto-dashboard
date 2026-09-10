# CryptoDash

Multi-timeframe crypto trading recommendation dashboard: pick any pair → get
directional recommendations from three engines (classic TA, ICT, sentiment)
across multiple timeframes, plus macro relations to **US M2 money supply, gold,
Brent crude, and Bitcoin** — with a conviction-weighted composite signal.

## Quick start

```bash
make venv sync          # python env + deps
cp .env.example .env    # then generate SESSION_SECRET & MASTER_KEY (see below)
make run                # embedded Postgres + API + built frontend on :8400
# or develop:
make build-frontend     # npm ci && vite build → frontend/dist
.venv/bin/python backend/run.py --port 8400
```

`.env` (never committed; template in `.env.example`) — minimum required:

| var | purpose | generate with |
|---|---|---|
| `SESSION_SECRET` | signs session cookies | `python -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `MASTER_KEY` | Fernet key for at-rest encryption of user API keys | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` |
| `FRED_API_KEY` | *(optional)* free FRED key — improves macro data priority | fred.stlouisfed.org |

## Architecture

```
backend/src/cryptodash/
  app.py                FastAPI factory: lifespan, security headers, HTTPS redirect (prod)
  config.py             pydantic-settings; secrets come ONLY from environment
  api/                  routes: auth / analysis / market / settings (all rate-limited)
  db/                   embedded Postgres (embedded-postgres), RLS on owner_id,
                        least-privilege app role, parameterized queries only
  security/             Argon2id passwords · itsdangerous sessions · Fernet vault · sliding-window rate limiter
  data/                 providers (Binance / Yahoo / FRED keyless CSV) + TTL cache in Postgres
  analysis/             classic.py · ict.py · sentiment.py · macro.py · aggregator.py · service.py
  jobs/                 APScheduler warming of hot series
frontend/               React 18 + TS + Vite (pair picker, composite card, TF grid,
                        sentiment bar, macro relation panel w/ sparklines, SVG candle chart)
tests/                  security primitives, RLS isolation (real embedded PG), engine pipelines, app smoke over HTTP
```

### Signal pipeline

Each timeframe (5m…1w) → three independent engines scoring **−100 … +100**:
* **Classic** — trend/momentum/volatility/regime factors (EMA structure, RSI, MACD, ATR, ADX…)
* **ICT** — market-structure sweeps, order blocks, liquidity grabs, fair-value gaps (SMC)
* **Sentiment** — Fear & Greed index, funding rates, long/short ratios, taker flow

The **aggregator** blends per-TF signals into a conviction-weighted composite
(higher TFs weigh more), with an explicit vote breakdown. The **macro engine**
runs alongside: BTC beta (return correlation × 30d momentum), US M2 YoY growth
vs its trailing baseline, gold hedge-regime linkage, Brent inflation channel,
and Fed balance-sheet liquidity — each with transparent per-asset notes and
correlations over 90d/1y windows.

### Data sources & accuracy

| series | primary source | fallback |
|---|---|---|
| crypto OHLCV | Binance klines | Yahoo (`BTC-USD`, …) |
| gold | FRED LBMA (PGOLDAMUSDM, keyless CSV or keyed API) | COMEX `GC=F` → PAXG proxy; live spot cross-check via goldprice.dev |
| Brent | Yahoo `BZ=F` | EIA daily Brent via FRED (`DCOILBRENTEU`) → WTI `CL=F` |
| US M2 | FRED `M2SL` (monthly) | — (stale-tolerant cache, 90d window) |
| Fed liquidity | FRED `WALCL` (weekly) | — (stale-tolerant cache, 30d window) |

All cached in Postgres with per-series TTLs; monthly/weekly series survive
upstream outages via the stale-fallback path. Correlations use *daily log
returns aligned on UTC calendar day* so cross-provider timestamps join cleanly.

## Security model (implemented & tested)

- **Keys/secrets**: only from environment (`config.py`); `.env` gitignored; user-supplied API keys stored as Fernet ciphertext (rotating `MASTER_KEY` re-encrypts on write).
- **Database**: row-level security on per-user tables keyed by a GUC set from the authenticated session; least-privilege app role; every query parameterized.
- **Auth/sessions**: Argon2id password hashing, minimum-strength policy, server-side signed+timed cookies (HttpOnly/Secure/SameSite), per-route rate limits (auth stricter than API).
- **External**: strict CSP + X-Frame-Options DENY + referrer/permissions policies; HTTPS 301 redirect in production (`FORCE_HTTPS=1` behind a TLS-terminating proxy); supply-chain scans via `make audit` (pip-audit + npm audit, zero known vulns as of v0.1).

## Tests & verification

```bash
make test        # 48 tests: security primitives, real-PG RLS isolation, engines over synthetic data, app smoke over HTTP
make lint        # ruff
make audit       # python + node dependency vulnerability scan
.venv/bin/python scripts/e2e_verify.py   # live end-to-end against a running server (register → login → recommend)
```

## CodeGraph

Project is indexed (`~/.local/bin/codegraph init`). Re-index after big changes: `make codegraph`.
