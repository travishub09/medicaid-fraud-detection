"""
test_remaining_buildout.py — the "build all the remaining items" batch.

NADAC drug spread, HCRIS cost-report anomaly, DocGraph referral edges +
referral-ring detection, state-licensing → exclusion schema, SSA Death Master
File (DOB-corroborated vs name-only), openFDA recall events, OpenSanctions →
exclusion schema, and the GLiNER wrapper (injected fake model — no weights).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.ingest_cms import (
    compute_nadac_reference, drug_spread_anomaly,
    compute_hcris_metrics, hcris_anomaly, build_referral_edges)
from src.entity_graph.ring_detection import referral_rings
from src.enforcement.state_licensing import normalize_state_licensing
from src.enforcement.death_master import (
    parse_dmf, match_deceased_providers, billing_after_death)
from src.enforcement.opensanctions import normalize_opensanctions
from src.feeds.openfda import fetch_enforcement, enforcement_events
from src.nlp.extract import extract_entities
from src.model_a.scheme_subscores import compute_subscores

XW = pd.DataFrame({"npi": ["1003000415", "1003000407"],
                   "org_node_id": ["org:a", "org:b"]})


# --------------------------------------------------------------- NADAC ---

def test_nadac_reference_and_spread():
    ref = compute_nadac_reference(pd.DataFrame([
        {"NDC": "00071", "NADAC Per Unit": "100.0", "Classification for Rate Setting": "B"},
        {"NDC": "00002", "NADAC Per Unit": "1.0", "Classification for Rate Setting": "G"},
    ]))
    r = ref.set_index("ndc")
    assert r.loc["00071", "is_high_cost"] == 1 and r.loc["00002", "is_high_cost"] == 0
    claims = pd.DataFrame([
        {"billing_npi": "1003000415", "ndc": "00071", "units": 10, "billed_cost": 2000.0},
        {"billing_npi": "1003000415", "ndc": "00002", "units": 10, "billed_cost": 10.0},
    ])
    out = drug_spread_anomaly(claims, ref, XW).set_index("org_node_id")
    # billed $2000 vs NADAC benchmark 10*100=1000 → $1000 excess on $2010 total
    assert out.loc["org:a", "above_nadac_paid"] == 1000.0
    assert 0 < out.loc["org:a", "drug_spread_anomaly"] < 1


# --------------------------------------------------------------- HCRIS ---

def test_hcris_metrics_and_anomaly():
    rng = __import__("numpy").random.default_rng(1)
    n = 40
    raw = pd.DataFrame({
        "PRVDR_NUM": [f"45{i:04d}" for i in range(n)],
        "TOTAL_COSTS": rng.uniform(1e6, 2e6, n),
        "TOTAL_CHARGES": rng.uniform(2e6, 4e6, n),
        "ADMIN_COSTS": rng.uniform(1e5, 2e5, n),
        "RELATED_PARTY_COSTS": rng.uniform(0, 1e4, n),
        "STATE": "TX",
    })
    raw.loc[0, "RELATED_PARTY_COSTS"] = raw.loc[0, "TOTAL_COSTS"] * 0.6   # outlier
    metrics, q = compute_hcris_metrics(raw)
    assert q == 0 and "cost_to_charge_ratio" in metrics.columns
    anom = hcris_anomaly(metrics, min_peer=5).set_index("ccn")
    assert anom.loc["450000", "hcris_cost_anomaly"] >= 0.9


def test_hcris_public_costreport_columns_and_absent_related_party():
    # the public data.cms.gov "Cost Report" files use Title-Case names and have
    # NO related-party column — must not crash, and must still score on overhead
    rng = __import__("numpy").random.default_rng(2)
    n = 40
    raw = pd.DataFrame({
        "Provider CCN": [f"67{i:04d}" for i in range(n)],
        "Total Costs": rng.uniform(1e6, 2e6, n),
        "Total Charges": rng.uniform(2e6, 4e6, n),
        "Overhead Non-Salary Costs": rng.uniform(1e5, 2e5, n),  # → admin_costs
        "State Code": "TX",
        # deliberately NO related-party column (the regression that crashed)
    })
    raw.loc[0, "Overhead Non-Salary Costs"] = raw.loc[0, "Total Costs"] * 0.5  # outlier
    metrics, q = compute_hcris_metrics(raw)
    assert q == 0
    assert metrics["cost_to_charge_ratio"].notna().all()       # Total Charges mapped
    assert metrics["admin_cost_share"].notna().all()           # Overhead mapped
    anom = hcris_anomaly(metrics, min_peer=5).set_index("ccn")
    assert anom.loc["670000", "hcris_cost_anomaly"] >= 0.9      # overhead outlier flagged


# ----------------------------------------------------- DocGraph + rings ---

def test_referral_edges_and_ring_detection():
    # A→B→C→A closed loop + a dangling edge that isn't part of a cycle
    npi_org = pd.DataFrame({"npi": ["1", "2", "3", "4"],
                            "org_node_id": ["org:A", "org:B", "org:C", "org:D"]})
    dg = pd.DataFrame([
        {"from_npi": "1", "to_npi": "2", "patient_count": 50},
        {"from_npi": "2", "to_npi": "3", "patient_count": 40},
        {"from_npi": "3", "to_npi": "1", "patient_count": 30},   # closes the loop
        {"from_npi": "3", "to_npi": "4", "patient_count": 99},   # not in a cycle
    ])
    # canonicalize_series will reject 1-digit NPIs; use Luhn-valid ones instead
    npi_org["npi"] = ["1003000415", "1003000407", "1003000308", "1003000316"]
    dg["from_npi"] = dg["from_npi"].map(dict(zip(["1", "2", "3"],
                     ["1003000415", "1003000407", "1003000308"]))).fillna("1003000308")
    dg["to_npi"] = dg["to_npi"].map(dict(zip(["1", "2", "3", "4"],
                   ["1003000415", "1003000407", "1003000308", "1003000316"])))
    edges = build_referral_edges(dg, npi_org)
    assert (edges["edge_type"] == "refers_to").all() and len(edges) == 4
    rings = referral_rings(edges)
    assert len(rings) >= 1
    top = rings.iloc[0]
    assert top["cycle_len"] == 3
    assert top["shared_patient_volume"] == 30.0          # the thinnest edge in the loop


def test_referral_rings_empty_without_edges():
    assert referral_rings(None).empty


# ----------------------------------------------------- state licensing ---

def test_state_licensing_to_exclusion_schema():
    raw = pd.DataFrame([
        {"Name": "JOHN DOE MD", "Action": "License Revoked", "Date": "2024-02-01"},
        {"Name": "JANE ROE MD", "Action": "Annual Renewal", "Date": "2024-01-01"},
    ])
    out = normalize_state_licensing(
        raw, {"name": "Name", "action": "Action", "action_date": "Date"}, "TX")
    assert len(out) == 1                                  # renewal dropped
    assert out.iloc[0]["name_key"] == "JOHN DOE MD"
    assert out.iloc[0]["excl_type"].startswith("state_board:TX")
    assert set(out.columns) >= {"npi", "name_key", "excl_type", "currently_active"}


# --------------------------------------------------------- death master ---

def test_dmf_high_vs_low_confidence_and_billing():
    dmf = parse_dmf(pd.DataFrame([
        {"last_name": "SMITH", "first_name": "JOHN", "DOB": "1950-01-01",
         "DOD": "2023-06-01"}]))
    pdim = pd.DataFrame([
        {"npi": "1003000415", "provider_name": "John Smith", "dob": "1950-01-01"},  # DOB match → high
        {"npi": "1003000407", "provider_name": "John Smith", "dob": "1970-05-05"},  # name only → low
    ])
    m = match_deceased_providers(pdim, dmf).set_index("npi")
    assert m.loc["1003000415", "match_confidence"] == "high"
    assert m.loc["1003000407", "match_confidence"] == "low"
    spending = pd.DataFrame([
        {"billing_npi": "1003000415", "service_month": "2023-09", "total_paid": 300_000.0},
        {"billing_npi": "1003000415", "service_month": "2023-01", "total_paid": 100_000.0},
    ])
    out = billing_after_death(spending, m.reset_index(), XW).set_index("org_node_id")
    # only the high-confidence match feeds the score; 300k of 400k is post-death
    assert out.loc["org:a", "billing_after_death"] == 0.75


# ----------------------------------------------------------- openFDA ---

def test_openfda_enforcement_events():
    def _fake(url, params=None, headers=None):
        return {"results": [
            {"recalling_firm": "Acme Labs LLC", "reason_for_recall": "contamination",
             "status": "Ongoing", "report_date": "20250401", "classification": "Class I"},
            {"recalling_firm": "Unknown Co", "reason_for_recall": "labeling",
             "status": "Completed", "report_date": "20250101", "classification": "Class III"},
        ]}
    recalls = fetch_enforcement("drug", search="acme", fetch_json=_fake)
    assert recalls.iloc[0]["name_key"] == "ACME LABS"
    orgs = pd.DataFrame({"org_node_id": ["org:acme"], "org_name": ["ACME LABS"],
                         "aliases": [""]})
    ev = enforcement_events(orgs, recalls)
    assert len(ev) == 1 and ev.iloc[0]["event_type"] == "fda_recall"
    assert ev.iloc[0]["org_node_id"] == "org:acme"


# ------------------------------------------------------- OpenSanctions ---

def test_opensanctions_to_exclusion_schema():
    entities = [
        {"schema": "Organization",
         "properties": {"name": ["Bad Care LLC"], "topics": ["debarment"],
                        "startDate": ["2022-05-01"]},
         "datasets": ["us_med_exclusions"]},
        {"schema": "Vehicle", "properties": {"name": ["not an entity"]}},  # skipped
    ]
    out = normalize_opensanctions(entities)
    assert len(out) == 1
    assert out.iloc[0]["name_key"] == "BAD CARE"
    assert out.iloc[0]["excl_type"] == "opensanctions:us_med_exclusions"
    assert out.iloc[0]["currently_active"] == 1


# ----------------------------------------------------------- GLiNER ---

class _FakeGliner:
    def predict_entities(self, text, labels, threshold=0.5):
        out = []
        if "Acme" in text:
            out.append({"text": "Acme Health LLC", "label": "organization", "score": 0.98})
        if "coder" in text:
            out.append({"text": "coder", "label": "job title", "score": 0.91})
        return out


def test_gliner_extract_with_injected_model():
    texts = {"d1": "United States ex rel. Doe v. Acme Health LLC",
             "d2": "A coder reported the pressure to upcode", "d3": ""}
    out = extract_entities(texts, model=_FakeGliner())
    assert set(out["text_id"]) == {"d1", "d2"}              # empty text skipped
    assert (out[out["label"] == "organization"]["text"] == "Acme Health LLC").any()
    assert (out["score"] > 0.5).all()


# ----------------------------------------------------- registry wiring ---

def test_new_schemes_registered():
    feats = pd.DataFrame({
        "drug_spread_anomaly": [0.95, 0.0], "controlled_substance_share": [0.1, 0.1],
        "high_cost_drug_share": [0.1, 0.1],
        "billing_after_death": [0.9, 0.0], "billing_after_deactivation": [0.1, 0.1],
        "hcris_cost_anomaly": [0.9, 0.0],
    })
    subs, cov = compute_subscores(feats)
    assert "drug_spread_anomaly" in cov["drug_outlier"]
    assert "billing_after_death" in cov["invalid_identity"]
    assert "subscore_cost_report_fraud" in subs.columns
    for s in ("drug_outlier", "invalid_identity", "cost_report_fraud"):
        assert subs.loc[0, f"subscore_{s}"] > subs.loc[1, f"subscore_{s}"]
