"""
event_timeline.py — exit-after-event catalysts (expansion plan A8).

The manifesto: "exits after audits, acquisitions, layoffs, leadership changes,
payer terminations are high-signal." A departure that follows a catalyst is a
warmer relator lead than a cold one. We can't yet see person departures (Model B
is gated on people data), so A8's deliverable today is the ORG-LEVEL half: a
unified event timeline and a recency-weighted catalyst score that marks which
organizations are "hot" right now. When people data lands, that org score
multiplies into the B2 propensity (the weights table already anticipates it).

  build_event_timeline   merge the event sources we already produce — WARN
                         layoffs, CHOW owner entry/exit (A7), enforcement actions
                         (case DB), retaliation/qui-tam dockets — into one
                         org-keyed table: org_node_id, event_type, event_date,
                         source, detail. Every source is optional.
  org_catalyst_score     per org: a 0–1 score that decays with months since the
                         most recent catalyst (recent layoff/CHOW/enforcement =
                         hot), plus the most-recent event and the event count.

These outputs are leads for human review, not conclusions; a catalyst raises
*receptiveness to outreach*, it is never evidence of fraud.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

CATALYST_HALFLIFE_MONTHS = 12.0       # a catalyst's heat halves every ~year
CATALYST_WINDOW_MONTHS = 36.0         # older than this contributes ~nothing

TIMELINE_COLS = ["org_node_id", "event_type", "event_date", "source", "detail"]


def _norm(df: pd.DataFrame | None, org_col: str, date_col: str,
          event_type: str, source: str, detail_col: str | None) -> pd.DataFrame:
    if df is None or not len(df) or org_col not in df.columns:
        return pd.DataFrame(columns=TIMELINE_COLS)
    out = pd.DataFrame({
        "org_node_id": df[org_col].astype(str),
        "event_type": event_type,
        "event_date": df[date_col].astype(str) if date_col in df.columns else "",
        "source": source,
        "detail": (df[detail_col].astype(str)
                   if detail_col and detail_col in df.columns else ""),
    })
    return out[out["org_node_id"].fillna("").ne("") & out["org_node_id"].ne("nan")]


def build_event_timeline(warn_matched: pd.DataFrame | None = None,
                         chow_events: pd.DataFrame | None = None,
                         enforcement_matched: pd.DataFrame | None = None,
                         docket_events: pd.DataFrame | None = None) -> pd.DataFrame:
    """Merge the available event sources into one org-keyed timeline.

    Expected (lenient) shapes:
      warn_matched        org_node_id, notice_date|NOTICE_DATE  (WARN monitor out)
      chow_events         org_node_id, event_type, event_date   (ownership_churn)
      enforcement_matched org_node_id, announced_date           (case DB joined to orgs)
      docket_events       org_node_id, date_filed               (docket monitor)
    """
    parts = [
        _norm(warn_matched, "org_node_id",
              "notice_date" if warn_matched is not None
              and "notice_date" in getattr(warn_matched, "columns", [])
              else "NOTICE_DATE", "layoff", "warn", "company"
              if warn_matched is not None
              and "company" in getattr(warn_matched, "columns", []) else "COMPANY"),
        _chow(chow_events),
        _norm(enforcement_matched, "org_node_id", "announced_date",
              "enforcement", "case_db", "defendant_name"),
        _norm(docket_events, "org_node_id", "date_filed",
              "docket", "courtlistener", "case_name"),
    ]
    out = pd.concat([p for p in parts if len(p)], ignore_index=True) \
        if any(len(p) for p in parts) else pd.DataFrame(columns=TIMELINE_COLS)
    return out.reset_index(drop=True)


def _chow(chow_events: pd.DataFrame | None) -> pd.DataFrame:
    """CHOW owner entries/exits → timeline rows ('ownership_change')."""
    if chow_events is None or not len(chow_events):
        return pd.DataFrame(columns=TIMELINE_COLS)
    c = chow_events.copy()
    return pd.DataFrame({
        "org_node_id": c["org_node_id"].astype(str),
        "event_type": "ownership_change",
        "event_date": c["event_date"].astype(str),
        "source": "chow",
        "detail": (c["event_type"].astype(str) + ":" + c.get(
            "owner_key", pd.Series("", index=c.index)).astype(str)),
    })


def _months_between(later: str, earlier: pd.Series) -> pd.Series:
    ref = pd.to_datetime(later, errors="coerce")
    dates = pd.to_datetime(earlier, errors="coerce")
    return (ref - dates).dt.days / 30.44


def org_catalyst_score(timeline: pd.DataFrame, as_of: str | None = None
                       ) -> pd.DataFrame:
    """Per-org recency-weighted catalyst score in [0, 1].

    Each event contributes ``0.5 ** (months_ago / halflife)`` (clipped to a
    36-month window); the org score is the max over its events (one strong recent
    catalyst is enough to be "hot"). Returns org_node_id, catalyst_score,
    n_events, most_recent_event, most_recent_date.
    """
    cols = ["org_node_id", "catalyst_score", "n_events",
            "most_recent_event", "most_recent_date"]
    if timeline is None or not len(timeline):
        return pd.DataFrame(columns=cols)
    as_of = as_of or datetime.utcnow().strftime("%Y-%m-%d")
    t = timeline.copy()
    t["months_ago"] = _months_between(as_of, t["event_date"])
    t = t[t["months_ago"].notna() & (t["months_ago"] >= 0)]
    t["weight"] = np.where(t["months_ago"] <= CATALYST_WINDOW_MONTHS,
                           0.5 ** (t["months_ago"] / CATALYST_HALFLIFE_MONTHS), 0.0)

    rows = []
    for org, g in t.groupby("org_node_id"):
        recent = g.loc[g["event_date"].idxmax()]
        rows.append({"org_node_id": org,
                     "catalyst_score": round(float(g["weight"].max()), 4),
                     "n_events": int(len(g)),
                     "most_recent_event": str(recent["event_type"]),
                     "most_recent_date": str(recent["event_date"])})
    return pd.DataFrame(rows, columns=cols).sort_values(
        "catalyst_score", ascending=False).reset_index(drop=True)


def apply_catalyst_to_propensity(person_scores: pd.DataFrame,
                                 catalyst: pd.DataFrame,
                                 max_boost: float = 0.25) -> pd.DataFrame:
    """Hook (used once people data exists): scale a person's B2 propensity by
    their employer's catalyst heat — a departure right after a layoff/CHOW/
    enforcement event is warmer. Adds ``catalyst_score`` and
    ``propensity_with_catalyst``; leaves the base score intact for audit.

    Org-level only; introduces no person-level signal (guardrails unchanged).
    """
    if "org_node_id" not in person_scores.columns or "propensity" not in person_scores.columns:
        return person_scores
    cat = dict(zip(catalyst["org_node_id"].astype(str),
                   catalyst["catalyst_score"])) if len(catalyst) else {}
    out = person_scores.copy()
    out["catalyst_score"] = out["org_node_id"].astype(str).map(cat).fillna(0.0)
    out["propensity_with_catalyst"] = (
        out["propensity"] * (1.0 + max_boost * out["catalyst_score"])).clip(upper=1.0)
    return out
