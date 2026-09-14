"""test_registry_rerun.py — discovered forward-label file names, re-registration
superseding the earlier row, and --replace on a pre-registration."""

from __future__ import annotations

import json
from pathlib import Path

from src.model_a.run_registry import dedupe_rows, future_bans_file, parse_run_dir


def test_future_bans_file_is_discovered_not_assumed(tmp_path: Path):
    d = tmp_path / "frozen_2021-12_v7"; d.mkdir()
    assert future_bans_file(d) is None
    (d / "future_bans_after_2021-12__all.csv").write_text("npi\n")
    assert future_bans_file(d) is None                      # variants never count
    (d / "future_bans_after_2021-12.csv").write_text(
        "npi,is_prospective_positive,label_policy\n1,1,hard\n2,0,hard\n")
    assert future_bans_file(d).name == "future_bans_after_2021-12.csv"
    (d / "feature_manifest.json").write_text(json.dumps({"asof_cutoff": "2021-12"}))
    row = parse_run_dir(d)
    assert row["forward_positives"] == 1
    assert row["label_policy"] == "hard"
    assert row["series"] == "2021-12|hard"
    assert row["future_bans_path"].endswith("future_bans_after_2021-12.csv")


def test_dedupe_keeps_last_row_per_tag():
    rows = [{"tag": "v3", "x": 1}, {"tag": "v7", "x": 1}, {"tag": "v7", "x": 2},
            {"tag": "v8", "x": 1}]
    out = dedupe_rows(rows)
    assert [(r["tag"], r["x"]) for r in out] == [("v3", 1), ("v7", 2), ("v8", 1)]
