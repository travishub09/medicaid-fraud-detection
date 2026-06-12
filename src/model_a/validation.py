"""
validation.py — temporal-holdout validation, generalized from the LEIE backtest.

Per ``docs/platform/04-model-a.md`` §2.7: score with data through a cut date,
then test whether the flagged organizations were named in enforcement *after*
it. The honest metrics for a tiny base rate are precision@k and lift over a
baseline — never raw accuracy.

This generalizes ``src/backtest`` (which validated the v3 anomaly score against
LEIE) to arbitrary outcome tables (LEIE exclusions, the DOJ case DB via the
defendant name key, future PACER outcomes) and arbitrary score columns, so every
Model A iteration is validated the same way.

Inputs are org-grain frames:
    scores   org_node_id + a score column (e.g. adjusted_prob or erv)
    outcomes org_node_id + outcome_date   (one row per adverse event)
"""

from __future__ import annotations

import pandas as pd


def temporal_holdout_precision_at_k(scores: pd.DataFrame,
                                    outcomes: pd.DataFrame,
                                    cut_date: str,
                                    k: int = 100,
                                    score_col: str = "adjusted_prob",
                                    baseline_col: str | None = None) -> dict:
    """precision@k and lift after ``cut_date``; optional baseline comparison.

    Positives = orgs with an outcome strictly after the cut date (events at or
    before it are information the model could legitimately have known — they are
    excluded from the holdout, matching the backtest's timing discipline).
    """
    s = scores.copy()
    s["org_node_id"] = s["org_node_id"].astype(str)
    assert s["org_node_id"].is_unique, "scores must be one row per org"
    cut = pd.Timestamp(cut_date)

    o = outcomes.copy()
    o["org_node_id"] = o["org_node_id"].astype(str)
    o["outcome_date"] = pd.to_datetime(o["outcome_date"], errors="coerce")
    post = set(o.loc[o["outcome_date"] > cut, "org_node_id"])
    pre = set(o.loc[o["outcome_date"] <= cut, "org_node_id"])

    # holdout universe: orgs without a pre-cut event (those were already known)
    s = s[~s["org_node_id"].isin(pre - post)].copy()
    s["is_positive"] = s["org_node_id"].isin(post).astype(int)

    n = len(s)
    n_pos = int(s["is_positive"].sum())
    base_rate = n_pos / n if n else 0.0
    k = min(k, n)

    def _p_at_k(col: str) -> float:
        top = s.nlargest(k, col)
        return float(top["is_positive"].mean()) if k else 0.0

    p_at_k = _p_at_k(score_col)
    result = {
        "cut_date": str(cut.date()),
        "n_orgs": n,
        "n_positives": n_pos,
        "base_rate": round(base_rate, 6),
        "k": k,
        "precision_at_k": round(p_at_k, 6),
        "lift": round(p_at_k / base_rate, 3) if base_rate > 0 else None,
    }
    if baseline_col and baseline_col in s.columns:
        bp = _p_at_k(baseline_col)
        result["baseline_precision_at_k"] = round(bp, 6)
        result["baseline_lift"] = round(bp / base_rate, 3) if base_rate > 0 else None
        result["beats_baseline"] = bool(p_at_k > bp)
    return result


def outcomes_from_case_db(case_db: pd.DataFrame,
                          org_nodes: pd.DataFrame) -> pd.DataFrame:
    """DOJ case DB → outcome rows, joined to the graph on the defendant name key.

    Matches ``defendant_name_key`` against each org's canonical name and aliases
    (the same norm key both sides already carry). Unmatched cases are simply not
    outcomes for this universe — they are counted, not dropped silently.
    """
    from src.entity_graph.resolve_entities import norm_org_name
    idx: dict[str, str] = {}
    for r in org_nodes.itertuples():
        names = {str(getattr(r, "org_name", "") or "")}
        names.update(a.strip() for a in str(getattr(r, "aliases", "") or "").split(";"))
        for nme in names:
            key = norm_org_name(nme)
            if key:
                idx.setdefault(key, str(r.org_node_id))

    c = case_db.copy()
    c["org_node_id"] = c["defendant_name_key"].astype(str).map(idx)
    matched = c[c["org_node_id"].notna()]
    out = matched.rename(columns={"announced_date": "outcome_date"})[
        ["org_node_id", "outcome_date"]].reset_index(drop=True)
    out.attrs["n_cases_total"] = len(c)
    out.attrs["n_cases_matched"] = len(matched)
    return out
