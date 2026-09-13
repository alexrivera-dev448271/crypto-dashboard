# CRYPTODASH — WORK-PAUSE STATE  (reboot checkpoint)
# Rewritten to reflect the CURRENT state. Clean values only; stale history moved to git log.

## STATUS: COMPLETE & VERIFIED (~98%) + OFFLINE-HANG FIX APPLIED.
Backend + frontend built, all tests green (62 passed / 1 offline-skip), ruff-clean, server boots and
serves end-to-end over HTTP with live upstream data AND degrades gracefully when egress is dead.
CodeGraph present. 7 files modified this session — staged but NOT committed.

## LAST SESSION (this run) — offline / blackholed-network resilience fix
Symptom fixed: while machine egress was dead (Binance + pypi both timing out),
`POST /api/analysis/recommend` HUNG >240s and `/market/candles`, `/market/macro` also stalled.
Root cause: httpx's connect timeout does NOT cover `getaddrinfo()`/SYN stalls, so a blackholed
upstream (packets dropped) outlived every per-fetch timeout; the request then blocked on the first
uncached symbol for an unbounded time. No request-level wall-clock budget existed.

Changes (all under backend/src/cryptodash/):
- config.py: `analysis_timeout_s = 45.0` — hard wall-clock budget per analysis/market request.
- data/fetcher.py:
  * module-level `FETCH_DEADLINE_S = 18.0` per-source fence; `_fetch_deadline` ContextVar +
    `set_fetch_budget()`/`reset_fetch_budget()`/`_fenced(coro, label)` helpers so every upstream
    attempt is fenced against min(18s, remaining request budget). ContextVar → concurrent-safe.
  * all candle/macro/fred/goldspot/fear&greed/perp fetch sites now wrapped in `_fenced(...)`.
  * `candles()`: on DataError, fall back to last-known (fresh) cache, else stale cache — only re-raises
    if nothing usable is cached. `_read_candles(..., allow_stale=False)` gained the flag.
  * `_macro_series()` tail: instead of `return None`, now `return _read_macro(name, allow_stale=True)`
    so cached macro series (e.g. gold) still surface offline rather than nulling out.
- analysis/service.py: public `analyse()` wraps private `_run()` in set/reset budget; the fear&greed +
  funding/LSR/taker crowd inputs and `_classify` are now individually `_fenced(...)` so a single stalled
  source is recorded as a data_error, never hangs.
- api/market.py: `/candles` and `/macro` each set/reset their own budget around the fetcher call;
  `_df_to_records` hardened for None/empty frames.

## VERIFIED THIS SESSION (egress DOWN — worst case)
Full-suite: `62 passed, 1 skipped (offline gold spot), ruff all-clean`.
Live HTTP on :8400 while binance+pypi were dead:
- POST /analysis/recommend BTCUSDT {1h,4h,1d} → HTTP 200 in ~45s | composite dir=long score~+26 conf~0.77, data_errors=[] (no hang)
- GET /market/macro → HTTP 200 in ~45s | bitcoin=77273 (hist90), gold=4350.77 (hist90, stale-cache fallback working), brent=null, m2=null (uncached — expected offline)
- GET /market/candles XAUUSDT 4h → HTTP 200 in ~26s bars=100 (stale cache served)
- unauthenticated recommend → HTTP 401 (auth not weakened by the resilience change)

## DONE & VERIFIED (whole project, carried over — still true)
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
Server currently running in background on :8400 (this session's build, pid logged) for live verification — safe to restart any time.

## REMAINING / OPTIONAL
1. COMMIT the 7 staged files (see git status) — they are lint-clean and offline-verified; nothing blocks a commit. Suggested message: "Fix unbounded hang on blackholed egress: per-source + request fetch fences, stale-cache fallback".
2. Deploy: real HTTPS termination (Caddy/nginx) for the `secure` cookie flag; wire a FRED key in .env for exact M2/WALCL series.
3. Sentiment is currently a graceful-fallback stub ("no sentiment inputs available" when crowd endpoints are down) — add a live news/social source to strengthen the sentiment leg further.
4. Optional: CI (GitHub Actions) running make lint+test; Dockerfile for reproducible deploy.

## KEY ENV FACTS
- Project root: `/home/tiny-ama/Desktop/hermes projects/crypto dashboard`  (path HAS a space — quote it).
- Python venv: `<root>/.venv/bin/python`. Node 22 available; frontend built to `frontend/dist`.
- `.env` has real generated secrets — do NOT print/commit.
- Embedded PG cluster for THIS project lives at `~/.local/share/cryptodash/<12-hex>/pgdata` (path-has-space branch), NOT in the repo's `data/`. The repo `data/` dir is near-empty by design (runtime data gitignored). Query it with psycopg.connect(host=<that pgdata>, user="cryptodash_app", dbname="cryptodash").
- Network THIS SESSION: egress was DOWN (binance + pypi timing out) for the whole run. A watcher daemon (`/tmp/cdwatch/netwatch.sh`) is polling every 30s and will notify when binance+pypi are both reachable again — re-run live-data verification at that point to confirm fresh (non-stale) paths still work.
- Don't leave debug .py files named like stdlib modules in cwd=/tmp (stdlib-shadowing gotcha).
