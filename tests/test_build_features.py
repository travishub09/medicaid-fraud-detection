"""test_build_features.py — per-NPI v3 concepts → per-org company features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.build_features import build_company_features


def test_rollup_takes_max_concept_and_sums_payments():
    leads = pd.DataFrame({
        "npi": ["1", "2", "3"],
        "concentration":      [0.90, 0.20, 0.50],
        "payment_intensity":  [0.10, 0.95, 0.30],
        "service_intensity":  [0.40, 0.40, 0.40],
        "specialty_mismatch": [np.nan, 0.70, 0.10],   # NaN member ignored by max
        "temporal":           [0.20, 0.30, 0.60],
        "net_paid":           [100.0, 200.0, 50.0],
    })
    xw = pd.DataFrame({"npi": ["1", "2", "3"],
                       "org_node_id": ["org:A", "org:A", "org:B"]})
    out = build_company_features(leads, xw).set_index("org_node_id")
    assert out.loc["org:A", "concentration"] == 0.90        # max over members
    assert out.loc["org:A", "payment_intensity"] == 0.95
    assert out.loc["org:A", "specialty_mismatch"] == 0.70   # NaN skipped
    assert out.loc["org:A", "payments"] == 300.0            # summed net_paid
    assert out.loc["org:B", "payments"] == 50.0
    assert out.index.is_unique                               # one row per org


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
