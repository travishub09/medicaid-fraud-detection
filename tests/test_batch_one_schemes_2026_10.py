"""
test_batch_one_schemes_2026_10.py — batch one of the deep dive's unwired schemes
(docs/SCHEME_DEEP_DIVE_2026-10-22 section 5): evv_personal_care, home_health_intensity,
lab_unbundling, session_inflation — metrics from the spending fact, NULL where the
family is absent, registered everywhere a scheme must be.
"""

from __future__ import annotations

import pandas as pd
import pytest


def _fact(tmp_path):
    rows = [
        # a personal-care agency: 2 patients x 2 months on T1019, 400 lines a month, $8k a month
        ("1003000126", "T1019", "2024-01", 8000.0, 400.0, 2.0),
        ("1003000126", "T1019", "2024-02", 8000.0, 400.0, 2.0),
        ("1003000126", "99213", "2024-01", 500.0, 5.0, 5.0),
        # a home-health agency: G0299 + aide visits
        ("1003000134", "G0299", "2024-01", 3000.0, 30.0, 3.0),
        ("1003000134", "G0156", "2024-01", 1000.0, 20.0, 2.0),
        ("1003000134", "99213", "2024-01", 1000.0, 10.0, 10.0),
        # a lab that unbundles: CMP components separately, panel rarely, a draw per test
        ("1003000142", "80053", "2024-01", 200.0, 10.0, 10.0),
        ("1003000142", "82947", "2024-01", 300.0, 30.0, 10.0),
        ("1003000142", "82565", "2024-01", 300.0, 30.0, 10.0),
        ("1003000142", "36415", "2024-01", 100.0, 35.0, 10.0),
        # a therapist: mostly 60-minute sessions, prolonged add-ons on E/M, interactive add-ons
        ("1003000159", "90837", "2024-01", 3000.0, 30.0, 15.0),
        ("1003000159", "90834", "2024-01", 800.0, 10.0, 8.0),
        ("1003000159", "90785", "2024-01", 100.0, 8.0, 8.0),
        ("1003000159", "99214", "2024-01", 1000.0, 10.0, 10.0),
        ("1003000159", "99417", "2024-01", 300.0, 6.0, 6.0),
        # a provider with none of the families
        ("1003000167", "99213", "2024-01", 900.0, 9.0, 9.0),
    ]
    df = pd.DataFrame(rows, columns=["billing_npi", "hcpcs_code", "service_month",
                                     "total_paid", "total_claim_lines", "total_patients"])
    p = tmp_path / "spending_fact.parquet"
    df.to_parquet(p, index=False)
    return p


def test_batch_one_metrics(tmp_path):
    from src.ingest_cms.sector_schemes import BATCH_ONE_METRICS, sector_metrics_from_parquet
    out = sector_metrics_from_parquet(_fact(tmp_path)).set_index("npi")
    assert set(BATCH_ONE_METRICS) <= set(out.columns)
    pc = out.loc["1003000126"]
    assert pc["pc_paid_share"] == pytest.approx(16000.0 / 16500.0)
    assert pc["pc_lines_per_patient_month"] == pytest.approx(800.0 / 4.0)      # 200 units per patient-month
    assert pc["pc_paid_per_patient_month"] == pytest.approx(16000.0 / 4.0)
    assert pd.isna(pc["hh_paid_share"]) and pd.isna(pc["psy_60min_share"])      # absent, never zero
    hh = out.loc["1003000134"]
    assert hh["hh_paid_share"] == pytest.approx(4000.0 / 5000.0)
    assert hh["hh_lines_per_patient_month"] == pytest.approx(50.0 / 5.0)
    lab = out.loc["1003000142"]
    assert lab["lab_component_share"] == pytest.approx(600.0 / 800.0)
    assert lab["lab_draw_per_test"] == pytest.approx(35.0 / (10 + 30 + 30))   # draws per test line (draws excluded)
    psy = out.loc["1003000159"]
    assert psy["psy_60min_share"] == pytest.approx(30.0 / 40.0)
    assert psy["psy_interactive_share"] == pytest.approx(8.0 / 40.0)
    assert psy["em_prolonged_share"] == pytest.approx(6.0 / 10.0)
    # an E/M-only provider: no prolonged add-ons is a REAL observation (0 on that
    # share), every other family NULL
    plain = out.loc["1003000167"]
    assert plain["em_prolonged_share"] == 0.0
    assert pd.isna(plain["pc_paid_share"]) and pd.isna(plain["hh_paid_share"]) \
        and pd.isna(plain["lab_component_share"]) and pd.isna(plain["psy_60min_share"])


def test_batch_one_registered_end_to_end():
    from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS, compute_subscores
    from src.model_a.scheme_code_families import family_for
    from src.model_a.sector_priors import SCHEME_EXPOSURE_BASIS, SCHEME_RECOVERY_MULTIPLIER
    from src.model_a.dossier import SCHEME_NARRATIVES
    from src.model_a.case_validation import SECTOR_TO_SUBSCHEME, DOJ_TO_SUBSCHEME
    from src.model_a.provider_features_export import ADAPTER_FEATURE_COLS
    from src.ingest_cms.sector_schemes import BATCH_ONE_METRICS
    for s in ("evv_personal_care", "home_health_intensity", "lab_unbundling", "session_inflation"):
        assert s in DEFAULT_SCHEME_WEIGHTS and s in SCHEME_NARRATIVES
        assert SCHEME_EXPOSURE_BASIS[s] == "own_billing" and 0 < SCHEME_RECOVERY_MULTIPLIER[s] <= 1
        assert family_for(s)[0] == "codes" and len(family_for(s)[1]) > 0
    assert "T1019" in family_for("evv_personal_care")[1]
    assert "G0299" in family_for("home_health_intensity")[1]
    assert {"80053", "82947", "36415"} <= set(family_for("lab_unbundling")[1])
    assert {"90837", "99417", "90785"} <= set(family_for("session_inflation")[1])
    assert "evv_personal_care" in SECTOR_TO_SUBSCHEME["personal_care"]
    assert "home_health_intensity" in SECTOR_TO_SUBSCHEME["home_health"]
    assert "lab_unbundling" in SECTOR_TO_SUBSCHEME["lab"]
    assert "session_inflation" in SECTOR_TO_SUBSCHEME["behavioral"]
    assert "evv_personal_care" in DOJ_TO_SUBSCHEME["phantom_billing"]
    for c in BATCH_ONE_METRICS:
        assert c in ADAPTER_FEATURE_COLS                  # peer-normalized by the export
    feats = pd.DataFrame({"pc_paid_share": [0.95, None], "pc_lines_per_patient_month": [0.99, None],
                          "pc_paid_per_patient_month": [0.97, None]})
    subs, cov = compute_subscores(feats)
    assert subs["subscore_evv_personal_care"].iloc[0] > 0.5
    assert pd.isna(subs["subscore_evv_personal_care"].iloc[1])
    assert cov["evv_personal_care"] == sorted(feats.columns)
