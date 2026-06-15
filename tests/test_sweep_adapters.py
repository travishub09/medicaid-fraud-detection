"""
test_sweep_adapters.py — the June-2026 research-sweep adapters (doc 15).

Opioid prescriber rates → pill_mill; NPPES deactivation → invalid_identity
(billing on/after the deactivation date); 340B OPAIS → contract_pharmacy. Each
parses real PUF headers, keeps string IDs, quarantines bad rows, and feeds a
registered scheme.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.ingest_cms import (
    compute_opioid_metrics, deactivated_npis, billing_after_deactivation,
    covered_entities, attach_340b)
from src.model_a.scheme_subscores import compute_subscores


# ---------------------------------------------------------------- opioid ---

def test_opioid_shares_and_quarantine():
    raw = pd.DataFrame([
        {"Prscrbr_NPI": "1003000415", "Tot_Clms": "1000",
         "Opioid_Tot_Clms": "800", "Opioid_LA_Tot_Clms": "600"},   # pill-mill shape
        {"Prscrbr_NPI": "1003000407", "Tot_Clms": "1000",
         "Opioid_Tot_Clms": "50", "Opioid_LA_Tot_Clms": "5"},      # normal
        {"Prscrbr_NPI": "12345", "Tot_Clms": "10",
         "Opioid_Tot_Clms": "1", "Opioid_LA_Tot_Clms": "0"},       # bad NPI
    ])
    m, quarantined = compute_opioid_metrics(raw)
    assert quarantined == 1
    g = m.set_index("npi")
    assert g.loc["1003000415", "opioid_claim_share"] == 0.8
    assert g.loc["1003000415", "opioid_long_acting_share"] == 0.75
    assert g.loc["1003000407", "opioid_claim_share"] == 0.05


# ------------------------------------------------------ deactivation ---

def test_billing_after_deactivation_share():
    deact_raw = pd.DataFrame([
        {"NPI": "1003000415", "NPI Deactivation Date": "2024-01-01"},
        {"NPI": "12345", "NPI Deactivation Date": "2023-06-01"},      # bad NPI
    ])
    deact, quarantined = deactivated_npis(deact_raw)
    assert quarantined == 1
    assert deact.set_index("npi").loc["1003000415", "deactivation_date"].year == 2024

    spending = pd.DataFrame([
        # billed before AND after the 2024-01-01 deactivation
        {"billing_npi": "1003000415", "service_month": "2023-06", "total_paid": 100_000.0},
        {"billing_npi": "1003000415", "service_month": "2024-03", "total_paid": 300_000.0},
        {"billing_npi": "1003000407", "service_month": "2024-03", "total_paid": 500_000.0},
    ])
    xw = pd.DataFrame({"npi": ["1003000415", "1003000407"],
                       "org_node_id": ["org:dead", "org:live"]})
    out = billing_after_deactivation(spending, deact, xw).set_index("org_node_id")
    # 300k of the dead org's 400k was billed after deactivation
    assert out.loc["org:dead", "post_deactivation_paid"] == 300_000.0
    assert out.loc["org:dead", "billing_after_deactivation"] == 0.75
    assert out.loc["org:live", "billing_after_deactivation"] == 0.0


# ------------------------------------------------------------- 340B ---

def test_340b_entities_and_attach():
    raw = pd.DataFrame([
        {"340B ID": "DSH001", "Entity Name": "MERCY HOSPITAL",
         "Entity Type": "DSH", "State": "TX", "Contract Pharmacy Name": f"PH{i}"}
        for i in range(30)
    ] + [
        {"340B ID": "RW001", "Entity Name": "SMALL CLINIC",
         "Entity Type": "RW", "State": "TX", "Contract Pharmacy Name": "PH1"},
    ])
    ent = covered_entities(raw).set_index("name_key")
    assert ent.loc["MERCY HOSPITAL", "n_contract_pharmacies"] == 30
    assert ent.loc["SMALL CLINIC", "n_contract_pharmacies"] == 1

    org_nodes = pd.DataFrame({"org_node_id": ["org:m", "org:s", "org:none"],
                              "org_name": ["Mercy Hospital", "Small Clinic", "Other"],
                              "aliases": ["", "", ""]})
    feats = pd.DataFrame({"org_node_id": ["org:m", "org:s", "org:none"]})
    out = attach_340b(feats, org_nodes, covered_entities(raw)).set_index("org_node_id")
    assert out.loc["org:m", "is_340b_covered_entity"] == 1
    assert out.loc["org:m", "contract_pharmacy_concentration"] == 1.0   # 30 ≥ saturation 25
    assert out.loc["org:s", "contract_pharmacy_concentration"] == pytest.approx(1 / 25)
    assert out.loc["org:none", "is_340b_covered_entity"] == 0
    assert len(out) == 3                                                 # no fan-out


# ----------------------------------------------------- registry wiring ---

def test_new_sweep_schemes_registered():
    feats = pd.DataFrame({
        "opioid_claim_share": [0.95, 0.1],
        "opioid_long_acting_share": [0.9, 0.1],
        "contract_pharmacy_concentration": [0.9, 0.0],
        "billing_after_deactivation": [0.8, 0.0],
    })
    subs, cov = compute_subscores(feats)
    for scheme in ("pill_mill", "contract_pharmacy", "invalid_identity"):
        assert f"subscore_{scheme}" in subs.columns
        assert subs.loc[0, f"subscore_{scheme}"] > subs.loc[1, f"subscore_{scheme}"]
