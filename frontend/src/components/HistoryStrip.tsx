import { useEffect, useState } from "react";
import { api } from "../api";

interface HistItem {
  symbol: string;
  interval: string;
  engine: string;
  direction: string;
  score: number;
  confidence: number;
  created_at: string;
}

interface Props {
  symbol?: string;
  onPick: (s: string) => void;
}

export function HistoryStrip({ onPick }: Props) {
  const [items, setItems] = useState<HistItem[]>([]);

  useEffect(() => {
    api.history()
      .then((r) => setItems(r.items))
      .catch(() => undefined);
  }, []);

  if (items.length === 0) return null;

  // dedupe to the most recent run per symbol
  const latest = new Map<string, HistItem>();
  for (const it of items) {
    if (!latest.has(it.symbol)) latest.set(it.symbol, it);
  }
  const rows = [...latest.values()];

  return (
    <div className="card history">
      <h3>Recent runs</h3>
      <div className="hist-chips">
        {rows.slice(0, 12).map((it) => (
          <button key={it.symbol} className={`chip hist ${it.direction}`} onClick={() => onPick(it.symbol)}>
            {it.symbol.replace(/USDT$/, "/")} {Math.round(it.score)}
          </button>
        ))}
      </div>
    </div>
  );
}
