import { useCallback, useEffect, useRef, useState } from "react";
import { api, RecommendationResponse, ApiError } from "./api";
import { AuthScreen } from "./components/AuthScreen";
import { PairPicker } from "./components/PairPicker";
import { CompositeCard } from "./components/CompositeCard";
import { TfGrid } from "./components/TfGrid";
import { MacroPanel } from "./components/MacroPanel";
import { SentimentBar } from "./components/SentimentBar";
import { CandleChart } from "./components/CandleChart";
import { HistoryStrip } from "./components/HistoryStrip";
import { SettingsModal } from "./components/SettingsModal";
import { ErrorBoundary } from "./components/ErrorBoundary";

type Me = { id: number; email: string; display_name: string } | null;

const AUTO_KEY = "cd.auto_refresh_sec";   // 0 = off, N = seconds between full re-analyses

function loadAuto(): number {
  const v = Number(localStorage.getItem(AUTO_KEY) ?? "60");
  return Math.max(0, Math.min(3600, isFinite(v) ? v : 60));
}

export function App() {
  const [me, setMe] = useState<Me>(undefined as unknown as Me); // undefined = loading
  const [sym, setSym] = useState("BTCUSDT");
  const [tfs, setTfs] = useState<string[]>(["1h", "4h", "1d"]);
  const [result, setResult] = useState<RecommendationResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [autoSec, setAutoSecState] = useState<number>(loadAuto);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [candleTick, setCandleTick] = useState(0);            // bump to refetch candles/macro panels
  const reqSeq = useRef(0);
  const busyRef = useRef(false);   // live in-flight flag (immune to stale closures)

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
  }, []);

  const run = useCallback(async (symbol: string, intervals: string[], fromAuto = false) => {
    if (!me || busyRef.current) return;
    busyRef.current = true;
    const seq = ++reqSeq.current;
    if (!fromAuto) setBusy(true);
    setError(null);
    try {
      const r = await api.recommend(symbol, intervals);
      if (seq === reqSeq.current) {
        setResult(r);
        setCandleTick((t) => t + 1);
        setUpdatedAt(new Date());
      }
    } catch (e) {
      if (seq === reqSeq.current)
        setError(e instanceof ApiError ? e.message : "Analysis failed — check the pair symbol or try again.");
    } finally {
      busyRef.current = false;
      if (!fromAuto) setBusy(false);
    }
  }, [me]);

  // full re-analysis on interval (auto refresh of signals)
  useEffect(() => {
    if (!result || autoSec <= 0 || !me) return;
    const id = window.setInterval(() => {
      if (!document.hidden && !busyRef.current) run(sym, tfs, true);
    }, autoSec * 1000);
    return () => window.clearInterval(id);
  }, [result, autoSec, me, sym, tfs, run]);

  // fast chart refresh: refetch candles (and macro history panels) more often than full analysis
  useEffect(() => {
    if (!result || !me) return;
    const id = window.setInterval(() => {
      if (!document.hidden && result && autoSec > 0) setCandleTick((t) => t + 1);
    }, Math.min(30, Math.max(5, autoSec / 2)) * 1000);
    return () => window.clearInterval(id);
  }, [result, me, autoSec]);

  function setAuto(sec: number) {
    setAutoSecState(sec);
    localStorage.setItem(AUTO_KEY, String(sec));
  }

  if (me === undefined) return <div className="boot">loading…</div>;
  if (!me) return <AuthScreen onAuthed={(u) => setMe(u)} />;

  const candleKey = result ? `${sym}:${tfs[0] || "1h"}:${candleTick}` : "";

  return (
    <ErrorBoundary label="Dashboard">
      <div className="app">
        <header className="topbar">
          <div className="brand">
            <span className="brand-dot" /> CryptoDash
            <span className="sub">multi-timeframe signal desk</span>
          </div>
          <div className="top-actions">
            <label className="auto-ctl small muted" title="Auto refresh — re-run analysis every N seconds (0 = off)">
              auto{" "}
              <select value={String(autoSec)} onChange={(e) => setAuto(Number(e.target.value))}>
                <option value="0">off</option>
                <option value="30">30s</option>
                <option value="60">1m</option>
                <option value="120">2m</option>
                <option value="300">5m</option>
              </select>
            </label>
            {updatedAt && (
              <span className="small muted updated" title={updatedAt.toLocaleTimeString()}>
                ● live · {updatedAt.toLocaleTimeString()}
              </span>
            )}
            <button className="ghost" onClick={() => setSettingsOpen(true)}>settings</button>
            <span className="user-chip">{me.display_name}</span>
            <button
              className="ghost"
              onClick={async () => {
                await api.logout();
                setMe(null);
              }}
            >
              sign out
            </button>
          </div>
        </header>

        <section className="controls">
          <PairPicker sym={sym} tfs={tfs} onSym={setSym} onTfs={setTfs} />
          <button className="primary" disabled={busy || !me} onClick={() => run(sym, tfs)}>
            {busy ? "analysing…" : result ? "re-analyse" : "analyse"}
          </button>
        </section>

        {error && <div className="alert">{error}</div>}

        {result && (
          <>
            <ErrorBoundary label="Composite verdict">
              <CompositeCard composite={result.composite} />
            </ErrorBoundary>
            <div className="row">
              <ErrorBoundary label="Price chart">
                <CandleChart symbol={sym} interval={tfs[0] || "1h"} key={candleKey} />
              </ErrorBoundary>
              <ErrorBoundary label="Sentiment panel">
                <SentimentBar sentiment={result.sentiment} fngNow={result.fear_greed_now} />
              </ErrorBoundary>
            </div>
            <ErrorBoundary label="Timeframe grid">
              <TfGrid timeframes={result.timeframes} />
            </ErrorBoundary>
            {result.macro && (
              <ErrorBoundary label="Macro relations panel">
                <MacroPanel macro={result.macro} />
              </ErrorBoundary>
            )}
          </>
        )}

        {!result && !busy && !error && (
          <div className="card empty-hint">
            Pick a pair and timeframes, then hit <b>analyse</b>. Signals blend classic TA, ICT/smart-money and
            sentiment across every timeframe, plus macro relations to M2 money supply, gold, Brent crude and Bitcoin.
          </div>
        )}

        <ErrorBoundary label="Recent runs">
          <HistoryStrip symbol={sym} onPick={(s) => setSym(s)} />
        </ErrorBoundary>

        <footer className="foot">
          Educational signal engine — not financial advice. Data: Binance · Yahoo · alternative.me
          {result && ` · last run ${(result.elapsed_ms / 1000).toFixed(1)}s`}
        </footer>

        {settingsOpen && (
          <SettingsModal onClose={() => setSettingsOpen(false)} onSaved={() => run(sym, tfs)} />
        )}
      </div>
    </ErrorBoundary>
  );
}
