import { useEffect, useState } from "react";

const PRESETS: [string, string][] = [
  ["BTC/USDT", "BTCUSDT"],
  ["ETH/USDT", "ETHUSDT"],
  ["SOL/USDT", "SOLUSDT"],
  ["XRP/USDT", "XRPUSDT"],
  ["DOGE/USDT", "DOGEUSDT"],
  ["LTC/USDT", "LTCUSDT"],
  ["ADA/USDT", "ADAUSDT"],
  ["AVAX/USDT", "AVAXUSDT"],
  ["LINK/USDT", "LINKUSDT"],
  ["BCH/USDT", "BCHUSDT"],
];

const ALL_INTERVALS = ["5m", "1h", "4h", "1d"];

interface Props {
  sym: string;
  tfs: string[];
  onSym: (s: string) => void;
  onTfs: (t: string[]) => void;
}

export function PairPicker({ sym, tfs, onSym, onTfs }: Props) {
  const [custom, setCustom] = useState("");
  const [validPairs, setValidPairs] = useState<Set<string>>(new Set(PRESETS.map(([, s]) => s)));

  // discover extra liquid pairs from Binance (best-effort; presets always available offline)
  useEffect(() => {
    let cancelled = false;
    fetch("/api/analysis/symbols", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((j: unknown) => {
        if (cancelled || !j) return;
        const obj = j as { presets?: Record<string, string[]> };
        const arr = obj?.presets && Array.isArray(obj.presets["major_pairs"]) ? obj.presets["major_pairs"] : [];
        setValidPairs(new Set([...PRESETS.map(([, s]) => s), ...arr]));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  function submitCustom() {
    const cleaned = custom.trim().toUpperCase();
    if (!cleaned) return;
    // normalize: BTC/USDT → BTCUSDT, btcusdt → BTCUSDT
    let normalized = cleaned.replace(/[^A-Z0-9]/g, "");
    onSym(normalized);
    setCustom("");
  }

  function toggleTf(tf: string) {
    const next = tfs.includes(tf) ? tfs.filter((t) => t !== tf) : [...tfs, tf];
    onTfs(next.length ? ALL_INTERVALS.filter((a) => next.includes(a)) : ["1h"]);
  }

  const known = PRESETS.some(([, s]) => s === sym);

  return (
    <div className="picker">
      <select value={known || !custom ? sym : ""} onChange={(e) => onSym(e.target.value)}>
        {PRESETS.map(([label, code]) => (
          <option key={code} value={code}>{label}</option>
        ))}
        {!known && <option value={sym}>{sym}</option>}
      </select>

      <input
        className="custom-input"
        placeholder="or type any pair… (e.g. ARBUSDT)"
        value={custom}
        onChange={(e) => setCustom(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submitCustom()}
      />

      <div className="tf-group">
        {ALL_INTERVALS.map((tf) => (
          <button key={tf} className={`chip ${tfs.includes(tf) ? "on" : ""}`} onClick={() => toggleTf(tf)}>
            {tf}
          </button>
        ))}
      </div>
    </div>
  );
}
