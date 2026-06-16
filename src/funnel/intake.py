"""
intake.py — confidential intake triage (manifesto §5 intake handoff, §20.10).

Scores a structured intake on the four FCA viability gates so counsel review is
prioritized, capturing ONLY what the manifesto allows at the top of funnel:
role, tenure, current/former, whether knowledge is non-public (the original-source
pivot), prior internal reporting, and timeframe. Hard guardrails:

  * NEVER invite or store PHI or stolen records — a ``phi_present`` /
    ``sensitive_free_text_present`` flag routes the record to secure,
    counsel-reviewed handling and OUT of analytics;
  * the output is a triage/routing aid, never a legal conclusion;
  * privilege from first contact — everything routes through counsel.

Fields consumed (all non-PHI): role_tier (A/B/C), tenure_overlaps_conduct (bool),
is_former (bool), knowledge_is_nonpublic (bool), prior_internal_report (bool),
within_statute (bool), plus the public-disclosure / first-to-file flags Model A/C
already produce.
"""

from __future__ import annotations

import pandas as pd

_ROLE_TIER = {"A": 1.0, "B": 0.75, "C": 0.55, "": 0.5}


def triage_intake(intake: pd.DataFrame) -> pd.DataFrame:
    """Per-intake triage score + the gate flags counsel needs, with PHI routing.

    Returns intake_triage_score (0–1, prioritization only), original_source_posture
    / first_to_file_clear / public_disclosure_risk flags surfaced for counsel,
    route ('secure_counsel' if any sensitive content, else 'standard_review'), and
    triage_reasons. No fraud determination, no PHI retained in the score.
    """
    df = intake.copy()
    n = len(df)

    def col(name, default):
        # fill NaN with the default — a missing flag must not become truthy
        # (NaN.astype(bool) is True), the difference between a clean row and a
        # mis-routed sensitive one.
        if name in df.columns:
            return df[name].fillna(default)
        return pd.Series(default, index=df.index)

    role = col("role_tier", "").astype(str).str.upper().map(_ROLE_TIER).fillna(0.5)
    tenure = col("tenure_overlaps_conduct", False).astype(bool).astype(float)
    former = col("is_former", False).astype(bool).astype(float)
    nonpublic = col("knowledge_is_nonpublic", False).astype(bool).astype(float)
    prior_report = col("prior_internal_report", False).astype(bool).astype(float)
    within_statute = col("within_statute", True).astype(bool).astype(float)

    # credibility/viability blend (prioritization weights, not legal weights)
    score = (0.30 * role + 0.15 * tenure + 0.10 * former + 0.20 * nonpublic
             + 0.10 * prior_report + 0.15 * within_statute).clip(0, 1)

    # sensitive-content routing (the guardrail): any PHI/free-text → secure only
    sensitive = (col("phi_present", False).astype(bool)
                 | col("sensitive_free_text_present", False).astype(bool))
    route = sensitive.map(lambda s: "secure_counsel" if s else "standard_review")

    out = pd.DataFrame({
        "intake_triage_score": score.round(4),
        # gates surfaced for COUNSEL to assess — not adjudicated here
        "original_source_posture": nonpublic.map({1.0: "non-public", 0.0: "review"}),
        "first_to_file_clear": col("first_to_file_clear", pd.NA),
        "public_disclosure_risk": pd.to_numeric(
            col("public_disclosure_flag", 0), errors="coerce").fillna(0).astype(int),
        "route": route,
    }, index=df.index)

    def _reasons(i) -> str:
        r = []
        if nonpublic.iloc[i]: r.append("knowledge is non-public (original-source pivot)")
        if former.iloc[i]: r.append("former employee")
        if prior_report.iloc[i]: r.append("prior internal report (warm)")
        if not within_statute.iloc[i]: r.append("statute-of-limitations risk")
        if sensitive.iloc[i]: r.append("sensitive content → secure handling")
        return "; ".join(r) if r else "standard intake"
    out["triage_reasons"] = [_reasons(i) for i in range(n)]
    return out
