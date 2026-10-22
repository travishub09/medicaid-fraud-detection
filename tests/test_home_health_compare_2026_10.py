"""
test_home_health_compare_2026_10.py — the Home Health Care Compare adapter (v22):
tolerant column resolution (exact then keywords), one-sided metrics, quarantine of
non-numeric scores, and the registration (pre-ranked prefix, weights, partition group).
"""

from __future__ import annotations

import pandas as pd
import pytest


def _raw():
    return pd.DataFrame({
        "CMS Certification Number (CCN)": ["017001", "17002", "017003", "017004"],
        "Provider Name": ["A", "B", "C", "D"],
        "State": ["TX", "TX", "FL", "FL"],
        "Quality of patient care star rating": ["4.5", "2", "Not Available", "3"],
        "How often the home health team began their patients' care in a timely manner": ["98", "70", "-", "90"],
        "How often home health patients had to be admitted to the hospital": ["12", "25", "15", ""],
        "How often patients receiving home health care needed urgent, unplanned care in the ER without being admitted": ["10", "20", "11", "9"],
        "DTC Observed Rate": ["80", "60", "75", "70"],
        "DTC Denominator": ["100", "100", "100", "100"],
        "PPR Observed Rate": ["4", "9", "5", "6"],
        "PPR Percentile": ["20", "90", "30", "50"],
    })


def test_resolve_and_metrics():
    from src.ingest_cms.home_health_compare import HHQ_METRICS, compute_hh_quality, resolve_hh_columns
    r = resolve_hh_columns(list(_raw().columns))
    assert r["dtc"] == "DTC Observed Rate" and r["ppr"] == "PPR Observed Rate"     # never the denominator / percentile
    assert r["er"].startswith("How often patients receiving") and r["hospitalization"].endswith("hospital")
    out, q = compute_hh_quality(_raw())
    out = out.set_index("ccn")
    assert set(HHQ_METRICS) <= set(out.columns)
    assert out.loc["017002", "hhq_star_deficit"] == pytest.approx(3.0)          # 5 - 2
    assert out.loc["017001", "hhq_timely_gap"] == pytest.approx(2.0)            # 100 - 98
    assert out.loc["017002", "hhq_hospitalization_rate"] == pytest.approx(25.0)
    assert out.loc["017001", "hhq_dtc_gap"] == pytest.approx(20.0)
    assert out.loc["017002", "hhq_ppr_rate"] == pytest.approx(9.0)
    assert "17002" not in out.index and "017002" in out.index                  # CCN zero-padded to 6
    assert pd.isna(out.loc["017003", "hhq_star_deficit"]) and pd.isna(out.loc["017003", "hhq_timely_gap"])
    assert q == 2                                                              # 'Not Available' and '-' (blank is absent, not quarantined)
    assert out.loc["017003", "state"] == "FL"


def test_keyword_fallback_on_a_renamed_edition():
    from src.ingest_cms.home_health_compare import compute_hh_quality
    raw = pd.DataFrame({"CMS Certification Number": ["017001"], "State": ["TX"],
                        "Quality of Patient Care Star Rating (2026)": ["3.5"],
                        "Percent of episodes begun in a timely manner": ["95"],
                        "HHCAHPS Survey Summary Star Rating": ["4"]})
    out, _ = compute_hh_quality(raw)
    assert out.loc[0, "hhq_star_deficit"] == pytest.approx(1.5)                # the care star, not the survey star
    assert out.loc[0, "hhq_timely_gap"] == pytest.approx(5.0)


def test_requires_ccn_and_at_least_one_measure():
    from src.ingest_cms.home_health_compare import compute_hh_quality
    with pytest.raises(ValueError):
        compute_hh_quality(pd.DataFrame({"Provider Name": ["A"], "DTC Observed Rate": ["1"]}))
    with pytest.raises(ValueError):
        compute_hh_quality(pd.DataFrame({"CMS Certification Number (CCN)": ["017001"], "Provider Name": ["A"]}))


def test_registration():
    from src.model_a.provider_features_export import PRE_RANKED_PREFIXES, _is_pre_ranked, ADAPTER_FEATURE_COLS
    from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS
    from src.model_a.feature_partition import EXTRA_SOURCE_GROUPS
    from src.preflight import CORE, OPTIONAL
    assert "hhq_" in PRE_RANKED_PREFIXES and _is_pre_ranked("hhq_star_deficit")
    w = DEFAULT_SCHEME_WEIGHTS["home_health_intensity"]
    assert {"hhq_star_deficit", "hhq_hospitalization_rate", "hhq_timely_gap", "hhq_ppr_rate"} <= set(w)
    assert "hhq_star_deficit" in ADAPTER_FEATURE_COLS
    assert "home_health_compare" in EXTRA_SOURCE_GROUPS["facility_quality"]
    assert any(s.key == "home_health_compare" for s in CORE + OPTIONAL)
