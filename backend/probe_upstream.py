"""Quick upstream health probe (throwaway): Yahoo ranges + FRED keyless CSV."""
import datetime
import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}


def get(url: str, timeout=25) -> bytes:
    req = urllib.request.Request(url, headers={**UA, "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            data = gzip.decompress(data)
        return data


def yahoo(sym: str, rng: int):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"
    try:
        d = json.loads(get(url))
        res = (d.get("chart") or {}).get("result")
        if not res:
            print(f"{sym:>8} range={rng}: NO RESULT {str((d.get('chart') or {}).get('error'))[:80]}")
            return
        r0 = res[0]
        ts = r0["timestamp"]
        q = r0["indicators"]["quote"][0]
        n = sum(1 for o, h, l, c in zip(q["open"], q["high"], q["low"], q["close"])
                if None not in (o, h, l, c))
        first = datetime.datetime.fromtimestamp(ts[0], datetime.UTC).date()
        last = datetime.datetime.fromtimestamp(ts[-1], datetime.UTC).date()
        print(f"{sym:>8} range={rng}: valid_bars={n}/{len(ts)} span={first}..{last}")
    except Exception as e:  # noqa: BLE001
        print(f"{sym:>8} range={rng}: EXC {type(e).__name__}: {e}")


for sym in ("GC=F", "BZ=F", "BTC-USD"):
    for rng in (114, 420):
        yahoo(sym, rng)

print()
url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=M2SL"
for i in range(3):
    try:
        body = get(url).decode(errors="replace")
        print(f"FRED M2SL try{i}: bytes={len(body)} head={body[:70]!r}")
    except Exception as e:  # noqa: BLE001
        print(f"FRED M2SL try{i}: EXC {type(e).__name__}: {e}")
