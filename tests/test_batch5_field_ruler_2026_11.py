"""test_batch5_field_ruler_2026_11.py — the held-out field-outcome ruler
(src/model_a/field_ruler.py, batch 5b-F, 2026-11-02): the date gate, the trained-on
refusal, the exact ruler schema the diagnostics discover."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from src.enforcement.field_outcomes import load_field_outcomes
from src.model_a import field_ruler as fr
from src.model_a.accuracy_diagnostics import _discover_rulers, eval_frame, load_ruler

# Luhn-valid toy NPIs (the 80840 prefix)
A, B, C, D, E, F, G = "1000000004", "1000000012", "1000000020", "1000000038", "1000000046", "1000000053", "1000000061"


def _outcomes(tmp: Path) -> Path:
    p = tmp / "field_outcomes.csv"
    pd.DataFrame([
        {"org_id": "", "npi": A, "date": "2026-10-29", "outcome": "over_capacity", "basis": "121 at the door on a licence of 60", "note": "", "source": "pi_sage"},
        {"org_id": "", "npi": B, "date": "2026-10-20", "outcome": "no_fleet", "basis": "2 vans for 185 legs", "note": "", "source": "pi_siu"},   # before the list date
        {"org_id": "", "npi": C, "date": "2026-11-01", "outcome": "confirmed", "basis": "x", "note": "", "source": "pi"},   # trained on
        {"org_id": "", "npi": D, "date": "2026-10-30", "outcome": "cleared", "basis": "16 buses on record", "note": "", "source": "pi"},
        {"org_id": "", "npi": E, "date": "2026-10-30", "outcome": "inconclusive", "basis": "closed on the day", "note": "", "source": "pi"},
        {"org_id": "org:7", "npi": "", "date": "2026-11-02", "outcome": "contradiction", "basis": "two filings disagree", "note": "", "source": "desk"},
        {"org_id": "", "npi": A, "date": "2026-11-05", "outcome": "cleared", "basis": "a second count", "note": "", "source": "pi"},   # adverse wins, earliest
        {"org_id": "", "npi": "1234567890", "date": "2026-11-02", "outcome": "confirmed", "basis": "", "note": "", "source": "pi"},   # bad Luhn: residue
    ]).to_csv(p, index=False)
    return p


def _n2o() -> pd.DataFrame:
    return pd.DataFrame({"npi": [F, G, A], "org_id": ["org:7", "org:7", "org:1"]})


def test_date_gate_training_gate_and_schema(tmp_path):
    out = load_field_outcomes(_outcomes(tmp_path))
    ruler, counts = fr.build_field_ruler(out, "2023-12", "2026-10-28", horizon_months=33,
                                         npi_to_org=_n2o(), trained_on={C})
    assert list(ruler.columns) == fr.RULER_COLS
    r = ruler.set_index("npi")
    assert counts["refused_by_date"] == 1 and B not in r.index                 # dated before the list
    assert counts["refused_trained_on"] == 1 and C not in r.index              # the model trained on it
    assert counts["inconclusive_dropped"] == 1 and E not in r.index
    assert counts["unresolved"] == 1 and counts["residue"][0]["drop_reason"].startswith("NPI fails")
    assert counts["positives"] == 3 and counts["negatives_cleared"] == 1 and counts["npis"] == 4
    assert set(r.index[r["is_prospective_positive"] == 1]) == {A, F, G}       # the org row reaches both members
    assert r.loc[A, "first_excl_date"] == "2026-10-29" and r.loc[A, "excl_type"] == "field:over_capacity"
    assert r.loc[A, "fca_tier"] == "hard" and r.loc[D, "fca_tier"] == "excluded"
    assert r.loc[D, "is_prospective_positive"] == 0 and r.loc[D, "was_excluded_pre_cutoff"] == 0
    assert (r["label_policy"] == "field").all() and (r["undated_exclusion"] == 0).all()
    assert counts["beyond_horizon"] == 3                                       # 2023-12 + 33 = 2026-09: the visits are later


def test_pre_cutoff_outcome_is_known_not_positive(tmp_path):
    out = load_field_outcomes(_outcomes(tmp_path))
    ruler, counts = fr.build_field_ruler(out, "2026-11", "2026-10-28", horizon_months=12, npi_to_org=_n2o())
    r = ruler.set_index("npi")
    # the 2026-10-29 visit and the one dated exactly on the 2026-11 freeze instant are on/before it:
    # known at the freeze, never a positive (the one-day boundary rule prospective_label applies)
    assert r.loc[A, "was_excluded_pre_cutoff"] == 1 and r.loc[A, "is_prospective_positive"] == 0
    assert r.loc[C, "was_excluded_pre_cutoff"] == 1 and r.loc[C, "is_prospective_positive"] == 0
    assert counts["known_pre_cutoff"] == 2 and counts["positives"] == 2 and counts["beyond_horizon"] == 0


def test_empty_and_all_refused(tmp_path):
    empty, counts = fr.build_field_ruler(pd.DataFrame(columns=["org_id", "npi", "date", "outcome", "basis", "note", "source"]),
                                         "2023-12", "2026-10-28")
    assert list(empty.columns) == fr.RULER_COLS and counts["positives"] == 0
    out = load_field_outcomes(_outcomes(tmp_path))
    none, counts = fr.build_field_ruler(out, "2023-12", "2027-01-01", npi_to_org=_n2o())
    assert len(none) == 0 and counts["refused_by_date"] == 8        # 7 valid rows, the org row on two members


def test_trained_on_npis_reads_the_label_and_its_provenance():
    m = pd.DataFrame({"npi": [A, B, C, D],
                      "provider_on_exclusion": [1, 0, 0, 0],
                      "exclusion_label_sources": ["leie", "", "doj_case;field", "field_outcomes_not_a_source"]})
    assert fr.trained_on_npis(m) == {A, C, D}
    assert fr.trained_on_npis(m.drop(columns=["exclusion_label_sources"])) == {A}
    assert fr.trained_on_npis(pd.DataFrame(columns=["npi"])) == set()


def test_discovered_by_the_diagnostics_and_graded(tmp_path):
    out = load_field_outcomes(_outcomes(tmp_path))
    run = tmp_path / "run"
    run.mkdir()
    ruler, _ = fr.build_field_ruler(out, "2023-12", "2026-10-28", horizon_months=36, npi_to_org=_n2o(), trained_on={C})
    p = run / fr.ruler_filename("2023-12", 36)
    assert p.name == "future_bans_after_2023-12__field36.csv"
    ruler.to_csv(p, index=False)
    found = _discover_rulers(run, "2023-12")
    assert "field" in found and found["field"] == p
    loaded = load_ruler(p)
    assert set(loaded["npi"][loaded["is_prospective_positive"] == 1]) == {A, F, G}
    matrix = pd.DataFrame({"npi": [A, B, C, D, E, F, G], "provider_on_exclusion": [0, 0, 1, 0, 0, 0, 0]})
    ev, stats = eval_frame(matrix, loaded, "2023-12", horizon_months=36)
    assert stats["positives"] == 3 and stats["dropped_known"] == 1
    assert set(ev.loc[ev["y"] == 1, "npi"]) == {A, F, G} and D in set(ev["npi"])


def test_header_only_field_ruler_is_read_and_not_graded(tmp_path):
    """v29 (2026-11-05): no visit inside the horizon -> a header-only ruler csv; the diagnostics
    died mapping its EMPTY datetime column (pandas 3 rebuilds an empty mapper as float64).
    The eval frame reads zero positives and run_diagnostics sets the ruler aside by name."""
    from src.model_a.accuracy_diagnostics import run_diagnostics
    run = tmp_path / "run"
    run.mkdir()
    empty, _ = fr.build_field_ruler(pd.DataFrame(columns=["org_id", "npi", "date", "outcome", "basis", "note", "source"]),
                                    "2023-12", "2026-10-28", horizon_months=33)
    p = run / fr.ruler_filename("2023-12", 33)
    empty.to_csv(p, index=False)
    loaded = load_ruler(p)
    assert len(loaded) == 0 and str(loaded["event_date"].dtype).startswith("datetime64")
    matrix = pd.DataFrame({"npi": [A, B, C], "provider_on_exclusion": [0, 0, 1]})
    ev, stats = eval_frame(matrix, loaded, "2023-12", horizon_months=33)
    assert stats["positives"] == 0 and len(ev) == 2 and ev["event_date"].isna().all()
    # beside a ruler that carries positives the empty one is listed, never graded, never the headline
    live = pd.DataFrame({"npi": [A, B], "first_excl_date": ["2024-06-01", "2024-07-01"], "excl_type": "1128a1",
                         "is_prospective_positive": [1, 1], "was_excluded_pre_cutoff": 0, "undated_exclusion": 0,
                         "fca_tier": "hard"})
    live["event_date"] = pd.to_datetime(live["first_excl_date"])
    book = pd.DataFrame({"org_id": ["org:1", "org:2"], "org_name": ["X", "Y"], "priority_rank": [1, 2],
                         "member_npis": [A, B], "n_providers": [1, 1], "total_billed": [1e6, 1e6]})
    big = pd.DataFrame({"npi": [A, B, C, D, E, F, G], "provider_on_exclusion": 0, "org_id": ["org:1", "org:2", "org:3", "org:4", "org:5", "org:6", "org:7"],
                        "net_paid": 1e5, "f1": [0.9, 0.8, 0.1, 0.2, 0.3, 0.4, 0.5]})
    res = run_diagnostics(big, {"feature_partition": {"trainable": ["f1"]}}, {"field": loaded, "hard": live}, "2023-12",
                          priority_list=book, score_source="priority_list", min_damages=0.0, horizon_months=33,
                          headline="field")
    assert "field" in res["rulers_skipped"] and "field" not in res["rulers"]
    assert res["headline_ruler"] == "hard"
    from src.model_a.accuracy_diagnostics import kpi_block, to_markdown
    md = to_markdown(res, "t")
    assert "field (not graded: no positive in the universe)" in md
    assert not any(k.endswith("_field") for k in kpi_block(res))


def test_cli_with_run_dir_registry_date_and_matrix(tmp_path):
    root = tmp_path / "d"
    (root / "enforcement").mkdir(parents=True)
    (root / "graph").mkdir()
    (root / "model_a").mkdir()
    _outcomes(root / "enforcement")
    _n2o().to_parquet(root / "graph" / "npi_to_org.parquet", index=False)
    run = root / "model_a" / "frozen_2023-12_v28"
    run.mkdir()
    pd.DataFrame({"npi": [A, B, C, D, E, F, G], "provider_on_exclusion": [0, 0, 1, 0, 0, 0, 0],
                  "score": [0.1] * 7}).to_parquet(run / "provider_features_for_model.parquet", index=False)
    (root / "model_a" / "run_registry.jsonl").write_text(
        json.dumps({"run_dir": str(run.resolve()), "tag": run.name, "registered_at": "2026-10-28T10:00:00+00:00"}) + "\n"
        + json.dumps({"run_dir": "/elsewhere/v1", "tag": "v1", "registered_at": "2020-01-01T00:00:00+00:00"}) + "\n")
    assert fr.list_date_from_registry(root, run) == pd.Timestamp("2026-10-28T10:00:00")
    r = subprocess.run([sys.executable, "-m", "src.model_a.field_ruler", "--data-root", str(root), "--cutoff", "2023-12",
                        "--horizon", "33", "--run-dir", str(run)], capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode == 0, r.stderr
    assert "list date 2026-10-28" in r.stdout and "1 training positives" in r.stdout
    assert "3 positives" in r.stdout and "refused 1 as trained-on, 1 on/before the list date" in r.stdout
    out = run / "future_bans_after_2023-12__field33.csv"
    df = pd.read_csv(out, dtype=str)
    assert list(df.columns) == fr.RULER_COLS and set(df.loc[df["is_prospective_positive"] == "1", "npi"]) == {A, F, G}
    md = (run / "future_bans_after_2023-12__field33.FIELD.md").read_text()
    assert "refused_trained_on | 1" in md and "beyond cutoff + horizon" in md and "NPI fails" in md
    # explicit --list-date and --out, no run dir
    out2 = tmp_path / "x" / "future_bans_after_2023-12__field36.csv"
    r2 = subprocess.run([sys.executable, "-m", "src.model_a.field_ruler", "--data-root", str(root), "--cutoff", "2023-12",
                         "--horizon", "36", "--list-date", "2026-10-31", "--out", str(out2)], capture_output=True, text=True,
                        cwd=str(Path(__file__).resolve().parents[1]))
    assert r2.returncode == 0, r2.stderr
    df2 = pd.read_csv(out2, dtype=str)
    # no matrix: C is not refused; A (10-29) and the cleared D (10-30) are on/before the list date
    assert set(df2.loc[df2["is_prospective_positive"] == "1", "npi"]) == {C, F, G}
    assert D not in set(df2["npi"])
    # neither list date nor run dir: refused
    r3 = subprocess.run([sys.executable, "-m", "src.model_a.field_ruler", "--data-root", str(root), "--cutoff", "2023-12",
                         "--out", str(out2)], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
    assert r3.returncode != 0 and "--list-date is required" in r3.stderr
