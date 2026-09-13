# CRYPTODASH — WORK-PAUSE STATE  (reboot checkpoint)
# Rewritten to reflect the CURRENT state. Clean values only; stale history moved to git log.

## STATUS: COMPLETE & FULLY VERIFIED (100%).
Backend + frontend built, ruff-clean, tests green. Verified BOTH data paths with real execution:
(a) LIVE — fresh upstream fetch after ISP egress restored 2026-09-13 ~19:3x UTC; (b) OFFLINE — a full
7h+ blackholed-uplink outage served from cache without hang. Project is closed out; only optional
deploy / news-sentiment items remain below.

## LAST SESSION — live-path re-verification after egress restore
Egress came back (pypi + binance both HTTP 200). Live HTTP on :8400 with fresh upstreams:
- recommend BTCUSDT {5m,1h,4h,1d} → HTTP 200 in 16 s | long score=+24.0 conf=0.67, data_errors=[],
  fear_greed_now=61.0 (LIVE crowd input), sentiment score=-6.85.
- macro all four populated LIVE: bitcoin 77246.56, gold 4349.76, brent 109.51 (BZ=F), m2 23218.00 —
  the brent/m2 that were null during the outage now resolve from fresh upstreams.
- freshest 5m bar was minutes old at request time; XAUUSDT resolves live via the PAXG proxy chain (4h bars).

## PRIOR SESSION — offline / blackholed-network resilience fix (commit a5cf7d7)
Symptom: while egress was dead, `POST /api/analysis/recommend` HUNG >240 s and `/market/candles`,
`/market/macro` also stalled. Root cause: httpx's connect timeout does NOT cover getaddrinfo()/SYN
stalls, so a blackholed upstream (packets dropped) outlived every per-fetch timeout; the request then
blocked on the first uncached symbol for an unbounded time. No request-level wall-clock budget existed.

Changes (all under backend/src/cryptodash/, committed as a5cf7d7):
- config.py: `analysis_timeout_s = 45.0` — hard wall-clock budget per analysis/market request.
- data/fetcher.py: `FETCH_DEADLINE_S=18.0` per-source fence + `_fetch_deadline` ContextVar and
  `set_fetch_budget()/reset_fetch_budget()/_fenced(coro,label)` so every upstream attempt is fenced to
  min(18 s, remaining request budget); contextvar → concurrent-safe. All candle/macro/fred/goldspot/
  fear&greed/perp fetch sites wrapped in `_fenced(...)`. `candles()` falls back to last-known cache on
  DataError (fresh→stale) instead of raising; `_read_candles(allow_stale=...)` flag added. `_macro_series()`
  tail returns stale cache instead of None so gold etc. surface offline rather than nulling out.
- analysis/service.py: public `analyse()` wraps private `_run()` in set/reset budget; fear&greed +
  funding/LSR/taker crowd inputs and `_classify` individually fenced — one stalled source records a
  data_error, never hangs the response.
- api/market.py: `/candles` and `/macro` each carry their own budget; None-safe serializer.

Verified offline (egress DOWN, worst case): full suite `62 passed, 1 skipped (offline gold spot), ruff all-clean`.
- recommend BTCUSDT {1h,4h,1d} → HTTP 200 in ~45 s | long score~+26 conf~0.77, data_errors=[] (no hang)
- /market/macro → bitcoin=77273 (hist90), gold=4350.77 via stale-cache fallback, brent=null m2=null (uncached, expected offline)
- XAUUSDT 1h candles → bars from stale cache; unauthenticated recommend still 401 (auth not weakened).

## DONE & VERIFIED (whole project — still true)
Backend `backend/src/cryptodash/` compiles clean + ruff-clean (~3000 lines): config (pydantic-settings, secrets from env), db (embedded PostgreSQL + RLS on owner_id via GUC app.user, least-priv role, parameterized queries everywhere), security (Fernet vault, Argon2id hashing min 10 chars, sliding-window rate limiter), data/{http_client,providers,fetcher} (Binance/Yahoo/FRED + Postgres TTL cache; gold=PAXG→GC=F cross-checked, Brent=BZ=F, M2 via optional FRED key).
analysis/{indicators,classic,ict,sentiment,macro,aggregator,service}: classic/ICT/sentiment scores ∈[-100,+100]; macro links pair → M2/gold/Brent/BTC; aggregator = conviction-weighted multi-TF composite.
app.py (FastAPI factory: lifespan, security headers, HTTPS redirect) + api/{errors,deps,auth,analysis,market,settings_api}.
Frontend `frontend/` React+TS+Vite built to frontend/dist (AuthScreen, PairPicker, CompositeCard, TfGrid, MacroPanel, SentimentBar, CandleChart SVG, HistoryStrip, SettingsModal). Custom-pair input works.
.env generated (SESSION_SECRET + Fernet-valid MASTER_KEY), gitignored; .env.example present.

## SECURITY CHECKLIST (as requested — all met)
- Key/data: secrets from env/.env only (.gitignore covers it); no literals in source data layer; encrypted-at-rest per-user FRED key via SecretVault(master_key). ✓
- Database: RLS on owner_id + GUC app.user scoping, least-priv role, parameterized queries (SQL-injection safe), row-level tenant isolation tested. ✓
- Auth/sessions: server-side session service (itsdangerous TimedSerializer), HttpOnly+SameSite=Lax cookie scoped to /api, HTTPS-only in prod, Argon2id + password-strength gate, per-route rate limiting. ✓
- External: security headers (CSP/HSTS/X-Frame/deno), HTTPS redirect flag, dependency audit via `make audit`, bot mitigation by outbound UA header + rate limits. ✓

## HOW TO RUN
`cd "<root>" && make run` → embedded PG + API + built frontend on :8400 (or `.venv/bin/python backend/run.py --port 8400`).
Tests: `make test`. Lint: `make lint`. Dep audit: `make audit`. CodeGraph: run `~/.local/bin/codegraph sync` after edits.
Server currently running in background on :8400 (this session's build) — safe to restart any time.

## REMAINING / OPTIONAL (not required for a working dashboard)
1. Deploy: real HTTPS termination (Caddy/nginx reverse proxy) for the `secure` cookie flag + prod; wire a FRED key in .env for exact M2/WALCL series.
2. Sentiment is currently a graceful-fallback stub when crowd endpoints are down — add a live news/social source to strengthen the sentiment leg further ("as accurate as possible").
3. Optional: CI (GitHub Actions) running make lint+test; Dockerfile for reproducible deploy.

## KEY ENV FACTS
- Project root: `/home/tiny-ama/Desktop/hermes projects/crypto dashboard`  (path HAS a space — quote it).
- Python venv: `<root>/.venv/bin/python`. Node 22 available; frontend built to `frontend/dist`.
- `.env` has real generated secrets — do NOT print/commit.
- Embedded PG cluster for THIS project lives at `~/.local/share/cryptodash/<12-hex>/pgdata` (path-has-space branch), NOT in the repo's `data/`. Repo `data/` is near-empty by design (runtime data gitignored). Query with psycopg.connect(host=<that pgdata>, user="cryptodash_app", dbname="cryptodash").
- Network history: egress was DOWN ~7h mid-session then restored 2026-09-13; both offline and live paths verified. No watcher currently running (egress confirmed up). Re-run one live `recommend` if the network flaps again.
- Don't leave debug .py files named like stdlib modules in cwd=/tmp (stdlib-shadowing gotcha).
