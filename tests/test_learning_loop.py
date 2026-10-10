"""learning_loop: label noise (confident learning), the active-learning queue, feature
drift, near misses on the forward ruler, the calibration monitor. Synthetic matrices
with planted answers; no real data."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a import learning_loop as ll


def _matrix(n=600, seed=0, shift=0.0):
    rng = np.random.default_rng(seed)
    orgs = [f"org:{i // 3:03d}" for i in range(n)]
    hot = rng.random(n) < 0.12
    y = (hot & (rng.random(n) < 0.8)).astype(int)
    m = pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "org_node_id": orgs, "group_id": orgs,
        "provider_name": [f"PROVIDER {i}" for i in range(n)],
        "practice_state": ["RI"] * n, "addr_city": ["PROVIDENCE"] * n, "entity_type": ["1"] * n,
        "provider_on_exclusion": y,
        "exclusion_label_sources": np.where(y == 1, "leie", ""),
        "net_paid": rng.uniform(1e5, 5e6, n),
        "shell_score": rng.normal(0, 1, n) + hot * 2.5 + shift,
        "facility_code_share__peerpct": rng.random(n),
        "subscore_upcoding": rng.random(n) * (0.2 + 0.8 * hot),
    })
    manifest = {"label": "provider_on_exclusion",
                "raw_feature_cols": ["net_paid", "shell_score"], "peerpct_cols": ["facility_code_share__peerpct"],
                "subscore_cols": ["subscore_upcoding"], "embedding_cols": [], "leakage_hard": [], "leakage_adjacent": [],
                "sources_used": {"spending": ["net_paid", "shell_score"]}, "scheme_coverage": {},
                "asof_cutoff": "2021-12"}
    return m, manifest


def test_label_noise_queue_drift_near_misses_and_calibration(tmp_path: Path):
    m, man = _matrix()
    # plant a mislabelled positive: cold billing, label says excluded
    cold = m.index[(m["provider_on_exclusion"] == 0) & (m["shell_score"] < -1.5)][0]
    m.loc[cold, "provider_on_exclusion"] = 1
    m.loc[cold, "exclusion_label_sources"] = "sam:hhs"
    planted_npi = m.loc[cold, "npi"]
    cur = tmp_path / "v9"
    cur.mkdir()
    m.to_parquet(cur / "provider_features_for_model.parquet", index=False)
    (cur / "feature_manifest.json").write_text(json.dumps(man))
    # the previous run: same universe, shell_score shifted hard (a drift the loop must flag)
    prev_m, _ = _matrix(shift=3.0)
    prev = tmp_path / "v8"
    prev.mkdir()
    prev_m.to_parquet(prev / "provider_features_for_model.parquet", index=False)
    # a forward ruler: two hot providers that never got a label, plus one cold one (the near miss)
    hot_unlabelled = m[(m["provider_on_exclusion"] == 0) & (m["shell_score"] > 2.5)]["npi"].head(2).tolist()
    cold_pos = m[(m["provider_on_exclusion"] == 0) & (m["shell_score"] < -1.0)]["npi"].iloc[1]
    ruler = pd.DataFrame({"npi": hot_unlabelled + [cold_pos],
                          "is_prospective_positive": [1, 1, 1], "was_excluded_pre_cutoff": [0, 0, 0],
                          "event_date": ["2023-01-01"] * 3})
    ruler.to_csv(cur / "future_bans_after_2021-12__fca36.csv", index=False)

    res = ll.run(cur, prev, None, top_frac=0.05, queue_size=20, sample_n=500)
    # 1. the planted mislabel is a suspect positive, with its provenance
    sus = pd.read_csv(cur / "label_noise_suspect_positives.csv", dtype=str)
    assert planted_npi in set(sus["npi"])
    assert sus.set_index("npi").loc[planted_npi, "exclusion_label_sources"] == "sam:hhs"
    assert res["label_noise"]["n_candidate"] >= 1                      # hot unlabelled rows surface
    # 2. the queue + prompts, in the dossier's dispatch schema, public records only
    q = pd.read_csv(cur / "label_review_queue.csv", dtype=str)
    assert 0 < len(q) <= 20 and {"suspect_positive", "candidate_positive"} <= set(q["kind"])
    items = [json.loads(l) for l in (cur / "label_review_prompts.jsonl").read_text().splitlines()]
    assert all(i["org_id"].startswith("npi:") and "PUBLIC sources only" in i["prompt"]
               and "contact nobody" in i["prompt"] for i in items)
    # 3. drift: shell_score moved by 3 sigma -> FAIL; the untouched columns do not
    drift = pd.read_csv(cur / "feature_drift.csv")
    d = drift.set_index("feature")
    assert d.loc["shell_score", "flag"] == "FAIL" and d.loc["shell_score", "psi"] > ll.PSI_FAIL
    assert d.loc["facility_code_share__peerpct", "flag"] != "FAIL"
    # 4. near misses: the cold forward positive is missed and its gap names shell_score
    nm = pd.read_csv(cur / "near_misses.csv", dtype=str)
    assert cold_pos in set(nm["npi"])
    assert "shell_score" in nm.set_index("npi").loc[cold_pos, "gap_features"]
    assert res["near_misses"]["n_forward_positives"] == 3
    # 5. calibration monitor + conformal p on every row, written back
    oof = pd.read_parquet(cur / ll.OOF_FILE)
    assert "conformal_p" in oof.columns and oof["conformal_p"].between(0, 1).all()
    assert isinstance(res["calibration"], dict)
    md = (cur / "LEARNING_LOOP.md").read_text(encoding="utf-8")
    assert "Label noise" in md and "Feature drift" in md and "Near misses" in md and "—" not in md
    # the ledger reads the loop's json
    from src.model_a.run_issues import collect_issues
    (cur / "EXPECTATIONS_REPORT.md").write_text("# x\n")
    issues, _ = collect_issues(cur)
    ids = {i["id"] for i in issues}
    assert "drift:shell_score:feature_drift" in ids and "label:training_label:suspect_positives" in ids
    assert "evaluation:near_misses:feature_requests" in ids


def test_psi_is_zero_for_identical_and_large_for_shifted():
    rng = np.random.default_rng(1)
    a = rng.normal(0, 1, 5000)
    assert ll._psi(a, a.copy()) < 1e-9
    assert ll._psi(a, a + 2.0) > ll.PSI_FAIL
    assert ll._psi(a[:10], a) != ll._psi(a[:10], a)                  # too few rows -> NaN
