"""run_issues: one ranked ledger of everything a run's own reports raised, tracked
across runs (recurring escalates, fixed is recorded), with the next pre-registration's
checks suggested. Synthetic run folders only."""

from __future__ import annotations

import json
from pathlib import Path

from src.model_a.run_issues import collect_issues, suggested_checks, to_markdown, update_ledger


def _run_dir(tmp_path: Path, name: str, fails: int = 1, skip_source: bool = True,
             hits50: int = 1, base50: float = 0.2, verdict: str = "NO MEASURABLE SIGNAL") -> Path:
    d = tmp_path / name
    d.mkdir()
    audit = [{"source": "dmepos", "status": "used", "detail": "x"}]
    if skip_source:
        audit.append({"source": "hospice", "status": "skipped", "detail": "no file in preclean/hospice/"})
    (d / "feature_manifest.json").write_text(json.dumps({
        "asof_cutoff": "2021-12", "label": "provider_on_exclusion", "n_providers": 1000,
        "expectations": {"fails": fails, "warns": 1}, "sources_audit": audit,
        "feature_partition": {"unmapped": []}, "graph_substrate": {"address_layer_frozen": True}}))
    rows = ["| FAIL | `hospice_live_discharge_rate` | all_nan_feature | 0 non-null values | a produced feature carries data | its source loaded zero rows |"] * (1 if fails else 0)
    rows.append("| WARN | `shell_score` | tie_flooded_top_decile | 66% of the top decile is one value | the top decile discriminates | ranking draws among ties |")
    (d / "EXPECTATIONS_REPORT.md").write_text(
        f"# EXPECTATIONS_REPORT\n\n_{fails} FAIL / 1 WARN._\n\n| severity | column/scope | check | observed | expected | go look at |\n|---|---|---|---|---|---|\n"
        + "\n".join(rows) + "\n")
    (d / "scheme_health.json").write_text(json.dumps({"schemes": [
        {"scheme": "pill_mill", "status": "CHECK_DIRECTION", "label_auc": 0.41, "n_label_pos": 80},
        {"scheme": "dme_ring", "status": "DEGRADED", "n_inputs_present": 1, "n_inputs_total": 2,
         "missing_inputs": "dme_ineligible_referred_dollars", "subscore_coverage": 0.1},
        {"scheme": "upcoding", "status": "HEALTHY", "label_auc": 0.62, "n_label_pos": 300}], "regressions": []}))
    (d / "SCHEME_TRIAGE.md").write_text(
        "# SCHEME TRIAGE\n\n| source | inferred vintage | verdict | file detail |\n|---|---|---|---|\n"
        "| `address` | unknown | **UNKNOWN** | x |\n| `pos` | 2023Q4 | **POST-CUTOFF** | pos_2023q4.csv |\n")
    (d / "NETWORK_AB_REPORT.md").write_text(f"# NETWORK A/B REPORT\n\n- **VERDICT: {verdict}: the network family.**\n")
    (d / "accuracy_diagnostics__pursuit.json").write_text(json.dumps({
        "list_name": "provider_priority_list_pursuit.csv",
        "kpis": {"acc_org_hits50": hits50, "acc_base50": base50, "acc_inst_share50": 0.5, "acc_top50_jaccard": 0.4}}))
    return d


def test_collect_ranks_fail_first_and_names_the_fix(tmp_path):
    d = _run_dir(tmp_path, "frozen_2021-12_v7")
    issues, summary = collect_issues(d, prereg={"tag": "frozen_2021-12_v7", "checks": ["hits50 >= 2", "frozen = True"]})
    ids = [i["id"] for i in issues]
    assert issues[0]["severity"] == "FAIL"
    assert "feature:hospice_live_discharge_rate:all_nan_feature" in ids
    assert "vintage:pos:post_cutoff_edition" in ids                      # a post-cutoff edition is a FAIL
    assert "policy:preregistration:hits50 >= 2" in ids                   # the locked check failed (1 < 2)
    assert "data:hospice:source_skipped" in ids
    assert "scheme:pill_mill:inverted_direction" in ids and "scheme:dme_ring:missing_inputs" in ids
    assert "evaluation:network_family:verdict_not_keep" in ids
    assert "evaluation:provider_priority_list_pursuit.csv:institutional_share_high" in ids
    assert "evaluation:provider_priority_list_pursuit.csv:top50_unstable" in ids
    assert not any("frozen = True" in i for i in ids)                     # the passing check raises nothing
    assert all(i["action"] for i in issues)                              # every row names a fix
    sev = [i["severity"] for i in issues]
    assert sev == sorted(sev, key=lambda s: {"FAIL": 0, "WARN": 1, "INFO": 2}[s])
    assert summary["n_fail"] >= 3 and summary["hits50"] == 1
    checks = suggested_checks(issues, summary)
    assert "fails = 0" in checks and "network = KEEP" in checks and "inst_share50 <= 0.4" in checks
    md = to_markdown("frozen_2021-12_v7", issues, summary, [], checks)
    assert "## The list" in md and "hospice_live_discharge_rate" in md and "never accusations" in md


def test_ledger_tracks_recurrence_and_resolution(tmp_path):
    ledger = tmp_path / "issues_ledger.jsonl"
    d7 = _run_dir(tmp_path, "v7", fails=1)
    i7, _ = collect_issues(d7)
    i7, res7 = update_ledger(ledger, "v7", i7)
    assert res7 == [] and all(i["n_runs"] == 1 for i in i7)
    # v8: the all-NaN feature is fixed, hospice still skipped
    d8 = _run_dir(tmp_path, "v8", fails=0)
    i8, _ = collect_issues(d8, prev_run_dir=d7)
    i8, res8 = update_ledger(ledger, "v8", i8)
    assert {r["id"] for r in res8} == {"feature:hospice_live_discharge_rate:all_nan_feature"}
    skip = next(i for i in i8 if i["id"] == "data:hospice:source_skipped")
    assert skip["n_runs"] == 2 and skip["first_seen"] == "v7"
    # recurring issues sort before new ones of the same severity
    warns = [i for i in i8 if i["severity"] == "WARN"]
    assert warns[0]["n_runs"] >= warns[-1]["n_runs"]
    recs = {json.loads(l)["id"]: json.loads(l) for l in ledger.read_text().splitlines() if l.strip()}
    assert recs["feature:hospice_live_discharge_rate:all_nan_feature"]["status"] == "resolved"
    assert recs["data:hospice:source_skipped"]["runs"] == ["v7", "v8"]
    # v9 regresses: a new skip and more FAILs than v8 are flagged against v8
    d9 = _run_dir(tmp_path, "v9", fails=1)
    (d9 / "feature_manifest.json").write_text(json.dumps({**json.loads((d9 / "feature_manifest.json").read_text()),
        "sources_audit": [{"source": "dmepos", "status": "skipped", "detail": "gone"},
                          {"source": "hospice", "status": "skipped", "detail": "no file"}]}))
    i9, _ = collect_issues(d9, prev_run_dir=d8)
    ids = {i["id"] for i in i9}
    assert "regression:dmepos:newly_skipped" in ids and "regression:expectations:more_fails_than_previous" in ids
