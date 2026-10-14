"""
test_data_refresh_and_questions.py — the standing answers written from a run folder
(2026-10-14): DATA_REFRESH classifies every source's edition against the freeze and
lists the downloads due; QUESTIONS_READOUT answers the six operator questions from
the artifacts and says which artifact is missing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.model_a.data_refresh import classify_sources, due_downloads, run as run_refresh
from src.model_a.questions_readout import run as run_questions

AUDIT = [
    {"source": "pos", "status": "used", "detail": "read POS_File_CLIA_Q4_2023.csv; 37,271 orgs"},
    {"source": "pbj", "status": "used", "detail": "read pbj_daily_staffing_2021Q4.csv; read pbj_daily_staffing_2023Q4.csv"},
    {"source": "hcris", "status": "used", "detail": "3 files -> 30,754 CCN rows; read snf_cost_report.csv"},
    {"source": "growth", "status": "used", "detail": "434,708 orgs, cols: growth_level_shift"},
    {"source": "partb", "status": "used", "detail": "1,123,589 providers from partb_2021.csv"},
    {"source": "death_master", "status": "skipped", "detail": "needs SSA DMF file (preclean/dmf/)"},
]


def test_classify_sources_against_the_freeze():
    t = classify_sources(AUDIT, "2021-12").set_index("source")
    assert t.loc["pos", "verdict"] == "POST-CUTOFF" and t.loc["pos", "reminder"] == "pre_cutoff_editions_2021"
    assert t.loc["pbj", "verdict"] == "MIXED"
    assert t.loc["hcris", "verdict"] == "UNDATED"
    assert t.loc["growth", "verdict"] == "DERIVED"
    assert t.loc["partb", "verdict"] == "OK" and t.loc["partb", "vintage"] == "2021-12"
    assert t.loc["death_master", "verdict"] == "SKIPPED"
    # current-day: a 2023 edition is simply OK
    assert classify_sources(AUDIT, None).set_index("source").loc["pos", "verdict"] == "OK"


def test_due_downloads_reads_the_cadence(tmp_path):
    p = tmp_path / "reminders.json"
    p.write_text(json.dumps({"cadence": [
        {"key": "leie", "what": "LEIE", "every_months": 1, "last_done": "2026-09-16", "url": "https://oig"},
        {"key": "hcris", "what": "HCRIS", "every_months": 12, "last_done": "2026-08-02"},
        {"key": "never", "what": "never done", "every_months": 3, "last_done": None},
    ]}))
    from datetime import date
    d = due_downloads(p, today=date(2026, 10, 14)).set_index("key")
    assert d.loc["leie", "status"] == "coming up" and d.loc["leie", "due"] == "2026-10-16"
    assert d.loc["never", "status"] == "DUE NOW"
    assert "hcris" not in d.index


def _run_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    rd = root / "model_a" / "frozen_2021-12_vT"
    rd.mkdir(parents=True)
    (rd / "feature_manifest.json").write_text(json.dumps({"asof_cutoff": "2021-12", "sources_audit": AUDIT,
                                                          "dropped_constant": ["dead_col"]}))
    (rd / "scheme_health.json").write_text(json.dumps({"schemes": [
        {"scheme": "good", "status": "HEALTHY", "label_auc": 0.7, "n_label_pos": 100, "fca_auc": 0.66, "n_fca_pos": 40, "fca_verdict": "signal"},
        {"scheme": "noise", "status": "HEALTHY", "label_auc": 0.51, "n_label_pos": 100, "fca_auc": 0.5, "n_fca_pos": 40, "fca_verdict": "coin-flip"},
        {"scheme": "thin", "status": "DEGRADED", "label_auc": 0.5, "n_label_pos": 5, "missing_inputs": "x_input"},
    ], "regressions": []}))
    pd.DataFrame([{"dimension": "hcpcs_code", "value": "G0299", "n_pos": 40, "n_univ": 60, "share_pos": 1.0,
                   "share_univ": 0.15, "lift": 6.7, "p_tail": 1e-9, "supported": 1}]).to_csv(rd / "label_enrichment.csv", index=False)
    (rd / "training_label_policy.json").write_text(json.dumps({"label_tier": "hard", "n_positives": 1300,
                                                               "n_doj_positives": 1100, "n_charged_positives": 0}))
    pd.DataFrame({"npi": ["1", "2", "3"], "is_prospective_positive": [1, 1, 0],
                  "was_excluded_pre_cutoff": [0, 0, 1]}).to_csv(rd / "future_bans_after_2021-12__fca36.csv", index=False)
    (root / "preclean").mkdir()
    (root / "preclean" / "VINTAGE_INSPECT.md").write_text("# VINTAGE\n\n## The TRUE re-download list\n- snf_cost_report.csv: nothing dates it\n\n## Inventory\n")
    return rd


def test_reports_are_written_and_name_missing_artifacts(tmp_path):
    rd = _run_dir(tmp_path)
    root = rd.parent.parent
    rem = tmp_path / "reminders.json"
    rem.write_text(json.dumps({"cadence": [{"key": "leie", "what": "LEIE", "every_months": 1, "last_done": "2026-01-01"}]}))
    out = run_refresh(rd, root, rem)
    txt = out.read_text(encoding="utf-8")
    assert "POST-CUTOFF (1)" in txt and "`pos`" in txt and "pre_cutoff_editions_2021" in txt
    assert "snf_cost_report.csv: nothing dates it" in txt and "`leie`" in txt
    dj = json.loads((rd / "data_refresh.json").read_text())
    assert dj["n_post_cutoff"] == 1 and dj["n_undated"] == 1 and dj["due_now"] == ["leie"]
    q = run_questions(rd, root).read_text(encoding="utf-8")
    assert "**Signal on the FCA ruler:** good" in q and "**Prune candidates**" in q and "noise" in q
    assert "skipped `death_master`" in q and "thin (DEGRADED): missing x_input" in q
    assert "| hcpcs_code | G0299 | 40 | 6.70 |" in q
    assert "2 forward positives on paper, 1 known at the freeze" in q
    assert "LABEL_AUDIT.md not in this run folder" in q and "no MANUS_LANES report" in q
    assert "1 post-cutoff, 1 undated" in q
