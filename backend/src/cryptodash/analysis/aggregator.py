"""Multi-engine / multi-timeframe aggregation.

The headline recommendation is a *conviction-weighted* blend across timeframes
where higher timeframes dominate (a 4h/1d long with 5m chop underneath is still
long), and engines are blended by confidence rather than raw score so thin-data
signals can't drown out well-supported ones.

Also computes the cross-timeframe **alignment ratio** — a simple, powerful
accuracy proxy: the fraction of analysed timeframes pointing the same way as the
net composite (1.0 = all timeframes agree → highest-confidence signal).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# TF hierarchy weight: larger = more structural. Sum doesn't need to be 1.
TF_WEIGHTS: dict[str, float] = {
    "5m": 0.35,
    "1h": 0.70,
    "4h": 1.20,
    "1d": 2.00,
    "1w": 3.00,
}

ENGINE_WEIGHTS = {          # relative engine trust inside one timeframe
    "classic": 1.00,        # broadest signal coverage
    "ict": 0.85,            # high precision but sparse
    "sentiment": 0.65,      # crowd positioning
}


@dataclass
class TFSignal:
    interval: str
    direction: str                      # classic+ict net for this TF ('long'/'short'/'neutral')
    score: float                        # -100..+100 TF-level composite
    engines: dict[str, dict] = field(default_factory=dict)   # per-engine result dicts


def composite(tf_signals: list[TFSignal], macro_score: float | None = None,
              macro_confidence: float | None = None) -> dict:
    """Blend all inputs into the final recommendation object for the UI."""
    if not tf_signals and macro_score is None:
        return {
            "direction": "neutral", "score": 0.0, "confidence": 0.0,
            "alignment_ratio": 0.0, "timeframes_analysed": 0,
            "vote_breakdown": [], "note": "no analysable data for the requested symbol/timeframes",
        }

    votes: list[dict] = []
    total_w = 0.0
    weighted_score = 0.0

    for tf in sorted(tf_signals, key=lambda t: TF_WEIGHTS.get(t.interval, 0.5)):
        w_tf = TF_WEIGHTS.get(tf.interval, 0.5)
        # engine-level blend within the timeframe, confidence-weighted
        eng_scores = []
        for name, res in tf.engines.items():
            ew = ENGINE_WEIGHTS.get(name, 0.5) * max(res["confidence"], 0.15)
            eng_scores.append((ew, float(res["score"])))
        if not eng_scores:
            continue
        tw = sum(w for w, _ in eng_scores)
        tf_composite = sum(w * s for w, s in eng_scores) / tw if tw else 0.0

        votes.append({
            "interval": tf.interval,
            "direction": tf.direction or ("long" if tf_composite >= 8 else "short" if tf_composite <= -8 else "neutral"),
            "score": round(tf_composite, 2),
            "weight": round(w_tf, 2),
        })
        weighted_score += w_tf * tf_composite
        total_w += w_tf

    # macro is an independent context vote with its own confidence weight
    if macro_score is not None:
        mw = (macro_confidence or 0.3) * 1.6      # comparable scale to a large TF
        weighted_score += mw * float(macro_score)
        total_w += mw
        votes.append({
            "interval": "macro",
            "direction": "long" if macro_score >= 8 else "short" if macro_score <= -8 else "neutral",
            "score": round(float(macro_score), 2),
            "weight": round(mw, 2),
        })

    net = (weighted_score / total_w) if total_w > 0 else 0.0
    direction = "long" if net >= 8 else "short" if net <= -8 else "neutral"

    # alignment: fraction of votes sharing the composite direction (excluding neutral votes from denominator)
    directional_votes = [v for v in votes if v["direction"] != "neutral"]
    if directional_votes and direction != "neutral":
        aligned = sum(1 for v in directional_votes if v["direction"] == direction)
        alignment_ratio = aligned / len(directional_votes)
    else:
        alignment_ratio = 0.5

    # confidence: magnitude of net, data breadth, and agreement
    breadth = min(1.0, (len(votes)) / 4.0)
    confidence = float(max(0.0, min(1.0,
                  0.4 * min(abs(net) / 60.0, 1.0)
                + 0.25 * breadth
                + 0.35 * (alignment_ratio if direction != "neutral" else 0.3))))

    return {
        "direction": direction,
        "score": round(float(net), 2),
        "confidence": round(confidence, 3),
        "alignment_ratio": round(alignment_ratio, 3),
        "timeframes_analysed": len([v for v in votes if v["interval"] != "macro"]),
        "vote_breakdown": votes,
        "note": _verdict_note(direction, alignment_ratio, confidence),
    }


def _verdict_note(direction: str, alignment: float, confidence: float) -> str:
    strength = "high" if confidence >= 0.65 else "moderate" if confidence >= 0.4 else "low"
    agree = f"{alignment * 100:.0f}% of directional timeframes align"
    return {
        "long": f"Bullish composite — {agree} ({strength} conviction).",
        "short": f"Bearish composite — {agree} ({strength} conviction).",
        "neutral": "No structural edge right now — timeframes conflict; stand aside or use tight invalidations.",
    }[direction]
