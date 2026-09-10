"""Row-Level-Security isolation tests against a real (embedded) PostgreSQL instance.

Boots an isolated cluster in a temp dir, applies the schema DDL + grants, creates two
users, then proves via ``db.tenant()`` that each user sees only their own rows on every
tenant table and that cross-tenant writes are rejected by Postgres itself (the RLS
policy WITH CHECK), not just application code.

This exercises the real bootstrap path (roles, tables, policies, grants) so a future
schema regression fails here before it ships.

Run:  .venv/bin/pytest tests/test_db_rls.py -q
"""
from __future__ import annotations

import asyncio
import os

import psycopg
import pytest


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    # Point the singleton at a throwaway cluster BEFORE it starts (env is read lazily).
    tmp = tmp_path_factory.mktemp("pgdata_rls")
    os.environ["DATA_DIR_OVERRIDE"] = str(tmp)

    from cryptodash.config import get_settings

    get_settings.cache_clear()  # pytest runs all modules in one process; don't reuse an earlier config
    s = get_settings()
    assert s.data_dir == tmp, "config override not in effect before DB boot"

    from cryptodash.db import db as db_singleton

    try:
        db_singleton.start(s.pgdata_dir)
    except Exception as exc:  # pragma: no cover - embedded-postgres unavailable?
        pytest.fail(f"embedded postgres could not start in {tmp}: {exc}")
    try:
        yield db_singleton
    finally:
        db_singleton.stop()


def _run(coro):
    return asyncio.run(coro)


async def _two_users(db) -> tuple[int, int]:
    async with db.admin() as conn:
        u1 = await db.fetch_val(
            conn,
            "INSERT INTO users(email,password_hash,display_name)"
            " VALUES (%s,%s,%s) RETURNING id",
            ("alice@example.com", "$argon2id$fake-aaa", "Alice"),
        )
        u2 = await db.fetch_val(
            conn,
            "INSERT INTO users(email,password_hash,display_name)"
            " VALUES (%s,%s,%s) RETURNING id",
            ("bob@example.com", "$argon2id$fake-bbb", "Bob"),
        )
    return int(u1), int(u2)


async def _seed_for(db, uid: int):
    async with db.tenant(uid) as conn:
        await conn.execute(
            "INSERT INTO watchlist(owner_id, symbol, position) VALUES (%s,'BTCUSDT',0)", (uid,)
        )
        await conn.execute(
            "INSERT INTO recommendations(owner_id,symbol,interval,engine,direction,score,confidence)"
            " VALUES (%s,'ETHUSDT','1h','classic','long',55.0,0.7)",
            (uid,),
        )
        await conn.execute(
            "INSERT INTO user_secrets(owner_id,kind,ciphertext) VALUES (%s,'fred_api_key','%s::bytea')",
            (uid, b"c2VjcmV0"),  # 'secret' base64-encoded
        )


@pytest.fixture(scope="module")
def users(db):
    u1, u2 = _run(_two_users(db))
    for uid in (u1, u2):
        asyncio.run(_seed_for(db, uid))
    return (u1, u2)


def test_rls_isolation(db, users):
    u1, u2 = users

    async def scenario():
        # tenant 1 sees exactly its own rows on each tenant table...
        async with db.tenant(u1) as c:
            assert int(await db.fetch_val(c, "SELECT count(*) FROM watchlist")) == 1
            assert int(await db.fetch_val(c, "SELECT count(*) FROM recommendations")) == 1
            assert int(await db.fetch_val(c, "SELECT count(*) FROM user_secrets")) == 1

        # ...and tenant 2 sees its own, never the other user's (RLS filters the rows).
        async with db.tenant(u2) as c:
            assert int(await db.fetch_val(c, "SELECT count(*) FROM watchlist")) == 1
            assert int(await db.fetch_val(c, "SELECT min(owner_id) FROM recommendations")) == u2

    asyncio.run(scenario())


def test_cross_tenant_write_is_discarded_by_policy(db, users):
    """A tenant inserting a row owned by *another* user is filtered out by the RLS policy.

    Postgres Row Level Security (WITH CHECK) does not raise on a blocked write — it simply
    matches zero rows and discards it. So we assert: after u2 tries to create a watchlist
    entry claiming owner_id=u1, no such row exists *anywhere* in the table.
    """
    u1, _u2 = users

    async def scenario():
        # As tenant 2, attempt to insert a watchlist row that claims ownership by user 1.
        async with db.tenant(_u2) as c:
            try:
                await c.execute(
                    "INSERT INTO watchlist(owner_id, symbol) VALUES (%s,'SOLUSDT')", (u1,)
                )
                blocked = False
            except psycopg.errors.InsufficientPrivilege as exc:  # noqa: PERF203 - explicit guard below
                assert "row-level security policy" in str(exc)
                blocked = True
            assert blocked, "RLS did not block cross-tenant write (no error raised)"

        # Confirm from a superuser view (bypasses RLS — it would see ANY orphan row),
        # and from tenant 1's own scoped view.
        async with db.admin() as c2:
            n = await db.fetch_val(
                c2, "SELECT count(*) FROM watchlist WHERE owner_id=%s AND symbol='SOLUSDT'", (u1,)
            )
            assert int(n) == 0, f"cross-tenant row was persisted despite RLS: {n}"

        async with db.tenant(u1) as c3:
            n = await db.fetch_val(c3, "SELECT count(*) FROM watchlist WHERE symbol='SOLUSDT'")
            assert int(n) == 0, f"orphan row visible to victim tenant: {n}"

    asyncio.run(scenario())


def test_app_role_without_tent_scope_sees_no_rows(db, users):
    """A raw low-privilege connection with no GUC set sees zero tenant rows."""
    u1 = users[0]

    async def scenario():
        # db.shared() does NOT set the app.user GUC -> RLS policy matches nothing.
        async with db.shared() as c:
            assert int(await db.fetch_val(c, "SELECT count(*) FROM watchlist")) == 0
            assert int(
                await db.fetch_val(c, "SELECT count(*) FROM recommendations WHERE owner_id=%s", (u1,))
            ) == 0

    asyncio.run(scenario())


def test_shared_market_tables_are_visible(db):
    """candles / macro_series are intentionally shared (no RLS) -> readable without a tenant."""

    async def scenario():
        async with db.shared() as c:
            exists = await db.fetch_val(
                c,
                "SELECT count(*) FROM information_schema.tables"
                " WHERE table_name IN ('candles','macro_series')",
            )
            assert int(exists) == 2

    asyncio.run(scenario())
