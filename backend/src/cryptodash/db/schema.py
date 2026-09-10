"""Schema DDL for CryptoDash (PostgreSQL 18).

Conventions: ``owner_id`` is the tenant column on every user-owned table; all
tenant tables enable RLS with policies bound to ``current_setting('app.user')``.
The app role has no superuser rights — it can only do what these grants allow,
and only inside its own rows.

Statements are applied in order at startup and are idempotent (IF NOT EXISTS /
ON CONFLICT). Bump SCHEMA_VERSION when you change shapes and add a migration
statement; ``ensure_schema`` re-applies everything.
"""
from __future__ import annotations

SCHEMA_VERSION = "1"

# RLS policy bodies: tenant rows are visible/writable only while the session
# GUC app.user matches owner_id (set per request by db.tenant()).
_RLS_USING = "owner_id::text = current_setting('app.user', true)"


def _policy(table: str, name: str) -> list[str]:
    # NOTE: PostgreSQL has no "CREATE OR REPLACE POLICY" (that is invalid syntax in every
    # release). Make it idempotent the portable way — drop-then-create. Both statements are
    # individually safe to re-run, which matches how DB._bootstrap applies them one at a time.
    policy = f"tenant_{name}_isolation"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;",  # applies even to the table-owner role
        f"DROP POLICY IF EXISTS {policy} ON {table};",
        (
            f"CREATE POLICY {policy} ON {table} "
            f"FOR ALL USING ({_RLS_USING}) WITH CHECK ({_RLS_USING});"
        ),
    ]


DDL_STATEMENTS: list[str] = [
    # ── base objects ────────────────────────────────────────────────────────
    "CREATE EXTENSION IF NOT EXISTS citext;",

    """CREATE TABLE IF NOT EXISTS meta(
        key   text PRIMARY KEY,
        value text NOT NULL
    );""",

    # Users (managed via the boot role; not tenant-partitioned)
    """CREATE TABLE IF NOT EXISTS users(
        id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        email         citext UNIQUE NOT NULL,
        password_hash text   NOT NULL,
        display_name  text   NOT NULL,
        is_active     boolean NOT NULL DEFAULT true,
        created_at    timestamptz NOT NULL DEFAULT now()
    );""",

    # Per-user encrypted secrets (Fernet ciphertext — plaintext never stored)
    """CREATE TABLE IF NOT EXISTS user_secrets(
        id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        owner_id   bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind       text   NOT NULL,          -- e.g. 'fred_api_key'
        ciphertext bytea  NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (owner_id, kind)
    );""",

    # ── market data cache (shared, not tenant-scoped; read-only to app) ────
    """CREATE TABLE IF NOT EXISTS candles(
        symbol   text   NOT NULL,            -- 'BTCUSDT' / 'PAXGUSDT' / 'BZ=F' ...
        interval text   NOT NULL,
        ts_ms    bigint NOT NULL,            -- bar open time (UTC ms)
        o double precision NOT NULL, h double precision NOT NULL,
        l double precision NOT NULL, c double precision NOT NULL,
        v double precision NOT NULL DEFAULT 0,
        PRIMARY KEY (symbol, interval, ts_ms)
    );""",

    """CREATE TABLE IF NOT EXISTS macro_series(
        series   text   NOT NULL,            -- 'm2_usd' | 'gold' | 'brent' ...
        ts      timestamptz NOT NULL,
        value   double precision NOT NULL,
        PRIMARY KEY (series, ts)
    );""",

    # ── tenant tables (RLS) ────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS watchlist(
        id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        owner_id   bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        symbol     text   NOT NULL,
        position   int    NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (owner_id, symbol)
    );""",

    """CREATE TABLE IF NOT EXISTS recommendations(
        id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        owner_id    bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        symbol      text   NOT NULL,
        interval    text   NOT NULL,
        engine      text   NOT NULL,         -- 'classic' | 'sentiment' | 'ict' | 'macro' | 'composite'
        direction   text   NOT NULL,         -- 'long' | 'short' | 'neutral'
        score       double precision NOT NULL,     -- -100..+100 conviction
        confidence  double precision NOT NULL,     -- data-coverage adjusted 0..1
        detail      jsonb  NOT NULL DEFAULT '{}',
        created_at  timestamptz NOT NULL DEFAULT now()
    );""",

    """CREATE INDEX IF NOT EXISTS rec_lookup_idx
        ON recommendations (owner_id, symbol, interval, engine, created_at DESC);""",

    # ── RLS policies for tenant tables ─────────────────────────────────────
    *_policy("user_secrets", "secrets"),
    *_policy("watchlist", "watch"),
    *_policy("recommendations", "recs"),

    # ── grants: app role gets exactly what it needs and nothing more ───────
    "GRANT CONNECT ON DATABASE cryptodash TO cryptodash_app;",
    "GRANT USAGE ON SCHEMA public TO cryptodash_app;",
]


# ── grants that depend on object existence are applied after DDL above ─────
GRANT_STATEMENTS: list[str] = [
    "ALTER TABLE users           OWNER TO cryptodash_app;",  # app role can manage rows via GRANTs below
    "ALTER TABLE user_secrets    OWNER TO cryptodash_app;",
    "ALTER TABLE watchlist       OWNER TO cryptodash_app;",
    "ALTER TABLE recommendations OWNER TO cryptodash_app;",
    # Shared market-data cache: written by the low-privilege app role via db.shared()
    # (no RLS on these tables — public series shared across all users, by design)
    "GRANT SELECT, INSERT ON candles      TO cryptodash_app;",
    "GRANT SELECT, INSERT ON macro_series TO cryptodash_app;",
    "GRANT SELECT ON meta TO cryptodash_app;",
    "GRANT INSERT, UPDATE ON meta TO cryptodash_app;",
    "GRANT SELECT ON users TO cryptodash_app;",
    """GRANT ALL PRIVILEGES ON TABLE user_secrets, watchlist, recommendations TO cryptodash_app;""",
    """GRANT USAGE, SELECT ON SEQUENCE user_secrets_id_seq, watchlist_id_seq, recommendations_id_seq TO cryptodash_app;""",
]
