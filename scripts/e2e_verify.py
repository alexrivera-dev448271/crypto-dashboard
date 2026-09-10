"""End-to-end HTTP verification for the crypto dashboard (register -> login -> recommend)."""
import json
import subprocess
import time

BASE = "http://localhost:8400"


def curl(args):
    r = subprocess.run(["curl", "-sS", *args], capture_output=True, text=True, timeout=600)
    return r.stdout, r.stderr, r.returncode


# 1. health + security headers on a response
out, _, _ = curl([BASE + "/api/health"])
print("HEALTH:", out.strip()[:200])
hdrs = curl(["-I", BASE + "/api/health"])[0]
for line in hdrs.splitlines():
    low = line.lower()
    if any(k in low for k in ("x-frame-options", "content-security-policy",
                              "strict-transport", "referrer-policy",
                              "permissions-policy")):
        print("  HDR:", line.strip())

# 2. register (unique email; throwaway dev account)
email = f"e2e_{int(time.time())}@example.com"
_pw = "Tr@der2026!xK9mQwL7pVz"          # stable dev credential for this script

body, err, rc = curl(["-X", "POST", BASE + "/api/auth/register",
                      "-H", "Content-Type: application/json",
                      "--data", json.dumps({"email": email, "password": _pw}),
                      "-c", "/tmp/cd_cookie.txt"])
print("\nREGISTER:", body.strip()[:200] or err.strip())

# 3. login (refreshes the session cookie in the jar)
body, err, rc = curl(["-X", "POST", BASE + "/api/auth/login",
                      "-H", "Content-Type: application/json",
                      "--data", json.dumps({"email": email, "password": _pw}),
                      "-c", "/tmp/cd_cookie.txt"])
print("LOGIN:", body.strip()[:150] or err.strip())

# 4. recommend BTCUSDT across multiple timeframes (first call fetches everything live)
payload = json.dumps({"symbol": "BTCUSDT", "timeframes": ["1h", "4h", "1d"]})
t0 = time.time()
body, err, rc = curl(["-X", "POST", BASE + "/api/analysis/recommend",
                      "-H", "Content-Type: application/json",
                      "--data", payload,
                      "-b", "/tmp/cd_cookie.txt"])
dt = time.time() - t0
print(f"\nRECOMMEND ({dt:.1f}s): rc={rc}")

if rc != 0 or not body.strip():
    print("RAW:", (body + "\n" + err)[:800])
else:
    d = json.loads(body)
    c = d.get("composite", {})
    print("COMPOSITE:", {k: c.get(k) for k in ("direction", "score", "confidence")})
    if c.get("data_errors"):
        print("DATA_ERRORS:", json.dumps(c["data_errors"], indent=2))
    # per-timeframe engines present? (timeframes is a LIST of {interval, classic, ict})
    for t in (d.get("timeframes") or []):
        tf = t.get("interval")
        brief = {}
        for e in ("classic", "sentiment", "ict"):
            v = t.get(e)
            if isinstance(v, dict):
                brief[e] = (v.get("direction"), round(v.get("score"), 1))
            elif isinstance(v, list):
                brief[e] = [(f["name"], f["value"]) for f in v][:3]
        print(f"  TF {tf}: {brief}")
    # macro relations? (top-level in response)
    m = d.get("macro") or {}
    f = m.get("factors", [])
    if not f:
        print("MACRO: (no factors — see data_errors)")
    else:
        for fac in f:
            corr = fac.get("corr_90d")
            print(f"  - {fac['name']}: value={fac.get('value')} "
                  f"corr90={'-' if corr is None else round(corr,3)} :: {str(fac.get('note'))[:76]}")
    open("/tmp/cd_e2e_final.json", "w").write(body)
