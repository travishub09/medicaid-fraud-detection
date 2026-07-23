"""
test_source_ablation.py — the core-vs-full ablation runs and reports a delta.

Synthetic matrix where an EXTRA feature genuinely carries signal the core
features lack, so full should beat core; plus the markdown renders.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.source_ablation import run_source_ablation, to_markdown


def _matrix(n=600, seed=0):
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, n)
    # core features: weak/noisy; one extra feature strongly tracks y
    return pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "group_id": [f"g{i % 120}" for i in range(n)],
        "provider_on_exclusion": y,
        "shell_score": rng.normal(0, 1, n) + 0.1 * y,          # core, weak
        "net_paid": rng.normal(0, 1, n),                        # core, noise
        "op_payment_concentration": rng.normal(0, 1, n) + 1.5 * y,  # extra, strong
    })


def _manifest():
    return {
        "raw_feature_cols": ["shell_score", "net_paid", "op_payment_concentration"],
        "peerpct_cols": [], "subscore_cols": [], "embedding_cols": [],
        "leakage_hard": [],
        "sources_used": {
            "entity_graph": ["shell_score"], "spending": ["net_paid"],
            "open_payments": ["op_payment_concentration"]},
        "scheme_coverage": {},
    }


def test_ablation_runs_and_full_beats_core_when_extra_has_signal():
    res = run_source_ablation(_matrix(), _manifest(), splits=2, seeds=(0, 1))
    assert res["n_core"] == 2 and res["n_extra"] == 1
    # full (with the strong extra feature) should out-ROC core-only
    assert res["full"]["roc_auc"] > res["core"]["roc_auc"]
    # open_payments should carry a positive marginal
    assert res["per_group"]["open_payments"]["marginal"]["delta"] > 0
    assert res["per_group"]["open_payments"]["doj_deferred"] is True


def test_markdown_renders_decision_language():
    res = run_source_ablation(_matrix(), _manifest(), splits=2, seeds=(0,))
    md = to_markdown(res)
    assert "Full minus core" in md and "leave-one-group-out" in md
    assert "open_payments" in md
