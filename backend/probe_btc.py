
import asyncio, logging
logging.basicConfig(level=logging.WARNING)

async def main():
    from cryptodash.config import get_settings
    s = get_settings()
    import httpx
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0"}) as c:
        from cryptodash.data.providers import BinanceProvider
        b=BinanceProvider(c, s.binance_base)
        print("live ticker BTCUSDT:", await b.ticker("BTCUSDT"))
    from cryptodash.db import db
    db.start(s.pgdata_dir)
    async with db.shared() as conn:
        rows = await db.fetch_all(conn, "SELECT count(*) n, min(ts_ms) lo, max(ts_ms) hi FROM candles WHERE symbol='BTCUSDT' AND interval='1d'")
        print("cache BTCUSDT 1d:", [(k, (str(v)[:19] if 'ts' in k else v)) for r0 in rows for k,v in r0.items()])
        last = await db.fetch_all(conn, "SELECT to_timestamp(ts_ms::numeric/1000) d, c FROM candles WHERE symbol='BTCUSDT' AND interval='1d' ORDER BY ts_ms DESC LIMIT 5")
        print("cache last closes:", [(str(r["d"])[:16], round(float(r["c"]),2)) for r in last])
        bad = await db.fetch_all(conn, "SELECT count(*) n FROM candles WHERE symbol='BTCUSDT' AND interval='1d' AND (c<100 OR c>500000)")
        print("out-of-range cached closes:", bad[0]["n"])

asyncio.run(main())
