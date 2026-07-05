"""
test_sector_schemes_and_fca.py — §I4/I5/I3 sector schemes + the State-FCA overlay.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.model_a.state_fca import has_state_fca, case_value_multiplier


def _fact(tmp_path):
    df = pd.DataFrame({
        "billing_npi": ["1003000126"] * 3 + ["1003000134"] * 2,
        "hcpcs_code": ["A0425", "T2003", "99213",      # transport-heavy provider
                       "H0004", "90837"],              # behavioral-health provider
        "service_month": ["2024-01", "2024-01", "2024-02", "2024-01", "2024-01"],
        "total_paid": [6000.0, 2000.0, 2000.0, 3000.0, 1000.0],
        "total_claim_lines": [600.0, 100.0, 20.0, 90.0, 10.0],
        "total_patients": [10.0, 10.0, 10.0, 30.0, 5.0],
    })
    p = tmp_path / "spending_fact.parquet"
    df.to_parquet(p, index=False)
    return p


def test_nemt_and_bh_metrics(tmp_path):
    from src.ingest_cms.sector_schemes import sector_metrics_from_parquet
    out = sector_metrics_from_parquet(_fact(tmp_path)).set_index("npi")
    r = out.loc["1003000126"]
    assert r["nemt_paid_share"] == pytest.approx(8000.0 / 10000.0)
    assert r["nemt_lines_per_patient"] == pytest.approx(700.0 / 20.0)  # padding tell
    b = out.loc["1003000134"]
    assert b["bh_paid_share"] == pytest.approx(1.0)
    assert pd.isna(b.get("nemt_paid_share"))          # absent, never zero


def test_impossible_day_lights_up_with_minutes_map(tmp_path):
    from src.ingest_cms.sector_schemes import sector_metrics_from_parquet
    fact = _fact(tmp_path)
    mins = tmp_path / "hcpcs_minutes.csv"
    mins.write_text("hcpcs,minutes\n90837,60\nH0004,45\n")
    out = sector_metrics_from_parquet(fact, mins).set_index("npi")
    # BH provider: 90×45 + 10×60 = 4650 minutes in 2024-01 → /22 working days
    assert out.loc["1003000134", "time_minutes_per_day"] == pytest.approx(4650 / 22)
    assert pd.isna(out.loc["1003000126", "time_minutes_per_day"])


def test_new_schemes_registered_end_to_end():
    from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS, compute_subscores
    from src.model_a.sector_priors import SCHEME_EXPOSURE_BASIS
    assert "nemt_fraud" in DEFAULT_SCHEME_WEIGHTS
    assert SCHEME_EXPOSURE_BASIS["behavioral_health"] == "own_billing"
    feats = pd.DataFrame({"nemt_paid_share": [0.99, None],
                          "nemt_lines_per_patient": [0.98, None]})
    subs, cov = compute_subscores(feats)
    assert subs["subscore_nemt_fraud"].iloc[0] > 0.5
    assert pd.isna(subs["subscore_nemt_fraud"].iloc[1])   # NULL-aware


def test_state_fca_overlay():
    assert has_state_fca("TX") and has_state_fca("California")
    assert has_state_fca("OH") is False                    # the sharp fact
    assert has_state_fca("OREGON") is False                # FCA but no qui tam
    assert has_state_fca("PUERTO RICO") is None
    mult = case_value_multiplier(pd.Series(["TX", "OH", "??"]))
    assert list(mult) == [1.15, 1.0, 1.0]
