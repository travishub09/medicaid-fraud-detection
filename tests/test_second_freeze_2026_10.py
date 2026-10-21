"""The second freeze and the design note's three gap-closers (2026-10-21):
current-day edition picking (hold_folders_to_live), the vintage ledger, the
freeze-input resolver (as-of NPPES for any cutoff), the owner-edition archive,
the leak flag, and the two-ruler read-out."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.model_a import freeze_inputs as fz
from src.model_a import freeze_pair as fp
from src.model_a import provider_features_export as pfe
from src.model_a import run_issues as ri
from src.model_a.scheme_health import leak_suspect
from src.entity_graph import owners_archive as oa


def _touch(d: Path, *names: str) -> list[Path]:
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for n in names:
        (d / n).write_text("x")
        out.append(d / n)
    return out


def test_current_day_reads_newest_not_pooled(tmp_path):
    pfe._VINTAGE.clear()
    pos = _touch(tmp_path / "pos", "POS_File_Hospital_Q4_2021.csv", "POS_File_CLIA_Q4_2021.csv",
                 "POS_File_Hospital_Q1_2026.csv", "POS_File_CLIA_Q1_2026.csv")
    logs: list[str] = []
    got = pfe._asof_files(pos, None, logs.append, "pos", keep="newest")
    assert sorted(p.name for p in got) == ["POS_File_CLIA_Q1_2026.csv", "POS_File_Hospital_Q1_2026.csv"]
    assert any("set aside" in l for l in logs)
    v = pfe._VINTAGE[-1]
    assert v["verdict"] == "current_day_newest" and v["edition"] == "2026-03" and v["cutoff"] is None
    # frozen: the Q4 2021 pair, 2026 skipped
    pfe._VINTAGE.clear()
    got = pfe._asof_files(pos, "2021-12", logs.append, "pos", keep="newest")
    assert sorted(p.name for p in got) == ["POS_File_CLIA_Q4_2021.csv", "POS_File_Hospital_Q4_2021.csv"]
    assert pfe._VINTAGE[-1]["verdict"] == "pre_cutoff" and pfe._VINTAGE[-1]["staleness_months"] == 0


def test_pbj_keeps_four_quarters_and_newest_edition_by_date(tmp_path):
    pfe._VINTAGE.clear()
    pbj = _touch(tmp_path / "facility", "pbj_daily_staffing_2021Q3.csv", "pbj_daily_staffing_2021Q4.csv",
                 "pbj_daily_staffing_2024Q4.csv", "pbj_daily_staffing_2025Q1.csv", "pbj_daily_staffing_2025Q2.csv",
                 "pbj_daily_staffing_2025Q4.csv")
    got = pfe._asof_files(pbj, None, None, "facility/pbj", keep=4)
    assert [p.name for p in got] == [p.name for p in pbj[2:]]       # the newest four quarters
    got = pfe._asof_files(pbj, "2021-12", None, "facility/pbj", keep=4)
    assert [p.name for p in got] == [p.name for p in pbj[:2]]
    # _asof_newest current-day: the NEWEST edition, not the alphabetically first
    _touch(tmp_path / "facility", "NH_Ownership_Nov2023.csv", "NH_Ownership_Jul2026.csv", "NH_Ownership_Feb2022.csv")
    pick = pfe._asof_newest(tmp_path / "facility", ("NH_Ownership*.csv",), None, None, "facility/nh_ownership")
    assert pick.name == "NH_Ownership_Jul2026.csv"
    pick = pfe._asof_newest(tmp_path / "facility", ("NH_Ownership*.csv",), "2023-06", None, "facility/nh_ownership")
    assert pick.name == "NH_Ownership_Feb2022.csv"


def test_undated_substitution_is_a_ledger_row(tmp_path):
    pfe._VINTAGE.clear()
    files = _touch(tmp_path / "hcris", "hcris.csv", "hcris_2024.csv")
    got = pfe._asof_files(files, "2021-12", None, "hcris", keep="newest")
    assert [p.name for p in got] == ["hcris.csv"]
    v = pfe._VINTAGE[-1]
    assert v["verdict"] == "undated_substituted" and v["skipped"] == ["hcris_2024.csv"]
    # _asof_pick with a later reference edition: post_cutoff_reference, staleness recorded
    pfe._VINTAGE.clear()
    _touch(tmp_path / "nadac", "nadac_2023.csv")
    p = pfe._asof_pick(tmp_path / "nadac", "nadac.csv", asof_cutoff="2021-12", fallback_newest=True)
    assert p.name == "nadac_2023.csv"
    assert pfe._VINTAGE[-1]["verdict"] == "post_cutoff_reference" and pfe._VINTAGE[-1]["staleness_months"] == 24


def test_run_issues_grades_the_ledger_and_the_freeze_inputs(tmp_path):
    run = tmp_path / "frozen_2023-06_vX"
    run.mkdir()
    (run / "feature_manifest.json").write_text(json.dumps({
        "asof_cutoff": "2023-06", "sources_audit": [], "vintage_audit": [
            {"source": "pos", "cutoff": "2023-06", "picked": ["POS_Q4_2021.csv"], "skipped": [], "edition": "2021-12",
             "staleness_months": -18, "verdict": "pre_cutoff", "note": ""},
            {"source": "hcris", "cutoff": "2023-06", "picked": ["hcris.csv"], "skipped": ["hcris_2024.csv"],
             "edition": None, "staleness_months": None, "verdict": "undated_substituted", "note": "n"},
            {"source": "nadac", "cutoff": "2023-06", "picked": ["nadac_2025.csv"], "skipped": [], "edition": "2025-12",
             "staleness_months": 30, "verdict": "post_cutoff_reference", "note": "lookup"},
            {"source": "bug", "cutoff": "2023-06", "picked": ["x_2024.csv"], "skipped": [], "edition": "2024-12",
             "staleness_months": 18, "verdict": "pre_cutoff", "note": ""}]}))
    (run / "freeze_inputs.json").write_text(json.dumps({"cutoff": "2023-06", "asof_nppes": {
        "rule": "nearest_later", "edition": "2023-12", "staleness_months": 6}}))
    (run / "scheme_health.json").write_text(json.dumps({"schemes": [
        {"scheme": "worthless_services", "status": "HEALTHY", "label_auc": 0.52, "n_label_pos": 40,
         "fca_auc": 0.81, "n_fca_pos": 30, "fca_verdict": "signal", "leak_suspect": True}]}))
    issues, summary = ri.collect_issues(run)[:2]
    keys = {(i["area"], i["subject"], i["check"], i["severity"]) for i in issues}
    assert ("vintage", "hcris", "undated_substituted", "WARN") in keys
    assert ("vintage", "nadac", "post_cutoff_reference", "WARN") in keys
    assert ("vintage", "bug", "post_cutoff_edition", "FAIL") in keys
    assert ("vintage", "asof_nppes", "nppes_edition_after_freeze", "WARN") in keys
    assert ("scheme", "worthless_services", "leak_suspect", "FAIL") in keys
    assert not any(i["subject"] == "pos" and i["area"] == "vintage" for i in issues)


def test_leak_suspect_rule():
    assert leak_suspect(0.52, 0.81, 40, 30)
    assert not leak_suspect(0.70, 0.78, 40, 30)          # a small gap is not a leak
    assert not leak_suspect(0.40, 0.60, 40, 30)          # forward AUC below 0.70
    assert not leak_suspect(0.52, 0.81, 40, 10)          # thin forward label
    assert not leak_suspect(None, 0.9, 40, 30)


def test_freeze_inputs_picks_on_or_before_else_nearest_later(tmp_path):
    (tmp_path / "preclean" / "nppes").mkdir(parents=True)
    (tmp_path / "preclean" / "nppes_history").mkdir()
    (tmp_path / "preclean" / "nppes" / "npidata_pfile_20050523-20211212.csv").write_text("x")
    (tmp_path / "preclean" / "nppes_history" / "npidata_pfile_20050523-20231210_thru2023.csv").write_text("x")
    r = fz.pick_asof_nppes(tmp_path, "2021-12")
    assert r["rule"] == "on_or_before" and r["edition"] == "2021-12" and r["staleness_months"] == 0
    r = fz.pick_asof_nppes(tmp_path, "2023-06")
    assert r["rule"] == "nearest_later" and r["edition"] == "2023-12" and r["staleness_months"] == 6
    assert r["path"].endswith("thru2023.csv")
    r = fz.pick_asof_nppes(tmp_path, "2024-06")
    assert r["rule"] == "on_or_before" and r["edition"] == "2023-12" and r["staleness_months"] == -6
    res = fz.resolve(tmp_path, "2023-06", 36)
    assert res["horizon_end"] == "2026-06" and res["graph_dir"].endswith("graph_asof_2023-06_full")
    assert fz.nppes_edition("NPPES.csv") is None


def test_owners_archive_is_idempotent(tmp_path):
    cur = tmp_path / "preclean" / "owners"
    cur.mkdir(parents=True)
    body = "ENROLLMENT ID,ASSOCIATE ID - OWNER,ROLE TEXT - OWNER\nO2020,1234,OWNER\n"
    (cur / "SNF_All_Owners_2026.10.01.csv").write_text(body)
    (cur / "HHA_All_Owners.csv").write_text(body.replace("O2020", "O3030"))      # undated: mtime month
    (cur / "notes.csv").write_text("a,b\n1,2\n")                                   # not an All-Owners layout
    rows = oa.archive_editions(tmp_path)
    acts = {r["file"]: r["action"] for r in rows}
    assert acts["SNF_All_Owners_2026.10.01.csv"] == "copied -> SNF_All_Owners_2026.10.01.csv"
    assert acts["HHA_All_Owners.csv"].startswith("copied -> HHA_All_Owners_")
    assert acts["notes.csv"] == "not_all_owners"
    arc = tmp_path / "preclean" / "owners_prior"
    n = len(list(arc.glob("*.csv")))
    assert n == 2
    rows = oa.archive_editions(tmp_path)                                           # second run: nothing new
    assert len(list(arc.glob("*.csv"))) == n
    assert all(r["action"] in ("already_archived", "not_all_owners") or r["action"].startswith("identical_to")
               for r in rows)
    # a re-download under a new date with IDENTICAL content is not archived twice
    (cur / "SNF_All_Owners_2026.11.01.csv").write_text(body)
    rows = oa.archive_editions(tmp_path)
    assert any(r["action"].startswith("identical_to") for r in rows if r["file"].startswith("SNF_All_Owners_2026.11"))
    assert len(list(arc.glob("*.csv"))) == n


def _fake_run(d: Path, cutoff: str, hits50: int, used: list[str], skipped: list[str], jac: float):
    d.mkdir(parents=True)
    (d / "feature_manifest.json").write_text(json.dumps({
        "asof_cutoff": cutoff,
        "sources_audit": [{"source": s, "status": "used", "detail": ""} for s in used]
                         + [{"source": s, "status": "skipped", "detail": "no file"} for s in skipped],
        "vintage_audit": [{"source": "pos", "verdict": "pre_cutoff"}]}))
    (d / "training_label_policy.json").write_text(json.dumps({"label_tier": "hard", "n_positives": 1500, "n_doj_positives": 1300}))
    (d / "freeze_inputs.json").write_text(json.dumps({"cutoff": cutoff, "horizon_months": 36,
                                                      "asof_nppes": {"rule": "on_or_before", "staleness_months": 0}}))
    acc = {"headline_ruler": "fca", "horizon_months": 36, "stability": {"jaccard_top_org": jac},
           "rulers": {"fca": {"universe": {"positives": 583},
                              "org_budgets": [{"budget": k, "hits": h, "random_expected": e, "random_p": 0.01, "wilson_lo": 0.01}
                                              for k, h, e in ((16, 1, 0.1), (50, hits50, 0.4), (100, 5, 0.7), (250, 7, 1.8))],
                              "npi_budgets": [{"budget": "top 1.0%", "lift_vs_random": 9.0}],
                              "error_taxonomy": [{"k": 50, "cause": "institutional", "n": 0}, {"k": 50, "cause": "miss", "n": 50}],
                              "lead_time": {"median_lead_months": 25.5}}}}
    (d / "accuracy_diagnostics.json").write_text(json.dumps(acc))


def test_freeze_pair_reads_two_runs_and_names_the_source_difference(tmp_path):
    a = tmp_path / "frozen_2021-12_v19"
    b = tmp_path / "frozen_2023-06_v19b"
    _fake_run(a, "2021-12", 2, ["partb", "pos"], ["order_referring", "ownership_churn"], 0.64)
    _fake_run(b, "2023-06", 0, ["partb", "pos", "order_referring", "ownership_churn"], [], 0.55)
    runs = [fp.read_run(a), fp.read_run(b)]
    cmp = fp.compare(runs)
    by = {c["check"]: c for c in cmp["checks"]}
    assert by["pursuit hits@50 > random"]["verdict"] == "holds on frozen_2021-12_v19"
    assert by["pursuit hits@100 > random"]["verdict"] == "HOLDS ON BOTH"
    assert by["pursuit top50_jaccard >= 0.6"]["holds"] == [True, False]
    assert {r["source"] for r in cmp["source_diff"]} == {"order_referring", "ownership_churn"}
    md = fp.render(runs, cmp)
    assert "HOLDS ON BOTH" in md and "order_referring" in md and "2023-06" in md
    out = tmp_path / "FREEZE_PAIR.md"
    out.write_text(md)
    assert out.exists()
