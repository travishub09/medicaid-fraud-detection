"""
test_models_funnel_resolver.py — the model/software batch.

Supervised graduation (PU classifier + quantile recovery, synthetic labels);
the funnel layer (event ad-safety, composite lead score, intake triage) with its
guardrails; and the person↔employer resolver feeding Model B (guardrails intact).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.model_a.supervised import train_pu_classifier, train_exposure_quantiles
from src.funnel.events import ad_safe_payload, validate_event, DATA_LAYER_FIELDS
from src.funnel.lead_score import composite_lead_score, LEAD_SCORE_WEIGHTS
from src.funnel.intake import triage_intake
from src.entity_graph.person_resolver import (
    resolve_people_to_orgs, build_employed_by_edges, tenure_overlaps)


# ----------------------------------------------------- supervised (PU) ---

def _labeled_data(n=400, seed=0):
    rng = np.random.default_rng(seed)
    # positives have a shifted feature; only ~half the positives are LABELED (PU)
    y_true = rng.random(n) < 0.3
    f1 = rng.normal(np.where(y_true, 2.0, 0.0), 1.0)
    f2 = rng.normal(np.where(y_true, 1.0, 0.0), 1.0)
    labeled = y_true & (rng.random(n) < 0.5)        # only half of positives observed
    feats = pd.DataFrame({"f1": f1, "f2": f2})
    return feats, pd.Series(labeled.astype(int)), pd.Series(y_true)


def test_pu_classifier_ranks_positives_and_estimates_c():
    feats, labeled, y_true = _labeled_data()
    model = train_pu_classifier(feats, labeled)
    assert 0.0 < model.c <= 1.0
    p = model.predict_proba(feats)
    assert ((p >= 0) & (p <= 1)).all()
    # PU-corrected score separates true positives from true negatives
    assert p[y_true.to_numpy()].mean() > p[~y_true.to_numpy()].mean() + 0.2
    assert set(model.feature_importance) == {"f1", "f2"}


def test_exposure_quantiles_ordered_and_increase_with_feature():
    rng = np.random.default_rng(1)
    n = 400
    f = rng.uniform(0, 1, n)
    recovery = np.where(rng.random(n) < 0.6, np.expm1(8 + 4 * f) * rng.uniform(0.7, 1.3, n), 0.0)
    feats = pd.DataFrame({"f": f})
    qm = train_exposure_quantiles(feats, pd.Series(recovery))
    pred = qm.predict(pd.DataFrame({"f": [0.1, 0.9]}))
    assert (pred["recovery_p10"] <= pred["recovery_p50"]).all()
    assert (pred["recovery_p50"] <= pred["recovery_p90"]).all()
    assert pred["recovery_p50"].iloc[1] > pred["recovery_p50"].iloc[0]   # bigger feature → bigger recovery


# --------------------------------------------------------------- funnel ---

def test_ad_safe_payload_suppresses_sensitive_and_phi():
    event = {"fraud_typology": "DME", "persona_segment": "Billing_Manager",
             "employer_name": "ACME", "allegation_text": "they upcoded",
             "patient_dob": "1950-01-01", "trust_stage": "fear",
             "unknown_field": "x"}
    safe = ad_safe_payload(event)
    assert safe == {"fraud_typology": "DME", "persona_segment": "Billing_Manager"}
    assert "employer_name" not in safe and "allegation_text" not in safe
    assert "patient_dob" not in safe                 # PHI-like key dropped
    assert "trust_stage" not in safe                 # sensitive behavior, first-party only


def test_validate_event_routes_sensitive_to_secure():
    assert validate_event({"sensitive_free_text_present": True})["_secure_only"]
    assert validate_event({"allegation_text": "x"})["_secure_only"]
    assert not validate_event({"fraud_typology": "Hospice"})["_secure_only"]


def test_composite_lead_score_weights_drivers_no_fraud_flag():
    leads = pd.DataFrame({
        "org_anomaly": [0.9, 0.1], "persona_fit": [0.8, 0.2],
        "behavior": [0.9, 0.1], "intake_quality": [0.7, 0.0],
        "recency": [1.0, 0.0],
    })
    out = composite_lead_score(leads)
    assert out.loc[0, "lead_score"] > out.loc[1, "lead_score"]
    assert abs(sum(LEAD_SCORE_WEIGHTS.values()) - 1.0) < 1e-9
    assert "org_anomaly" in out.loc[0, "lead_score_drivers"]
    assert out.loc[0, "trust_stage"] == "ready_for_counsel"
    # the guardrail: no fraud/accusation column anywhere
    assert not any("fraud" in c.lower() for c in out.columns)


def test_lead_score_renormalizes_when_components_missing():
    full = composite_lead_score(pd.DataFrame({"org_anomaly": [1.0], "persona_fit": [1.0],
                                              "behavior": [1.0], "intake_quality": [1.0],
                                              "recency": [1.0]}))
    partial = composite_lead_score(pd.DataFrame({"org_anomaly": [1.0]}))
    assert full.loc[0, "lead_score"] == 1.0 and partial.loc[0, "lead_score"] == 1.0


def test_triage_routes_phi_to_secure_and_surfaces_gates():
    intake = pd.DataFrame([
        {"role_tier": "A", "is_former": True, "knowledge_is_nonpublic": True,
         "prior_internal_report": True, "within_statute": True,
         "tenure_overlaps_conduct": True, "public_disclosure_flag": 0},
        {"role_tier": "C", "is_former": False, "knowledge_is_nonpublic": False,
         "sensitive_free_text_present": True, "within_statute": False},
    ])
    out = triage_intake(intake)
    assert out.loc[0, "intake_triage_score"] > out.loc[1, "intake_triage_score"]
    assert out.loc[0, "original_source_posture"] == "non-public"
    assert out.loc[1, "route"] == "secure_counsel"           # sensitive content
    assert out.loc[0, "route"] == "standard_review"
    assert "non-public" in out.loc[0, "triage_reasons"]


# ----------------------------------------------------- person resolver ---

ORG_NODES = pd.DataFrame({
    "org_node_id": ["org:acme", "org:beta"],
    "org_name": ["ACME HOME HEALTH", "BETA HOSPICE"],
    "aliases": ["ACME HOME HEALTH LLC", ""],
    "addr_state": ["TX", "CA"],
})


def test_resolve_people_bands_and_confidence():
    workforce = pd.DataFrame([
        {"person_id": "p1", "employer_name": "Acme Home Health, LLC", "state": "TX"},
        {"person_id": "p2", "employer_name": "Acme Home Hlth", "state": "TX"},   # fuzzy
        {"person_id": "p3", "employer_name": "Unrelated Widgets Inc", "state": "TX"},
    ])
    out = resolve_people_to_orgs(workforce, ORG_NODES).set_index("person_id")
    assert out.loc["p1", "decision"] == "auto_accept"
    assert out.loc["p1", "org_node_id"] == "org:acme"
    assert out.loc["p3", "decision"] == "auto_reject"
    assert out.loc["p3", "org_node_id"] == ""                # rejected → no link
    assert 0.0 <= out.loc["p2", "match_confidence"] <= 1.0


def test_employed_by_edges_and_tenure_overlap():
    matches = pd.DataFrame([
        {"person_id": "p1", "org_node_id": "org:acme", "match_confidence": 0.95,
         "decision": "auto_accept", "role": "coder",
         "start_date": "2021-01-01", "end_date": "2023-06-01"},
        {"person_id": "p3", "org_node_id": "", "match_confidence": 0.1,
         "decision": "auto_reject"},
    ])
    edges = build_employed_by_edges(matches)
    assert len(edges) == 1                                    # only the accepted link
    assert edges.iloc[0]["src_id"] == "person:p1"
    assert edges.iloc[0]["edge_type"] == "employed_by"
    # tenure overlaps a 2022 scheme window but not a 2024 one
    assert tenure_overlaps(edges, "2022-01-01", "2022-12-31").iloc[0]
    assert not tenure_overlaps(edges, "2024-01-01", "2024-12-31").iloc[0]


def test_resolver_output_carries_no_pii_columns():
    out = resolve_people_to_orgs(
        pd.DataFrame([{"person_id": "p1", "employer_name": "ACME HOME HEALTH"}]),
        ORG_NODES)
    # opaque person_id + org link only — no name/email/phone/address fields
    assert set(out.columns) == {"person_id", "org_node_id", "match_confidence",
                                "match_provenance", "decision"}
