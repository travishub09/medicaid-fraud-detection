"""
test_medicare_phase2.py — Medicare fact + multi-year growth (Run 2 §B).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ingest_cms.medicare_fact import (
    partb_year_fact, partd_year_fact, provider_stats)
from src.ingest_cms.medicare_growth import growth_features

VALID_NPI = "1003000126"        # Luhn-valid test NPI used across the suite
VALID_NPI2 = "1003000134"


def test_partb_fact_conserves_dollars_and_grain():
    raw = pd.DataFrame({
        "Rndrng_NPI": [VALID_NPI, VALID_NPI, VALID_NPI2],
        "HCPCS_Cd": ["99213", "99213", "E0601"],   # dup code rows collapse
        "Tot_Srvcs": [10.0, 5.0, 2.0],
        "Avg_Mdcr_Alowd_Amt": [100.0, 100.0, 500.0],
    })
    fact, quarantined = partb_year_fact(raw, 2023)
    assert quarantined == 0
    assert set(fact["source"]) == {"partb"} and set(fact["year"]) == {2023}
    f = fact.set_index(["npi", "code"])
    assert f.loc[(VALID_NPI, "99213"), "dollars"] == 1500.0   # (10+5)×100
    assert float(fact["dollars"].sum()) == 2500.0             # conserved


def test_partd_fact_uses_generic_name_and_conserves():
    raw = pd.DataFrame({
        "Prscrbr_NPI": [VALID_NPI, VALID_NPI],
        "Brnd_Name": ["XARELTO", "OZEMPIC"],
        "Gnrc_Name": ["RIVAROXABAN", ""],          # second falls back to brand
        "Tot_Clms": [10, 20],
        "Tot_Drug_Cst": [1000.0, 4000.0],
    })
    fact, _ = partd_year_fact(raw, 2022)
    codes = set(fact["code"])
    assert "RIVAROXABAN" in codes and "OZEMPIC" in codes
    assert float(fact["dollars"].sum()) == 5000.0


def _multi_year_fact() -> pd.DataFrame:
    """RAMP: 100 → 200 → 800 dollars over 3 years with a code pivot in the
    latest year. STEADY: flat. NEWBIE: latest year only."""
    rows = []
    for year, dollars, code in [(2021, 100.0, "A1"), (2022, 200.0, "A1"),
                                (2023, 800.0, "Z9")]:
        rows.append({"npi": "RAMP", "code": code, "year": year,
                     "source": "partb", "units": 1.0, "dollars": dollars})
    for year in (2021, 2022, 2023):
        rows.append({"npi": "STEADY", "code": "B2", "year": year,
                     "source": "partb", "units": 1.0, "dollars": 500.0})
    rows.append({"npi": "NEWBIE", "code": "C3", "year": 2023,
                 "source": "partb", "units": 1.0, "dollars": 900.0})
    return pd.DataFrame(rows)


def test_growth_features_ramp_vs_steady_vs_newbie():
    g = growth_features(_multi_year_fact()).set_index("npi")
    assert g.loc["RAMP", "mc_yoy_growth"] == pytest.approx(3.0)       # 200→800
    assert g.loc["RAMP", "mc_max_yoy_growth"] == pytest.approx(3.0)
    assert g.loc["RAMP", "mc_growth_cagr"] == pytest.approx(8.0 ** 0.5 - 1)
    assert g.loc["RAMP", "mc_new_code_share"] == 1.0                  # pivoted to Z9
    assert g.loc["STEADY", "mc_yoy_growth"] == pytest.approx(0.0)
    assert g.loc["STEADY", "mc_new_code_share"] == 0.0
    # the pre-window lesson: a latest-year-only provider is UNSCORED, not "all new"
    assert np.isnan(g.loc["NEWBIE", "mc_yoy_growth"])
    assert np.isnan(g.loc["NEWBIE", "mc_new_code_share"])
    assert g.loc["NEWBIE", "mc_entered_recently"] == 1
    assert g.loc["RAMP", "mc_entered_recently"] == 0


def test_provider_stats_grain():
    stats = provider_stats(_multi_year_fact()).set_index("npi")
    assert stats.loc["RAMP", "years_active"] == 3
    assert stats.loc["RAMP", "n_distinct_codes"] == 2
    assert stats.loc["NEWBIE", "first_year"] == 2023
