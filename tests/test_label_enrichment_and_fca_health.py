"""
test_label_enrichment_and_fca_health.py — the measurement block (2026-10-14).

scheme_health grades every scheme on the FORWARD FCA ruler too (fca_auc / fca_verdict,
known-at-freeze rows dropped); label_enrichment finds the code / taxonomy / size
pattern the forward cases concentrate on, with counts and a tail probability.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from src.model_a.label_enrichment import (build_report, enrich_dimension, run as run_enrichment,
                                          to_markdown)
from src.model_a.scheme_health import audit_scheme_health, forward_label_vector, to_markdown as health_md


def _matrix(n: int = 400, seed: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    npi = [f"{1000000000 + i}" for i in range(n)]
    m = pd.DataFrame({
        "npi": npi,
        "primary_taxonomy": rng.choice(["207Q00000X", "363L00000X", "251E00000X"], n),
        "practice_state": rng.choice(["FL", "GA", "TX"], n),
        "entity_type": rng.choice(["1", "2"], n),
        "gross_paid": rng.lognormal(13, 1.2, n),
        "tenure_months": rng.integers(1, 150, n),
        "n_distinct_hcpcs": rng.integers(1, 120, n),
        "provider_on_exclusion": 0,
        "subscore_good": rng.uniform(0, 1, n),
        "subscore_noise": rng.uniform(0, 1, n),
    })
    # forward positives: 40 providers, concentrated in 251E (home health) and in the top of subscore_good
    hh_pool = [i for i in np.where(m["primary_taxonomy"] == "251E00000X")[0] if i > 1]
    hh = list(rng.choice(hh_pool, 30, replace=False))
    others = [i for i in range(n) if i not in set(hh) and i > 1]
    pos_idx = hh + list(rng.choice(others, 10, replace=False))
    assert len(set(pos_idx)) == 40
    m.loc[pos_idx, "subscore_good"] = rng.uniform(0.85, 1.0, len(pos_idx))
    known = [npi[0], npi[1]]                      # known at the freeze: dropped from the ruler
    fl = pd.DataFrame({"npi": [npi[i] for i in pos_idx] + known,
                       "is_prospective_positive": [1] * len(pos_idx) + [0, 0],
                       "was_excluded_pre_cutoff": [0] * len(pos_idx) + [1, 1]})
    return m, fl


def test_forward_label_vector_drops_known_at_freeze():
    m, fl = _matrix()
    y, keep = forward_label_vector(m, fl)
    assert y.sum() == 40 and (~keep).sum() == 2 and not keep[0] and not keep[1]
    assert forward_label_vector(m, None) == (None, None)


def test_scheme_health_grades_on_the_fca_ruler_too():
    m, fl = _matrix()
    weights = {"good": {"subscore_good": 1.0}, "noise": {"subscore_noise": 1.0}}
    manifest = {"label": "provider_on_exclusion"}
    df = audit_scheme_health(m, manifest, weights=weights, forward=fl).set_index("scheme")
    assert df.loc["good", "fca_auc"] > 0.9 and df.loc["good", "fca_verdict"] == "signal"
    assert df.loc["good", "n_fca_pos"] == 40
    assert 0.35 < df.loc["noise", "fca_auc"] < 0.65 and df.loc["noise", "fca_verdict"] == "coin-flip"
    md = health_md(df.reset_index(), [])
    assert "FCA AUC" in md and "| good |" in md
    # without a forward file the columns are absent from the table and the markdown
    df0 = audit_scheme_health(m, manifest, weights=weights)
    assert df0["fca_auc"].isna().all() and "FCA AUC" not in health_md(df0, [])


def test_enrichment_finds_the_planted_taxonomy_and_scheme(tmp_path):
    m, fl = _matrix()
    # a spending fact where the positives bill a code the rest rarely do
    rows = []
    pos = set(fl.loc[fl["is_prospective_positive"] == 1, "npi"])
    rng = np.random.default_rng(5)
    for n in m["npi"]:
        rows.append({"billing_npi": n, "hcpcs_code": "99213", "service_month": "2021-01", "total_paid": 1000.0})
        if n in pos or rng.uniform() < 0.05:
            rows.append({"billing_npi": n, "hcpcs_code": "G0299", "service_month": "2021-02", "total_paid": 900.0})
    sp = tmp_path / "spending_asof_2021-12.parquet"
    pd.DataFrame(rows).to_parquet(sp, index=False)
    table, info = build_report(m, fl, {"subscore_cols": ["subscore_good", "subscore_noise"]},
                               spending_path=sp, cutoff="2021-12", log=lambda *_: None)
    assert info["n_forward_positives"] == 40 and info["n_known_at_freeze_dropped"] == 2
    t = table.set_index(["dimension", "value"])
    assert t.loc[("taxonomy", "251E00000X"), "lift"] > 1.8 and t.loc[("taxonomy", "251E00000X"), "p_tail"] < 1e-6
    assert t.loc[("hcpcs_code", "G0299"), "lift"] > 3 and t.loc[("hcpcs_code", "G0299"), "n_pos"] == 40
    assert t.loc[("scheme:good", "top decile"), "lift"] > 3
    assert abs(t.loc[("scheme:noise", "top decile"), "lift"] - 1.0) < 0.8
    md = to_markdown(table, info)
    assert "G0299" in md and "251E00000X" in md and "Headline" in md
    # the CLI path on a run folder (default forward-label + spending discovery)
    run_dir = tmp_path / "model_a" / "frozen_2021-12_vX"
    run_dir.mkdir(parents=True)
    (tmp_path / "interim").mkdir()
    sp.rename(tmp_path / "interim" / "spending_asof_2021-12.parquet")
    m.to_parquet(run_dir / "provider_features_for_model.parquet", index=False)
    (run_dir / "feature_manifest.json").write_text(json.dumps({"asof_cutoff": "2021-12",
                                                               "subscore_cols": ["subscore_good"]}))
    fl.to_csv(run_dir / "future_bans_after_2021-12__fca36.csv", index=False)
    out = run_enrichment(run_dir, None, None, None, 5, 25)
    assert out.exists() and (run_dir / "label_enrichment.csv").exists()
    assert "G0299" in out.read_text(encoding="utf-8")


def test_enrich_dimension_marks_thin_rows_unsupported():
    v = pd.Series(["a"] * 90 + ["b"] * 10)
    pos = np.array([1] * 3 + [0] * 87 + [1] * 2 + [0] * 8)
    keep = np.ones(100, dtype=bool)
    t = enrich_dimension(v, pos, keep, "dim", min_pos=5).set_index("value")
    assert t.loc["b", "n_pos"] == 2 and t.loc["b", "supported"] == 0
    assert t.loc["a", "supported"] == 0 and t.loc["a", "n_pos"] == 3
    assert 0 <= t["p_tail"].min() <= t["p_tail"].max() <= 1
