import { MacroPayload } from "../api";
import { dirColor, fmtNum, fmtPct } from "../ui";

interface Props {
  macro: MacroPayload;
}

export function MacroPanel({ macro }: Props) {
  return (
    <div className="card macro-card">
      <header>
        <h3>Macro relations</h3>
        <span className={`pill ${macro.direction}`} style={{ color: "var(--bg)" }}>
          {macro.score >= 0 ? "+" : ""}
          {Math.round(macro.score)} · conf {Math.round(macro.confidence * 100)}%
        </span>
      </header>

      <div className="macro-grid">
        {macro.assets.map((a) => (
          <div key={a.name} className={`asset ${a.corr_90d != null && a.change_30d_pct != null ? "corr" : ""}`}>
            <div className="asset-head">
              <b>{label(a.name)}</b>
              {a.current != null && (
                <span className="mono">
                  {fmtAssetValue(a)}
                  {a.name === "gold" && macro.live_gold_spot != null && (
                    <>
                      {" · "}live{" "}
                      <span title="Keyless live XAU/USD spot (cross-check)">{fmtNum(macro.live_gold_spot, 2)}</span>
                    </>
                  )}
                </span>
              )}
            </div>

            <div className="asset-metrics">
              <Metric label="30d" value={fmtPct(a.change_30d_pct)} />
              <Metric label="corr 90d" value={a.corr_90d != null ? a.corr_90d.toFixed(2) : "—"} hint={a.name === "m2_usd" || a.name === "m2_sl" ? "vs BTC" : undefined} />
              {a.spark && <Sparkline data={a.spark} />}
            </div>

            {Object.entries(a.extra).map(([k, v]) => (
              <Metric key={k} label={k.replace(/_/g, " ")} value={typeof v === "number" ? v.toFixed(2) : String(v)} />
            ))}
          </div>
        ))}
      </div>

      {macro.factors.length > 0 && (
        <ul className="factors">
          {macro.factors.map((f) => (
            <li key={f.name}>
              <span>{label(f.name)}</span>
              <em>{f.note}</em>
            </li>
          ))}
        </ul>
      )}

      {macro.note && <p className="note small">{macro.note}</p>}
    </div>
  );
}

// FRED M2SL is in $billions; WALCL (Fed balance sheet) is in $millions. Render both as a
// compact USD amount so the raw magnitude never reads like a price.
function fmtAssetValue(a: MacroPayload["assets"][number]): string {
  if (a.current == null) return "";
  const billions = a.name === "m2_usd" ? a.current : a.current / 1e3;
  const trillions = billions / 1e3;
  if (Math.abs(trillions) >= 0.95 && Math.abs(billions) > 1) return `$${trillions.toFixed(2)}T`;
  if (Math.abs(billions) >= 1) return `$${billions.toFixed(1)}B`;
  return fmtNum(a.current, 2);
}

function label(name: string): string {
  const map: Record<string, string> = {
    m2_usd: "M2 money supply (USD)",
    m2_sl: "M2 money supply",
    gold: "Gold",
    brent_crude: "Brent crude oil",
    bitcoin: "Bitcoin (BTC/USDT)",
    fed_assets: "Central-bank liquidity (Fed balance sheet, WALCL)",
    global_liquidity: "Global liquidity index (legacy BIS series)",
  };
  return map[name] ?? name;
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <span className="metric">
      <i>{label}{hint ? ` (${hint})` : ""}</i>
      <b>{value}</b>
    </span>
  );
}

function Sparkline({ data }: { data: number[] }) {
  const w = 120;
  const h = 34;
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / span) * h}`).join(" ");
  const up = data[data.length - 1] >= data[0];
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={up ? "#34d399" : "#f87171"} strokeWidth="2" />
    </svg>
  );
}
