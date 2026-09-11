# CRYPTODASH — WORK-PAUSE STATE  (reboot checkpoint)
# Rewritten to reflect the CURRENT state. Clean values only; stale history moved to git log.

## STATUS: COMPLETE & VERIFIED (~98%). Backend + frontend built, all tests green, server boots and serves end-to-end over HTTP with live upstream data. CodeGraph synced. Git clean (only new test file was untracked pre-last-commit; now committed). Remaining is polish/deploy only.

## LAST SESSION (this run) — multi-source candle resolver fix
Symptom fixed: "chart unavailable ... yahoo XAUUSDT -> HTTP 429". Root cause: a commodity
typed with a crypto-style quote (XAUUSDT / GOLD / BRENT) fell through to Yahoo as a *literal*
ticker and hit Yahoo's rate limit, which then hard-failed the whole request even though good
sources existed. Changes in commit `2173eae`:
- `_candle_candidates(symbol)` → ordered accuracy-first source list per symbol:
  recognised commodity/FX (gold/silver/crude) → on-exchange token proxy first (PAXG ≈ XAU/oz),
  else precise Yahoo front-month futures (GC=F / SI=F / BZ=F / CL=F); exact quote-bearing crypto
  pair (...USDT, A/B) → Binance spot first then the Yahoo dash twin (BTC-USD); bare base ("BTC")
  → its USDT spot then Yahoo spot; unknown → as-typed on each provider. `_fetch_candles` walks the
  list and returns the first that yields data, so one upstream 429/HTTP error degrades to the next
  source instead of killing the request (provenance in df.attrs['source']).
- YahooProvider: bounded exponential backoff on transient 429/5xx + network errors (`_chart_with_retry`).
- `.gitignore`: anchor runtime-data ignore to repo root. The bare `data/` rule was ALSO matching the
  SOURCE package backend/src/cryptodash/data/ and silently excluding it from git (a fresh clone couldn't
  build). Whitelisted the source pkg; runtime cluster stays ignored. Data layer is now committed.
- tests/test_candle_resolver.py: offline unit tests for candidate ordering + _split_pair (no network).

## VERIFIED END-TO-END (live, via the app's own fetcher)
- XAUUSDT / XAU/USD / GOLD → 60 bars, last_close ≈ 4349.6, source=binance:PAXGUSDT, cands=[PAXGUSDT, GC=F]
- BRENT → 60 bars, last_close ≈ 104.15, source=yahoo:BZ=F, cands=[BZ=F]
- ARBUSDT → 60 bars, last_close ≈ 0.1417, source=binance:ARBUSDT, cands=[ARBUSDT, ARB-USD]
- Full /api/analysis/recommend {symbol:"XAUUSDT", timeframes:["1h","4h","1d"]} → per-TF classic+ICT
  (1h -6.7/-14.6; 4h -9.5/+12.7; 1d -36.7 conf 0.84/+24.4) + macro relations panel long bias:
  bitcoin v=21.0, m2_money_supply v=5.41%, gold v=-1.33, brent_crude v=+17.4 → composite +34.5 long;
  sentiment degrades gracefully ("no sentiment inputs available"). fear_greed_now=None (alt.me optional).

## DONE & VERIFIED (whole project)
- Backend `backend/src/cryptodash/` compiles clean, ruff-clean, ~2900 lines: config (pydantic-settings,
  secrets from env), db (embedded PostgreSQL + RLS on owner_id w/ GUC app.user, least-priv role,
  parameterized queries everywhere), security (Fernet vault, Argon2id password hashing min 10 chars,
  sliding-window rate limiter), data/{http_client,providers,fetcher} (Binance/Yahoo/FRED + Postgres TTL
  cache; gold=PAXG→GC=F cross-checked, Brent=BZ=F→EIA CSV, M2 via optional FRED key).
- analysis/{indicators,classic,ict,sentiment,macro,aggregator,service}: classic/ICT/sentiment scores ∈[-100,+100];
  macro links pair → M2/gold/Brent/BTC; aggregator = conviction-weighted multi-TF composite.
- app.py (FastAPI factory: lifespan, security headers, HTTPS redirect) + api/{errors,deps,auth,analysis,market,settings_api}.
- Frontend `frontend/` React+TS+Vite builds to frontend/dist (AuthScreen, PairPicker, CompositeCard, TfGrid,
  MacroPanel, SentimentBar, CandleChart SVG, HistoryStrip, SettingsModal). Custom-pair input works — Analyse uses exactly what's typed.
- .env generated (SESSION_SECRET + Fernet-valid MASTER_KEY), gitignored; .env.example present.

## SECURITY CHECKLIST (as requested)
- Key/data security: secrets from env/.env only (.gitignore covers it); no literals in source data layer; encrypted-at-rest per-user FRED key via SecretVault(master_key). ✓
- Database: RLS on owner_id + GUC app.user scoping, least-priv role, parameterized queries (SQL injection safe), row-level tenant isolation tested. ✓
- Auth/sessions: server-side session service (itsdangerous TimedSerializer), HttpOnly+SameSite=Lax cookie scoped to /api, HTTPS-only in prod, Argon2id hashing + password-strength gate, per-route rate limiting (auth_attempts_limited + strict analysis limit). ✓
- External: security headers (CSP/HSTS/X-Frame/deno), HTTPS redirect flag, dependency audit via `make audit`, frontend build. Bots mitigated by UA header on outbound + rate limits. ✓

## HOW TO RUN
- `cd "<root>" && make run`  → embedded PG + API + built frontend on :8400 (or `.venv/bin/python backend/run.py --port 8400`).
- Tests: `make test` (64 passed). Lint: `make lint`. Dep audit: `make audit`. CodeGraph: `.codegraph/` present; run `~/.local/bin/codegraph sync` after edits.
- Server currently running in background on :8400 (started this session) for live verification — safe to restart any time.

## REMAINING / OPTIONAL (not required for a working dashboard)
1. Deploy: real HTTPS termination (Caddy/nginx reverse proxy) for the `secure` cookie flag + prod; wire a FRED key in .env for exact M2/WALCL series.
2. Sentiment is currently a graceful-fallback stub ("no sentiment inputs available") — add a live news/social source if desired to strengthen "as accurate as possible" on the sentiment leg.
3. Optional: CI (GitHub Actions) running make lint+test; Dockerfile for reproducible deploy.

## KEY ENV FACTS
- Project root: `/home/tiny-ama/Desktop/hermes projects/crypto dashboard`  (path HAS a space — quote it).
- Python venv: `<root>/.venv/bin/python`. Node 22 available; frontend built to `frontend/dist`.
- `.env` has real generated secrets — do NOT print/commit.
- Network: Binance + alternative.me reachable; Yahoo works for BZ=F/GC=F/PAXG w/ browser UA; Stooq blocked; FRED needs user key.
- Don't leave debug .py files named like stdlib modules in cwd=/tmp (stdlib-shadowing gotcha).
