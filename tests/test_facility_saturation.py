"""
test_facility_saturation.py — expansion plan B1 + B2.

B2 Market Saturation: county over-supply index within service type, FIPS as
   strings, bene-weighted state fallback (never the worst county), org attach
   only for mapped sectors.
B1 PBJ + Care Compare: understaffing is the NEGATED hours-per-resident-day so
   one-sided ranking keeps "excess = suspicious"; live-discharge measures
   extracted from the long format; CCNs keep leading zeros; facility peers =
   size band × state; CCN → org rollup via the PECOS crosswalk.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ingest_cms import (
    compute_saturation_metrics, state_saturation_index, attach_market_saturation,
    compute_pbj_metrics, compute_hospice_metrics, compute_deficiency_counts,
    facility_peer_percentiles, rollup_ccn_to_org,
)
from src.model_a.scheme_subscores import compute_subscores


# ------------------------------------------------------------------- B2 ---

def _saturation_frame():
    # real PUF headers; one over-supplied HH county, one normal, one hospice row
    return pd.DataFrame([
        {"Type of Service": "Home Health", "State and County FIPS Code": "01001",
         "State Name": "Alabama", "County Name": "Autauga",
         "Number of Providers": "200", "Number of Fee-for-Service Beneficiaries": "1000"},
        {"Type of Service": "Home Health", "State and County FIPS Code": "48201",
         "State Name": "Texas", "County Name": "Harris",
         "Number of Providers": "10", "Number of Fee-for-Service Beneficiaries": "10000"},
        {"Type of Service": "Hospice", "State and County FIPS Code": "48201",
         "State Name": "Texas", "County Name": "Harris",
         "Number of Providers": "50", "Number of Fee-for-Service Beneficiaries": "10000"},
    ])


def test_saturation_metrics_and_string_fips():
    m = compute_saturation_metrics(_saturation_frame())
    assert m["fips"].iloc[0] == "01001"                      # leading zero kept
    hh = m[m["service_type"] == "Home Health"].set_index("fips")
    assert hh.loc["01001", "providers_per_1k_benes"] == 200.0
    assert hh.loc["01001", "market_saturation_index"] == 1.0  # ranked within service
    assert hh.loc["48201", "market_saturation_index"] == 0.5


def test_state_saturation_is_bene_weighted_not_max():
    counties = compute_saturation_metrics(pd.DataFrame([
        {"Type of Service": "Home Health", "State and County FIPS Code": "48001",
         "State Name": "Texas", "County Name": "Big",
         "Number of Providers": "10", "Number of Fee-for-Service Beneficiaries": "99000"},
        {"Type of Service": "Home Health", "State and County FIPS Code": "48003",
         "State Name": "Texas", "County Name": "Tiny",
         "Number of Providers": "100", "Number of Fee-for-Service Beneficiaries": "1000"},
    ]))
    s = state_saturation_index(counties)
    rate = s.loc[(s["state"] == "TX"), "providers_per_1k_benes"].iloc[0]
    assert rate == pytest.approx(110 * 1000 / 100000)        # 1.1, nowhere near 100


def test_attach_only_for_mapped_sectors():
    org_nodes = pd.DataFrame({
        "org_node_id": ["org:hh", "org:office"],
        "primary_taxonomy": ["251E00000X", "207Q00000X"],     # home health vs FP
        "addr_state": ["TX", "TX"],
    })
    features = pd.DataFrame({"org_node_id": ["org:hh", "org:office"]})
    out = attach_market_saturation(features, org_nodes,
                                   compute_saturation_metrics(_saturation_frame()))
    o = out.set_index("org_node_id")
    assert pd.notna(o.loc["org:hh", "market_saturation_index"])
    assert pd.isna(o.loc["org:office", "market_saturation_index"])  # unmapped sector
    assert len(out) == 2                                      # no fan-out


# ------------------------------------------------------------------- B1 ---

def _pbj_frame():
    rows = []
    for d in range(1, 4):
        # thin facility: 100 residents, 100 total nurse hours/day (1.0 HPRD)
        rows.append({"PROVNUM": "675001", "STATE": "TX", "WorkDate": f"2025010{d}",
                     "MDScensus": "100", "Hrs_RN": "20", "Hrs_LPN": "30",
                     "Hrs_CNA": "50"})
        # staffed facility: 100 residents, 400 hours/day (4.0 HPRD)
        rows.append({"PROVNUM": "12345", "STATE": "TX", "WorkDate": f"2025010{d}",
                     "MDScensus": "100", "Hrs_RN": "100", "Hrs_LPN": "100",
                     "Hrs_CNA": "200"})
    rows.append({"PROVNUM": "675001", "STATE": "TX", "WorkDate": "20250104",
                 "MDScensus": "0", "Hrs_RN": "0", "Hrs_LPN": "0",
                 "Hrs_CNA": "0"})                              # closed day: excluded
    rows.append({"PROVNUM": "", "STATE": "TX", "WorkDate": "20250101",
                 "MDScensus": "50", "Hrs_RN": "10", "Hrs_LPN": "10",
                 "Hrs_CNA": "10"})                             # no CCN: quarantine
    return pd.DataFrame(rows)


def test_pbj_understaffing_direction_and_ccn_padding():
    m, quarantined = compute_pbj_metrics(_pbj_frame())
    assert quarantined == 1
    f = m.set_index("ccn")
    assert "012345" in f.index                                 # leading-zero pad
    assert f.loc["675001", "nurse_hours_per_resident_day"] == pytest.approx(1.0)
    assert f.loc["675001", "days_observed"] == 3               # closed day excluded
    # NEGATED: the thin facility must rank HIGHER on the suspicion axis
    assert f.loc["675001", "pbj_understaffing"] > f.loc["012345", "pbj_understaffing"]


def test_hospice_live_discharge_from_long_measures():
    raw = pd.DataFrame([
        {"CMS Certification Number (CCN)": "671500",
         "Measure Code": "H_012", "Measure Name": "Live Discharge Rate",
         "Score": "42.5"},
        {"CMS Certification Number (CCN)": "671500",
         "Measure Code": "H_001", "Measure Name": "Hospice Visits in Last Days",
         "Score": "90"},                                       # different measure
        {"CMS Certification Number (CCN)": "671501",
         "Measure Code": "H_012", "Measure Name": "Live Discharge Rate",
         "Score": "Not Available"},                            # quarantined
    ])
    m, quarantined = compute_hospice_metrics(raw)
    assert quarantined == 1
    assert m.set_index("ccn").loc["671500", "hospice_live_discharge_rate"] == 42.5
    assert "671501" not in m["ccn"].values


def test_deficiency_counts():
    raw = pd.DataFrame({"Federal Provider Number": ["675001", "675001", "012345", ""]})
    m, quarantined = compute_deficiency_counts(raw)
    assert quarantined == 1
    assert m.set_index("ccn").loc["675001", "deficiency_count"] == 2


def test_facility_peer_percentiles_size_band_ladder():
    rng = np.random.default_rng(7)
    n = 40
    metrics = pd.DataFrame({
        "ccn": [f"67{i:04d}" for i in range(n)],
        "state": ["TX"] * (n // 2) + ["FL"] * (n // 2),
        "avg_daily_census": rng.uniform(40, 60, n).round(),
        "pbj_understaffing": -rng.uniform(3.5, 4.5, n),
    })
    metrics.loc[0, "pbj_understaffing"] = -1.0                 # the thin one
    pct = facility_peer_percentiles(metrics, ["pbj_understaffing"], min_peer=5)
    assert pct.set_index("ccn").loc["670000", "pbj_understaffing"] >= 0.9


def test_rollup_ccn_to_org_max():
    feats = pd.DataFrame({"ccn": ["675001", "675002"],
                          "pbj_understaffing": [0.95, 0.20]})
    xw = pd.DataFrame({"ccn": ["675001", "675002"],
                       "npi": ["1003000415", "1003000407"]})
    npi_to_org = pd.DataFrame({"npi": ["1003000415", "1003000407"],
                               "org_node_id": ["org:a", "org:a"]})
    out = rollup_ccn_to_org(feats, xw, npi_to_org).set_index("org_node_id")
    assert out.loc["org:a", "pbj_understaffing"] == 0.95       # max per org


# ------------------------------------------------------- registry wiring ---

def test_new_schemes_fire_only_when_features_exist():
    base = pd.DataFrame({"concentration": [0.5]})
    subs, coverage = compute_subscores(base)
    assert "subscore_hospice_ineligibility" not in subs.columns   # absent = skipped

    feats = pd.DataFrame({"hospice_live_discharge_rate": [0.95, 0.10],
                          "pbj_understaffing": [0.9, 0.1],
                          "deficiency_count": [0.9, 0.1],
                          "market_saturation_index": [0.9, 0.1]})
    subs, coverage = compute_subscores(feats)
    for s in ("hospice_ineligibility", "worthless_services", "saturation_fraud"):
        assert f"subscore_{s}" in subs.columns
        assert subs.loc[0, f"subscore_{s}"] > subs.loc[1, f"subscore_{s}"]
    assert coverage["worthless_services"] == ["deficiency_count", "pbj_understaffing"]
