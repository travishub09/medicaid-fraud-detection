"""
test_exposure_and_ingest.py — items 1 & 2: real exposure + the CMS adapters.

Exposure: dollar conservation, unresolved bucket, subpart combination at org
grain. Adapters: tiny synthetic CSV frames using the REAL PUF header names, so a
genuine download drops in unmodified.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.entity_graph.__main__ import run as run_graph
from src.model_a.exposure import annual_payments_per_org, attach_payments
from src.ingest_cms import (
    compute_partb_metrics, compute_partd_metrics, compute_dmepos_metrics,
    to_peer_percentiles, rollup_to_org,
)
from tests.fixtures.synthetic import build_synthetic_inputs, build_spending


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    return run_graph(build_synthetic_inputs(), tmp_path_factory.mktemp("graph"))


# ---------------------------------------------------------------- exposure ---

def test_exposure_dollar_conservation_and_unresolved(graph):
    payments, recon = annual_payments_per_org(build_spending(), graph["npi_to_org"])
    assert abs((recon["total_matched"] + recon["total_unresolved"])
               - recon["total_in"]) < 0.01
    assert recon["total_unresolved"] == 777_777.0        # the unknown NPI's dollars
    assert recon["unresolved_npis"] == 1


def test_exposure_mean_annual_and_subpart_combination(graph):
    payments, _ = annual_payments_per_org(build_spending(), graph["npi_to_org"])
    p = payments.set_index("org_node_id")
    npi2org = graph["npi_to_org"].set_index("npi")["org_node_id"]

    mill = npi2org.loc["1003000415"]
    assert p.loc[mill, "payments"] == 25_000_000.0       # mean annual over 2 years
    assert p.loc[mill, "years_observed"] == 2

    # the two PAC subparts combine into one org's payments: 3M + 2M per year
    sub = npi2org.loc["1003000308"]
    assert p.loc[sub, "payments"] == 5_000_000.0


def test_attach_payments_overrides_and_no_fanout(graph):
    payments, _ = annual_payments_per_org(build_spending(), graph["npi_to_org"])
    feats = pd.DataFrame({"org_node_id": payments["org_node_id"],
                          "payments": 999.0})             # stale column must lose
    out = attach_payments(feats, payments)
    assert len(out) == len(feats)
    assert (out["payments"] != 999.0).all()


# ------------------------------------------------------------ CMS adapters ---

def _partb_frame():
    # real PUF headers; one upcoder (all 99215), one balanced, one bad NPI row
    return pd.DataFrame([
        {"Rndrng_NPI": "1003000415", "HCPCS_Cd": "99215", "Tot_Srvcs": "900",
         "Tot_Benes": "100", "Avg_Mdcr_Alowd_Amt": "110"},
        {"Rndrng_NPI": "1003000415", "HCPCS_Cd": "99213", "Tot_Srvcs": "100",
         "Tot_Benes": "80", "Avg_Mdcr_Alowd_Amt": "70"},
        {"Rndrng_NPI": "1003000407", "HCPCS_Cd": "99213", "Tot_Srvcs": "500",
         "Tot_Benes": "400", "Avg_Mdcr_Alowd_Amt": "70"},
        {"Rndrng_NPI": "1003000407", "HCPCS_Cd": "99214", "Tot_Srvcs": "100",
         "Tot_Benes": "90", "Avg_Mdcr_Alowd_Amt": "90"},
        {"Rndrng_NPI": "12345", "HCPCS_Cd": "99213", "Tot_Srvcs": "10",
         "Tot_Benes": "10", "Avg_Mdcr_Alowd_Amt": "70"},   # bad NPI → quarantine
    ])


def test_partb_metrics_real_headers():
    metrics, quarantined = compute_partb_metrics(_partb_frame())
    assert quarantined == 1
    m = metrics.set_index("npi")
    assert m.loc["1003000415", "em_high_level_share"] == 0.9      # 900/1000
    assert m.loc["1003000407", "em_high_level_share"] == pytest.approx(100 / 600)
    assert m.loc["1003000415", "code_concentration_hhi"] > \
           m.loc["1003000407", "code_concentration_hhi"]


def test_partd_metrics_real_headers():
    raw = pd.DataFrame([
        # brand-heavy prescriber: brand (Brnd != Gnrc) and expensive
        {"Prscrbr_NPI": "1003000415", "Brnd_Name": "ELIQUIS",
         "Gnrc_Name": "APIXABAN", "Tot_Clms": "100", "Tot_Drug_Cst": "50000"},
        {"Prscrbr_NPI": "1003000415", "Brnd_Name": "LISINOPRIL",
         "Gnrc_Name": "LISINOPRIL", "Tot_Clms": "50", "Tot_Drug_Cst": "500"},
        # generic-heavy prescriber
        {"Prscrbr_NPI": "1003000407", "Brnd_Name": "METFORMIN",
         "Gnrc_Name": "METFORMIN", "Tot_Clms": "200", "Tot_Drug_Cst": "1000"},
    ])
    metrics, quarantined = compute_partd_metrics(raw)
    assert quarantined == 0
    m = metrics.set_index("npi")
    assert m.loc["1003000415", "brand_generic_cost_ratio"] == 100.0   # 50000/500
    assert pd.isna(m.loc["1003000407", "brand_generic_cost_ratio"]) or \
        m.loc["1003000407", "brand_generic_cost_ratio"] == 0.0


def test_dmepos_metrics_real_headers():
    raw = pd.DataFrame([
        # high-cost-item biller (expensive braces dominate)
        {"Rfrg_NPI": "1003000415", "HCPCS_Cd": "L0650", "Tot_Suplr_Srvcs": "500",
         "Avg_Suplr_Mdcr_Alowd_Amt": "900"},
        {"Rfrg_NPI": "1003000415", "HCPCS_Cd": "A4550", "Tot_Suplr_Srvcs": "50",
         "Avg_Suplr_Mdcr_Alowd_Amt": "20"},
        # cheap-supplies biller
        {"Rfrg_NPI": "1003000407", "HCPCS_Cd": "A4550", "Tot_Suplr_Srvcs": "400",
         "Avg_Suplr_Mdcr_Alowd_Amt": "20"},
    ])
    metrics, _ = compute_dmepos_metrics(raw, high_cost_decile=0.6)
    m = metrics.set_index("npi")
    assert m.loc["1003000415", "dme_high_cost_item_share"] > 0.99
    assert m.loc["1003000407", "dme_high_cost_item_share"] == 0.0


def test_peer_percentiles_and_org_rollup(graph):
    metrics, _ = compute_partb_metrics(_partb_frame())
    pct = to_peer_percentiles(metrics, ["em_high_level_share", "code_concentration_hhi"],
                              provider_dim=None, min_peer_count=1)
    assert pct["em_high_level_share"].between(0, 1).all()
    p = pct.set_index("npi")
    assert p.loc["1003000415", "em_high_level_share"] > \
           p.loc["1003000407", "em_high_level_share"]

    org = rollup_to_org(pct, graph["npi_to_org"])
    assert "org_node_id" in org.columns and len(org) == 2
