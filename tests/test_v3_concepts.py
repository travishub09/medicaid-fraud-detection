"""
test_v3_concepts.py — the v3 concept-scoring core (the last untested surface).

``score_concepts`` is THE anomaly methodology — reused verbatim at provider and
company grain (refine_layer2_v3, company_lead_tracker, backtest). These tests
pin its load-bearing behaviors on a deterministic 40-provider peer cell:
the volume gate, peer fallback, concept de-correlation (one fact counts once),
the >=2-signals lead bar, and the raw-dollars-never-primary context rule.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.attempt_2.leads.refine_layer2_v3 import (
    score_concepts, ALL_FEATS, CONTEXT_FEAT, CONCEPTS,
    SVC_MIN, LINES_MIN, MIN_CONCEPT_SIGNALS,
)

N_BASE = 38   # baseline providers in one taxonomy×entity cell (>= MIN_PEER=30)


def _universe() -> pd.DataFrame:
    """One peer cell + planted rows.

    Row roles (by provider_id):
      base00..base37  spread of mild values (linspace) — the peer mass
      OUTLIER         max on BOTH concentration feats + BOTH payment feats
                      → exactly 2 concept signals (not 4) → a lead
      CONTEXT_ONLY    max ONLY on log_max_single_month (raw-dollar context)
                      → 0 concept signals → never a lead
      LOW_VOLUME      extreme values but below the volume gate → not scored
      NO_TAX          missing taxonomy → not scored
      ZERO_PAID       gross_paid == 0 → not scored
    """
    rows = []
    spread = np.linspace(0.10, 0.50, N_BASE)
    # de-correlate the baseline: each feature gets a differently-rotated spread,
    # so no single baseline row is the peer maximum on more than one concept
    # (a comonotonic baseline would fabricate a multi-concept "lead" by accident)
    rolled = {f: np.roll(spread, k * 5) for k, f in enumerate(ALL_FEATS)}
    for i in range(N_BASE):
        rows.append({
            "provider_id": f"base{i:02d}", "primary_taxonomy": "207Q00000X",
            "entity_type": "1", "gross_paid": 100_000.0,
            "service_volume": 500.0, "total_claim_lines": 1_000.0,
            "top_hcpcs_paid_share": rolled["top_hcpcs_paid_share"][i],
            "hcpcs_hhi": rolled["hcpcs_hhi"][i],
            "paid_per_patient_instance": 50 + 100 * rolled["paid_per_patient_instance"][i],
            "paid_per_claim_line": 20 + 50 * rolled["paid_per_claim_line"][i],
            "lines_per_patient_instance": 1 + 2 * rolled["lines_per_patient_instance"][i],
            "rare_share_te": rolled["rare_share_te"][i] / 10,
            "yoy_growth_net_paid": rolled["yoy_growth_net_paid"][i],
            "month_to_month_volatility": rolled["month_to_month_volatility"][i],
            "log_max_single_month": 8 + spread[i],
        })
    rows.append({  # the planted lead: extreme on concentration + payment_intensity
        "provider_id": "OUTLIER", "primary_taxonomy": "207Q00000X",
        "entity_type": "1", "gross_paid": 5_000_000.0,
        "service_volume": 800.0, "total_claim_lines": 2_000.0,
        "top_hcpcs_paid_share": 0.99, "hcpcs_hhi": 0.98,          # concentration ×2
        "paid_per_patient_instance": 5_000.0, "paid_per_claim_line": 2_000.0,  # payment ×2
        "lines_per_patient_instance": 1.5,                         # mid
        "rare_share_te": 0.02, "yoy_growth_net_paid": 0.3,
        "month_to_month_volatility": 0.3, "log_max_single_month": 8.3,
    })
    rows.append({  # huge single-month dollars, ordinary everything else
        "provider_id": "CONTEXT_ONLY", "primary_taxonomy": "207Q00000X",
        "entity_type": "1", "gross_paid": 9_000_000.0,
        "service_volume": 700.0, "total_claim_lines": 1_500.0,
        "top_hcpcs_paid_share": 0.30, "hcpcs_hhi": 0.30,
        "paid_per_patient_instance": 80.0, "paid_per_claim_line": 35.0,
        "lines_per_patient_instance": 1.6,
        "rare_share_te": 0.03, "yoy_growth_net_paid": 0.3,
        "month_to_month_volatility": 0.3, "log_max_single_month": 15.0,   # the max
    })
    rows.append({  # extreme ratios but unreliable volume → gated
        "provider_id": "LOW_VOLUME", "primary_taxonomy": "207Q00000X",
        "entity_type": "1", "gross_paid": 50_000.0,
        "service_volume": SVC_MIN - 1, "total_claim_lines": LINES_MIN - 1,
        "top_hcpcs_paid_share": 1.0, "hcpcs_hhi": 1.0,
        "paid_per_patient_instance": 9_999.0, "paid_per_claim_line": 9_999.0,
        "lines_per_patient_instance": 50.0,
        "rare_share_te": 0.5, "yoy_growth_net_paid": 5.0,
        "month_to_month_volatility": 5.0, "log_max_single_month": 12.0,
    })
    rows.append({"provider_id": "NO_TAX", "primary_taxonomy": "",
                 "entity_type": "1", "gross_paid": 100_000.0,
                 "service_volume": 500.0, "total_claim_lines": 1_000.0,
                 **{f: 0.3 for f in ALL_FEATS}, CONTEXT_FEAT: 8.3})
    rows.append({"provider_id": "ZERO_PAID", "primary_taxonomy": "207Q00000X",
                 "entity_type": "1", "gross_paid": 0.0,
                 "service_volume": 500.0, "total_claim_lines": 1_000.0,
                 **{f: 0.3 for f in ALL_FEATS}, CONTEXT_FEAT: 8.3})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def scored() -> pd.DataFrame:
    return score_concepts(_universe()).set_index("provider_id")


def test_volume_gate_blocks_unreliable_ratios(scored):
    lv = scored.loc["LOW_VOLUME"]
    assert lv["not_scored"]
    assert lv["not_scored_reason"] == "low_volume_unreliable"
    assert not lv["anomaly_lead_v3"]            # extreme values, but never a lead


def test_not_scored_reasons(scored):
    assert scored.loc["NO_TAX", "not_scored_reason"] == "missing_taxonomy"
    assert scored.loc["ZERO_PAID", "not_scored_reason"] == "degenerate_zero_gross_paid"
    assert scored.loc["base00", "not_scored_reason"] == ""    # scorable


def test_concept_decorrelation_one_fact_counts_once(scored):
    """OUTLIER is the max on FOUR features — but they collapse into TWO
    concepts (concentration, payment_intensity). One fact never double-counts."""
    o = scored.loc["OUTLIER"]
    assert o["n_concept_signals"] == 2
    contrib = " ".join(o["anomaly_contributing_concepts"])
    assert "concentration" in contrib and "payment_intensity" in contrib
    assert "service_intensity" not in contrib


def test_lead_bar_requires_two_independent_signals(scored):
    assert scored.loc["OUTLIER", "anomaly_lead_v3"]           # 2 signals → lead
    assert int(scored.loc["OUTLIER", "n_concept_signals"]) >= MIN_CONCEPT_SIGNALS
    base = scored.loc[[f"base{i:02d}" for i in range(N_BASE)]]
    assert not base["anomaly_lead_v3"].any()                  # the peer mass: no leads


def test_raw_dollars_never_primary(scored):
    """CONTEXT_ONLY has the largest single-month dollars in the cell — and the
    rule is that raw dollar magnitude is context, never a driver: zero concept
    signals, not a lead, but the context feature IS surfaced for the reader."""
    c = scored.loc["CONTEXT_ONLY"]
    assert c["n_concept_signals"] == 0
    assert not c["anomaly_lead_v3"]
    assert any(CONTEXT_FEAT in s for s in c["anomaly_contributing_concepts"])


def test_outlier_is_the_unique_lead(scored):
    """The lead bar is the P99 signal COUNT, not the mean score (the mean is a
    ranking aid within the lead set). The planted outlier must be the only row
    in the cell clearing the bar, and must outrank the dollar-context row."""
    scorable = scored[~scored["not_scored"]]
    leads = scorable[scorable["anomaly_lead_v3"]]
    assert list(leads.index) == ["OUTLIER"]
    assert (scorable.drop("OUTLIER")["n_concept_signals"] < MIN_CONCEPT_SIGNALS).all()
    assert scored.loc["OUTLIER", "anomaly_score_v3"] > \
           scored.loc["CONTEXT_ONLY", "anomaly_score_v3"]


def test_peer_fallback_taxonomy_only_and_too_small():
    """A 5-provider entity cell inside a >=30 taxonomy falls back to the
    taxonomy-only baseline; a tiny isolated taxonomy is not scored at all."""
    df = _universe()
    # 5 organizations (entity_type 2) inside the same big taxonomy
    for i in range(5):
        row = df.iloc[0].copy()
        row["provider_id"] = f"org{i}"; row["entity_type"] = "2"
        df.loc[len(df)] = row
    # 3 providers in their own tiny taxonomy
    for i in range(3):
        row = df.iloc[0].copy()
        row["provider_id"] = f"tiny{i}"; row["primary_taxonomy"] = "999X00000X"
        df.loc[len(df)] = row
    out = score_concepts(df).set_index("provider_id")
    assert out.loc["org0", "peer_basis"] == "taxonomy_only"
    assert not out.loc["org0", "not_scored"]
    assert out.loc["tiny0", "not_scored"]
    assert out.loc["tiny0", "not_scored_reason"] == "peer_group_too_small(<30)"
