"""
scheme_subscores.py — group features into scheme-specific subscores (SCAFFOLD).

Per ``docs/platform/04-model-a.md`` §2.4: combine the clipped, one-sided robust-z
features with domain-prior weights and squash to 0–1 per scheme:

    subscore_s = sigmoid( Σ_k  w[s,k] · clipped_feature[k] )

The feature inputs already exist in ``src/attempt_2/ingest/features.py`` and the
graph features now come from ``src/entity_graph/graph_features.py``.
"""

from __future__ import annotations

import pandas as pd

# Scheme → contributing features (the feature dictionary, grouped). Weights are
# domain priors to be tuned; see the feature table in 04-model-a.md.
SCHEME_FEATURES: dict[str, list[str]] = {
    "upcoding": ["em_high_level_share", "em_level_mean"],
    "overutilization": ["services_per_bene", "allowed_per_bene"],
    "impossible_day": ["bene_per_day_p95", "time_minutes_per_day"],
    "single_service_mill": ["code_concentration_hhi"],
    "pharma_kickback": ["op_payment_utilization_corr", "op_payment_concentration"],
    "dme_ring": ["dme_high_cost_item_share", "dme_ordering_md_concentration"],
    "ownership_integrity": ["excluded_party_distance", "related_party_density",
                            "shell_score", "referral_ring_flag"],
    "saturation": ["market_saturation_index"],
    "cost_report": ["hcris_cost_alloc_anomaly"],
}


def compute_subscores(features: pd.DataFrame,
                      weights: dict[str, dict[str, float]]) -> pd.DataFrame:
    """One subscore column per scheme (0–1) for each organization."""
    raise NotImplementedError(
        "scheme subscores: sigmoid(Σ w·clipped_z) per scheme — see 04-model-a.md §2.4")
