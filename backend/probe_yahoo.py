"""Probe why Yahoo v8 returns only 1 bar for daily range requests."""
import datetime
import json
import urllib.request


def get(url: str, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def probe(tag, url):
    try:
        d = get(url)
        chart = d.get("chart") or {}
        res = chart.get("result")
        err = chart.get("error")
        if not res:
            print(f"{tag:>28}: ERROR {err}")
            return
        r0 = res[0]
        meta = r0["meta"]
        ts = r0.get("timestamp", [])
        gran = meta.get("dataGranularity")
        vr = str(meta.get("validRanges"))[:40]
        exch = meta.get("fullExchangeName")
        sym = meta.get("symbol")
        print(f"{tag:>28}: bars={len(ts)} range={meta.get('range')} gran={gran} "
              f"validRanges={vr} exchange={exch!r} symbol={sym!r}")
    except Exception as e:  # noqa: BLE001
        print(f"{tag:>28}: EXC {type(e).__name__}: {e}")


p0, p1 = int(datetime.datetime(2025, 9, 1, tzinfo=datetime.UTC).timestamp()), \
          int(datetime.datetime(2026, 9, 9, tzinfo=datetime.UTC).timestamp())

probe("query2 range=420 1d", "https://query2.finance.yahoo.com/v8/finance/chart/GC=F?range=420&interval=1d")
probe("query2 period1/2 1d", f"https://query2.finance.yahoo.com/v8/finance/chart/GC=F?period1={p0}&period2={p1}&interval=1d")
probe("query1 range=420 1d", "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?range=420&interval=1d")
probe("query1 period1/2 1d", f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?period1={p0}&period2={p1}&interval=1d")
