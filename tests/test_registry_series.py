"""test_registry_series.py — one champion per (freeze | label policy) series;
lifts are never compared across series."""

from __future__ import annotations

import json
from pathlib import Path

from src.model_a.run_registry import (champion_pointer, recommend,
                                      resolve_run_dir, run_series, to_markdown)


def _row(tag, cutoff, policy, delta, decision=None, lo=0.2):
    r = {"tag": tag, "asof_cutoff": cutoff, "label_policy": policy,
         "expectation_fails": 0, "network_verdict": "KEEP",
         "ablation_lift_delta": delta, "ablation_lift_lo": lo,
         "ablation_lift_hi": delta + 0.3, "run_dir": f"/runs/{tag}",
         "core_fingerprint": "core"}
    r["series"] = run_series(r)
    if decision:
        r["decision"] = decision
    return r


def test_run_series_key():
    assert run_series({"asof_cutoff": "2021-12", "label_policy": "hard"}) == "2021-12|hard"
    assert run_series({"asof_cutoff": "2023-12"}) == "2023-12|all"
    assert run_series({}) == "?|all"
    # pre-manifest rows: the freeze is in the folder name
    assert run_series({"run_dir": r"C:\data\model_a\frozen_2023-12_v3"}) == "2023-12|all"
    assert run_series({"run_dir": "/x/frozen_2023-12"}) == "2023-12|all"


def test_new_series_is_not_compared_to_old_champion():
    rows = [_row("v3", "2023-12", "all", 0.66, "PROMOTE"),
            _row("v6", "2023-12", "all", 0.48, "HOLD"),
            _row("v7", "2021-12", "hard", 0.49)]          # far below v3's 0.66
    rec = recommend(rows)
    # no lift-drop reason: v7 opens its own series
    assert not any("dropped" in r for r in rec["reasons"])
    assert rec["decision"] == "PROMOTE"
    assert rec["series"] == "2021-12|hard"
    assert rec["series_note"] and "first run of series" in rec["series_note"]
    assert rec["champion"] == "v7"                          # champion of ITS series
    md = to_markdown(rows[:-1] + [dict(rows[-1], decision="PROMOTE")], rec)
    assert "Champions by series" in md
    assert "`2023-12|all` → `v3`" in md and "`2021-12|hard` → `v7`" in md


def test_same_series_still_enforces_lift_drop():
    rows = [_row("v7", "2021-12", "hard", 0.80, "PROMOTE"),
            _row("v8", "2021-12", "hard", 0.40)]
    rec = recommend(rows)
    assert rec["decision"] == "HOLD"
    assert any("dropped" in r for r in rec["reasons"])
    assert rec["series_note"] is None


def test_champion_pointer_by_series(tmp_path: Path):
    reg = tmp_path / "model_a"; reg.mkdir()
    rows = [_row("v3", "2023-12", "all", 0.66, "PROMOTE"),
            _row("v7", "2021-12", "hard", 0.49, "PROMOTE")]
    (reg / "run_registry.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert champion_pointer(tmp_path)["tag"] == "v7"                   # newest overall
    assert champion_pointer(tmp_path, "2023-12|all")["tag"] == "v3"
    assert champion_pointer(tmp_path, "2021-12|hard")["tag"] == "v7"
    assert champion_pointer(tmp_path, "2019-12|hard") is None
    assert resolve_run_dir("@champion:2023-12|all", tmp_path) == "/runs/v3"
    assert resolve_run_dir("@champion", tmp_path) == "/runs/v7"
    assert resolve_run_dir("/somewhere/else", tmp_path) == "/somewhere/else"
