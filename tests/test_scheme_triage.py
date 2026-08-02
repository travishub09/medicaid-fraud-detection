"""
test_scheme_triage.py — the six-question underperformance loop: selection
triggers, each question answered from the right evidence, and the report shape.
"""

from __future__ import annotations

import json

import pandas as pd

from src.model_a.scheme_triage import (diagnose, load_run, run_triage,
                                       select_underperformers, to_markdown)


def _health_rows():
    return pd.DataFrame([
        {"scheme": "pill_mill", "status": "CHECK_DIRECTION",
         "weight_present": 1.0, "n_inputs_present": 2, "n_inputs_total": 2,
         "missing_inputs": "", "subscore_coverage": 0.17,
         "subscore_constant": False, "label_auc": 0.35, "n_label_pos": 40,
         "direction": "INVERTED?"},
        {"scheme": "dme_ring", "status": "DEGRADED", "weight_present": 0.5,
         "n_inputs_present": 2, "n_inputs_total": 4,
         "missing_inputs": "dme_ordering_md_concentration;ineligible_referral_share",
         "subscore_coverage": 0.06, "subscore_constant": False,
         "label_auc": 0.83, "n_label_pos": 25, "direction": ""},
        {"scheme": "upcoding", "status": "HEALTHY", "weight_present": 1.0,
         "n_inputs_present": 2, "n_inputs_total": 2, "missing_inputs": "",
         "subscore_coverage": 0.13, "subscore_constant": False,
         "label_auc": 0.51, "n_label_pos": 80, "direction": ""},
        {"scheme": "rapid_ramp", "status": "HEALTHY", "weight_present": 1.0,
         "n_inputs_present": 3, "n_inputs_total": 3, "missing_inputs": "",
         "subscore_coverage": 0.78, "subscore_constant": False,
         "label_auc": 0.69, "n_label_pos": 200, "direction": ""},
    ])


def _run_dir(tmp_path):
    _health_rows().to_json(tmp_path / "scheme_health.json")
    manifest = {
        "sources_used": {
            "opioid": ["opioid_claim_share", "opioid_long_acting_share"],
            "partb": ["em_high_level_share", "em_level_mean"]},
        "sources_audit": [
            {"source": "opioid", "status": "used",
             "detail": "1,380,665 providers from opioid_prescriber_2023.csv"},
            {"source": "partb", "status": "used",
             "detail": "1,175,281 providers from partb_2023.csv"}],
    }
    (tmp_path / "feature_manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "EXPECTATIONS_REPORT.md").write_text(
        "| severity | column/scope | check | observed | expected | go |\n"
        "|---|---|---|---|---|---|\n"
        "| WARN | `subscore_pill_mill` | tie_flooded_top_decile | 71% one "
        "value | discriminates | look |\n", encoding="utf-8")
    return tmp_path


def test_selection_triggers_status_and_weak_auc():
    under = select_underperformers(_health_rows())
    schemes = set(under["scheme"])
    assert "pill_mill" in schemes and "dme_ring" in schemes
    assert "rapid_ramp" not in schemes                # healthy, strong AUC
    assert "upcoding" in schemes                      # healthy but AUC 0.51
    row = under.set_index("scheme").loc["upcoding"]
    assert "label AUC" in row["trigger"]


def test_diagnose_answers_all_six_with_the_right_evidence(tmp_path):
    ev = load_run(_run_dir(tmp_path))
    under = select_underperformers(ev["health"])
    d = {x["scheme"]: x for x in
         (diagnose(r, ev) for _, r in under.iterrows())}

    pm = d["pill_mill"]
    assert any("INVERTED" in s or "BELOW average" in s for s in pm["q1"])
    assert any("re-derive the sign" in s for s in pm["q4"])       # Q4 action
    assert any("opioid_prescriber_2023.csv" in s for s in pm["q5"])  # wiring
    assert any("registry" in s.lower() for s in pm["q6"])         # file check
    # exclusion-blind framing present for pill_mill
    assert any("BY CONSTRUCTION" in s for s in pm["q1"])

    dr = d["dme_ring"]
    assert any("dme_ordering_md_concentration" in s for s in dr["q3"])
    assert any("ineligible_referral_share" in s for s in dr["q3"])


def test_report_renders_and_wires_into_run_dir(tmp_path):
    out = run_triage(_run_dir(tmp_path))
    text = out.read_text(encoding="utf-8")
    assert "SCHEME TRIAGE" in text
    for q in ("1. Why do we think", "2. Were the analytics built",
              "3. Are data fields missing", "4. Were the analytics coded",
              "5. Were the data wired", "6. Did we grab the right"):
        assert q in text
    assert "pill_mill" in text and "dme_ring" in text
    assert "never gate the current run" in text


def test_empty_health_yields_clean_report(tmp_path):
    (tmp_path / "feature_manifest.json").write_text("{}")
    pd.DataFrame().to_json(tmp_path / "scheme_health.json")
    out = run_triage(tmp_path)
    assert "No underperforming schemes" in out.read_text(encoding="utf-8")
