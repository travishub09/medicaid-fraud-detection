"""
test_bundle389_arm_flags_2026_10.py — the v23 instrumentation gap closed (2026-10-24):
(1) every refit arm and every graded book in both runners inherits the SHIPPED prior
    encoder ($PRIOR_FLAGS), so an arm is the shipped policy plus ONE change (v23's
    broadcast / prune / charged arms refit under the manifest default = the weighted
    encoder while the ship was floor-only);
(2) the shipped list records the EFFECTIVE training policy it was built under
    (training_label_policy.json carries ``training_policy``), and the registry prefers it
    over the manifest's keys: a re-grade copies the SOURCE run's manifest, so v23's row
    would have read the encoder as 0 / off while the lists were built under 300 / off.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

import src.model_a.company_list as cl


def _matrix():
    return pd.DataFrame({"npi": [str(1000000000 + i) for i in range(12)],
                         "provider_on_exclusion": [1, 0] * 6,
                         "exclusion_label_sources": ["leie", ""] * 6,
                         "label_weight": 1.0})


def test_training_label_summary_carries_the_effective_policy():
    cl.set_training_policy(None, None, None)
    cl.set_label_policy("all")
    try:
        manifest = {"training_policy": {"n_seeds": 10, "class_priors": ["taxonomy", "entity"]}}   # a v21 manifest
        s = cl.training_label_summary(_matrix(), manifest=manifest)
        tp = s["training_policy"]
        assert tp["n_seeds"] == 10 and tp["class_priors"] == ["taxonomy", "entity"]
        assert tp["prior_min_n"] == 300 and tp["prior_weighted"] is True        # the v22 defaults, recorded
        cl.set_training_policy(prior_min_n=300, prior_weighted="0")            # the v23 ship: floor-only
        tp = cl.training_label_summary(_matrix(), manifest=manifest)["training_policy"]
        assert tp["prior_min_n"] == 300 and tp["prior_weighted"] is False      # the override is what shipped
        assert cl.training_label_summary(_matrix())["training_policy"]["prior_weighted"] is False   # manifest-less call
    finally:
        cl.set_training_policy(None, None, None)


def test_registry_prefers_the_shipped_lists_policy_over_the_manifest(tmp_path):
    from src.model_a.run_registry import parse_run_dir, eval_check
    d = tmp_path / "frozen_2021-12_v23"
    d.mkdir()
    # the SOURCE run's manifest, copied by the re-grade: no encoder keys, v21 priors
    (d / "feature_manifest.json").write_text(json.dumps({
        "asof_cutoff": "2021-12", "training_policy": {"n_seeds": 10, "broadcast_rows": "weight",
                                                      "group_policy": "case_cluster",
                                                      "class_priors": ["taxonomy", "entity"]}}))
    (d / "training_label_policy.json").write_text(json.dumps({
        "label_tier": "hard", "n_positives": 1746, "n_doj_positives": 1596,
        "training_policy": {"n_seeds": 10, "broadcast": "weight", "group_policy": "case_cluster",
                            "class_priors": ["taxonomy", "entity"], "drop_schemes": [],
                            "prior_min_n": 300, "prior_weighted": False}}))
    row = parse_run_dir(d)
    assert row["prior_min_n"] == 300 and row["prior_weighted"] is False
    assert row["class_priors"] == "taxonomy,entity" and row["drop_schemes"] == "none"
    assert eval_check("prior_floor = 300", row)["ok"] and eval_check("prior_weighted = False", row)["ok"]
    # a v21-era folder without the block keeps the manifest's reading (the v21 encoder)
    (d / "training_label_policy.json").write_text(json.dumps({"label_tier": "hard", "n_positives": 1746}))
    row = parse_run_dir(d)
    assert row["prior_min_n"] == 0 and row["prior_weighted"] is False


def test_every_refit_arm_and_graded_book_inherits_the_shipped_encoder():
    for f in ("ops/vm/regrade_from.sh", "ops/vm/run_frozen_night.sh"):
        lines = Path(f).read_text().splitlines()
        refits = [l for l in lines if "--score-source refit" in l]
        assert len(refits) >= 6, f
        for l in refits:
            # the prior arms set their own encoder; every other arm carries the shipped one
            assert "$PRIOR_FLAGS" in l or "--prior-min-n" in l, (f, l)
        books = [l for l in lines if re.search(r"--list-name provider_priority_list_\w+\.csv", l)]
        assert len(books) >= 3, f
        for l in books:
            assert "$PRIOR_FLAGS" in l, (f, l)
