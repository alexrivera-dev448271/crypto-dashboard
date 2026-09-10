
import asyncio, logging
logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
for noisy in ("asyncio","httpx"): logging.getLogger(noisy).setLevel(logging.WARNING)

async def main():
    from cryptodash.config import get_settings
    s = get_settings()
    print("fred configured:", bool(s.fred_api_key), "| binance_base:", s.binance_base)

    import httpx
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64)"}) as c:
        from cryptodash.data.providers import BinanceProvider, YahooProvider, FearGreedProvider, FredProvider, GoldPriceProvider
        binance=BinanceProvider(c, s.binance_base); yahoo=YahooProvider(c)
        fng=FearGreedProvider(c); fred=FredProvider(c, s.fred_api_key); gp=GoldPriceProvider(c)

        for name, coro in [
            ("fear&greed index(3)", fng.index(limit=3)),
            ("yahoo BTC-USD 1d x5", yahoo.candles("BTC-USD","1d",limit=5)),
            ("yahoo GC=F gold x5",  yahoo.candles("GC=F","1d",limit=5)),
            ("yahoo BZ=F brent x5", yahoo.candles("BZ=F","1d",limit=5)),
            ("goldprice.dev spot", gp.last_price()),
        ]:
            try:
                r = await coro
                n=len(r) if hasattr(r,"__len__") else "-"
                last=(r.iloc[-1].to_dict() if hasattr(r,"iloc") and len(r) else None)
                print(f"OK   {name}: rows={n} last={last}")
            except Exception as e:
                import traceback; print(f"FAIL {name}: {type(e).__name__}: {e}")

        try:
            df = await fred.series("M2SL", limit=13) if fred.configured else None
            print(f"M2 via FRED provider: {'None (no key)' if not fred.configured else ('rows=%d last=%s'%(len(df),df['value'].iloc[-1]) if df is not None and len(df) else 'EMPTY')}")
        except Exception as e:
            import traceback; print(f"FAIL FRED M2SL: {type(e).__name__}: {e}")

    # full bundle through the real code path (no DB needed for macro_bundle? it uses self.candles -> cache reads db)
    from cryptodash.db import db
    await db.start(s.pgdata_dir); await db.init_schema(); await db.wait_ready(25)
    from cryptodash.data.fetcher import DataFetcher
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0"}) as c:
        binance=BinanceProvider(c, s.binance_base); yahoo=YahooProvider(c)
        fng=FearGreedProvider(c); fred=FredProvider(c, s.fred_api_key); gp=GoldPriceProvider(c)
        fetcher = DataFetcher(binance, yahoo, fred, gp)
        bundle = await fetcher.macro_bundle()
        print("\n=== macro_bundle ===")
        for k,v in bundle.items():
            if v is None: print(f"  {k}: None")
            else: print(f"  {k}: rows={len(v)} last_dt={v['dt'].iloc[-1] if 'dt' in v and len(v) else '-'} last_val={v.iloc[-1].get('value') if len(v) else '-'}")

asyncio.run(main())
