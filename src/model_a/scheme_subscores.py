"""
scheme_subscores.py — scheme-specific subscores from whatever features exist today.

Per ``docs/platform/04-model-a.md`` §2.4: combine 0–1-normalized features with
domain-prior weights and squash to 0–1 per scheme:

    subscore_s = sigmoid( STEEPNESS · ( Σ_k w[s,k]·x_k / Σ_k w[s,k]  −  THRESHOLD ) )

Design points (v1, label-free):
  * The registry maps each scheme to the features that evidence it. Features that
    are not present in the input are skipped, and per-scheme coverage is recorded —
    so the same engine runs on today's Medicaid concept scores and absorbs the
    Part B/D/DMEPOS features (09-data-procurement.md) the day they land.
  * Inputs are 0–1 (the v3 concept *percentiles* and the graph features, which are
    already bounded). The weighted mean of 0–1 inputs is centered at THRESHOLD and
    sharpened by STEEPNESS, so 0.5 → 0.5, ~0.9 → ~0.92, ~0.1 → ~0.08.
  * Graph features appear ONLY in the ownership_integrity scheme; the separate
    graph-risk *boost* in scoring.py uses ring-structure membership instead, so a
    single fact never double-counts (the de-correlation principle from v3).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STEEPNESS = 6.0
THRESHOLD = 0.5

# scheme -> {feature_column: weight}. Columns marked (future) arrive with the
# Priority-1 procurement sources; absent columns are skipped at runtime.
DEFAULT_SCHEME_WEIGHTS: dict[str, dict[str, float]] = {
    # available today: v3 de-correlated concept percentiles (company grain)
    "single_service_mill": {"concentration": 1.0},
    "payment_outlier": {"payment_intensity": 1.0},
    "overutilization": {"service_intensity": 1.0},
    "specialty_mismatch": {"specialty_mismatch": 1.0},
    "rapid_ramp": {"temporal": 1.0},
    # available today: entity-graph features (src/entity_graph/graph_features.py)
    "ownership_integrity": {
        "within_2_hops_of_exclusion": 0.6,
        "shell_score": 0.5,
        "related_party_density_norm": 0.3,
    },
    # future (Part B): upcoding/impossible-day
    "upcoding": {"em_high_level_share": 0.7, "em_level_mean": 0.3},
    "impossible_day": {"bene_per_day_p95": 0.6, "time_minutes_per_day": 0.4},
    # future (Part D / Open Payments / DMEPOS)
    "pharma_kickback": {"op_payment_utilization_corr": 0.7, "op_payment_concentration": 0.3},
    "drug_outlier": {"controlled_substance_share": 0.5, "high_cost_drug_share": 0.5},
    "dme_ring": {"dme_high_cost_item_share": 0.5, "dme_ordering_md_concentration": 0.5},
}


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def normalize_graph_features(features: pd.DataFrame) -> pd.DataFrame:
    """Bound the unbounded graph features to 0–1 so they mix with percentiles.

    related_party_density (a count) squashes at 10+ related orgs;
    within_2_hops_of_exclusion and shell_score are already 0/1 and 0–1.
    """
    out = features.copy()
    if "related_party_density" in out.columns:
        out["related_party_density_norm"] = (
            out["related_party_density"].clip(lower=0) / 10.0).clip(upper=1.0)
    return out


def compute_subscores(features: pd.DataFrame,
                      weights: dict[str, dict[str, float]] | None = None
                      ) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """One 0–1 subscore column per scheme, plus which features each scheme used.

    Returns ``(subscores, coverage)`` where subscores has a ``subscore_<scheme>``
    column for every scheme with at least one present feature, and coverage maps
    scheme → the feature columns actually used (the explainability record).
    """
    weights = weights or DEFAULT_SCHEME_WEIGHTS
    feats = normalize_graph_features(features)
    out = pd.DataFrame(index=feats.index)
    coverage: dict[str, list[str]] = {}
    for scheme, wmap in weights.items():
        present = {c: w for c, w in wmap.items() if c in feats.columns}
        if not present:
            continue
        wsum = sum(present.values())
        x = sum(feats[c].fillna(0).clip(0, 1) * w for c, w in present.items()) / wsum
        out[f"subscore_{scheme}"] = _sigmoid(STEEPNESS * (x - THRESHOLD))
        coverage[scheme] = sorted(present)
    return out, coverage
