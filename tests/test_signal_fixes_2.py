"""
test_signal_fixes_2.py — second signal-fix batch (analytics review + audit):

  1. Digital twin: volume_residual catches volume inflation the price twin
     conditions away; missing-taxonomy rows never form a fake cohort; fallback
     residuals rank in their own pool.
  2. peers.assign_peer_groups: Python None / "None" never forms a peer cell.
  3. growth.new_code_burst: young orgs (no pre-window history) are NaN, not ≈1.
  4. retrospective harness: ablation grid runs end-to-end and exposes a planted
     circular-negatives inflation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.expected_billing import expected_billing_residual, volume_residual
from src.analytics.peers import assign_peer_groups
from src.analytics.growth import growth_features
from src.model_a.retrospective import run_retrospective, write_report


def _twin_matrix(n_peers: int = 60) -> pd.DataFrame:
    """One taxonomy of honest providers + one volume-inflater whose price per
    unit is normal (the case the price twin is blind to by construction)."""
    rng = np.random.default_rng(7)
    tenure = rng.integers(24, 120, n_peers).astype(float)
    volume = tenure * 50 + rng.normal(0, 50, n_peers)      # volume tracks tenure
    df = pd.DataFrame({
        "npi": [f"1{i:09d}" for i in range(n_peers)],
        "primary_taxonomy": "207Q00000X",
        "tenure_months": tenure,
        "n_active_months": np.minimum(tenure, 84.0),
        "org_member_count": 1.0,
        "service_volume": np.maximum(volume, 10.0),
        "total_claim_lines": np.maximum(volume, 10.0),
        "n_distinct_hcpcs": 12.0,
        "net_paid": np.maximum(volume, 10.0) * 20.0,        # flat $20/unit price
    })
    # the inflater: young practice, 40x the volume its tenure explains, at a
    # perfectly normal price per unit
    df.loc[0, ["tenure_months", "n_active_months"]] = 24.0
    df.loc[0, "service_volume"] = df.loc[0, "total_claim_lines"] = 48_000.0
    df.loc[0, "net_paid"] = 48_000.0 * 20.0
    return df


def test_volume_twin_catches_what_price_twin_launders():
    df = _twin_matrix()
    price = expected_billing_residual(df).set_index("npi")
    vol = volume_residual(df).set_index("npi")
    inflater = "1000000000"
    # price twin: normal $/unit → mid-pack, NOT flagged (the laundering)
    assert price.loc[inflater, "billing_residual"] < 0.9
    # volume twin: 40x the volume tenure explains → top of the cohort
    assert vol.loc[inflater, "volume_residual"] > 0.95


def test_twin_missing_taxonomy_is_not_a_cohort():
    df = _twin_matrix()
    df["primary_taxonomy"] = None                    # nobody has a taxonomy
    out = volume_residual(df).set_index("npi")
    # rows still score (global fallback pool) — but check no fake "None" cohort
    # produced a degenerate all-equal ranking: the inflater still tops the pool
    assert out.loc["1000000000", "volume_residual"] > 0.95


def test_peer_groups_none_is_incomplete():
    df = pd.DataFrame({
        "taxonomy_code": [None, None, None, "X", "X", "X"] * 10,
        "entity_type": ["1"] * 60,
        "state": ["TX"] * 60,
    })
    out = assign_peer_groups(df, min_peer=5)
    # None-taxonomy rows must be NOT SCORED, never a "None" peer cell
    none_rows = out[df["taxonomy_code"].isna()]
    assert (none_rows["peer_level"] == -1).all()
    assert (~none_rows["peer_id"].str.contains("None", na=False)).all()


def test_new_code_burst_requires_pre_window_history():
    # 9 months of history: enough for level-shift, NOT enough pre-window months
    rows = []
    for m in range(1, 10):
        rows.append({"billing_npi": "1000000001", "service_month": f"2024-{m:02d}",
                     "total_paid": 1000.0, "hcpcs_code": f"9921{m}"})
    spending = pd.DataFrame(rows)
    xw = pd.DataFrame({"npi": ["1000000001"], "org_node_id": ["org:a"]})
    out = growth_features(spending, xw).set_index("org_node_id")
    assert np.isnan(out.loc["org:a", "new_code_burst"])   # young org: unscored


def _retro_matrix(n: int = 3000) -> tuple[pd.DataFrame, dict]:
    """Planted setup: f1 carries real (weak) signal; anomaly_score is built FROM
    f1, so zero-signal negatives make separation artificially easy."""
    rng = np.random.default_rng(0)
    label = (rng.random(n) < 0.05).astype(int)
    f1 = rng.normal(0, 1, n) + label * 0.8               # weak true signal
    f2 = rng.normal(0, 1, n)                             # noise
    anomaly = (f1 > 1.0).astype(float)                   # derived from f1
    df = pd.DataFrame({
        "npi": [f"1{i:09d}" for i in range(n)],
        "f1": f1, "f2": f2,
        "subscore_x": (f1 > 0.5).astype(float),
        "anomaly_score": anomaly,
        "provider_on_exclusion": label,
        "group_id": [f"g{i % 400}" for i in range(n)],
    })
    manifest = {
        "label": "provider_on_exclusion",
        "raw_feature_cols": ["f1", "f2"],
        "peerpct_cols": [],
        "subscore_cols": ["subscore_x"],
        "leakage_hard": [], "leakage_adjacent": [],
        "identifier_cols": ["npi"], "label_metadata": [],
        "group_cols": ["group_id"],
    }
    return df, manifest


def test_retrospective_grid_exposes_circular_negatives(tmp_path):
    df, manifest = _retro_matrix()
    grid = run_retrospective(df, manifest, seed=0)
    # full grid: 2 negative schemes x 2 feature sets x 2 splits
    ran = grid[grid.note == ""]
    assert len(ran) == 8
    assert ran["pr_auc"].between(0, 1).all()
    cell = lambda n, s: float(ran[(ran.negatives == n) & (ran.split == s)
                                  & (ran.features == "raw+peerpct")].pr_auc.iloc[0])
    # zero-signal negatives (chosen on a score built FROM f1) must inflate
    # PR-AUC vs fair random negatives — the circularity the harness exists to expose
    assert cell("zero_signal", "random") > cell("random", "random") + 0.05
    report = write_report(grid, tmp_path / "RETRO_REPORT.md")
    assert "circularity check" in report
    assert (tmp_path / "RETRO_REPORT.md").exists()
