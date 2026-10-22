"""
test_prune_ab_2026_10.py — the prune A/B (v21): ONE prune rule in scheme_health,
a drop_schemes training policy that holds the named subscores out of the scorer
(manifest or CLI override), and the registry spelling.
"""

from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pandas as pd


def _health():
    return {"schemes": [
        {"scheme": "pill_mill", "status": "HEALTHY", "label_auc": 0.524, "n_label_pos": 699,
         "fca_auc": 0.572, "n_fca_pos": 317, "fca_verdict": "coin-flip"},
        {"scheme": "specialty_mismatch", "status": "HEALTHY", "label_auc": 0.616, "n_label_pos": 2464,
         "fca_auc": 0.649, "n_fca_pos": 912, "fca_verdict": "signal"},
        {"scheme": "nemt_fraud", "status": "HEALTHY", "label_auc": 0.525, "n_label_pos": 79,
         "fca_auc": 0.636, "n_fca_pos": 13, "fca_verdict": "thin"},            # unjudged, never pruned
        {"scheme": "invalid_identity", "status": "DEGRADED", "label_auc": 0.879, "n_label_pos": 240,
         "fca_auc": 0.547, "n_fca_pos": 9, "fca_verdict": "thin"},
        {"scheme": "upcoding", "status": "HEALTHY", "label_auc": 0.53, "n_label_pos": 131,
         "fca_auc": 0.444, "n_fca_pos": 53, "fca_verdict": "coin-flip"},
        {"scheme": "dormant_x", "status": "DORMANT", "label_auc": None, "n_label_pos": None,
         "fca_verdict": None},
        {"scheme": "tiny", "status": "HEALTHY", "label_auc": 0.40, "n_label_pos": 12,
         "fca_auc": 0.41, "n_fca_pos": 30, "fca_verdict": "inverted"},        # < 20 label positives
    ]}


def test_prune_rule_is_one_rule(tmp_path):
    from src.model_a.scheme_health import prune_candidates
    assert prune_candidates(_health()) == ["pill_mill", "upcoding"]
    assert prune_candidates({}) == [] and prune_candidates({"schemes": []}) == []
    # the CLI prints the comma list for the runners (no matrix needed)
    p = tmp_path / "scheme_health.json"
    p.write_text(json.dumps(_health()))
    out = subprocess.run([sys.executable, "-m", "src.model_a.scheme_health", "--prune-list", str(p)],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == "pill_mill,upcoding"
    out = subprocess.run([sys.executable, "-m", "src.model_a.scheme_health", "--prune-list", str(tmp_path / "missing.json")],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == ""
    # run_issues reads the same rule
    from src.model_a.run_issues import collect_issues
    d = tmp_path / "frozen_2021-12_vY"
    d.mkdir()
    (d / "feature_manifest.json").write_text(json.dumps({
        "asof_cutoff": "2021-12", "label": "provider_on_exclusion", "n_providers": 10,
        "expectations": {"fails": 0, "warns": 0}, "sources_audit": [],
        "feature_partition": {"unmapped": []}, "graph_substrate": {"address_layer_frozen": True}}))
    (d / "scheme_health.json").write_text(json.dumps(_health()))
    issues, _ = collect_issues(d, prereg=None)
    pruned = {i["id"] for i in issues if i["id"].endswith(":prune_candidate")}
    assert pruned == {"scheme:pill_mill:prune_candidate", "scheme:upcoding:prune_candidate"}


def test_drop_schemes_policy_holds_subscores_out_of_both_fold_loops(monkeypatch):
    import src.model_a.company_list as cl
    from src.model_a import accuracy_diagnostics as ad
    from src.model_a import network_ab as nab
    widths = []

    def fake_fit(Xtr, ytr, Xte, seed=0, sample_weight=None, **_):
        widths.append(Xtr.shape[1])
        return np.linspace(0, 1, len(Xte))

    monkeypatch.setattr(cl, "_fit_predict", fake_fit)
    monkeypatch.setattr(nab, "_fit_predict", fake_fit)
    rng = np.random.default_rng(3)
    n = 240
    y = rng.integers(0, 2, n)
    m = pd.DataFrame({"npi": [f"{1000000000 + i}" for i in range(n)],
                      "org_node_id": [f"org:{i % 30}" for i in range(n)],
                      "group_id": [f"org:{i % 30}" for i in range(n)],
                      "org_display_name": [f"ORG {i % 30}" for i in range(n)],
                      "provider_on_exclusion": y, "net_paid": 1e5,
                      "shell_score": rng.normal(size=n) + 0.5 * y,
                      "subscore_pill_mill": rng.uniform(size=n),
                      "subscore_upcoding": rng.uniform(size=n),
                      "subscore_specialty_mismatch": rng.uniform(size=n)})
    man = {"raw_feature_cols": ["shell_score"], "peerpct_cols": [],
           "subscore_cols": ["subscore_pill_mill", "subscore_upcoding", "subscore_specialty_mismatch"],
           "embedding_cols": [], "leakage_hard": [], "sources_used": {"entity_graph": ["shell_score"]},
           "scheme_coverage": {"pill_mill": ["shell_score"], "upcoding": ["shell_score"],
                               "specialty_mismatch": ["shell_score"]},
           "training_policy": {"n_seeds": 1, "broadcast_rows": "weight", "group_policy": "org",
                               "drop_schemes": ["pill_mill", "upcoding"]}}
    cl.set_training_policy(None, None, None, None)
    try:
        assert cl.training_policy(man)["drop_schemes"] == ["pill_mill", "upcoding"]
        cl.oof_scores(m, man, n_splits=2)
        assert widths and all(w == 2 for w in widths)                 # shell_score + specialty_mismatch
        widths.clear()
        # the diagnostics' column screen applies the same policy
        y_arr = np.asarray(y)
        cols = ad._screened_columns(m, man, y_arr)
        assert "subscore_pill_mill" not in cols and "subscore_specialty_mismatch" in cols
        # CLI override: '' = keep every scheme even though the manifest drops two
        cl.set_training_policy(drop_schemes="")
        assert cl.training_policy(man)["drop_schemes"] == []
        cl.oof_scores(m, man, n_splits=2)
        assert all(w == 4 for w in widths)
        widths.clear()
        # an override list of one; a scheme with no column is reported, not an error
        cl.set_training_policy(drop_schemes="specialty_mismatch, dormant_x")
        cl.oof_scores(m, man, n_splits=2)
        assert all(w == 3 for w in widths)
    finally:
        cl.set_training_policy(None, None, None, None)
    assert cl.parse_schemes(" Pill_Mill ,none, upcoding") == ["pill_mill", "upcoding"]
    from src.model_a.run_registry import CHECK_ALIASES
    assert CHECK_ALIASES["prune"] == "drop_schemes"
