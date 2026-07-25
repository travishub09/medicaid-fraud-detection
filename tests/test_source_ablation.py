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


def test_prospective_label_read_correctly_not_zero_positives():
    """The forward-label CSV is (npi, first_excl_date, ...,
    is_prospective_positive, was_excluded_pre_cutoff). Reading the FIRST non-npi
    column (a date) collapsed every provider to 0; the label must come from
    is_prospective_positive, and pre-cutoff-excluded rows must be dropped."""
    m = _matrix()
    npis = list(m["npi"])
    fl = pd.DataFrame({
        "npi": npis[:10],
        "first_excl_date": ["2024-03-01"] * 6 + [""] * 4,   # the decoy first column
        "is_prospective_positive": [1, 1, 1, 1, 1, 1, 0, 0, 0, 0],
        "was_excluded_pre_cutoff": [0, 0, 0, 0, 0, 0, 1, 1, 1, 1],
    })
    res = run_source_ablation(m, _manifest(), future_label=fl,
                              splits=2, seeds=(0,))
    # 6 forward positives (not 0), and the 4 pre-cutoff-excluded are dropped
    assert res["positives"] == 6
    assert res["n"] == len(m) - 4
    assert res["label"] == "forward:is_prospective_positive"


def test_cluster_draw_keeps_groups_whole_and_counts_duplicates():
    """The bootstrap must resample GROUPS, not rows, and a group drawn twice must
    contribute its rows TWICE. The old np.isin/set-membership version dropped the
    duplicate, which is not a cluster bootstrap."""
    from src.model_a.source_ablation import _group_index, _draw_cluster

    g = np.array(["a", "a", "a", "b", "c", "c"])          # text ids, ragged sizes
    order, starts, counts = _group_index(g)
    assert sorted(counts.tolist()) == [1, 2, 3]

    # draw the 3-row group twice and the 1-row group once
    code_of = {g[order[starts[i]]]: i for i in range(len(counts))}
    pick = np.array([code_of["a"], code_of["a"], code_of["b"]])
    idx = _draw_cluster(order, starts, counts, pick)
    got = g[idx]
    assert len(idx) == 7                                   # 3 + 3 + 1, duplicates kept
    assert (got == "a").sum() == 6 and (got == "b").sum() == 1
    # rows of a group always travel together (never split)
    assert sorted(idx[:3].tolist()) == sorted(idx[3:6].tolist())


def test_cluster_draw_empty_pick_is_safe():
    from src.model_a.source_ablation import _group_index, _draw_cluster

    order, starts, counts = _group_index(np.array(["a", "b"]))
    idx = _draw_cluster(order, starts, counts, np.array([], dtype=int))
    assert len(idx) == 0


def test_zero_positive_label_raises():
    import pytest
    m = _matrix()
    fl = pd.DataFrame({"npi": list(m["npi"])[:5],
                       "first_excl_date": ["2024-01-01"] * 5,
                       "is_prospective_positive": [0, 0, 0, 0, 0],
                       "was_excluded_pre_cutoff": [0, 0, 0, 0, 0]})
    with pytest.raises(ValueError):
        run_source_ablation(m, _manifest(), future_label=fl, splits=2, seeds=(0,))
