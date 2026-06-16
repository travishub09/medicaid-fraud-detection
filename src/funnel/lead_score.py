"""
lead_score.py — the composite behavioral lead score (manifesto §20.6).

Combines the platform's signals into one prioritization score for counsel-review
routing, with the manifesto's weights: organization anomaly (Model A), persona
fit (Model B), observed behavior (ListenLayer), intake quality, and recency.
It is a RANKING AID with named drivers — never a fraud determination, never an
accusation about a person. The score orders who to nurture/route first; humans
and counsel decide everything that matters.
"""

from __future__ import annotations

import pandas as pd

# manifesto §20.6: org-anomaly 25 / persona 25 / behavior 25 / intake 15 / recency 10
LEAD_SCORE_WEIGHTS: dict[str, float] = {
    "org_anomaly": 0.25,      # Model A adjusted_prob for the employer
    "persona_fit": 0.25,      # Model B knowledge×role fit (audience-level)
    "behavior": 0.25,         # observed engagement (downloads, return visits, consult)
    "intake_quality": 0.15,   # structured-intake completeness/credibility
    "recency": 0.10,          # departure/engagement recency (the 6–18mo window)
}


def composite_lead_score(leads: pd.DataFrame,
                         weights: dict[str, float] | None = None) -> pd.DataFrame:
    """Per-lead 0–1 composite + the named driver breakdown.

    ``leads`` carries any subset of the weight columns (each 0–1); absent
    components are skipped and the present weights renormalized (so a lead scored
    before intake isn't penalized for missing it). Returns lead_score,
    lead_score_drivers (the weighted contributions), and trust_stage routing.
    NEVER emits a fraud flag.
    """
    w = weights or LEAD_SCORE_WEIGHTS
    present = [c for c in w if c in leads.columns]
    out = leads.copy()
    if not present:
        out["lead_score"] = 0.0
        out["lead_score_drivers"] = ""
        return out

    wsum = sum(w[c] for c in present)
    contrib = {c: leads[c].clip(0, 1).fillna(0.0) * (w[c] / wsum) for c in present}
    out["lead_score"] = sum(contrib.values()).round(4)

    def _drivers(i) -> str:
        parts = sorted(((c, float(contrib[c].iloc[i])) for c in present),
                       key=lambda kv: -kv[1])
        return "; ".join(f"{c} {v:.2f}" for c, v in parts)
    out["lead_score_drivers"] = [_drivers(i) for i in range(len(out))]

    # behavior-derived trust stage drives nurture intensity (not a verdict)
    beh = leads.get("behavior", pd.Series(0.0, index=leads.index)).fillna(0.0)
    out["trust_stage"] = pd.cut(
        beh, [-0.01, 0.25, 0.5, 0.75, 1.01],
        labels=["awareness", "fear", "evidence_eval", "ready_for_counsel"]
    ).astype(str)
    return out
