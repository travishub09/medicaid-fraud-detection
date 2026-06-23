"""test_dossier_evidence.py — per-org billing evidence + data-grounded narrative."""

from __future__ import annotations

import pandas as pd

from src.model_a.dossier_evidence import gather_evidence
from src.model_a.dossier import render_dossier


def test_gather_evidence_codes_concentration_ramp(tmp_path):
    spend = pd.DataFrame({
        "billing_npi":   ["1", "1", "1", "1", "2"],
        "hcpcs_code":    ["A", "A", "B", "A", "C"],
        "service_month": ["2022-01", "2022-02", "2022-02", "2023-06", "2022-01"],
        "total_paid":    [100.0, 50.0, 30.0, 400.0, 9.0],
        "total_patients":[10.0, 5.0, 3.0, 20.0, 1.0],
    })
    p = tmp_path / "spending_fact.parquet"; spend.to_parquet(p, index=False)
    ev = gather_evidence(str(p), pd.DataFrame({"npi": ["1"], "org_node_id": ["org:A"]}))["org:A"]
    assert ev["total_paid"] == 580.0                 # 100+50+30+400 (npi 1 only)
    assert ev["n_codes"] == 2                         # A, B
    assert ev["top_codes"][0][0] == "A"               # A biggest (550)
    assert abs(ev["top_codes"][0][2] - 550.0/580.0) < 1e-6   # share
    assert ev["first_month"] == "2022-01" and ev["last_month"] == "2023-06"
    assert ev["ramp"]["peak_month"] == "2023-06"


def test_dossier_renders_evidence_story():
    ev = {"provenance": "Medicaid spending file", "total_paid": 580.0,
          "n_patients": 38, "n_codes": 2, "n_months": 3,
          "first_month": "2022-01", "last_month": "2023-06", "paid_per_patient": 15.26,
          "top_codes": [("A", 550.0, 0.948), ("B", 30.0, 0.052)],
          "ramp": {"peak_month": "2023-06", "peak_paid": 400.0,
                   "first_month_paid": 100.0, "last_month_paid": 400.0}}
    row = pd.Series({"org_node_id": "org:A", "org_name": "ACME HEALTH",
                     "scheme_hypothesis": "single_service_mill", "payments": 580.0})
    md = render_dossier(row, [], {}, evidence=ev)
    assert "What the data shows" in md
    assert "$580" in md and "procedure code **A**" in md   # concrete code + dollars
    assert "95%" in md                                       # 0.948 share → 95%
    assert "drawn from the Medicaid spending file" in md     # provenance
    assert "investigative hypothesis" in md                  # disclaimer kept
