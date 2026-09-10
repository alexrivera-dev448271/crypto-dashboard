import { useEffect, useState } from "react";
import { api, Candle } from "../api";

interface Props {
  symbol: string;
  interval: string;
}

export function CandleChart({ symbol, interval }: Props) {
  const [candles, setCandles] = useState<Candle[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.candles(symbol, interval, 180)
      .then((r) => !cancelled && setCandles(r.candles))
      .catch((e: Error) => !cancelled && setErr(e.message));
    return () => {
      cancelled = true;
    };
  }, [symbol, interval]);

  const w = 720;
  const h = 240;

  if (err) return <div className="card chart-card"><h3>Chart</h3><p className="warn small">chart unavailable: {err}</p></div>;
  if (!candles || candles.length === 0)
    return (
      <div className="card chart-card">
        <h3>Chart · {symbol} · {interval}</h3>
        <p className="muted small">loading…</p>
      </div>
    );

  const pad = 8;
  let lo = Infinity;
  let hi = -Infinity;
  for (const c of candles) {
    if (c.l < lo) lo = c.l;
    if (c.h > hi) hi = c.h;
  }
  const span = hi - lo || 1;
  const cw = (w - pad * 2) / candles.length;

  const y = (p: number) => pad + (hi - p) / span * (h - pad * 2);

  return (
    <div className="card chart-card">
      <h3>Chart · {symbol} · {interval}</h3>
      <svg viewBox={`0 0 ${w} ${h}`} className="candles" preserveAspectRatio="none">
        {[0.25, 0.5, 0.75].map((t) => (
          <line key={t} x1={pad} x2={w - pad} y1={pad + t * (h - pad * 2)} y2={pad + t * (h - pad * 2)} className="grid-line" />
        ))}
        {candles.map((c, i) => {
          const up = c.c >= c.o;
          const x = pad + i * cw;
          return (
            <g key={c.t_ms}>
              <line x1={x + cw / 2} x2={x + cw / 2} y1={y(c.h)} y2={y(c.l)} stroke={up ? "#34d399" : "#f87171"} strokeWidth="1" />
              <rect
                x={x + Math.max(0.5, cw * 0.15)}
                width={Math.max(1, cw * 0.7)}
                y={y(Math.max(c.o, c.c))}
                height={Math.max(1, Math.abs(y(c.o) - y(c.c)))}
                fill={up ? "#34d399" : "#f87171"}
              />
            </g>
          );
        })}
      </svg>
      <div className="chart-scale">
        <span>{hi.toFixed(2)}</span>
        <span>{lo.toFixed(2)}</span>
      </div>
    </div>
  );
}
