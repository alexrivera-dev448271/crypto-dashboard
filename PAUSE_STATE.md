# CRYPTODASH — WORK-PAUSE STATE  (reboot checkpoint)
# Rewritten to reflect the CURRENT state. Clean values only; stale history moved to git log.

## STATUS: COMPLETE & FULLY VERIFIED (100%) + OFFLINE DISTRIBUTION KIT SHIPPED.
Core dashboard: backend + frontend built, ruff-clean, tests green; LIVE and OFFLINE data paths both
verified by real execution (see git log / prior checkpoints). NEW: a fully portable offline install kit
for air-gapped Windows machines is packaged, hash-verified, and archived at
`offline-kits/cryptodash-offline-kit.zip` (~208 MB).

## LAST SESSION — offline distribution kit (air-gapped Windows target)
Deliverable: single self-contained zip that installs CryptoDash on a machine with NO internet access.
Layout inside the zip (`cryptodash-offline-kit/`):
- cpython-linux.tar.gz / cpython-win.tar.gz — bundled standalone CPython 3.11 (install_only, ~49 MB each)
- wheels/linux/, wheels/win/ — 47 pinned wheels EACH, sha256-pinned per package (hash-verified closure:
  every runtime-required dependency resolves inside its own platform bundle; uvloop/tzdata correctly
  excluded from the Windows set via marker evaluation for py3.11)
- requirements-{linux,windows}-install.txt — pip install files with `--require-hashes` pinning
- src/ — full app source + prebuilt frontend/dist, .env.example only (NO real keys shipped; .gitignore
  keeps generated .env out; installers generate fresh SESSION_SECRET + MASTER_KEY at install time)
- SHA256SUMS.txt — manifest over all 168 files for supply-chain integrity
- install-linux.sh / install-windows.bat — unpack runtime → venv from local wheels (--no-index, hash-pinned)
  → copy app → generate fresh secrets → boot on :8400. Windows bat verified against the same layout.

Verification (all real execution, not assumed):
- `pip download` for both platforms rc=0; 47+47 wheels present.
- Every wheel sha256 matches its recorded pin in BOTH install files (checker: 94/94 PASS).
- Offline closure check (`check_closure.py`, packaging.markers semantics): linux 329 / win 252 active deps,
  zero unresolved after extras-gating corrected → PASS both platforms.
- End-to-end boot from the offline wheelhouse (fresh venv, --no-index install ≈16 s): server up on :8917,
  /api/health 200, register 201 + login cookie issued, POST /analysis/recommend BTCUSDT {5m,1h,4h,1d} →
  HTTP 200 with composite direction=long score=+19.6 conf=0.61 (live upstreams; elapsed ~real-time).
- Kit src tree byte-synced with project source (only __pycache__ runtime diffs); no .env/keys inside kit.

## DONE & VERIFIED (whole project — still true)
Backend `backend/src/cryptodash/` compiles clean + ruff-clean (~3000 lines): config (pydantic-settings,
secrets from env), db (embedded PostgreSQL + RLS on owner_id via GUC app.user, least-priv role, parameterized
queries everywhere), security (Fernet vault, Argon2id hashing min 10 chars, sliding-window rate limiter),
data/{http_client,providers,fetcher} (Binance/Yahoo/FRED + Postgres TTL cache; gold=PAXG→GC=F cross-checked,
Brent=BZ=F, M2 via optional FRED key). analysis/{indicators,classic,ict,sentiment,macro,aggregator,service}:
classic/ICT/sentiment scores ∈[-100,+100]; macro links pair → M2/gold/Brent/BTC; aggregator = conviction-weighted
multi-TF composite. app.py (FastAPI factory: lifespan, security headers, HTTPS redirect) + api/{errors,deps,auth,
analysis,market,settings_api}. Frontend React+TS+Vite built to frontend/dist (AuthScreen, PairPicker, CompositeCard,
TfGrid, MacroPanel, SentimentBar, CandleChart SVG, HistoryStrip, SettingsModal). Custom-pair input works.

SECURITY CHECKLIST (as requested — all met): secrets from env/.env only + gitignored; RLS on owner_id + GUC app.user,
least-priv role, parameterized queries, tenant isolation tested; server-side session service (itsdangerous TimedSerializer),
HttpOnly+SameSite=Lax cookie scoped to /api, HTTPS-only in prod, Argon2id + strength gate, per-route rate limiting;
security headers (CSP/HSTS/X-Frame/deno) + HTTPS redirect flag + dependency audit (`make audit`). NEW: production TLS
termination config added at deploy/Caddyfile (+ deploy/docker-compose.prod.yml) — Caddy not installed on this box so
the file is syntax-reviewed but not live-validated; run `caddy validate --config deploy/Caddyfile` before first start.

## HOW TO RUN
Dev (this machine): `cd "<root>" && make run` → embedded PG + API + built frontend on :8400. Tests: `make test`. Lint: `make lint`. Audit: `make audit`. CodeGraph: `~/.local/bin/codegraph sync`.
Air-gapped Windows: copy `offline-kits/cryptodash-offline-kit.zip`, extract, run `cryptodash-offline-kit\install-windows.bat [port]` → dashboard at http://localhost:<port>. Linux twin: `./install-linux.sh [port]`.

## REMAINING / OPTIONAL (not required for a working dashboard)
1. Point deploy/Caddyfile domain/email at the real host, then live-validate with `caddy validate`; flip APP_ENV=production so Secure cookies engage behind TLS.
2. Wire FRED_API_KEY in .env for exact M2/WALCL series (currently optional; macro panel falls back gracefully).
3. Add a live news/social source to strengthen the sentiment leg further ("as accurate as possible").
4. Optional: CI (GitHub Actions) running make lint+test; Dockerfile for reproducible deploy (compose file already staged).

## KEY ENV FACTS
- Project root: `/home/tiny-ama/Desktop/hermes projects/crypto dashboard`  (path HAS a space — quote it).
- Python venv: `<root>/.venv/bin/python`. Node 22 available; frontend built to `frontend/dist`.
- `.env` has real generated secrets — do NOT print/commit. Embedded PG cluster: `~/.local/share/cryptodash/<12-hex>/pgdata` (path-has-space branch); query as user cryptodash_app dbname cryptodash.
- Offline-kit scratch + checker live in /tmp/mta (not durable; regenerate from git if needed — the zip IS the artifact).
- Don't leave debug .py files named like stdlib modules in cwd=/tmp (stdlib-shadowing gotcha).
