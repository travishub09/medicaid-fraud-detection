"""
test_release_system.py — prereg/signoff/capsule/cohort, dispositions,
monitors, acquisition queue.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


# ---------------- run_registry: prereg, signoff, capsule, cohorts ----------

def test_capsule_and_cohort_in_parse(tmp_path):
    from src.model_a.run_registry import parse_run_dir
    d = tmp_path / "vX"
    d.mkdir()
    (d / "feature_manifest.json").write_text(
        json.dumps({"asof_cutoff": "2023-12", "label": "provider_on_exclusion"}),
        encoding="utf-8")
    row = parse_run_dir(d)
    assert row["eval_cohort"] == "2023-12"
    cap = row["capsule"]
    assert cap["libs"].get("pandas")
    assert "feature_manifest.json" in cap["inputs"]
    assert len(cap["inputs"]["feature_manifest.json"]["sha256"]) == 16


def test_cohort_usage_warns_after_five():
    from src.model_a.run_registry import cohort_usage
    rows = [{"eval_cohort": "2023-12"}] * 6 + [{"eval_cohort": "2024-06"}]
    warns = cohort_usage(rows)
    assert len(warns) == 1 and "2023-12" in warns[0]
    assert not cohort_usage(rows[:5])


def test_report_shows_prereg_and_signoff():
    from src.model_a.run_registry import to_markdown, recommend
    rows = [{"tag": "v2", "registered_at": "2026-07-28T00:00:00",
             "expectation_fails": 0, "network_verdict": "KEEP (x)",
             "ablation_lift_delta": 0.74, "ablation_lift_lo": 0.41,
             "ablation_lift_hi": 1.11, "eval_cohort": "2023-12",
             "run_dir": "/x/v2"}]
    prereg = [{"tag": "v3", "goal": "beat champion with clean inputs",
               "expected": "lift CI clear of zero",
               "declared_at": "2026-07-28T01:00:00"}]
    signoffs = [{"tag": "v2", "by": "travis", "status": "reproduced",
                 "note": "", "signed_at": "2026-07-29T00:00:00"}]
    md = to_markdown(rows, recommend(rows), prereg, signoffs)
    assert "REPRODUCED (travis)" in md
    assert "beat champion with clean inputs" in md
    assert "run not registered yet" in md          # v3 declared, not yet run
    # unreproduced chip when no signoff exists
    md2 = to_markdown(rows, recommend(rows), [], [])
    assert "UNREPRODUCED" in md2


def test_html_renders_from_data():
    from src.model_a.run_registry import to_html, recommend
    rows = [{"tag": "v1", "ablation_lift_delta": 0.55,
             "ablation_lift_lo": 0.23, "ablation_lift_hi": 0.91,
             "registered_at": "2026-07-26T00:00:00", "run_dir": "/x",
             "expectation_fails": 0, "network_verdict": "KEEP",
             "eval_cohort": "2023-12"}]
    html = to_html(rows, recommend(rows), [], [])
    assert "<svg" in html and "+0.550" in html and "v1" in html


# ---------------- lead dispositions ---------------------------------------

def test_disposition_log_and_precision(tmp_path):
    from src.model_a.lead_dispositions import (log_disposition, load_log,
                                               summarize, to_markdown)
    p = tmp_path / "disp.jsonl"
    log_disposition(p, "1234567893", "v2", "reviewing")
    log_disposition(p, "1234567893", "v2", "pursued")      # latest wins
    log_disposition(p, "1999999992", "v2", "killed",
                    reason="FQHC grant program explains billing")
    log_disposition(p, "org:acme", "v2", "referred")
    log_disposition(p, "1111111116", "v2", "parked")
    by = summarize(load_log(p))
    v = by["v2"]
    assert v["total"] == 4
    assert v["by_disposition"]["pursued"] == 1
    assert v["judged"] == 3                                 # parked not judged
    assert abs(v["precision_in_practice"] - 2 / 3) < 1e-9
    md = to_markdown(by)
    assert "fqhc grant program" in md.lower()
    with pytest.raises(ValueError):
        log_disposition(p, "x", "v2", "vibes")


# ---------------- monitors -------------------------------------------------

def test_freshness_and_drift(tmp_path):
    from src.model_a.monitors import check_freshness, psi, check_drift
    (tmp_path / "preclean").mkdir()
    (tmp_path / "preclean" / "Caught.csv").write_text("npi\n1\n")
    fresh = check_freshness(tmp_path, {"preclean/Caught.csv": 120,
                                       "missing/file.csv": 30})
    st = {f["file"]: f["status"] for f in fresh}
    assert st["preclean/Caught.csv"] == "OK"
    assert st["missing/file.csv"] == "MISSING"

    rng = np.random.default_rng(0)
    base = rng.normal(0, 1, 5000)
    assert psi(base, rng.normal(0, 1, 5000)) < 0.1          # same dist
    assert psi(base, rng.normal(1.5, 1, 5000)) >= 0.2       # shifted

    a = tmp_path / "a.parquet"
    b = tmp_path / "b.parquet"
    pd.DataFrame({"net_paid": base, "provider_on_exclusion":
                  (rng.random(5000) < 0.02).astype(int)}).to_parquet(a)
    pd.DataFrame({"net_paid": base + 2.0, "provider_on_exclusion":
                  np.zeros(5000, dtype=int)}).to_parquet(b)
    drift = check_drift(a, b, features=["net_paid"])
    st = {d["feature"]: d["status"] for d in drift}
    assert st["net_paid"] == "ALARM"
    assert st["provider_on_exclusion (positives)"] == "ALARM"  # label shrank


# ---------------- acquisition queue ----------------------------------------

def test_queue_merges_three_reports(tmp_path):
    from src.model_a.acquisition_queue import (thin_schemes, join_gaps,
                                               to_markdown)
    run = tmp_path / "run"
    run.mkdir()
    (run / "SCHEME_EVAL.md").write_text(
        "# eval\n## Skipped\n\n- open_payments: only 12 case-labeled "
        "positives (< 30)\n\n_A skipped source is a harvest gap_\n",
        encoding="utf-8")
    hv = tmp_path / "harvest"
    hv.mkdir()
    (hv / "JOIN_AUDIT.md").write_text(
        "**Joinable now: 1,029 of 3,310 (31%).**\n"
        "| likely_public | 1,399 |\n| unknown | 664 |\n"
        "| unlikely_public | 118 |\n", encoding="utf-8")
    thin = thin_schemes(run)
    assert thin[0]["source"] == "open_payments"
    gaps = join_gaps(hv)
    assert gaps["joinable"] == 1029 and gaps["likely_public"] == 1399
    md = to_markdown([{"source": "mue", "status": "SKIPPED",
                       "detail": "needs preclean/reference/mue/mue.csv"}],
                     thin, gaps)
    assert "Tier 1" in md and "mue" in md
    assert "open_payments" in md and "1,399" in md
