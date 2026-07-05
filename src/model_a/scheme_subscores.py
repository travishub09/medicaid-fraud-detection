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
    "specialty_mismatch": {"specialty_mismatch": 0.5,
                           # data-derived clinical plausibility (A5); absent
                           # until spending + taxonomy are attached, then blends
                           "clinical_implausibility": 0.3,
                           # A5 second half: per-capita volume vs county pop
                           # (Census denominator); absent until ZIP→county lands
                           "local_volume_implausibility": 0.2},
    "rapid_ramp": {"temporal": 0.6,
                   # growth-shock signals from src/analytics/growth.py (A4):
                   # absent until spending-derived features are attached
                   "growth_level_shift": 0.8, "new_code_burst": 0.5},
    # available today: entity-graph features (src/entity_graph/graph_features.py)
    "ownership_integrity": {
        "within_2_hops_of_exclusion": 0.6,
        "shell_score": 0.5,
        "related_party_density_norm": 0.15,
        # CHOW churn from diffed owner snapshots (A7); absent until two+ monthly
        # snapshots accumulate (src/entity_graph/ownership_churn.py)
        "ownership_turnover": 0.3,
    },
    # Part B: upcoding (both features produced by ingest_cms/partb.py)
    "upcoding": {"em_high_level_share": 0.7, "em_level_mean": 0.3},
    # impossible-day: DORMANT BY DATA — bene_per_day_p95 and time_minutes_per_day
    # need per-day service counts / procedure-time minutes that the by-provider-
    # and-service PUF does not carry (they need the line-level/BETOS or a timed
    # source). Skip-missing keeps the scheme silent (never mis-fires) until that
    # data lands; do not interpret its absence as "no impossible-day risk".
    "impossible_day": {"bene_per_day_p95": 0.6, "time_minutes_per_day": 0.4},
    # future (Part D / Open Payments / DMEPOS)
    "pharma_kickback": {"op_payment_utilization_corr": 0.7, "op_payment_concentration": 0.3},
    "drug_outlier": {"controlled_substance_share": 0.4, "high_cost_drug_share": 0.4,
                     # NADAC markup/spread anomaly (B4); absent until NDC claims load
                     "drug_spread_anomaly": 0.3},
    # dme_ordering_md_concentration needs supplier×ordering-MD pairs (not in the
    # by-referring-provider PUF) — dormant by data; the other two are produced.
    "dme_ring": {"dme_high_cost_item_share": 0.4, "dme_ordering_md_concentration": 0.4,
                 # orders from referrers not eligible to order DME (sweep 2.6)
                 "ineligible_referral_share": 0.4},
    # future (B1/B2 facility + saturation adapters: ingest_cms/facility.py,
    # ingest_cms/saturation.py — percentile features at the org grain)
    # severity-weighted deficiencies carry the gravity (immediate jeopardy = 8×);
    # the raw count keeps a small weight (size/survey-frequency confounded)
    "worthless_services": {"pbj_understaffing": 0.7,
                           "deficiency_severity_weighted": 0.5,
                           "deficiency_count": 0.2,
                           # capacity-vs-billing "impossible org" (POS, sweep 2.5)
                           "capacity_mismatch": 0.6},
    "hospice_ineligibility": {"hospice_live_discharge_rate": 1.0},
    "saturation_fraud": {"market_saturation_index": 1.0},
    # future (June-2026 sweep adapters: ingest_cms/opioid.py, hrsa_340b.py,
    # nppes_deactivation.py) — absent until those files are loaded
    "pill_mill": {"opioid_claim_share": 0.6, "opioid_long_acting_share": 0.4},
    "contract_pharmacy": {"contract_pharmacy_concentration": 1.0},
    # billing under a deactivated OR (DOB-corroborated) deceased NPI
    "invalid_identity": {"billing_after_deactivation": 0.6, "billing_after_death": 0.6},
    # HCRIS cost-report fraud (B5); absent until the flattened extract loads
    "cost_report_fraud": {"hcris_cost_anomaly": 1.0},
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
        # /25 (was /10): a small shared-owner shell cluster is the concealment
        # signal; a large legitimate integrated system shares an owner with
        # hundreds of orgs and shouldn't max this on size alone. Weakly indicative
        # (low scheme weight) — the real fix for size-correlated flags is calibration.
        out["related_party_density_norm"] = (
            out["related_party_density"].clip(lower=0) / 25.0).clip(upper=1.0)
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
        # NULL-aware weighted mean: a missing feature means "provider absent from
        # that source" (the platform's NULL convention), NOT zero. Imputing 0 here
        # floored every provider uncovered by a thin source at sigmoid(-3)≈0.047 —
        # an identical constant for most of the universe that flooded the score's
        # top decile with ties and anti-correlated it with the label wherever the
        # excluded are under-covered. Weights renormalize over the features each
        # row actually has; rows with NO observed evidence stay NaN (unscored,
        # never force-scored).
        num = sum(feats[c].clip(0, 1).fillna(0) * w for c, w in present.items())
        den = sum(feats[c].notna() * w for c, w in present.items())
        x = num / den.where(den > 0)
        out[f"subscore_{scheme}"] = _sigmoid(STEEPNESS * (x - THRESHOLD))
        coverage[scheme] = sorted(present)
    return out, coverage
