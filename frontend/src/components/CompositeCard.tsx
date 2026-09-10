import { RecommendationResponse } from "../api";
import { dirColor, dirLabel, scoreBar } from "../ui";

interface Props {
  composite: RecommendationResponse["composite"];
}

export function CompositeCard({ composite }: Props) {
  const bar = scoreBar(composite.score);
  const confPct = Math.round(composite.confidence * 100);
  const aligned = Math.round(composite.alignment_ratio * 100);

  return (
    <div className="card composite" style={{ borderColor: dirColor(composite.direction) }}>
      <div className="composite-head">
        <div>
          <div className="muted small">{composite.symbol}</div>
          <div className="verdict" style={{ color: dirColor(composite.direction) }}>
            {dirLabel(composite.direction)}
            <span className="score-pill" style={{ background: dirColor(composite.direction) }}>{Math.round(composite.score)}</span>
          </div>
        </div>

        <div className="conf-block">
          <div className="muted small">confidence</div>
          <div className="meter"><i style={{ width: `${confPct}%` }} /></div>
          <div className="small muted">{confPct}% · {composite.timeframes_analysed} timeframes agree {aligned}%</div>
        </div>

        <div className="vote-block">
          <div className="muted small">votes</div>
          <ul className="votes">
            {composite.vote_breakdown.map((v) => (
              <li key={v.interval}>
                <span className={`dot ${v.direction}`} />
                <b>{v.interval}</b> {dirLabel(v.direction)} ({Math.round(v.score)})
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="scale">
        <span>SHORT</span>
        <div className="track">
          <i style={{ ...bar, background: dirColor(composite.direction) }} />
          <em style={{ left: "50%" }} />
        </div>
        <span>LONG</span>
      </div>

      {composite.note && <p className="note">{composite.note}</p>}

      {composite.data_errors && composite.data_errors.length > 0 && (
        <p className="warn small">partial data: {composite.data_errors.join("; ")}</p>
      )}
    </div>
  );
}
