"""
test_pos_order_referring.py — POS capacity reconciliation + Order & Referring.

POS (doc 15 §2.5): a facility billing far more volume than its beds support is
the "impossible org"; capacity_mismatch percentile-ranks billed-per-bed within
facility peers and feeds worthless_services.
Order & Referring (doc 15 §2.6): DME claims whose referrer isn't eligible to
order DME → ineligible_referral_share, sharpening dme_ring.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ingest_cms import (
    compute_pos_capacity, capacity_billing_mismatch,
    eligible_referrers, ineligible_referral_share)
from src.model_a.scheme_subscores import compute_subscores


# ------------------------------------------------------------------- POS ---

def test_pos_capacity_parsing_and_ccn():
    raw = pd.DataFrame([
        {"PRVDR_NUM": "675001", "BED_CNT": "120", "GNRL_FAC_TYPE_CD": "SNF",
         "STATE_CD": "tx"},
        {"PRVDR_NUM": "12345", "BED_CNT": "60", "GNRL_FAC_TYPE_CD": "SNF",
         "STATE_CD": "tx"},     # 5-digit → zero-padded to 012345
    ])
    cap = compute_pos_capacity(raw).set_index("ccn")
    assert cap.loc["675001", "bed_count"] == 120
    assert "012345" in cap.index
    assert cap.loc["675001", "state"] == "TX"


def test_capacity_mismatch_flags_impossible_volume():
    rng = np.random.default_rng(3)
    n = 40
    cap = pd.DataFrame({
        "ccn": [f"67{i:04d}" for i in range(n)],
        "bed_count": rng.integers(80, 120, n),
        "facility_type": "SNF",
        "state": ["TX"] * (n // 2) + ["FL"] * (n // 2),
    })
    # billed volume roughly proportional to beds, EXCEPT ccn 670000 bills 10x
    vol = pd.DataFrame({
        "ccn": cap["ccn"],
        "billed_volume": cap["bed_count"] * rng.uniform(90, 110, n),
    })
    vol.loc[0, "billed_volume"] = cap.loc[0, "bed_count"] * 1000   # impossible
    out = capacity_billing_mismatch(cap, vol, min_peer=5).set_index("ccn")
    assert out.loc["670000", "capacity_mismatch"] >= 0.9          # top of its peers
    assert out.loc["670000", "billed_per_bed"] > out["billed_per_bed"].median()


# -------------------------------------------------------- order & referring ---

def test_eligible_referrers_flags():
    raw = pd.DataFrame([
        {"NPI": "1003000415", "PARTB": "Y", "DME": "Y", "HHA": "N", "PMD": "N"},
        {"NPI": "1003000407", "PARTB": "Y", "DME": "N", "HHA": "Y", "PMD": "N"},
        {"NPI": "12345", "PARTB": "Y", "DME": "Y", "HHA": "Y", "PMD": "Y"},  # bad NPI
    ])
    elig, quarantined = eligible_referrers(raw)
    assert quarantined == 1
    g = elig.set_index("npi")
    assert g.loc["1003000415", "dme"] == 1
    assert g.loc["1003000407", "dme"] == 0           # not eligible to order DME


def test_ineligible_referral_share():
    elig = pd.DataFrame({"npi": ["1003000415"], "partb": [1], "dme": [1],
                         "hha": [0], "pmd": [0]})
    # org:mill's DME is referred half by an eligible NPI, half by an unknown one
    claims = pd.DataFrame([
        {"billing_npi": "1003000100", "referring_npi": "1003000415",
         "total_paid": 200_000.0},                          # eligible referrer
        {"billing_npi": "1003000100", "referring_npi": "9999999999",
         "total_paid": 600_000.0},                          # NOT eligible (absent)
    ])
    xw = pd.DataFrame({"npi": ["1003000100"], "org_node_id": ["org:mill"]})
    out = ineligible_referral_share(claims, elig, xw, order_type="dme").iloc[0]
    assert out["ineligible_referred_paid"] == 600_000.0
    assert out["ineligible_referral_share"] == 0.75


# ----------------------------------------------------- registry wiring ---

def test_new_features_feed_their_schemes():
    feats = pd.DataFrame({
        "capacity_mismatch": [0.95, 0.0],
        "pbj_understaffing": [0.1, 0.1], "deficiency_count": [0.1, 0.1],
        "ineligible_referral_share": [0.9, 0.0],
        "dme_high_cost_item_share": [0.1, 0.1],
        "dme_ordering_md_concentration": [0.1, 0.1],
    })
    subs, cov = compute_subscores(feats)
    assert "capacity_mismatch" in cov["worthless_services"]
    assert "ineligible_referral_share" in cov["dme_ring"]
    assert subs.loc[0, "subscore_worthless_services"] > subs.loc[1, "subscore_worthless_services"]
    assert subs.loc[0, "subscore_dme_ring"] > subs.loc[1, "subscore_dme_ring"]
