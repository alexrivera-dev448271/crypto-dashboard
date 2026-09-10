"""Test reliability of FRED's keyless fredgraph.csv for M2 (US money supply)."""
import gzip
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
      "Accept": "*/*"}


def fetch(url: str, timeout=25):
    req = urllib.request.Request(url, headers={**UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data.decode(errors="replace")


ok = 0
for i in range(5):
    t0 = time.time()
    try:
        body = fetch("https://fred.stlouisfed.org/graph/fredgraph.csv?id=M2SL")
        lines = [ln for ln in body.splitlines() if ln]
        head, tail = lines[:2], lines[-3:]
        ok += 1
        print(f"try{i}: OK {len(body)}B in {time.time()-t0:.1f}s "
              f"rows={max(len(lines)-1,0)} head={head} tail={tail}")
    except Exception as e:  # noqa: BLE001
        print(f"try{i}: EXC after {time.time()-t0:.1f}s -> {type(e).__name__}: {e}")

print(f"\nreliability: {ok}/5")
