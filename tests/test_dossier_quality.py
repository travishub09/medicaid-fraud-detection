"""
test_dossier_quality.py — the dossier-quality sprint (expansion plan A1/A2/A4).

A1 scheme-scoped damages: exposure from the suspect code family, not total
   billing, with the scope labeled.
A2 confidence bands: graded with named reasons; low-confidence merges and thin
   histories downgrade; never alters the score.
A4 growth shock: a mid-series step fires the level-shift signal where flat and
   noisy-but-trendless series do not; new-code bursts detected; short histories
   stay NaN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analytics.confidence import confidence_band
from src.analytics.growth import growth_features, growth_percentiles
from src.model_a.exposure import scoped_payments_per_org
from src.model_a.scoring import expected_recoverable_value
from src.model_a.dossier import render_dossier
from src.entity_graph.__main__ import run as run_graph
from src.model_a.__main__ import run as run_model_a
from tests.fixtures.synthetic import build_synthetic_inputs, build_company_features

XW = pd.DataFrame({"npi": ["1003000415", "1003000407"],
                   "org_node_id": ["org:pc", "org:mix"]})


def _spending() -> pd.DataFrame:
    """org:pc bills personal care (T1019) + office visits; org:mix is the
    diversified control."""
    rows = []
    for year in ("2023", "2024"):
        rows += [
            {"billing_npi": "1003000415", "service_month": f"{year}-03",
             "hcpcs_code": "T1019", "total_paid": 800_000.0},
            {"billing_npi": "1003000415", "service_month": f"{year}-06",
             "hcpcs_code": "99213", "total_paid": 200_000.0},
            {"billing_npi": "1003000407", "service_month": f"{year}-03",
             "hcpcs_code": "E0601", "total_paid": 300_000.0},
            {"billing_npi": "1003000407", "service_month": f"{year}-09",
             "hcpcs_code": "J1234", "total_paid": 100_000.0},
        ]
    return pd.DataFrame(rows)


# ------------------------------------------------------------------- A1 ---

def test_scoped_payments_per_family():
    scoped = scoped_payments_per_org(_spending(), XW).set_index("org_node_id")
    # personal-care family for org:pc = the T1019 dollars only (mean annual)
    assert scoped.loc["org:pc", "scoped__evv_personal_care"] == 800_000.0
    assert scoped.loc["org:pc", "scoped__upcoding"] == 200_000.0
    # DME prefix family catches the E-code; drug family the J-code
    assert scoped.loc["org:mix", "scoped__dme_ring"] == 300_000.0
    assert scoped.loc["org:mix", "scoped__drug_outlier"] == 100_000.0
    # top-code mode: the mill family = the org's dominant code dollars
    assert scoped.loc["org:pc", "scoped__single_service_mill"] == 800_000.0
    # scoped never exceeds the org's own total (asserted internally too)
    totals = {"org:pc": 1_000_000.0, "org:mix": 400_000.0}
    for org, total in totals.items():
        assert scoped.loc[org].filter(like="scoped__").max() <= total + 0.01


def test_erv_uses_scoped_exposure_with_label():
    subs = pd.DataFrame({"subscore_evv_personal_care": [0.9, 0.1],
                         "subscore_payment_outlier": [0.2, 0.8]},
                        index=[0, 1])
    payments = pd.Series([1_000_000.0, 400_000.0], index=[0, 1])
    scoped = pd.DataFrame({"scoped__evv_personal_care": [800_000.0, 0.0]},
                          index=[0, 1])
    r = expected_recoverable_value(subs, payments, pd.Series([1.0, 1.0]),
                                   pd.Series([0.0, 0.0]),
                                   scoped_payments=scoped)
    # org 0: EVV hypothesis → scoped basis + label
    assert r.loc[0, "exposure_scope"] == "scheme_code_family"
    assert r.loc[0, "payments_at_issue"] == 800_000.0
    # org 1: payment_outlier has no family → falls back to all payments
    assert r.loc[1, "exposure_scope"] == "all_payments"
    assert r.loc[1, "payments_at_issue"] == 400_000.0


# ------------------------------------------------------------------- A2 ---

def test_confidence_bands_and_reasons():
    df = pd.DataFrame({
        "merge_confidence": ["high", "low", "medium", "high"],
        "years_observed": [3, 3, 1, np.nan],
        "subscore_a": [0.5] * 4, "subscore_b": [0.5] * 4,
        "subscore_c": [0.5] * 4,
    })
    c = confidence_band(df)
    assert c.loc[0, "confidence"] == "high"
    assert c.loc[0, "confidence_reasons"] == "all checks passed"
    assert c.loc[1, "confidence"] == "low"
    assert "entity merge low-confidence" in c.loc[1, "confidence_reasons"]
    assert c.loc[2, "confidence"] == "medium"          # name merge + short history
    assert "under 2 years" in c.loc[2, "confidence_reasons"]
    assert c.loc[3, "confidence"] == "medium"          # missing history


def test_confidence_peer_inputs():
    df = pd.DataFrame({"peer_basis": ["taxonomy_code×entity_type×state",
                                      "taxonomy_code", "x"],
                       "peer_n": [120, 80, 12]})
    c = confidence_band(df)
    assert c.loc[0, "confidence"] == "high"
    assert c.loc[1, "confidence"] == "medium"          # coarse fallback
    assert c.loc[2, "confidence"] == "low"             # <30 peers


# ------------------------------------------------------------------- A4 ---

def _monthly_spending(org_npi: str, paids: list[float],
                      codes: list[str] | None = None) -> pd.DataFrame:
    months = pd.period_range("2023-01", periods=len(paids), freq="M")
    codes = codes or ["99213"] * len(paids)
    return pd.DataFrame({"billing_npi": org_npi,
                         "service_month": [str(m) for m in months],
                         "hcpcs_code": codes, "total_paid": paids})


def test_level_shift_fires_on_step_not_noise():
    flat = _monthly_spending("1003000415", [100.0 + (i % 3) for i in range(16)])
    step = _monthly_spending("1003000407",
                             [100.0 + (i % 3) for i in range(8)]
                             + [1000.0 + (i % 3) for i in range(8)])
    xw = pd.DataFrame({"npi": ["1003000415", "1003000407"],
                       "org_node_id": ["org:flat", "org:step"]})
    g = growth_features(pd.concat([flat, step]), xw).set_index("org_node_id")
    assert g.loc["org:step", "growth_level_shift"] > \
           g.loc["org:flat", "growth_level_shift"] * 5
    pct = growth_percentiles(g.reset_index()).set_index("org_node_id")
    assert pct.loc["org:step", "growth_level_shift"] == 1.0


def test_new_code_burst_and_short_history():
    stable = _monthly_spending("1003000415", [100.0] * 12,
                               ["A1", "A2", "A3", "A4"] * 3)
    pivot = _monthly_spending("1003000407", [100.0] * 12,
                              ["B1"] * 8 + ["NEW1", "NEW2", "NEW3", "NEW4"])
    short = _monthly_spending("1003000506", [100.0] * 4)   # under MIN_MONTHS
    xw = pd.DataFrame({"npi": ["1003000415", "1003000407", "1003000506"],
                       "org_node_id": ["org:stable", "org:pivot", "org:short"]})
    g = growth_features(pd.concat([stable, pivot, short]), xw) \
        .set_index("org_node_id")
    assert g.loc["org:pivot", "new_code_burst"] == pytest.approx(4 / 5)
    assert g.loc["org:stable", "new_code_burst"] == 0.0
    assert np.isnan(g.loc["org:short", "growth_level_shift"])   # never forced
    assert np.isnan(g.loc["org:short", "new_code_burst"])


# ----------------------------------------------------------- integration ---

def test_pipeline_carries_confidence_and_scope(tmp_path):
    g = run_graph(build_synthetic_inputs(), tmp_path / "graph")
    org_nodes = g["nodes/org_nodes"]
    res = run_model_a(org_nodes, g["org_graph_features"],
                      build_company_features(org_nodes),
                      g["rings/shared_address_shells"],
                      g["rings/common_owner_clusters"],
                      tmp_path / "ma", top_k_dossiers=1)
    assert {"confidence", "confidence_reasons",
            "exposure_scope", "payments_at_issue"} <= set(res.columns)
    assert res.iloc[0]["org_node_id"].startswith("org:")        # ranking intact
    top = sorted((tmp_path / "ma" / "dossiers").glob("*.md"))[0].read_text(encoding="utf-8")
    assert "Confidence:" in top
    assert "scope: all payments" in top or "code family" in top


def test_dossier_renders_scoped_exposure_line():
    row = pd.Series({"org_node_id": "org:x", "org_name": "X HOME CARE",
                     "scheme_hypothesis": "evv_personal_care",
                     "top_subscore": 0.9, "org_prob": 0.9,
                     "adjusted_prob": 0.9, "sector_prior": 1.5,
                     "graph_risk_boost": 0.0, "payments": 1_000_000.0,
                     "payments_at_issue": 800_000.0,
                     "exposure_scope": "scheme_code_family",
                     "exposure": 400_000.0, "erv": 360_000.0,
                     "scheme_recovery_multiplier": 0.5,
                     "confidence": "medium",
                     "confidence_reasons": "entity merge is name-based"})
    txt = render_dossier(row, [], {})
    assert "Payments at issue" in txt and "$800,000" in txt and "$1,000,000" in txt
    assert "Confidence: MEDIUM" in txt
    assert "entity merge is name-based" in txt
