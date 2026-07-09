"""
test_results_digest.py — the plain-English run summary. Covers artifact discovery,
graceful degradation, and that the key facts (health, top signal, network verdict)
land in the output.
"""

from __future__ import annotations

import json

import pandas as pd

from src.model_a.results_digest import build_digest, _network_verdict


def _write_run(tmp):
    (tmp / "feature_manifest.json").write_text(json.dumps({
        "n_providers": 617062, "label": "provider_on_exclusion",
        "raw_feature_cols": ["a", "b"], "peerpct_cols": ["a__peerpct"],
        "subscore_cols": ["subscore_upcoding"],
        "scheme_coverage": ["upcoding", "pill_mill"],
        "expectations": {"fails": 0, "warns": 1},
        "sources_audit": [
            {"source": "part_b", "status": "used", "reason": "", "file": "partb.csv"},
            {"source": "opioid", "status": "skipped", "reason": "file not found"},
        ],
    }))
    pd.DataFrame({
        "feature": ["subscore_upcoding", "provider_on_leie", "a__peerpct"],
        "kind": ["subscore", "LEAKAGE", "peerpct"],
        "coverage": [0.8, 1.0, 0.6],
        "n_covered_positives": [100, 1943, 80],
        "auc_covered": [0.72, 0.99, 0.65],
        "top_decile_lift_covered": [2.1, 9.9, 1.8],
    }).to_csv(tmp / "signal_ranking.csv", index=False)
    (tmp / "NETWORK_AB_REPORT.md").write_text(
        "# NETWORK A/B REPORT\n- **VERDICT: KEEP (size-independent): the network "
        "family lifts discrimination.**\n## MATCHED\n| metric | with | without | delta |\n"
        "|---|---|---|---|\n| ROC-AUC | 0.81 | 0.74 | +0.07 [+0.03, +0.11] |\n")


def test_full_digest(tmp_path):
    _write_run(tmp_path)
    d = build_digest(tmp_path)
    assert "617,062 providers" in d
    assert "1 warning" in d or "1 warning(s)" in d
    assert "1 used, 1 skipped" in d
    assert "opioid" in d                      # skipped source named
    assert "subscore_upcoding" in d           # top honest signal
    assert "provider_on_leie" in d            # leakage listed apart
    assert "KEEP" in d                        # network verdict surfaced
    assert "ROC-AUC" in d


def test_missing_inputs_graceful(tmp_path):
    d = build_digest(tmp_path)               # empty dir
    assert "no feature_manifest.json found" in d
    assert "no signal_ranking.csv found" in d
    assert "no NETWORK_AB_REPORT.md found" in d


def test_verdict_parser():
    v, m = _network_verdict("blah **VERDICT: SIZE ARTIFACT: dropped.**\n## MATCHED\n"
                            "| ROC-AUC | 0.7 | 0.7 | +0.00 [-0.02, 0.02] |\n")
    assert v.startswith("SIZE ARTIFACT")
    assert "ROC-AUC" in m
