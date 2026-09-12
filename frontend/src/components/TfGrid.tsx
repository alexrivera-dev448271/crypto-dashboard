import { RecommendationResponse } from "../api";
import { dirColor, dirLabel, fmtPct, scoreBar } from "../ui";

interface Props {
  timeframes: RecommendationResponse["timeframes"];
  symbol?: string;
}

export function TfGrid({ timeframes, symbol }: Props) {
  return (
    <div className="tf-grid">
      {timeframes.map((tf) => (
        <article key={tf.interval} className={`card tf-card dir-${tf.classic.direction}`}>
          <header>
            <h3>{symbol ? `${symbol} · ` : ""}{tf.interval.toUpperCase()}</h3>
            <span className={`pill ${tf.classic.direction}`} style={{ color: "var(--bg)" }}>
              {dirLabel(tf.classic.direction)} {Math.round(tf.classic.score)}
            </span>
          </header>

          {(["classic", "ict"] as const).map((eng) => {
            const e = tf[eng];
            const bar = scoreBar(e.score);
            return (
              <section key={eng} className="engine">
                <div className="engine-head">
                  <span>{eng === "classic" ? "Classic TA" : "ICT / smart money"}</span>
                  <b style={{ color: dirColor(e.direction) }}>{Math.round(e.score)}</b>
                </div>
                <div className="bar-cap">{eng === "classic" ? "classic technical score (short ⇄ long)" : "ICT / smart-money score (short ⇄ long)"}</div>
                <div className="track mini">
                  <i style={{ ...bar, background: dirColor(e.direction) }} />
                </div>
                <ul className="factors">
                  {e.factors.slice(0, 6).map((f) => (
                    <li key={f.name}>
                      <span className={`fb ${Math.abs(f.bias) > 0.15 ? (f.bias > 0 ? "bull" : "bear") : ""}`}>{f.name}</span>
                      {f.note && <em>{f.note}</em>}
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}

          <footer className="tf-foot">
            conf <b>{Math.round(Math.min(tf.classic.confidence, tf.ict.confidence) * 100)}%</b>
            {" · "}
            {Math.abs(tf.classic.score - tf.ict.score) <= 25 ? "engines agree" : "engines diverge"}
          </footer>
        </article>
      ))}
    </div>
  );
}
