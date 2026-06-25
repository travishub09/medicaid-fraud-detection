"""
test_expected_billing.py — the expected-billing "digital twin" residual (Pillar 4).

A counterfactual, not a percentile: model what a provider of this specialty/size/
breadth would be expected to bill, and score only the UNEXPLAINED excess. The big
legitimate biller (dollars proportional to volume) is NOT flagged; the provider
billing far more than its volume justifies is.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.entity_graph.__main__ import run as run_graph
from src.model_a.expected_billing import expected_billing_residual
from src.model_a.provider_features_export import build_provider_matrix
from tests.fixtures.synthetic import build_synthetic_inputs, build_provider_leads


def _cohort(n=40, seed=0):
    rng = np.random.default_rng(seed)
    vol = rng.integers(100, 1000, n).astype(float)
    # honest billing ~ proportional to volume; a large biller is still honest
    net = vol * 100.0 + rng.normal(0, 500, n)
    df = pd.DataFrame({
        "npi": [f"n{i:04d}" for i in range(n)],
        "primary_taxonomy": ["T"] * n,
        "net_paid": net.clip(min=1),
        "service_volume": vol,
        "total_claim_lines": vol * 2,
        "n_distinct_hcpcs": rng.integers(3, 12, n),
    })
    return df


def test_residual_flags_unexplained_excess_not_size():
    df = _cohort()
    # a HUGE honest biller (10x volume, 10x dollars — earned) and a SMALL inflater
    df.loc[0, ["service_volume", "total_claim_lines", "net_paid"]] = [9000, 18000, 900000]
    df.loc[1, ["service_volume", "total_claim_lines", "net_paid"]] = [200, 400, 600000]  # 30x expected
    out = expected_billing_residual(df).set_index("npi")
    big_honest = out.loc["n0000", "billing_residual"]
    small_inflater = out.loc["n0001", "billing_residual"]
    assert small_inflater > 0.9              # unexplained excess → top of the residual
    assert big_honest < 0.8                  # large but explained by volume → not flagged
    assert small_inflater > big_honest


def test_residual_nan_without_covariates():
    df = pd.DataFrame({"npi": ["a", "b"], "primary_taxonomy": ["T", "T"]})
    out = expected_billing_residual(df)
    assert out["billing_residual"].isna().all()   # no covariates → unscored, not forced


def test_export_carries_billing_residual(tmp_path):
    inputs = build_synthetic_inputs()
    g = run_graph(inputs, tmp_path / "graph")
    leads = build_provider_leads(inputs["provider_dim"])
    matrix, manifest = build_provider_matrix(
        leads, g["npi_to_org"], org_graph_features=g["org_graph_features"], min_peer=5)
    assert "billing_residual" in matrix.columns
    assert "billing_residual" in manifest["raw_feature_cols"]   # clean, size-adjusted feature
