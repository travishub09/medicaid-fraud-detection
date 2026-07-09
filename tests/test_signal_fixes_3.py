"""
test_signal_fixes_3.py — third fix batch (audit remainder):

  1. Deficiency citations weighted by CMS scope/severity (J-L immediate jeopardy
     = 8x a minimal citation); count-only files degrade gracefully.
  2. Part D "high-cost drug" threshold is day-supply-normalized (a 90-day fill
     is no longer 3x the "cost" of a 30-day fill of the same drug).
  3. complexity_adjust rework preserves behavior (guarded by existing tests) —
     here: residualized vs median-centered markers still recorded correctly.
  4. Saturation: county-grain attach when a ZIP→county crosswalk is provided;
     state fallback unchanged without it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ingest_cms.facility import compute_deficiency_counts
from src.ingest_cms.partd import compute_partd_metrics
from src.analytics.peers import assign_peer_groups, complexity_adjust
from src.ingest_cms.saturation import (
    compute_saturation_metrics, attach_market_saturation)


def test_deficiency_severity_weighting():
    raw = pd.DataFrame({
        "CMS Certification Number (CCN)": ["015009", "015009", "676543"],
        "Scope Severity Code": ["J", "B", "D"],   # jeopardy + minimal | mid
    })
    out, _ = compute_deficiency_counts(raw)
    out = out.set_index("ccn")
    assert out.loc["015009", "deficiency_count"] == 2
    assert out.loc["015009", "deficiency_severity_weighted"] == 9.0   # 8 + 1
    assert out.loc["676543", "deficiency_severity_weighted"] == 2.0
    # no severity column → weighted degrades to the plain count
    out2, _ = compute_deficiency_counts(
        raw[["CMS Certification Number (CCN)"]])
    out2 = out2.set_index("ccn")
    assert out2.loc["015009", "deficiency_severity_weighted"] == 2.0


def test_partd_high_cost_uses_day_supply():
    # same drug at 30-day and 90-day fills: identical $/day, so NEITHER should
    # be "high cost" relative to the genuinely expensive specialty drug
    raw = pd.DataFrame({
        "Prscrbr_NPI": ["1003000126"] * 3,
        "Brnd_Name": ["METFORMIN", "METFORMIN", "SPECIALTY"],
        "Gnrc_Name": ["METFORMIN", "METFORMIN", "SPECIALTY"],
        "Tot_Clms": [10, 10, 10],
        "Tot_Drug_Cst": [100.0, 300.0, 30000.0],
        "Tot_Day_Suply": [300.0, 900.0, 300.0],   # $0.33/day, $0.33/day, $100/day
    })
    out, _ = compute_partd_metrics(raw)     # default 0.9 decile
    r = out.set_index("npi").loc["1003000126"]
    # only the $100/day drug crosses the top-decile $/day threshold — the two
    # metformin fills (identical $/day despite 3x cost-per-claim) do not:
    # share = 30000 / 30400
    assert r["high_cost_drug_share"] == pytest.approx(30000.0 / 30400.0)


def test_complexity_adjust_markers_after_rework():
    n = 80
    rng = np.random.default_rng(3)
    df = pd.DataFrame({
        "taxonomy_code": ["A"] * 40 + ["B"] * 40,
        "entity_type": ["1"] * n,
        "state": ["TX"] * n,
        "total_services": rng.integers(10, 100, n).astype(float),
    })
    df["metric"] = df["total_services"] * 2 + rng.normal(0, 1, n)
    df = assign_peer_groups(df, min_peer=10)
    adj = complexity_adjust(df, ["metric"], ["total_services"])
    scored = df["peer_level"] >= 0
    assert (adj.loc[scored, "metric__adjustment"] == "residualized").all()
    # residualizing removes the size effect: adjusted ⊥ total_services
    corr = np.corrcoef(adj.loc[scored, "metric__adj"],
                       df.loc[scored, "total_services"])[0, 1]
    assert abs(corr) < 0.3


def test_saturation_county_grain_with_crosswalk():
    raw = pd.DataFrame({
        "Type of Service": ["Home Health"] * 2,
        "State and County FIPS Code": ["01001", "01003"],
        "State Name": ["ALABAMA", "ALABAMA"],
        "County Name": ["Autauga", "Baldwin"],
        "Number of Providers": [90, 5],           # 01001 saturated, 01003 not
        "Number of Fee-for-Service Beneficiaries": [1000, 1000],
    })
    county = compute_saturation_metrics(raw)
    features = pd.DataFrame({"org_node_id": ["org:hot", "org:cool"]})
    org_nodes = pd.DataFrame({
        "org_node_id": ["org:hot", "org:cool"],
        "primary_taxonomy": ["251E00000X", "251E00000X"],
        "addr_state": ["AL", "AL"],
        "addr_zip": ["36067", "36507"],
    })
    xw = pd.DataFrame({"zip": ["36067", "36507"], "county_fips": ["01001", "01003"]})
    out = attach_market_saturation(features, org_nodes, county,
                                   zip_to_county=xw).set_index("org_node_id")
    # county grain separates the two orgs; the state fallback would give both
    # the SAME state-level value
    assert out.loc["org:hot", "market_saturation_index"] > \
        out.loc["org:cool", "market_saturation_index"]
    out_state = attach_market_saturation(features, org_nodes, county
                                         ).set_index("org_node_id")
    assert out_state.loc["org:hot", "market_saturation_index"] == \
        out_state.loc["org:cool", "market_saturation_index"]

