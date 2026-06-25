"""
case_control.py — matched case-control training set (the output reframe).

"Compare fraud actors to known non-offenders" is, literally, a matched case-control
study. Handing a tree a 0.2%-positive imbalanced soup teaches it the base rate;
handing it each fraud actor next to comparable clean providers — same specialty,
geography, and size — teaches it WHAT DIFFERS holding the confounders fixed (the way
epidemiology isolates a risk factor). It also yields interpretable "fraud vs.
matched-clean" comparisons for counsel.

  match_cohorts(matrix, ...)  for each positive, draw up to ``n_controls`` clean
      controls from the same stratum, relaxing the strata ladder
      (taxonomy × state × size  →  taxonomy × size  →  taxonomy) until enough
      controls exist, recording the ``match_tier`` used. Returns the positives +
      their sampled controls with ``match_id`` (the case's npi), ``cohort``
      (case/control), and ``match_tier``.

Controls come from the manufactured ``confirmed_clean`` anchors when present, else
the unlabeled non-positives (a weaker contrast, flagged). Deterministic given seed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_LADDER = (
    ("mtax", "mstate", "msize"),
    ("mtax", "msize"),
    ("mtax",),
)
_TIER_LABEL = {"mtax": "tax", "mstate": "state", "msize": "size"}


def _size_bucket(net_paid: pd.Series, q: int = 4) -> pd.Series:
    v = pd.to_numeric(net_paid, errors="coerce")
    try:
        return pd.qcut(v.rank(method="first"), q, labels=False).astype("Int64").astype(str)
    except Exception:
        return pd.Series("0", index=net_paid.index)


def match_cohorts(matrix: pd.DataFrame, label_col: str = "provider_on_exclusion",
                  clean_col: str = "confirmed_clean", n_controls: int = 3,
                  seed: int = 42) -> pd.DataFrame:
    """Matched case-control set. One row per case + up to n_controls matched rows."""
    cols = list(matrix.columns)
    df = matrix.copy()
    df["mtax"] = df.get("peer_group_key", df.get("primary_taxonomy", "")).fillna("").astype(str)
    df["mstate"] = df.get("practice_state", pd.Series("", index=df.index)).fillna("").astype(str)
    df["msize"] = _size_bucket(df.get("net_paid", pd.Series(0.0, index=df.index)))

    if label_col not in df.columns:
        return pd.DataFrame()
    cases = df[pd.to_numeric(df[label_col], errors="coerce").fillna(0) == 1]
    if not len(cases):
        return pd.DataFrame()

    if clean_col in df.columns and (pd.to_numeric(df[clean_col], errors="coerce").fillna(0) == 1).any():
        pool = df[pd.to_numeric(df[clean_col], errors="coerce").fillna(0) == 1].copy()
        control_kind = "confirmed_clean"
    else:                                  # weaker fallback: unlabeled non-positives
        pool = df[pd.to_numeric(df[label_col], errors="coerce").fillna(0) != 1].copy()
        control_kind = "unlabeled"

    rng = np.random.default_rng(seed)
    out_rows = []
    used_case_npis = set(cases["npi"].astype(str))
    for ci, case in enumerate(cases.itertuples(index=False)):
        case_npi = str(getattr(case, "npi"))
        out_rows.append({"_src_npi": case_npi, "match_id": case_npi,
                         "cohort": "case", "match_tier": "self",
                         "control_kind": control_kind})
        chosen, tier_used = [], "none"
        last_cand, last_label = [], "none"
        for tier in _LADDER:
            cond = np.ones(len(pool), dtype=bool)
            for k in tier:
                cond &= (pool[k].to_numpy() == getattr(case, k))
            # don't draw a case as its own control
            cand = [j for j in pool.index[cond]
                    if str(pool.at[j, "npi"]) != case_npi
                    and str(pool.at[j, "npi"]) not in used_case_npis]
            label = "×".join(_TIER_LABEL[t] for t in tier)
            last_cand, last_label = cand, label       # broadest tier is evaluated last
            if len(cand) >= n_controls:               # most specific tier with enough
                chosen = list(rng.choice(cand, size=n_controls, replace=False))
                tier_used = label
                break
        else:                                         # no tier had n_controls → broadest
            if last_cand:
                chosen = list(rng.choice(last_cand, size=min(n_controls, len(last_cand)),
                                         replace=False))
                tier_used = last_label
        for j in chosen:
            out_rows.append({"_src_npi": str(pool.at[j, "npi"]), "match_id": case_npi,
                             "cohort": "control", "match_tier": tier_used,
                             "control_kind": control_kind})

    meta = pd.DataFrame(out_rows)
    if not len(meta):
        return pd.DataFrame()
    # attach the full feature row for each selected provider
    feat = df.set_index(df["npi"].astype(str))[cols]
    joined = meta.join(feat, on="_src_npi").drop(columns=["_src_npi"])
    return joined.reset_index(drop=True)
