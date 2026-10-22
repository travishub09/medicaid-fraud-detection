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

# INSTITUTIONAL DAMPING (v6): three schemes systematically over-fire on large
# institutional entities for reasons that are complexity, not fraud —
#   specialty_mismatch  a general acute-care hospital legitimately bills a
#                       huge code range, so "codes unusual for its specialty"
#                       fires on breadth;
#   contract_pharmacy   virtually every 340B hospital has contract pharmacies
#                       (and the metric is near-constant there);
#   rapid_ramp          system M&A reads as an organic billing ramp when a
#                       surviving NPI absorbs an acquired entity's volume —
#                       damped ONLY when institutional AND owner churn is
#                       observed, because operator-segment CHOW-then-ramp is
#                       a REAL fraud precursor (the PE-rollup pattern) and
#                       must keep firing.
# Damping halves the subscore for institutional rows (>= 25-member org or
# any recorded beds); it never zeroes a signal and never touches operators.
INSTITUTIONAL_DAMPING: dict[str, float] = {
    "specialty_mismatch": 0.5,
    "contract_pharmacy": 0.5,
}
INSTITUTIONAL_CHURN_DAMPING: dict[str, float] = {"rapid_ramp": 0.5}
_INSTITUTIONAL_MIN_MEMBERS = 25

# scheme -> {feature_column: weight}. Columns marked (future) arrive with the
# Priority-1 procurement sources; absent columns are skipped at runtime.
DEFAULT_SCHEME_WEIGHTS: dict[str, dict[str, float]] = {
    # Company-grain v3 concepts AND provider-grain equivalents. The concept
    # columns exist only in the detection pipeline's company features; the
    # per-NPI export never carried them, which left these three schemes DORMANT
    # on every provider run (2026-08-02 triage). Each scheme now ALSO points at
    # the per-NPI column that measures the same thing (skip-missing keeps
    # whichever grain is present).
    "single_service_mill": {"concentration": 1.0,
                            "code_concentration_hhi": 0.8},
    "payment_outlier": {"payment_intensity": 1.0,
                        "allowed_per_bene": 0.8},
    "overutilization": {"service_intensity": 1.0,
                        "services_per_bene": 0.8},
    "specialty_mismatch": {"specialty_mismatch": 0.5,
                           # billing-implied specialty distance (billing LM) —
                           # the per-NPI equivalent of the company concept
                           "billing_taxonomy_mismatch": 0.5,
                           # data-derived clinical plausibility (A5); absent
                           # until spending + taxonomy are attached, then blends
                           "clinical_implausibility": 0.3,
                           # A5 second half, local_volume_implausibility (per-capita
                           # volume vs county pop) is RESERVED: analytics.
                           # local_denominator_plausibility has no caller
                           # (PLATFORM_AUDIT H8); it rejoins the weights with its producer
                           },
    "rapid_ramp": {"temporal": 0.6,
                   # growth-shock signals from src/analytics/growth.py (A4):
                   # absent until spending-derived features are attached
                   "growth_level_shift": 0.8, "new_code_burst": 0.5},
    # available today: entity-graph features (src/entity_graph/graph_features.py)
    "ownership_integrity": {
        "within_2_hops_of_exclusion": 0.6,
        "shell_score": 0.5,
        # the graph emits related_party_density; the _norm variant exists only
        # on the company path — accept both (skip-missing)
        "related_party_density_norm": 0.15,
        "related_party_density": 0.15,
        # CHOW churn from diffed owner snapshots (A7); absent until two+ monthly
        # snapshots accumulate (src/entity_graph/ownership_churn.py)
        "ownership_turnover": 0.3,
        # exact disclosed stakes from Care Compare NH ownership (facility.py):
        # a dense owner web / concentrated control refine the graph counts
        "nh_owner_count": 0.15,
        "nh_owner_concentration": 0.1,
    },
    # Part B: upcoding (both features produced by ingest_cms/partb.py)
    "upcoding": {"em_high_level_share": 0.7, "em_level_mean": 0.3},
    # impossible-day: DORMANT BY DATA — bene_per_day_p95 and time_minutes_per_day
    # need per-day service counts / procedure-time minutes that the by-provider-
    # and-service PUF does not carry (they need the line-level/BETOS or a timed
    # source). Skip-missing keeps the scheme silent (never mis-fires) until that
    # data lands; do not interpret its absence as "no impossible-day risk".
    # bene_per_day_p95 is RESERVED (no producer anywhere in src/; PLATFORM_AUDIT H8)
    "impossible_day": {"time_minutes_per_day": 0.4,
                       # rendering-grain peak (model_a/servicing_grain.py): the busiest
                       # month's claim lines on THIS clinician's NPI, across every
                       # organization that billed for them — a producer this scheme
                       # never had before 2026-10-08
                       "sv_peak_lines_month": 0.5,
                       # CMS MUE conservative bound (ingest_cms/mue.py): monthly
                       # claim lines exceed the PUBLISHED daily unit maximum ×
                       # days — a categorical, CMS-citable impossible-units flag
                       # that finally wakes this scheme without per-day data
                       "mue_violation_share": 0.8},
    # future (Part D / Open Payments / DMEPOS)
    # Medicare side (Open Payments x Part D) + the MEDICAID side (Open Payments x the
    # NDC file through the FDA labeler map, medicaid_ndc.medicaid_kickback, 2026-10-10):
    # the share of Medicaid drug dollars on paying manufacturers' drugs and the
    # prescribing move after the first payment
    "pharma_kickback": {"op_payment_utilization_corr": 0.7, "op_payment_concentration": 0.3,
                        "px_paid_mfr_share": 0.5, "px_post_payment_lift": 0.5},
    # Medicaid-fact sector schemes (Run 2 I4/I5): transport + behavioral health,
    # computed straight from the spending fact (ingest_cms/sector_schemes.py)
    "nemt_fraud": {"nemt_paid_share": 0.5, "nemt_lines_per_patient": 0.5},
    "behavioral_health": {"bh_paid_share": 0.4, "bh_lines_per_patient": 0.6},
    # LABORATORY TESTING (v21, 2026-10-22; ingest_cms/sector_schemes.py): the scheme
    # the v19 LABEL_ENRICHMENT read asked for — forward FCA cases concentrate in
    # clinical labs (lift 26) and on definitive toxicology / genetic / respiratory-
    # panel codes no scheme named. Size (lab share of own dollars, panel breadth per
    # patient) and MIX (definitive drug testing, genetic, respiratory shares OF the
    # lab dollars), each peer-ranked one-sided. v21 semantics: new pre-registration.
    "lab_testing": {"lab_paid_share": 0.3, "lab_lines_per_patient": 0.5,
                    "lab_definitive_drug_share": 0.5, "lab_genetic_share": 0.5,
                    "lab_resp_panel_share": 0.3},
    "drug_outlier": {"controlled_substance_share": 0.4, "high_cost_drug_share": 0.4,
                     # NADAC markup/spread anomaly (B4); absent until NDC claims load
                     "drug_spread_anomaly": 0.3,
                     # the spread signal WITHOUT units (medicaid_ndc.pharmacy_spread_proxy,
                     # 2026-10-10): the pharmacy's paid per line over the national median
                     # for the same NDC, dollar-weighted, one-sided
                     "rx_spread_proxy": 0.3, "rx_spread_top_share": 0.2,
                     # the J-code peer-relative markup from the spending fact —
                     # the drug-spread essence with no NDC slice needed
                     "drug_markup_anomaly": 0.4},
    # dme_ordering_md_concentration needs supplier×ordering-MD pairs (not in the
    # by-referring-provider PUF) — dormant by data; the other two are produced.
    # dme_ordering_md_concentration is RESERVED (no producer; PLATFORM_AUDIT H8)
    "dme_ring": {"dme_high_cost_item_share": 0.4,
                 # orders from referrers not eligible to order DME (sweep 2.6)
                 "ineligible_referral_share": 0.4,
                 # referrer-grain fallback from the public DMEPOS detail file:
                 # THIS NPI ordered DME while not on the O&R DME-eligible list
                 # (the org-grain share above still needs referred_claims)
                 "dme_ineligible_referred_dollars": 0.4},
    # future (B1/B2 facility + saturation adapters: ingest_cms/facility.py,
    # ingest_cms/saturation.py — percentile features at the org grain)
    # severity-weighted deficiencies carry the gravity (immediate jeopardy = 8×);
    # the raw count keeps a small weight (size/survey-frequency confounded)
    "worthless_services": {"pbj_understaffing": 0.7,
                           "deficiency_severity_weighted": 0.5,
                           "deficiency_count": 0.2,
                           # capacity-vs-billing "impossible org" (POS, sweep 2.5)
                           "capacity_mismatch": 0.6,
                           # staffing that collapses when inspectors are off
                           "pbj_weekend_understaffing": 0.4,
                           # CMS fines/denials: enforcement-adjacent corroboration
                           "nh_fine_total": 0.2, "nh_penalty_count": 0.15},
    # the hospice measure panel (scheme-discovery buildout): live discharge
    # stays the anchor; NF-care share is the RI captive-referral pattern
    # generalized; the rest are OIG's standing hospice targets
    "hospice_ineligibility": {"hospice_live_discharge_rate": 1.0,
                              "hospice_nf_care_share": 0.5,
                              "hospice_gip_share": 0.4,
                              "hospice_visit_gap": 0.5,
                              "hospice_nurse_minute_deficit": 0.3,
                              "hospice_spend_per_bene": 0.3,
                              "hospice_dementia_share": 0.2},
    "saturation_fraud": {"market_saturation_index": 1.0},
    # future (June-2026 sweep adapters: ingest_cms/opioid.py, hrsa_340b.py,
    # nppes_deactivation.py) — absent until those files are loaded
    # Medicare opioid file (opioid.py) + the MEDICAID counterparts from the NDC file
    # (medicaid_ndc.py with the NADAC-derived opioid flag, 2026-10-10): the prescriber's
    # opioid share of Medicaid dollars and the long-acting share of those, plus the
    # dispensing side (a pharmacy whose dollars are mostly opioids)
    "pill_mill": {"opioid_claim_share": 0.6, "opioid_long_acting_share": 0.4,
                  "px_opioid_paid_share": 0.5, "px_opioid_la_share": 0.3,
                  "rx_opioid_paid_share": 0.3},
    "contract_pharmacy": {"contract_pharmacy_concentration": 1.0},
    # billing under a deactivated OR (DOB-corroborated) deceased NPI
    "invalid_identity": {"billing_after_deactivation": 0.6, "billing_after_death": 0.6},
    # HCRIS cost-report fraud (B5); absent until the flattened extract loads
    "cost_report_fraud": {"hcris_cost_anomaly": 1.0},
    # an INDIVIDUAL NPI whose dollars sit on facility-type codes (codes >= 90%
    # organization-billed nationally) — the Rhode Island T2046 lead-case shape
    # as a model input (entity_graph/code_rings.compute_facility_code_share;
    # individuals only, organizations stay unscored on this scheme)
    "facility_code_billing": {"facility_code_share": 1.0},
    # rendering identity (model_a/servicing_grain.py, 2026-10-08): the rendering
    # clinician's NPI appears on many organizations' claims in the SAME month, at a
    # volume one person cannot render, for dollars per patient peers do not reach —
    # the identity-lending / phantom-rendering shape. Individual NPIs are the natural
    # subject; an organization NPI as servicing NPI is scored the same way (it is
    # then "one org rendering for many billers", the pass-through shape).
    "rendering_identity": {"sv_max_orgs_same_month": 0.6, "sv_peak_lines_month": 0.5,
                           "sv_n_billing_orgs": 0.3, "sv_paid_per_patient_month": 0.3,
                           "sv_lines_per_active_month": 0.3},
}


def _institutional_mask(feats: pd.DataFrame) -> pd.Series:
    """Rows that are institutional-scale entities: a 25+-member organization
    or anything with recorded beds. Used only to DAMP the schemes above —
    never to boost, never to exclude."""
    m = pd.Series(False, index=feats.index)
    if "org_member_count" in feats.columns:
        org_n = pd.to_numeric(feats["org_member_count"], errors="coerce")
        m |= org_n.fillna(0) >= _INSTITUTIONAL_MIN_MEMBERS
    if "pos_bed_count" in feats.columns:
        beds = pd.to_numeric(feats["pos_bed_count"], errors="coerce")
        m |= beds.fillna(0) > 0
    return m


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
        # either/or aliases: a raw column and its _norm variant are ONE signal
        # listed twice so whichever grain lands is used. When BOTH land (the
        # provider path emits raw and normalize adds _norm), keep only the
        # bounded _norm — clip(0,1) on the raw COUNT binarizes it (any related
        # party at all = 1.0), a double-vote that tie-flooded the top decile
        # and helped invert ownership_integrity in v4/v5.
        for c in list(present):
            if f"{c}_norm" in present:
                del present[c]
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
        sub = _sigmoid(STEEPNESS * (x - THRESHOLD))
        if scheme in INSTITUTIONAL_DAMPING:
            sub = sub.where(~_institutional_mask(feats),
                            sub * INSTITUTIONAL_DAMPING[scheme])
        if (scheme in INSTITUTIONAL_CHURN_DAMPING
                and "ownership_turnover" in feats.columns):
            churn = pd.to_numeric(feats["ownership_turnover"],
                                  errors="coerce")
            hit = _institutional_mask(feats) & (churn.fillna(0) > 0)
            sub = sub.where(~hit,
                            sub * INSTITUTIONAL_CHURN_DAMPING[scheme])
        out[f"subscore_{scheme}"] = sub
        # evidence count: the NULL-aware mean's variance shrinks with the number
        # of observed inputs, so a one-input extreme is easier to reach than a
        # five-input one. Extreme-threshold consumers should require >= 2. Name
        # deliberately avoids the "subscore_" prefix so the noisy-OR scorers and
        # the manifest feature families never mistake it for a scheme score.
        out[f"evidence_n_{scheme}"] = sum(
            feats[c].notna().astype(int) for c in present)
        coverage[scheme] = sorted(present)
    return out, coverage
