"""Database layer.

Architecture
------------
An *embedded* local PostgreSQL 18 instance (managed via ``embedded-postgres``)
reachable over a unix socket inside the project data dir on POSIX, or over
loopback TCP on Windows — no external service, no public port, gitignored cluster.

Security model
--------------
* The app connects as a dedicated low-privilege role ``cryptodash_app``; the
  bootstrap superuser is only used at startup and never held by app code.
* Every tenant table carries ``owner_id`` and enables **Row Level Security**
  with policies keyed on the GUC ``app.user``, which each request sets for its
  own backend — cross-user reads/writes are rejected by Postgres itself, as a
  defense-in-depth layer above application logic.
* All SQL uses parameterized statements; there is no f-string SQL in this codebase.

Connection pool (psycopg_pool 3.x)
----------------------------------
``AsyncConnectionPool`` binds its asyncio primitives to the *running* event
loop at open time, and auto-opens on construction. We therefore construct pools
lazily with ``open=False`` from sync code and call ``await pool.open(wait=True)``
from within the event loop that will actually use them (``_ensure_pool``). If a
new event loop appears later (pytest's repeated ``asyncio.run``, uvicorn reloads)
the pool is rebuilt for it.

Two connection flavors:

* :meth:`DB.tenant`   – pooled, low-priv role, per-user RLS scoping (app data)
* :meth:`DB.shared`   – pooled, low-priv role, no tenant scope (public market cache)
* :meth:`DB.admin`    – boot-role connection for migrations / user management
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator

import psycopg
from embedded_postgres import get_server
from psycopg.rows import dict_row  # noqa: F401 - re-exported for convenience of callers
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger("cryptodash.db")

APP_ROLE = "cryptodash_app"
BOOT_ROLE = "postgres"
DBNAME = "cryptodash"


def _app_role_password() -> str:
    # The socket is loopback-only and inside the project data dir, so the app
    # role password is a mild secret; keep it out of source regardless.
    return os.environ.get("CRYPTODASH_APP_PW") or f"embedded-local-{APP_ROLE}"


class DBError(RuntimeError):
    """Raised when the database cannot be started or used."""


def _app_pool_kwargs() -> dict:
    return {"autocommit": False}  # one logical unit per context = one explicit transaction


class DB:
    def __init__(self) -> None:
        self._server = None
        self.sock_host: str | None = None   # unix-socket dir (POSIX) OR loopback host (TCP transport)
        self.tcp_port: int | None = None    # set only when the server is reached over TCP (Windows)
        self._pool: AsyncConnectionPool | None = None   # constructed open=False; opened lazily
        self._pool_loop_id: int | None = None           # event-loop id that owns the open pool

    @property
    def is_started(self) -> bool:
        return self.sock_host is not None

    def _conn_params(self) -> dict:
        """psycopg connection kwargs for this transport (unix socket dir vs loopback TCP)."""
        assert self.sock_host is not None, "DB.start() was never called"
        if self.tcp_port is None:
            return {"host": self.sock_host}          # POSIX: unix domain socket inside data dir
        return {"host": self.sock_host, "port": str(self.tcp_port)}  # Windows: loopback TCP

    def _conninfo_str(self, user: str) -> str:
        """Keyword conninfo string for *user* against the embedded server."""
        p = self._conn_params()
        s = f"host={p['host']} dbname={DBNAME} user={user}"
        if "port" in p:
            s += f" port={p['port']}"
        return s

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self, pgdata_dir) -> None:
        """Start embedded Postgres and bootstrap the schema (synchronous)."""
        if self.is_started:
            return
        log.info("starting embedded postgres in %s", pgdata_dir)
        try:
            self._server = get_server(str(pgdata_dir), cleanup_mode=None)  # stopped on app exit
            # POSIX: socket-only server, socket lives in the data dir (no public port).
            # Windows: the library auto-falls back to loopback TCP on a local port.
            pinfo = self._server.get_postmaster_info()
            if pinfo is not None and getattr(pinfo, "socket_dir", None) and os.name != "nt":
                self.sock_host, self.tcp_port = str(pinfo.socket_dir), None
            elif pinfo is not None and pinfo.port:
                self.sock_host, self.tcp_port = getattr(pinfo, "hostname", None) or "127.0.0.1", int(pinfo.port)
            else:  # pragma: no cover - defensive; should never happen once server is up
                self.sock_host, self.tcp_port = str(pgdata_dir), None

            boot_params = dict(self._conn_params(), user=BOOT_ROLE, autocommit=True)
            with psycopg.connect(dbname="postgres", **boot_params) as boot:
                cur = boot.cursor()
                exists = cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DBNAME,)).fetchall()
                if not exists:
                    cur.execute(f'CREATE DATABASE "{DBNAME}"')

            with psycopg.connect(dbname=DBNAME, **boot_params) as boot:
                self._bootstrap(boot.cursor())

            # Construct (not open) the pool — opening requires a running event loop.
            self._pool = AsyncConnectionPool(
                conninfo=self._app_conninfo(),
                min_size=1,
                max_size=8,
                timeout=10,
                open=False,
                kwargs=_app_pool_kwargs(),
            )
        except Exception as exc:  # noqa: BLE001 - wrap lifecycle errors uniformly
            self.stop()
            raise DBError(f"could not start embedded database: {exc}") from exc
        log.info(
            "postgres ready: %s (transport=%s%s)",
            DBNAME, "tcp" if self.tcp_port else "unix-socket", f":{self.tcp_port}" if self.tcp_port else "",
        )

    def _app_conninfo(self) -> str:
        return self._conninfo_str(APP_ROLE)

    # ── pool plumbing ─────────────────────────────────────────────────────
    async def _ensure_pool(self) -> AsyncConnectionPool:
        """Return a pool opened on the *current* event loop, rebuilding if needed."""
        assert self.sock_host is not None, "DB.start() was never called"
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError as exc:  # pragma: no cover - all callers are async
            raise DBError("pool access requires a running event loop") from exc

        if self._pool is None or (self._pool_loop_id is not None and self._pool_loop_id != loop_id):
            old, self._pool, self._pool_loop_id = self._pool, None, None
            if old is not None:  # discard a pool owned by a dead event loop (best effort)
                with suppress(Exception):
                    await asyncio.wait_for(old.close(timeout=5), timeout=10)
            self._pool = AsyncConnectionPool(
                conninfo=self._app_conninfo(),
                min_size=1, max_size=8, timeout=10, open=False,
                kwargs=_app_pool_kwargs(),
            )

        # open() is idempotent on an already-open pool; wait fills to min_size.
        await self._pool.open(wait=True, timeout=30)
        self._pool_loop_id = loop_id
        return self._pool

    @asynccontextmanager
    async def _tenant_conn(self, owner_id: int | None):
        pool = await self._ensure_pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row  # this fork has no row_factory kwarg on connection(); set per-connection
            if owner_id is not None:
                # local=True scopes the GUC to this transaction, which is exactly what we
                # commit on exit below — no other request can inherit another user's scope.
                await conn.execute("SELECT set_config('app.user', %s, false)", (str(owner_id),))
            try:
                yield conn
                await conn.commit()
            except Exception:
                with suppress(Exception):
                    await conn.rollback()
                raise

    # ── row helpers ───────────────────────────────────────────────────────
    # psycopg 3 has no connection.fetchrow/fetchval (that's SQLAlchemy/Core style).
    # All app code goes through these so the cursor handling lives in one place.
    async def fetch_one(self, conn: psycopg.AsyncConnection, sql: str, params=()) -> dict | None:
        cur = await conn.execute(sql) if not params else await conn.execute(sql, params)
        return await cur.fetchone()

    async def fetch_val(self, conn: psycopg.AsyncConnection, sql: str, params=()):
        cur = await conn.execute(sql) if not params else await conn.execute(sql, params)
        row = await cur.fetchone()
        if row is None:
            return None
        # factory-agnostic first column (works with dict_row or tuple rows alike)
        from collections.abc import Mapping as _Mapping

        return next(iter(row.values())) if isinstance(row, _Mapping) else row[0]

    async def fetch_all(self, conn: psycopg.AsyncConnection, sql: str, params=()) -> list[dict]:
        cur = await conn.execute(sql) if not params else await conn.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]

    async def execute_values(
        self, conn: psycopg.AsyncConnection, template: str, seq: list[tuple]
    ) -> None:
        """Batched multi-row INSERT via ``executemany`` (psycopg 3 has no
        cursor.execute_values). The template carries per-row ``%s`` placeholders —
        values stay bound server-side, so this stays injection-safe for any row."""
        if not seq:
            return
        cur = conn.cursor()  # sync on psycopg 3 async conns (returns an AsyncCursor)
        try:
            await cur.executemany(template, seq)
        finally:
            await cur.close()

    # ── public connection flavors ─────────────────────────────────────────
    @asynccontextmanager
    async def shared(self) -> AsyncIterator[psycopg.AsyncConnection]:
        """Pooled low-privilege connection *without* tenant scope.

        Used for public market data (candles / macro_series) — no RLS GUC is set,
        so all rows are visible to every user (by design: shared cache).
        """
        async with self._tenant_conn(None) as conn:
            yield conn

    @asynccontextmanager
    async def tenant(self, owner_id: int) -> AsyncIterator[psycopg.AsyncConnection]:
        """Pooled connection scoped to one user (RLS GUC set per backend)."""
        async with self._tenant_conn(owner_id) as conn:
            yield conn

    @asynccontextmanager
    async def admin(self) -> AsyncIterator[psycopg.AsyncConnection]:
        """Boot-role, autocommiting connection for migrations and user management."""
        assert self.sock_host is not None, "DB.start() was never called"
        pool = AsyncConnectionPool(
            conninfo=self._conninfo_str(BOOT_ROLE),
            min_size=1, max_size=2, open=False, kwargs={"autocommit": True},
        )
        await pool.open(wait=True, timeout=30)
        try:
            async with pool.connection() as conn:
                conn.row_factory = dict_row  # see _tenant_conn for why not a constructor kwarg
                yield conn
        finally:
            with suppress(Exception):
                await asyncio.wait_for(pool.close(timeout=5), timeout=10)

    def stop(self) -> None:
        """Tear down (sync, best effort).

        Pool worker tasks are bound to the event loop that opened them. If we're
        called from sync code with no running loop (app shutdown), dropping our
        reference lets the GC reclaim them as their loop dies; if a loop is still
        running (test teardown), there's nothing meaningful for sync code to await —
        the loop's close will cancel any leftovers. Either way Postgres itself is
        stopped below, which is what releases the data dir for re-use in tests.
        """
        self._pool, self._pool_loop_id = None, None
        if self._server is not None and getattr(self._server, "cleanup", None):
            try:
                self._server.cleanup()
            except Exception:  # noqa: BLE001 - best effort shutdown
                log.debug("embedded postgres stop error (ignored)", exc_info=True)
            self._server = None
        self.sock_host, self.tcp_port = None, None

    # ── schema bootstrap (idempotent, sync boot connection) ───────────────
    def _bootstrap(self, cur) -> None:
        from cryptodash.db import schema

        role_exists = cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (APP_ROLE,)).fetchall()
        if not role_exists:
            # CREATE ROLE is DDL — psycopg can't bind a parameter for the password
            # literal there. Use sql.Literal so the value is quoted/escaped on the
            # client side (no injection; works where %s would become an illegal $n).
            from psycopg import sql

            cur.execute(
                sql.SQL("CREATE ROLE {role} LOGIN PASSWORD {pw}").format(
                    role=sql.Identifier(APP_ROLE), pw=sql.Literal(_app_role_password())
                )
            )

        for stmt in schema.DDL_STATEMENTS:
            try:
                cur.execute(stmt)
            except psycopg.errors.DuplicateObject:  # roles/policies are idempotent on re-run
                pass

        cur.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', %(v)s)"
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            {"v": schema.SCHEMA_VERSION},
        )

        for stmt in schema.GRANT_STATEMENTS:  # ownership + least-privilege grants to app role
            try:
                cur.execute(stmt)
            except psycopg.errors.DuplicateObject:
                pass


db = DB()  # singleton wired by the app factory
