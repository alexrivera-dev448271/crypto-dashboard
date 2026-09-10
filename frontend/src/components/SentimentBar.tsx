import { EngineResult } from "../api";
import { dirColor, scoreBar } from "../ui";

interface Props {
  sentiment: EngineResult;
  fngNow: number | null;
}

export function SentimentBar({ sentiment, fngNow }: Props) {
  const bar = scoreBar(sentiment.score);
  return (
    <div className="card sent-card">
      <h3>Sentiment &amp; positioning</h3>
      <div className="sent-row">
        <div>
          <div className="muted small">crowd index now {fngNow != null ? `· F&G ${Math.round(fngNow)}` : ""}</div>
          <b style={{ color: dirColor(sentiment.direction), fontSize: 26 }}>
            {Math.round(sentiment.score)}
          </b>
        </div>
      </div>

      <div className="track">
        <i style={{ ...bar, background: dirColor(sentiment.direction) }} />
        <em style={{ left: "50%" }} />
      </div>

      <ul className="factors sent-factors">
        {sentiment.factors.map((f) => (
          <li key={f.name}>
            <span>{f.name}</span>
            {f.value != null && <b>{Math.round(f.value)}</b>}
            {f.note && <em>{f.note}</em>}
          </li>
        ))}
      </ul>

      <p className="note small">
        Sentiment is contrarian by design: extremes (euphoria / capitulation) fade. It blends in as a low-weight vote so it
        confirms or warns, never overrides structure.
      </p>
    </div>
  );
}
