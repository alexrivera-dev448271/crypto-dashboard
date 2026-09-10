// Typed API client for CryptoDash backend (same-origin; cookies ride along)

export interface Factor {
  name: string;
  value: number | null;
  bias: number; // -1..+1
  note: string;
}

export interface EngineResult {
  engine: string;
  direction: "long" | "short" | "neutral";
  score: number; // -100..+100
  confidence: number; // 0..1
  factors: Factor[];
}

export interface VoteBreakdown {
  interval: string;
  direction: string;
  score: number;
  weight: number;
}

export interface MacroAssetPayload {
  name: string;
  current: number | null;
  change_30d_pct: number | null;
  corr_90d: number | null;
  corr_1y: number | null;
  extra: Record<string, number>;
  spark: number[] | null;
}

export interface MacroPayload {
  direction: string;
  score: number;
  confidence: number;
  factors: Factor[];
  assets: MacroAssetPayload[];
  live_gold_spot?: number | null;
  note?: string;
}

export interface TfBlock {
  interval: string;
  classic: EngineResult;
  ict: EngineResult;
}

export interface RecommendationResponse {
  composite: {
    direction: "long" | "short" | "neutral";
    score: number;
    confidence: number;
    alignment_ratio: number;
    timeframes_analysed: number;
    vote_breakdown: VoteBreakdown[];
    note: string;
    symbol: string;
    generated_at: string;
    data_errors?: string[];
  };
  timeframes: TfBlock[];
  sentiment: EngineResult;
  macro: MacroPayload | null;
  fear_greed_now: number | null;
  elapsed_ms: number;
}

export interface Candle {
  t_ms: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v?: number;
}

class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  // auth
  register: (email: string, password: string) =>
    request<{ email: string }>("/api/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),
  login: (email: string, password: string) =>
    request<{ display_name: string } | null>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  me: () => request<{ id: number; email: string; display_name: string } | null>("/api/auth/me"),

  // analysis
  recommend: (symbol: string, timeframes?: string[]) =>
    request<RecommendationResponse>("/api/analysis/recommend", {
      method: "POST",
      body: JSON.stringify({ symbol, timeframes }),
    }),
  symbols: () =>
    request<{ presets: Record<string, string[] | Record<string, string>>; intervals: string[]; defaults: string[] }>("/api/analysis/symbols"),
  history: (symbol?: string) =>
    request<{ items: { symbol: string; interval: string; engine: string; direction: string; score: number; confidence: number; created_at: string }[] }>(
      "/api/analysis/history" + (symbol ? `?symbol=${encodeURIComponent(symbol)}` : "")
    ),

  // market data
  candles: (symbol: string, interval: string, limit = 200) =>
    request<{ symbol: string; interval: string; candles: Candle[] }>(
      `/api/market/candles?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=${limit}`
    ),
  macro: () => request<Record<string, { current: number | null; history?: [string, number][] } | null>>("/api/market/macro"),

  // settings
  setFredKey: (apiKey: string | null) =>
    request<{ has_fred_key: boolean }>("/api/settings/fred-key", { method: "POST", body: JSON.stringify({ api_key: apiKey }) }),
  secretsStatus: () => request<{ has_fred_key: boolean; server_wide_key_configured: boolean }>("/api/settings/secrets-status"),
};

export { ApiError };
