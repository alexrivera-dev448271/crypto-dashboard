// ── shared UI helpers ────────────────────────────────────────────────

export function dirColor(dir: string): string {
  if (dir === "long") return "#34d399"; // green
  if (dir === "short") return "#f87171"; // red
  return "#a0a6b8"; // neutral grey
}

export function dirLabel(dir: string): string {
  if (dir === "long") return "LONG";
  if (dir === "short") return "SHORT";
  return "NEUTRAL";
}

export function fmtPct(v: number | null, digits = 1): string {
  if (v == null || Number.isNaN(v)) return "—";
  const s = v > 0 ? "+" : "";
  return `${s}${v.toFixed(digits)}%`;
}

export function fmtNum(v: number | null, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1e6) return `${(v / 1e9).toFixed(2)}B`;
  if (Math.abs(v) >= 1e4) return v.toLocaleString(undefined, { maximumFractionDigits: digits });
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function scoreBar(score: number): { width: number; left: number } {
  const clamped = Math.max(-100, Math.min(100, score));
  if (clamped >= 0) return { width: clamped / 2, left: 50 };
  return { width: -clamped / 2, left: 50 + clamped / 2 };
}
