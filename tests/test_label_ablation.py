"""
test_label_ablation.py — the label-source ablation: variants built correctly,
the harness runs end to end, and the report renders honest language.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.model_a.label_ablation import (label_variants, run_label_ablation,
                                        to_markdown, _forward_eval,
                                        _source_sets)


def _matrix(n=600, seed=0):
    """Synthetic frozen matrix. LEIE positives track a real feature; a noisy
    fake source labels random providers, so dropping it should not hurt."""
    rng = np.random.default_rng(seed)
    signal = rng.normal(0, 1, n)
    leie = (signal > 1.2).astype(int)                     # ~11% — real pattern
    noise_src = (rng.random(n) < 0.08).astype(int)        # random labels
    y = (leie | noise_src).astype(int)
    prov = np.where(leie & noise_src, "leie;noisy",
                    np.where(leie == 1, "leie",
                             np.where(noise_src == 1, "noisy", "")))
    return pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "group_id": [f"g{i % 120}" for i in range(n)],
        "provider_on_exclusion": y,
        "exclusion_label_sources": prov,
        "shell_score": signal + rng.normal(0, 0.5, n),
        "net_paid": rng.normal(0, 1, n),
    }), signal


def _manifest():
    return {
        "raw_feature_cols": ["shell_score", "net_paid"],
        "peerpct_cols": [], "subscore_cols": [], "embedding_cols": [],
        "leakage_hard": [], "leakage_adjacent": [],
        "sources_used": {"entity_graph": ["shell_score"],
                         "spending": ["net_paid"]},
        "scheme_coverage": {},
    }


def _future_label(m, signal, seed=1):
    """Forward positives follow the same signal (so a model trained on the
    real source ranks them); pre-cutoff flags mark the LEIE positives."""
    rng = np.random.default_rng(seed)
    fwd = ((signal > 0.9) & (m["provider_on_exclusion"].to_numpy() == 0)
           & (rng.random(len(m)) < 0.5)).astype(int)
    pre = m["exclusion_label_sources"].str.contains("leie").astype(int)
    return pd.DataFrame({"npi": m["npi"],
                         "is_prospective_positive": fwd.astype(str),
                         "was_excluded_pre_cutoff": pre.astype(str)})


def test_source_sets_and_variants():
    m, _ = _matrix()
    masks = _source_sets(m)
    assert set(masks) == {"leie", "noisy"}
    v = label_variants(m)
    assert set(v) == {"all", "minus_leie", "minus_noisy", "leie_only"}
    # dual-labeled providers survive a single-source drop
    dual = m["exclusion_label_sources"].eq("leie;noisy").to_numpy()
    assert (v["minus_noisy"]["y"][dual] == 1).all()
    assert (v["minus_leie"]["y"][dual] == 1).all()
    only_noisy = m["exclusion_label_sources"].eq("noisy").to_numpy()
    assert (v["minus_noisy"]["y"][only_noisy] == 0).all()
    # leie_only == the leie mask exactly
    assert (v["leie_only"]["y"] == masks["leie"].astype(int)).all()


def test_variant_requires_provenance_column():
    m, _ = _matrix()
    with pytest.raises(ValueError, match="exclusion_label_sources"):
        _source_sets(m.drop(columns=["exclusion_label_sources"]))


def test_forward_eval_drops_precutoff_not_from_training():
    m, signal = _matrix()
    fl = _future_label(m, signal)
    y_fwd, eval_mask = _forward_eval(m, fl)
    pre = m["exclusion_label_sources"].str.contains("leie").to_numpy()
    assert (~eval_mask == pre).all()            # exactly the pre-cutoff set
    assert y_fwd[eval_mask].sum() > 0


def test_ablation_end_to_end_and_report():
    m, signal = _matrix()
    fl = _future_label(m, signal)
    res = run_label_ablation(m, _manifest(), fl, n_splits=3, n_boot=60)
    v = res["variants"]
    assert set(v) == {"all", "minus_leie", "minus_noisy", "leie_only"}
    assert v["all"]["train_positives"] > v["leie_only"]["train_positives"] * 0.9
    for name, r in v.items():
        if name != "all":
            assert "delta_vs_all" in r and "lift10" in r["delta_vs_all"]
    md = to_markdown(res)
    assert "LABEL ABLATION" in md and "minus_noisy" in md
    assert "EVIDENCE, not" in md                # the honest-reading note
    assert "verdict" in md


def test_overlap_guard_reports_attacked_lift():
    """A training positive that is also a forward positive must be counted and
    the overlap-removed lift reported."""
    m, signal = _matrix()
    fl = _future_label(m, signal)
    # force one clean provider to be positive in BOTH training and forward
    idx = int(np.argmax((m["provider_on_exclusion"].to_numpy() == 0)
                        & (fl["is_prospective_positive"].to_numpy() == "1")))
    m.loc[idx, "provider_on_exclusion"] = 1
    m.loc[idx, "exclusion_label_sources"] = "noisy"
    res = run_label_ablation(m, _manifest(), fl, n_splits=3, n_boot=40,
                             only={"minus_noisy"})
    assert res["variants"]["all"]["overlap_with_forward"] >= 1
    assert res["variants"]["all"].get("lift_overlap_removed") is not None
    md = to_markdown(res)
    assert "overlap-removed" in md
