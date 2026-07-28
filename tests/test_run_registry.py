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
    assert "Current champion:** `v2`" in md
    assert "HOLD" in md and "rollback is a pointer change" in md.lower() or \
           "Rollback" in md or "pointer" in md
