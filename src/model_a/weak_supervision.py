"""
weak_supervision.py — a Snorkel-style label model (Pillar 2, docs/platform/17).

Labels are the binding constraint: hard positives (LEIE/DOJ) are scarce and biased.
Weak supervision expands them. Instead of one hard label we write many noisy
LABELING FUNCTIONS — each votes FRAUD (+1), CLEAN (-1), or ABSTAIN (0) from a cheap
heuristic — then a LABEL MODEL learns each function's accuracy and fuses the votes
into a probabilistic label for EVERY provider, including the unlabeled mass.

We have a labeled development set for free: the high-confidence anchors (exclusion/
DOJ positives, manufactured ``confirmed_clean`` negatives). Each labeling function's
accuracy is estimated on the subset where it votes AND an anchor label exists
(Laplace-smoothed, clipped), giving a weight ``w = log(acc/(1-acc))``. The soft
label is ``sigmoid(bias + Σ w_j · vote_j)`` — a calibrated weighted vote.

The output ``weak_label_score`` is a TRAINING TARGET, not a feature (training on it
as an input would be circular), and it inherits the leakage-adjacency of the
exclusion-proximity functions — use it as a soft/semi-supervised target under the
same out-of-time discipline as the hard label. Labeling functions are transparent
and listed; the per-function accuracy/coverage is reported for audit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_VOTES = 5        # below this an LF's accuracy can't be estimated → weight 0
ACC_CLIP = (0.5, 0.99)
DEFAULT_PRIOR = 0.10


def _col(df: pd.DataFrame, c: str) -> pd.Series:
    if c in df.columns:
        return pd.to_numeric(df[c], errors="coerce")
    return pd.Series(np.nan, index=df.index)


# (name, polarity, predicate) — polarity +1 votes FRAUD, -1 votes CLEAN; the
# predicate fires (True) where the function votes, else it abstains.
LABELING_FUNCTIONS = [
    ("lf_billed_after_death", +1, lambda d: _col(d, "billing_after_death") > 0),
    ("lf_billed_after_deactivation", +1, lambda d: _col(d, "billing_after_deactivation") > 0),
    ("lf_near_exclusion", +1, lambda d: _col(d, "within_2_hops_of_exclusion") > 0),
    ("lf_high_fraud_proximity", +1, lambda d: _col(d, "graph_fraud_proximity") >= 0.9),
    ("lf_solo_institutional_scale", +1, lambda d: _col(d, "incons_solo_scale") == 1),
    ("lf_instant_scale", +1, lambda d: _col(d, "incons_instant_scale") == 1),
    ("lf_consistency_multi", +1, lambda d: _col(d, "consistency_flags") >= 2),
    ("lf_extreme_residual", +1, lambda d: _col(d, "billing_residual") >= 0.99),
    ("lf_single_service_mill", +1, lambda d: _col(d, "subscore_single_service_mill") >= 0.9),
    ("lf_payment_outlier", +1, lambda d: _col(d, "subscore_payment_outlier") >= 0.9),
    ("lf_ownership_integrity", +1, lambda d: _col(d, "subscore_ownership_integrity") >= 0.9),
    ("lf_confirmed_clean", -1, lambda d: _col(d, "confirmed_clean") == 1),
]


def apply_labeling_functions(matrix: pd.DataFrame) -> pd.DataFrame:
    """Votes matrix: one column per LF in {+1, -1, 0} (a missing input → abstain)."""
    out = pd.DataFrame(index=matrix.index)
    for name, pol, pred in LABELING_FUNCTIONS:
        fires = pred(matrix).fillna(False).astype(bool)
        out[name] = (pol * fires.astype(int)).astype(int)
    return out


def fit_label_model(votes: pd.DataFrame, y: pd.Series,
                    min_votes: int = MIN_VOTES) -> tuple[dict, dict]:
    """Per-LF weight + coverage, estimated where the LF votes and an anchor label
    exists. ``y``: 1/0/NaN aligned to votes (NaN = unlabeled)."""
    weights, cov = {}, {}
    for lf in votes.columns:
        v = votes[lf]
        mask = (v != 0) & y.notna()
        n = int(mask.sum())
        cov[lf] = n
        if n < min_votes:
            weights[lf] = 0.0
            continue
        votes_fraud = v[mask] > 0
        correct = (votes_fraud == (y[mask] == 1))
        acc = (int(correct.sum()) + 1) / (n + 2)         # Laplace smoothing
        acc = float(np.clip(acc, *ACC_CLIP))
        weights[lf] = float(np.log(acc / (1 - acc)))
    return weights, cov


def label_model_scores(votes: pd.DataFrame, weights: dict,
                       prior: float = DEFAULT_PRIOR) -> pd.Series:
    """Soft label sigmoid(bias + Σ w·vote) per row."""
    bias = float(np.log(prior / (1 - prior)))
    s = pd.Series(bias, index=votes.index, dtype=float)
    for lf, w in weights.items():
        if w:
            s = s + w * votes[lf]
    return 1.0 / (1.0 + np.exp(-s))


def weak_supervision(matrix: pd.DataFrame, pos_col: str = "provider_on_exclusion",
                     clean_col: str = "confirmed_clean", prior: float = DEFAULT_PRIOR
                     ) -> tuple[pd.DataFrame, dict]:
    """Orchestrate: vote → fit on anchors → score everyone. Returns
    (per-NPI weak labels, per-LF audit summary)."""
    votes = apply_labeling_functions(matrix)
    y = pd.Series(np.nan, index=matrix.index)
    pos = _col(matrix, pos_col).fillna(0) == 1
    if not pos.any():
        pos = _col(matrix, "provider_on_leie").fillna(0) == 1
    clean = (_col(matrix, clean_col).fillna(0) == 1) & ~pos
    y[pos] = 1.0
    y[clean] = 0.0

    weights, cov = fit_label_model(votes, y)
    score = label_model_scores(votes, weights, prior=prior)
    n_votes = (votes != 0).sum(axis=1)

    result = pd.DataFrame({
        "npi": matrix["npi"].astype(str).to_numpy(),
        "weak_label_score": score.to_numpy().round(4),
        "weak_label": (score >= 0.5).astype(int).to_numpy(),
        "weak_label_votes": n_votes.to_numpy(),
    })
    audit = {"n_anchor_labeled": int(y.notna().sum()),
             "lf_weight": {k: round(v, 3) for k, v in weights.items()},
             "lf_coverage": cov}
    return result, audit
