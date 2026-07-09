"""
test_scheme_audit_fixes.py — regression tests for the scheme-audit fixes:

  1. CMS blank-suppression → NaN (never a false-clean zero): opioid shares.
  2. Part B per-bene ratios pair numerator/denominator over benes-observed rows.
  3. Kickback matching: form-stripped + first-token keys across ALL product fields.
  4. Saturation: DME + ambulance sectors now map; lookup is deterministic.
  5. Subscores: NULL-aware weighted mean — no zero-imputed floor, NaN when a
     provider has no observed evidence for a scheme.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.ingest_cms.opioid import compute_opioid_metrics
from src.ingest_cms.partb import compute_partb_metrics
from src.ingest_cms.openpayments import kickback_co_occurrence
from src.ingest_cms.saturation import (
    compute_saturation_metrics, attach_market_saturation, SECTOR_TO_SERVICE)
from src.model_a.scheme_subscores import compute_subscores


def test_opioid_suppressed_blank_is_nan_not_zero():
    raw = pd.DataFrame({
        "Prscrbr_NPI": ["1003000126", "1003000134"],
        "Tot_Clms": [100, 200],
        # blank = CMS-suppressed (1-10 claims), NOT zero
        "Opioid_Tot_Clms": ["", 40],
        "Opioid_LA_Tot_Clms": ["", ""],
    })
    g, _ = compute_opioid_metrics(raw)
    g = g.set_index("npi")
    # suppressed prescriber: UNSCORED, not falsely clean
    assert np.isnan(g.loc["1003000126", "opioid_claim_share"])
    # observed prescriber: share computed; LA share NaN (suppressed numerator)
    assert g.loc["1003000134", "opioid_claim_share"] == 0.2
    assert np.isnan(g.loc["1003000134", "opioid_long_acting_share"])


def test_partb_per_bene_ratio_pairs_observed_rows():
    raw = pd.DataFrame({
        "Rndrng_NPI": ["1003000126"] * 2,
        "HCPCS_Cd": ["99213", "99215"],
        "Tot_Srvcs": [100.0, 900.0],
        "Tot_Benes": [50.0, ""],          # second row suppressed
        "Avg_Mdcr_Alowd_Amt": [10.0, 10.0],
    })
    out, _ = compute_partb_metrics(raw)
    r = out.set_index("npi").loc["1003000126"]
    # ratio = services on benes-observed rows / observed benes (100/50),
    # NOT (100+900)/50 = 20 (the old inflated ratio)
    assert r["services_per_bene"] == 2.0
    assert r["total_benes"] == 50.0


def test_kickback_matches_dosage_forms_and_all_product_fields():
    op = pd.DataFrame({
        "Covered_Recipient_NPI": ["1003000126", "1003000126"],
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": ["ACME", "ACME"],
        "Total_Amount_of_Payment_USDollars": [500.0, 500.0],
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1":
            ["XARELTO 20MG TABLET", ""],
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_2":
            ["", "ELIQUIS ORAL TABLET"],
    })
    partd = pd.DataFrame({
        "Prscrbr_NPI": ["1003000126", "1003000126", "1003000126"],
        "Brnd_Name": ["XARELTO", "ELIQUIS", "METFORMIN HCL"],
        "Gnrc_Name": ["RIVAROXABAN", "APIXABAN", "METFORMIN HCL"],
        "Tot_Clms": [10, 10, 10],
        "Tot_Drug_Cst": [600.0, 300.0, 100.0],
    })
    co = kickback_co_occurrence(op, partd).set_index("npi")
    # dosage-form OP names match bare Part D brands; field 2 counts too;
    # unpaid metformin does not: (600+300)/1000
    assert co.loc["1003000126", "op_payment_utilization_corr"] == 0.9


def test_saturation_maps_dme_and_ambulance():
    assert "dme" in SECTOR_TO_SERVICE and "ambulance" in SECTOR_TO_SERVICE
    raw = pd.DataFrame({
        "Type of Service": ["Durable Medical Equipment, Prosthetics, Orthotics "
                            "and Supplies", "Ambulance (Emergency & Non-Emergency)"],
        "State and County FIPS Code": ["01001", "01001"],
        "State Name": ["ALABAMA", "ALABAMA"],
        "County Name": ["Autauga", "Autauga"],
        "Number of Providers": [40, 15],
        "Number of Fee-for-Service Beneficiaries": [1000, 1000],
    })
    county = compute_saturation_metrics(raw)
    features = pd.DataFrame({"org_node_id": ["org:dme", "org:amb"]})
    org_nodes = pd.DataFrame({
        "org_node_id": ["org:dme", "org:amb"],
        "primary_taxonomy": ["332B00000X", "3416A0800X"],
        "addr_state": ["AL", "AL"],
    })
    out = attach_market_saturation(features, org_nodes, county).set_index("org_node_id")
    assert pd.notna(out.loc["org:dme", "market_saturation_index"])
    assert pd.notna(out.loc["org:amb", "market_saturation_index"])


def test_subscores_null_aware_no_floor():
    feats = pd.DataFrame({
        # single_service_mill = {concentration: 1.0}
        "concentration": [0.9, np.nan, 0.1],
        # pharma_kickback = {op_payment_utilization_corr: .7, op_payment_concentration: .3}
        "op_payment_utilization_corr": [np.nan, np.nan, 0.8],
        "op_payment_concentration": [1.0, np.nan, np.nan],
    })
    subs, _ = compute_subscores(feats)
    s_mill = subs["subscore_single_service_mill"]
    s_kick = subs["subscore_pharma_kickback"]
    # no observed evidence → NaN, never the sigmoid floor
    assert np.isnan(s_mill.iloc[1]) and np.isnan(s_kick.iloc[1])
    # partial evidence: weights renormalize over what's observed —
    # row 0 kickback = sigmoid over concentration-only value 1.0 (high),
    # row 2 kickback = utilization-only 0.8 (high); both must exceed 0.5
    assert s_kick.iloc[0] > 0.5 and s_kick.iloc[2] > 0.5
    # dense feature unchanged: high concentration scores high, low scores low
    assert s_mill.iloc[0] > 0.5 > s_mill.iloc[2]
