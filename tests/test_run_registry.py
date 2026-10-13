"""
test_run_registry.py — parse a synthetic run folder, apply the promotion policy.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.model_a.run_registry import parse_run_dir, recommend, to_markdown


def _make_run(tmp_path: Path, tag: str, fails=0, lift=(0.74, 0.41, 1.10),
              verdict="KEEP (size-independent)") -> Path:
    d = tmp_path / tag
    d.mkdir()
    (d / "EXPECTATIONS_REPORT.md").write_text(
        f"# expectations\n{fails} FAIL / 27 WARN\n", encoding="utf-8")
    (d / "NETWORK_AB_REPORT.md").write_text(
        f"- **VERDICT: {verdict}: the network family lifts discrimination "
        "(delta +0.026, CI clear of zero).**\n", encoding="utf-8")
    dl, lo, hi = lift
    (d / "SOURCE_ABLATION_OOF_FULL.md").write_text(
        "- n=616,140  positives=1,021\n"
        "| metric | core only | full | delta (full-core) [95% CI] |\n"
        "|---|--:|--:|---|\n"
        f"| top-decile lift | 3.1244 | 3.8492 | {dl:+.4f} [{lo:+.4f}, {hi:+.4f}] |\n"
        "| **full only** (broad data surfaces, core buries) | 205 |\n"
        "| **core only** (core surfaces, broad data buries) | 131 |\n",
        encoding="utf-8")
    import pandas as pd
    pd.DataFrame({"npi": ["1", "2", "3"],
                  "is_prospective_positive": ["1", "1", "0"],
                  "was_excluded_pre_cutoff": ["0", "0", "1"]}
                 ).to_csv(d / "future_bans_after_2023-12.csv", index=False)
    return d


def test_parse_and_promote(tmp_path):
    d = _make_run(tmp_path, "v2")
    row = parse_run_dir(d)
    assert row["tag"] == "v2"
    assert row["expectation_fails"] == 0
    assert row["network_verdict"].startswith("KEEP")
    assert abs(row["ablation_lift_delta"] - 0.74) < 1e-6
    assert row["ablation_lift_lo"] > 0
    assert row["forward_positives"] == 2
    assert row["catches_full_only"] == 205 and row["catches_core_only"] == 131

    rec = recommend([row])
    assert rec["decision"] == "PROMOTE"
    assert rec["champion"] == "v2"


def test_hold_on_fails_and_degradation(tmp_path):
    champ = parse_run_dir(_make_run(tmp_path, "v2"))
    champ["decision"] = "PROMOTE"

    bad = parse_run_dir(_make_run(tmp_path, "v3_fails", fails=2))
    rec = recommend([champ, bad])
    assert rec["decision"] == "HOLD"
    assert any("FAIL" in r for r in rec["reasons"])
    assert rec["champion"] == "v2"                # champion survives the HOLD

    degraded = parse_run_dir(_make_run(tmp_path, "v4_degraded",
                                       lift=(0.40, 0.10, 0.70)))
    rec2 = recommend([champ, degraded])
    assert rec2["decision"] == "HOLD"
    assert any("dropped" in r for r in rec2["reasons"])

    md = to_markdown([champ, bad], rec)
    assert "series** (`" in md and "`v2`" in md      # champion of the series line
    assert "HOLD" in md and "rollback is a pointer change" in md.lower() or \
           "Rollback" in md or "pointer" in md


def test_champion_pointer_and_resolve(tmp_path):
    import json
    from src.model_a.run_registry import champion_pointer, resolve_run_dir
    reg_dir = tmp_path / "model_a"; reg_dir.mkdir(parents=True)
    rows = [
        {"tag": "v3", "decision": "PROMOTE", "run_dir": "/runs/v3", "git_commit": "abc1234"},
        {"tag": "v4", "decision": "HOLD", "run_dir": "/runs/v4", "git_commit": "def5678"},
        {"tag": "v5", "decision": "HOLD", "run_dir": "/runs/v5", "git_commit": "aaa9999"},
    ]
    with open(reg_dir / "run_registry.jsonl", "w") as fh:
        for r in rows: fh.write(json.dumps(r) + "\n")
    champ = champion_pointer(tmp_path)
    assert champ["tag"] == "v3"                       # newest PROMOTE, not newest run
    assert resolve_run_dir("@champion", tmp_path) == "/runs/v3"
    assert resolve_run_dir("/explicit/dir", tmp_path) == "/explicit/dir"
    # a later promotion moves the pointer
    with open(reg_dir / "run_registry.jsonl", "a") as fh:
        fh.write(json.dumps({"tag": "v6", "decision": "PROMOTE",
                             "run_dir": "/runs/v6", "git_commit": "bbb0000"}) + "\n")
    assert champion_pointer(tmp_path)["tag"] == "v6"


def test_resolve_champion_fails_loudly_when_none(tmp_path):
    import pytest
    from src.model_a.run_registry import resolve_run_dir
    (tmp_path / "model_a").mkdir(parents=True)
    with pytest.raises(SystemExit):
        resolve_run_dir("@champion", tmp_path)


def test_check_right_hand_side_may_be_another_metric():
    """'prec50_lo > base50' compares two metrics of the same run. v8 / v9 / v10 were
    judged on the literal string 'base50' ("> needs numbers") and a passing check was
    recorded as a FAIL."""
    from src.model_a.run_registry import eval_check
    row = {"acc_org_prec50_lo": 0.011, "acc_base50": 0.005, "acc_org_hits50": 2}
    r = eval_check("prec50_lo > base50", row)
    assert r["ok"] and r["value_from"] == "acc_base50" and r["value"] == 0.005
    r2 = eval_check("prec50_lo > base50", {"acc_org_prec50_lo": 0.0, "acc_base50": 0.005})
    assert not r2["ok"] and "0.005" in r2["why"]
    assert eval_check("hits50 >= 2", row)["ok"]                        # literals still work
    assert not eval_check("hits50 >= nonsense", row)["ok"]             # an unknown name is not silently true


def test_registry_reads_the_training_label_policy(tmp_path):
    """The forward ruler's policy (label_policy) and the TRAINING policy (train_policy,
    written by the priority-list builder) are different things; v12 pre-registers
    "train = hard" so an 'all'-trained list can never pass as FCA-shaped."""
    d = _make_run(tmp_path, "v12")
    (d / "training_label_policy.json").write_text(json.dumps(
        {"label_tier": "hard", "n_positives": 2345, "n_doj_positives": 1644}), encoding="utf-8")
    row = parse_run_dir(d)
    assert row["train_policy"] == "hard" and row["train_positives"] == 2345
    from src.model_a.run_registry import eval_check
    assert eval_check("train = hard", row)["ok"]
    assert not eval_check("train = all", row)["ok"]
    assert eval_check("train_positives >= 2000", row)["ok"]


def test_registry_reads_the_large_book_kpis(tmp_path):
    """v12 (2026-10-13): the large-biller book was the first to pass hits50 >= 2 under
    the FCA ruler; v13 grades two books, so its KPIs ride beside the pursuit book's
    as acc_large_* with their own check aliases."""
    d = _make_run(tmp_path, "v13")
    (d / "accuracy_diagnostics__large.json").write_text(json.dumps(
        {"kpis": {"acc_org_hits50": 3, "acc_org_prec50_lo": 0.021, "acc_base50": 0.008}}), encoding="utf-8")
    row = parse_run_dir(d)
    assert row["acc_large_org_hits50"] == 3
    from src.model_a.run_registry import eval_check
    assert eval_check("large_hits50 >= 2", row)["ok"]
    assert eval_check("large_prec50_lo > large_base50", row)["ok"]
    assert not eval_check("large_hits50 >= 4", row)["ok"]
