
import asyncio, logging
logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
for noisy in ("asyncio","httpx"): logging.getLogger(noisy).setLevel(logging.WARNING)

async def main():
    from cryptodash.config import get_settings
    s = get_settings()
    import httpx
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64)"}) as c:
        from cryptodash.data.providers import BinanceProvider, YahooProvider, FredProvider, GoldPriceProvider
        binance=BinanceProvider(c, s.binance_base); yahoo=YahooProvider(c)
        fred=FredProvider(c, s.fred_api_key); gp=GoldPriceProvider(c)

        from cryptodash.db import db
        db.start(s.pgdata_dir)   # sync; schema bootstrapped inside
        from cryptodash.data.fetcher import DataFetcher
        fetcher = DataFetcher(binance, yahoo, fred, gp)
        bundle = await fetcher.macro_bundle()
        print("=== macro_bundle ===")
        for k in ("bitcoin","gold","brent","m2","liquidity"):
            v=bundle.get(k)
            if v is None: print(f"  {k}: None")
            else: print(f"  {k}: rows={len(v)} span={v['dt'].iloc[0].date()}..{v['dt'].iloc[-1].date()} last={float(v.iloc[-1]['value']):.2f}")

        # full macro score now?
        from cryptodash.analysis.macro import score as macro_score_fn
        ref = await fetcher.candles("BTCUSDT","1d",limit=400)
        res = macro_score_fn(ref_daily=ref, pair_name="BTCUSDT",
                             bitcoin=bundle["bitcoin"], gold=bundle["gold"],
                             brent=bundle["brent"], m2=bundle.get("m2"))
        d=res.to_dict()
        print("\nMACRO SCORE:", {k:d[k] for k in ("direction","score","confidence")})
        for f in d.get("factors",[]):
            corr = f.get("corr_90d"); 
            print(f"  - {f['name']}: value={f.get('value')} bias={round(float(f.get('bias') or 0),2)} corr90={None if corr is None else round(corr,3)} :: {str(f.get('note'))[:80]}")

asyncio.run(main())
