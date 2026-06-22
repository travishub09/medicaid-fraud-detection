"""test_build_features.py — per-NPI v3 concepts → per-org company features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.build_features import build_company_features


def test_rollup_paid_weighted_mean_and_sums_payments():
    leads = pd.DataFrame({
        "npi": ["1", "2", "3"],
        "concentration":      [0.90, 0.20, 0.50],
        "payment_intensity":  [0.10, 0.95, 0.30],
        "service_intensity":  [0.40, 0.40, 0.40],
        "specialty_mismatch": [np.nan, 0.70, 0.10],   # NaN member: no weight/value
        "temporal":           [0.20, 0.30, 0.60],
        "net_paid":           [100.0, 200.0, 50.0],
    })
    xw = pd.DataFrame({"npi": ["1", "2", "3"],
                       "org_node_id": ["org:A", "org:A", "org:B"]})
    out = build_company_features(leads, xw).set_index("org_node_id")
    # org:A = paid-weighted mean over members 1 (w100) & 2 (w200):
    assert abs(out.loc["org:A", "concentration"] - (0.9*100 + 0.2*200)/300) < 1e-9
    assert abs(out.loc["org:A", "payment_intensity"] - (0.1*100 + 0.95*200)/300) < 1e-9
    assert abs(out.loc["org:A", "specialty_mismatch"] - 0.70) < 1e-9   # only NPI2 weighted
    assert out.loc["org:A", "payments"] == 300.0                       # summed net_paid
    assert out.loc["org:B", "payments"] == 50.0
    assert out.index.is_unique                                          # one row per org
    # a large aggregator no longer pins at 1.0: a single fluke member can't dominate
    big = pd.DataFrame({"npi": [str(i) for i in range(100)],
                        "concentration": [0.99] + [0.10]*99,
                        "payment_intensity": [0.5]*100, "service_intensity": [0.5]*100,
                        "specialty_mismatch": [0.5]*100, "temporal": [0.5]*100,
                        "net_paid": [10.0]*100})
    bxw = pd.DataFrame({"npi": [str(i) for i in range(100)],
                        "org_node_id": ["org:big"]*100})
    bout = build_company_features(big, bxw).set_index("org_node_id")
    assert bout.loc["org:big", "concentration"] < 0.2     # was 0.99 under max-rollup


def test_npi_absent_from_crosswalk_is_dropped():
    leads = pd.DataFrame({
        "npi": ["1", "9"], "concentration": [0.5, 0.5],
        "payment_intensity": [0.5, 0.5], "service_intensity": [0.5, 0.5],
        "specialty_mismatch": [0.5, 0.5], "temporal": [0.5, 0.5],
        "net_paid": [10.0, 10.0]})
    xw = pd.DataFrame({"npi": ["1"], "org_node_id": ["org:A"]})
    out = build_company_features(leads, xw)
    assert list(out["org_node_id"]) == ["org:A"]
    assert out["payments"].iloc[0] == 10.0


def test_load_spending_aggregated_sums_to_grain(tmp_path):
    """DuckDB stream-aggregation collapses servicing NPI + drops heavy columns,
    summing total_paid to the (billing_npi, service_month, hcpcs) grain."""
    from src.model_a.exposure import load_spending_aggregated
    raw = pd.DataFrame({
        "billing_npi": ["1", "1", "1", "2"],
        "servicing_npi": ["a", "b", "a", "c"],          # collapsed away
        "service_month": ["2023-01", "2023-01", "2023-02", "2023-01"],
        "hcpcs_code": ["X", "X", "X", "Y"],
        "total_paid": [10.0, 5.0, 7.0, 3.0],
        "org_legal_name": ["N", "N", "N", "M"],         # heavy col, not read
    })
    p = tmp_path / "spending_fact.parquet"
    raw.to_parquet(p, index=False)
    out = load_spending_aggregated(str(p)).set_index(
        ["billing_npi", "service_month", "hcpcs_code"])
    assert set(out.columns) == {"total_paid"}
    assert out.loc[("1", "2023-01", "X"), "total_paid"] == 15.0   # summed servicing
    assert out.loc[("1", "2023-02", "X"), "total_paid"] == 7.0
    assert out.loc[("2", "2023-01", "Y"), "total_paid"] == 3.0
    assert len(out) == 3


def test_duckdb_exposure_matches_pandas(tmp_path):
    """annual_payments_per_org_duckdb returns the same per-org mean-annual
    payments and reconciliation as the pandas reference, without loading
    spending into pandas."""
    from src.model_a.exposure import (annual_payments_per_org,
                                      annual_payments_per_org_duckdb)
    spend = pd.DataFrame({
        "billing_npi":  ["1", "1", "1", "2", "9"],     # npi 9 unresolved
        "service_month": ["2022-03", "2022-07", "2023-02", "2023-05", "2023-01"],
        "hcpcs_code":   ["A", "A", "B", "C", "D"],
        "total_paid":   [100.0, 200.0, 400.0, 50.0, 999.0],
    })
    p = tmp_path / "spending_fact.parquet"
    spend.to_parquet(p, index=False)
    xw = pd.DataFrame({"npi": ["1", "2"], "org_node_id": ["org:A", "org:B"]})

    a_pd, r_pd = annual_payments_per_org(spend.copy(), xw)
    a_db, r_db = annual_payments_per_org_duckdb(str(p), xw)
    a_pd = a_pd.set_index("org_node_id"); a_db = a_db.set_index("org_node_id")
    # org:A mean annual = (2022: 300 + 2023: 400) / 2 = 350 ; org:B = 50
    assert abs(a_db.loc["org:A", "payments"] - 350.0) < 1e-6
    assert abs(a_db.loc["org:B", "payments"] - 50.0) < 1e-6
    assert abs(a_pd.loc["org:A", "payments"] - a_db.loc["org:A", "payments"]) < 1e-6
    assert r_db["unresolved_npis"] == 1
    assert abs(r_db["total_unresolved"] - 999.0) < 1e-6
    assert abs(r_pd["total_matched"] - r_db["total_matched"]) < 1e-6
