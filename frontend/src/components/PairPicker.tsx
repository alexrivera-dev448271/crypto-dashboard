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
  /** Currently committed symbol (what will be analysed if no typed pair is present). */
  sym: string;
  tfs: string[];
  /** Free-text draft in the custom box. Owned by the parent so Analyze can use it live. */
  draft: string;
  onDraftChange: (value: string) => void;
  /** Dropdown selection → commit that symbol and drop any stale typed pair. Does not auto-analyse. */
  onSelectPair: (symbol: string) => void;
  /** Analyse now — parent prefers the typed draft over `sym` when present. */
  onSubmitPair: () => void;
  onTfs: (tfs: string[]) => void;
}

/**
 * Presentational pair/timeframe picker. The typed text (`draft`) and committed
 * symbol live in the parent so the Analyze button can act on exactly what the
 * user last typed — even without pressing Enter first.
 */
export function PairPicker({ sym, tfs, draft, onDraftChange, onSelectPair, onSubmitPair, onTfs }: Props) {
  const known = PRESETS.some(([, code]) => code === sym);

  function toggleTf(tf: string) {
    const next = tfs.includes(tf) ? tfs.filter((t) => t !== tf) : [...tfs, tf];
    onTfs(next.length ? ALL_INTERVALS.filter((a) => next.includes(a)) : ["1h"]);
  }

  return (
    <div className="picker">
      {/* The dropdown always reflects the committed symbol; a non-preset symbol is added as its own option. */}
      <select value={draft.trim() ? "" : sym} onChange={(e) => onSelectPair(e.target.value)}>
        {PRESETS.map(([label, code]) => (
          <option key={code} value={code}>
            {label}
          </option>
        ))}
        {!known && draft.trim() === "" && <option value={sym}>{sym}</option>}
      </select>

      <input
        className="custom-input"
        placeholder="or type any pair… (e.g. ARBUSDT)"
        value={draft}
        onChange={(e) => onDraftChange(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && onSubmitPair()}
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
