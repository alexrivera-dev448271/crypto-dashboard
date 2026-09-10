
import asyncio, logging
logging.basicConfig(level=logging.WARNING)
async def main():
    from cryptodash.config import get_settings
    s = get_settings()
    from cryptodash.db import db
    db.start(s.pgdata_dir)
    async with db.admin() as conn:
        for t in ("recommendations", "candles", "macro_series"):
            n = await conn.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY")  # noqa: B608 - fixed names
        from psycopg import sql
    async with db.shared() as conn:
        for t in ("recommendations","candles","macro_series"):
            r1 = await db.fetch_all(conn, f"SELECT count(*)::int n FROM {t}")  # noqa: S608
            print(t, "now:", r1[0]["n"], "rows")
    db.stop()
asyncio.run(main())
