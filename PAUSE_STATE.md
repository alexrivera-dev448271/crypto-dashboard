# CRYPTODASH — WORK-PAUSE STATE  (reboot checkpoint)
# Reboot requested by user. This records EXACTLY where work stopped so it can resume cleanly.

## STATUS: ~85% done. Both backend + frontend built; resuming = fix test files, run tests, boot server, verify end-to-end.

## DONE & VERIFIED
- [x] Full backend source at `backend/src/cryptodash/` — COMPILES CLEAN (`.venv/bin/python -m compileall ... exit 0`, ~2884 lines).
    - config.py (pydantic-settings, secrets from env only)
    - db/__init__.py + db/schema.py  → embedded PostgreSQL via `embedded-postgres.get_server()`, RLS on owner_id w/ GUC app.user, least-priv role cryptodash_app. Verified API: PostgresServer HAS .cleanup(); connect socket with host=<pgdata_dir> user=postgres.
    - security/{secrets,auth,rate_limit}.py — Fernet vault (SecretVault(master_key:str)), Argon2id (hash_password/verify_password/check_password_strength min 10 chars letters+digits), SlidingWindow → class is RateLimiter with check(key, limit, window_s)→bool.
    - data/{http_client,providers,fetcher}.py — Binance/Yahoo/FRED providers + Postgres TTL cache (DataFetcher). Gold default = PAXG then GC=F; Brent=BZ=F; M2 via FRED key (optional).
    - analysis/{indicators,classic,ict,sentiment,macro,aggregator,service} — classic/ICT/sentiment score ∈[-100,+100]; macro links pair→M2,gold,Brent,BTC; aggregator conviction-weighted multi-TF.
    - app.py (FastAPI factory: lifespan, security headers, HTTPS redirect), api/{errors,deps,auth,analysis,market,settings_api}, jobs/warming, backend/run.py.
- [x] Frontend `frontend/` — React+TS+Vite. **npm run build SUCCEEDED** → frontend/dist exists (index + assets). Components: AuthScreen, PairPicker, CompositeCard, TfGrid, MacroPanel, SentimentBar, CandleChart (custom SVG), HistoryStrip, SettingsModal.
- [x] .env written with generated SESSION_SECRET + MASTER_KEY (Fernet-valid). pyproject/.gitignore/.env.example/Makefile present.
- [x] Backend deps installed in `.venv` (fastapi, psycopg[binary,pool], embedded-postgres, argon2-cffi, itsdangerous, cryptography, httpx, pandas, numpy, apscheduler) — verified import OK.

## EXACTLY WHERE I STOPPED
Was reading `security/auth.py`, `rate_limit.py`, `secrets.py` to FIX the test files (tests/test_security.py was written against guessed API names that are WRONG). Correct APIs (confirmed by reading source):
- Rate limiter: class `RateLimiter()`; method `check(key:str, limit:int, window_s=None)->bool`. NOT SlidingWindowLimiter. Module-level singleton = `rate_limiter`.
- Sessions: class `SessionService(secret)`; methods `issue(user_id)->(token,max_age)`, `resolve(token)->int|None` (NOT verify()). Token payload key is "uid". TTL=14d via itsdangerous URLSafeTimedSerializer. To test expiry patch time or use max_age small — itsdangerous uses time.time internally; easiest: monkeypatch `itsdangerous...` or just assert tampered → None and skip exact-expiry (or set tiny ttl by subclassing).
- Vault: `SecretVault(master_key:str)` then `.encrypt(str)->bytes`, `.decrypt(bytes)->str`. Ciphertext differs per call (Fernet IV). Use a VALID 44-char urlsafe-b64 key e.g. from Fernet.generate_key().decode() — plain ascii strings like "0123..." are NOT valid unless base64-decodable to 32 bytes; safest: `from cryptography.fernet import Fernet; KEY=Fernet.generate_key().decode()` in the test.
- auth also exports validate_email, AuthPayload dataclass.

## REMAINING WORK (in order)
1. Rewrite tests/test_security.py against correct APIs above (+ keep password/session/vault/limiter coverage). Fix conftest import paths if needed. Add a DB/RLS integration test (start embedded PG in temp dir via db.start(tmp), create 2 users, prove cross-user SELECT returns 0 rows under tenant() scoping) and an app smoke test with fastapi TestClient (register→login→recommend happy path may need network; guard it).
   - NOTE: `db` is a module singleton (`from cryptodash.db import db`); for tests use its .start(pgdata_dir) then .stop(). admin() opens a fresh pool each call.
2. Add pytest to .venv if missing (`.venv/bin/pip install pytest httpx` — httpx needed for TestClient).
3. Run full test suite: `cd project && .venv/bin/pytest -q`. Fix failures until green.
4. Boot server and verify end-to-end over HTTP:
   `.venv/bin/python backend/run.py --port 8400` (embedded PG starts, serves API + frontend/dist). Then curl /api/auth/register + login (cookie jar) + POST /api/analysis/recommend {symbol:"BTCUSDT",timeframes:["1h","4h","1d"]} and confirm composite+macro JSON. Confirm security headers present in response.
5. Run `codegraph init` in project folder (user explicitly requested). Also do a git-init + first commit AFTER secrets confirmed out of tree (.env ignored) — use `git add -A`, check .gitignore, `git commit`.
6. Final: report structure, how to run (make / python backend/run.py), security checklist status.

## KEY ENV FACTS (post-reboot)
- Project root: `/home/tiny-ama/Desktop/hermes projects/crypto dashboard`  (path HAS a space).
- Python venv: `<root>/.venv/bin/python`. Node 22 available; frontend already built to `frontend/dist` (no need to rebuild unless source changes).
- `.env` has real generated secrets — do NOT print/commit. SESSION_SECRET ~64 chars, MASTER_KEY Fernet-valid (43-44 urlsafe b64).
- Network confirmed: Binance + alternative.me Fear&Greed reachable; Yahoo chart works for BZ=F/GC=F/PAXG w/ browser UA; Stooq blocked; FRED needs user key.
- To avoid /tmp stdlib-shadowing gotcha, don't leave debug .py files named like stdlib modules in cwd=/tmp.
