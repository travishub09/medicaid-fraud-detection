"""
features.py — Model C case-feature assembly (cold-start).

Per ``docs/platform/06-model-c.md`` §features. Assembles one row per case from
three sources, any of which may be partial:

  model_a_signal   the Model A ``erv_ranked`` rows — corroboration strength
                   (adjusted_prob, the unique defensible input), scheme
                   hypothesis (DOJ-priority weighting), the damages proxy
                   (exposure / payments_at_issue), jurisdiction if known,
                   data-confidence band, and (when joined) the A3
                   public-disclosure flag.
  intake           per-relator fields when a human is in the funnel: evidence
                   strength, relator credibility/culpability, knowledge tier,
                   original-source strength, first-to-file clearance. OPTIONAL —
                   without it the row is a PRE-RELATOR pre-screen (the manifesto's
                   org-level Case Viability Score), with neutral relator priors
                   and ``has_relator = 0`` so the underwriter never pretends a
                   witness exists.
  enforcement_ctx  jurisdiction → intervention multiplier overrides and similar
                   (OPTIONAL; the curated priors tables are the default).

Output: one row per case with the normalized 0–1 feature columns the underwriter
reads, plus the raw damages dollars and the identifying keys. Cold-start and
explainable — no learned weights, every downstream multiplier names its driver.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# data-confidence band → a 0–1 numeric (low confidence widens the recovery band
# and softens corroboration credit; never changes the ranking, only the spread).
_CONFIDENCE_NUMERIC = {"high": 1.0, "medium": 0.6, "low": 0.3, "": 0.6}

# knowledge tier (relator persona) → a 0–1 credibility/access weight (07 §4.1).
_KNOWLEDGE_TIER = {"A": 1.0, "B": 0.75, "C": 0.55, "": 0.7}

# neutral pre-relator defaults: an org-level pre-screen, not a real witness.
_PRE_RELATOR_DEFAULTS = {
    "evidence_strength": 0.5,
    "relator_credibility": 0.6,
    "relator_culpability": 0.2,
    "original_source_strength": 0.5,
    "knowledge_tier_weight": 0.7,
    "first_to_file_cleared": 1,        # assume clear until a docket alert says otherwise
}


def _col(df: pd.DataFrame, name: str, default):
    return df[name] if name in df.columns else pd.Series(default, index=df.index)


def build_case_features(model_a_signal: pd.DataFrame,
                        intake: pd.DataFrame | None = None,
                        enforcement_context: pd.DataFrame | None = None
                        ) -> pd.DataFrame:
    """Assemble the per-case feature rows Model C scores.

    ``model_a_signal`` is required (one row per org/case, keyed by
    ``org_node_id``). ``intake`` (optional) joins on ``org_node_id`` to add the
    relator fields; rows with no intake become pre-relator pre-screens.
    """
    m = model_a_signal.copy()
    m["org_node_id"] = m["org_node_id"].astype(str)
    n0 = len(m)

    out = pd.DataFrame({"org_node_id": m["org_node_id"]})
    out["org_name"] = _col(m, "org_name", "").fillna("").astype(str)
    out["scheme"] = _col(m, "scheme_hypothesis", "default").fillna("default").astype(str)

    # corroboration: Model A's adjusted probability IS the public-data signal
    out["corroboration_strength"] = pd.to_numeric(
        _col(m, "adjusted_prob", 0.0), errors="coerce").fillna(0.0).clip(0, 1)

    # damages proxy: prefer the scheme-scoped exposure (A1); fall back to total
    exposure = pd.to_numeric(_col(m, "exposure", np.nan), errors="coerce")
    payments = pd.to_numeric(_col(m, "payments", np.nan), errors="coerce")
    out["damages_single"] = exposure.where(exposure.notna(), payments).fillna(0.0).clip(lower=0)
    out["exposure_scope"] = _col(m, "exposure_scope", "all_payments").fillna("all_payments")

    out["jurisdiction"] = _col(m, "jurisdiction", "").fillna("").astype(str)
    conf = _col(m, "confidence", "").fillna("").astype(str).str.lower()
    out["data_confidence"] = conf.map(_CONFIDENCE_NUMERIC).fillna(0.6)
    out["public_disclosure_flag"] = pd.to_numeric(
        _col(m, "public_disclosure_flag", 0), errors="coerce").fillna(0).astype(int)

    # relator fields from intake, else pre-relator defaults
    has_relator = pd.Series(0, index=m.index)
    relator = {k: pd.Series(v, index=m.index) for k, v in _PRE_RELATOR_DEFAULTS.items()}
    if intake is not None and len(intake):
        ik = intake.copy()
        ik["org_node_id"] = ik["org_node_id"].astype(str)
        ik = ik.drop_duplicates("org_node_id").set_index("org_node_id")
        present = out["org_node_id"].isin(ik.index)
        has_relator = present.astype(int).to_numpy()
        idx = out["org_node_id"]
        for field, default in _PRE_RELATOR_DEFAULTS.items():
            if field == "knowledge_tier_weight":
                tier = idx.map(ik["knowledge_tier"]) if "knowledge_tier" in ik.columns else None
                vals = (tier.map(_KNOWLEDGE_TIER) if tier is not None else None)
                relator[field] = (vals.fillna(default) if vals is not None
                                  else pd.Series(default, index=m.index))
            elif field in ik.columns:
                relator[field] = pd.to_numeric(idx.map(ik[field]),
                                               errors="coerce").fillna(default)
            # else keep the default series
        # first_to_file_cleared is 0/1 not a 0–1 score
        relator["first_to_file_cleared"] = relator["first_to_file_cleared"].round().astype(int)

    out["has_relator"] = np.asarray(has_relator).astype(int)
    for field in _PRE_RELATOR_DEFAULTS:
        out[field] = relator[field].to_numpy()

    # optional jurisdiction overrides (a table of jurisdiction → multiplier)
    if enforcement_context is not None and len(enforcement_context):
        ec = enforcement_context.copy()
        if {"jurisdiction", "intervention_multiplier"} <= set(ec.columns):
            jmap = dict(zip(ec["jurisdiction"].astype(str),
                            pd.to_numeric(ec["intervention_multiplier"], errors="coerce")))
            out["jurisdiction_multiplier_override"] = out["jurisdiction"].map(jmap)

    assert len(out) == n0, "case-feature assembly must be one row per signal row"
    return out.reset_index(drop=True)
