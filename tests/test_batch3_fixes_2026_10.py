"""
test_batch3_fixes_2026_10.py — the three batch-3 fixes the v19 read-out named.

(1) the deactivation edition picker reads the PRE-CUTOFF edition under a freeze (the
    newest on/before the cutoff), the newest only when nothing earlier exists, and the
    export's vintage ledger says which (pre_cutoff / post_cutoff_reference /
    current_day_newest);
(2) run_issues no longer grades a SCHEME_TRIAGE "POST-CUTOFF" row as a FAIL when the
    manifest's vintage ledger covers the source (the file is on disk, the picker did
    not read it); without a ledger row the FAIL stands;
(3) the label ablation trains under the SHIPPED tier policy (y_base + mask-not-negate)
    and skips a minus_ row for a source whose rows never train under that policy.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd


def _deact_zip(folder: Path, stamp: str) -> Path:
    zp = folder / f"NPPES_Deactivated_NPI_Report_{stamp}.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("Deactivated_NPI_Report.xlsx", b"not read by the picker")
    return zp


def test_picker_reads_the_pre_cutoff_edition_under_a_freeze(tmp_path):
    from src.ingest_cms.nppes_deactivation import pick_deactivation_file
    d = tmp_path / "nppes_deactivation"
    d.mkdir()
    _deact_zip(d, "060121")            # 2021-06-01
    _deact_zip(d, "121321")            # 2021-12-13 (inside the 2021-12 cutoff month)
    _deact_zip(d, "091426")            # 2026-09-14 (the newest)
    p, ed, listing = pick_deactivation_file(d)
    assert ed == "2026-09-14" and len(listing) == 3                     # current day: the newest
    p, ed, _ = pick_deactivation_file(d, asof_cutoff="2021-12")
    assert ed == "2021-12-13"                                           # frozen: newest on/before the month
    p, ed, _ = pick_deactivation_file(d, asof_cutoff="2021-09")
    assert ed == "2021-06-01"
    p, ed, _ = pick_deactivation_file(d, asof_cutoff="2020-12")
    assert ed == "2026-09-14"                                           # nothing earlier: the newest, flagged by the ledger


def test_export_ledger_names_the_verdict(tmp_path, monkeypatch):
    import src.model_a.provider_features_export as pfe
    d = tmp_path / "nppes_deactivation"
    d.mkdir()
    _deact_zip(d, "121321")
    _deact_zip(d, "091426")
    monkeypatch.setattr(pfe, "_VINTAGE", [])
    logs = []
    assert pfe._deactivation_file(d, logs.append, asof_cutoff="2021-12").name.endswith("121321.zip")
    assert pfe._VINTAGE[-1]["verdict"] == "pre_cutoff" and pfe._VINTAGE[-1]["cutoff"] == "2021-12"
    assert pfe._deactivation_file(d, logs.append, asof_cutoff="2020-12").name.endswith("091426.zip")
    assert pfe._VINTAGE[-1]["verdict"] == "post_cutoff_reference"
    assert pfe._deactivation_file(d, logs.append).name.endswith("091426.zip")
    assert pfe._VINTAGE[-1]["verdict"] == "current_day_newest" and pfe._VINTAGE[-1]["cutoff"] is None
    assert any("pre_cutoff" in line for line in logs)


def test_run_issues_defers_triage_post_cutoff_rows_to_the_ledger(tmp_path):
    from src.model_a.run_issues import collect_issues
    d = tmp_path / "frozen_2021-12_vX"
    d.mkdir()
    (d / "feature_manifest.json").write_text(json.dumps({
        "asof_cutoff": "2021-12", "label": "provider_on_exclusion", "n_providers": 1000,
        "expectations": {"fails": 0, "warns": 0}, "sources_audit": [{"source": "pos", "status": "used", "detail": ""}],
        "feature_partition": {"unmapped": []}, "graph_substrate": {"address_layer_frozen": True},
        "vintage_audit": [
            {"source": "pos", "cutoff": "2021-12", "picked": ["pos_2021q4.csv"], "skipped": ["pos_2023q4.csv"],
             "edition": "2021-12", "staleness_months": 0, "verdict": "pre_cutoff", "note": ""},
            {"source": "facility/pbj", "cutoff": "2021-12", "picked": ["pbj_2021q3.csv"], "skipped": [],
             "edition": "2021-09", "staleness_months": -3, "verdict": "pre_cutoff", "note": ""}]}))
    (d / "SCHEME_TRIAGE.md").write_text(
        "# SCHEME TRIAGE\n\n| source | inferred vintage | verdict | file detail |\n|---|---|---|---|\n"
        "| `pos` | 2023Q4 | **POST-CUTOFF** | pos_2023q4.csv |\n"
        "| `pbj` | 2023Q2 | **POST-CUTOFF** | pbj_2023q2.csv |\n"
        "| `docgraph` | 2022 | **POST-CUTOFF** | DocGraph_Hop_Teaming_2022.csv |\n")
    issues, _ = collect_issues(d, prereg=None)
    ids = {i["id"]: i["severity"] for i in issues}
    assert ids.get("vintage:pos:post_cutoff_on_disk") == "INFO"          # the ledger read the 2021 edition
    assert ids.get("vintage:pbj:post_cutoff_on_disk") == "INFO"          # token match: facility/pbj
    assert ids.get("vintage:docgraph:post_cutoff_edition") == "FAIL"     # no ledger row: the FAIL stands
    assert "vintage:pos:post_cutoff_edition" not in ids


def _ablation_matrix(n=600, seed=0):
    rng = np.random.default_rng(seed)
    signal = rng.normal(0, 1, n)
    hard = (signal > 1.2).astype(int)                  # doj_case: real pattern
    weak = (rng.random(n) < 0.08).astype(int)          # state_termination: random (weak tier)
    y = (hard | weak).astype(int)
    prov = np.where(hard & weak, "doj_case;state_termination",
                    np.where(hard == 1, "doj_case", np.where(weak == 1, "state_termination", "")))
    m = pd.DataFrame({"npi": [f"{1000000000 + i}" for i in range(n)],
                      "group_id": [f"g{i % 120}" for i in range(n)],
                      "provider_on_exclusion": y, "exclusion_label_sources": prov,
                      "shell_score": signal + rng.normal(0, 0.5, n), "net_paid": rng.normal(0, 1, n)})
    fl = pd.DataFrame({"npi": m["npi"], "is_prospective_positive": (signal + rng.normal(0, 0.8, n) > 1.5).astype(int),
                       "was_excluded_pre_cutoff": 0}).astype(str)
    return m, hard, weak, fl


def test_label_ablation_under_the_shipped_tier_policy():
    from src.model_a.label_ablation import label_variants, run_label_ablation, to_markdown
    m, hard, weak, fl = _ablation_matrix()
    man = {"raw_feature_cols": ["shell_score", "net_paid"], "peerpct_cols": [], "subscore_cols": [],
           "embedding_cols": [], "leakage_hard": [], "sources_used": {"spending": ["shell_score", "net_paid"]},
           "scheme_coverage": {}}
    # the hard policy keeps only the doj_case rows: that is y_base
    v = label_variants(m, y_base=hard, policy_name="hard")
    assert v["all"]["y"].sum() == hard.sum()
    assert "minus_state_termination" not in v                 # its rows never train under hard: nothing to remove
    # removing doj_case removes EVIDENCE: a row the weak source also labels stays positive
    assert "minus_doj_case" in v and v["minus_doj_case"]["y"].sum() == int(((hard == 1) & (weak == 1)).sum())
    assert "policy 'hard'" in v["all"]["note"]
    res = run_label_ablation(m, man, fl, n_splits=3, n_boot=40, y_base=hard, policy_name="hard")
    assert res["label_policy"] == "hard"
    # the weak-only rows (union positive, policy negative) are masked, never negatives
    assert res["ambiguous_masked"] == int(((weak == 1) & (hard == 0)).sum())
    md = to_markdown(res)
    assert "Training label policy: **hard**" in md and "masked from training" in md
    # policy 'all' still reads the raw column and says so
    res_all = run_label_ablation(m, man, fl, n_splits=3, n_boot=40)
    assert res_all["label_policy"] == "all" and res_all["ambiguous_masked"] == 0
    assert "minus_state_termination" in res_all["variants"]
    assert "re-run with --label-tier" in to_markdown(res_all)
