"""Tests for src.model_a.case_size — synthetic matrix only."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.case_size import (estimate_case_size, org_case_size,
                                   recovery_walk, size_line)


def _matrix():
    n = 20
    df = pd.DataFrame({
        "npi": [f"1{i:09d}" for i in range(n)],
        "org_node_id": [f"org:{i // 2}" for i in range(n)],
        "net_paid": 1_000_000.0,
        "expected_net_paid": 1_000_000.0,
        "mue_violation_share": 0.0,
        "dme_ineligible_referred_dollars": 0.0,
        "op_payment_utilization_corr__peerpct": np.linspace(0, 0.94, n),
    })
    # provider 0: $12M billed vs $2M expected -> $10M excess (the chase case)
    df.loc[0, ["net_paid", "expected_net_paid"]] = [12_000_000.0, 2_000_000.0]
    # provider 2: kickback tail — modest billing, extreme op correlation
    df.loc[2, "op_payment_utilization_corr__peerpct"] = 0.99
    df.loc[2, "net_paid"] = 3_000_000.0
    # provider 4: MUE share -> share-based damages
    df.loc[4, "mue_violation_share"] = 0.5
    return df


def test_estimators_and_basis():
    est = estimate_case_size(_matrix())
    # $10M raw excess on $12M paid caps at 40% of paid = $4.8M — the
    # institutional-tail guard (a state agency's 'excess' isn't damages)
    assert est.loc[0, "case_size_yr"] == 4_800_000.0
    assert est.loc[0, "case_size_basis"] == "billing_excess"
    # kickback taint: whole paid stream for the extreme-correlation tail
    assert est.loc[2, "case_size_yr"] == 3_000_000.0
    assert est.loc[2, "case_size_basis"] == "kickback_taint"
    assert est.loc[4, "case_size_yr"] == 400_000.0  # 0.5 share capped at 40% of paid
    assert est.loc[4, "case_size_basis"] == "impossible_day"
    # clean provider: no damages, basis 'none'
    assert est.loc[7, "case_size_yr"] == 0.0
    assert est.loc[7, "case_size_basis"] == "none"


def test_org_rollup_sums_members():
    orgs = org_case_size(_matrix()).set_index("org_id")
    # org:0 holds providers 0 and 1 -> the capped excess (40% of $12M)
    assert orgs.loc["org:0", "case_size_yr"] == 4_800_000.0
    assert orgs.loc["org:0", "case_size_basis"] == "billing_excess"
    assert orgs.loc["org:1", "case_size_basis"] == "kickback_taint"


def test_gov_screen_and_hot_gate():
    import re
    from src.model_a.pursuit_dossier import GOV_RE
    gov_names = ["COMMONWEALTH OF MASSACHUSETTS",
                 "LOS ANGELES COUNTY DEPARTMENT OF MENTAL HEALTH",
                 "CITY & COUNTY OF SAN FRANCISCO",
                 "NEW YORK CITY HEALTH AND HOSPITALS CORPORATION"]
    ok_names = ["BAYADA HOME HEALTH CARE, INC.", "DAP HEALTH, INC.",
                "SOUTHERN KENTUCKY HEART INSTITUTE, PSC"]
    for n in gov_names:
        assert re.search(GOV_RE, n), n
    for n in ok_names:
        assert not re.search(GOV_RE, n), n


def test_recovery_walk_math():
    w = recovery_walk(10_000_000.0)
    assert w["damages_period"] == 60_000_000.0          # 6-year lookback
    assert w["treble_exposure"] == 180_000_000.0
    assert w["settle_lo"] == 90_000_000.0               # 1.5x
    assert w["settle_hi"] == 120_000_000.0              # 2.0x
    assert w["relator_lo"] == 13_500_000.0              # 15% of settle_lo
    assert w["relator_hi"] == 30_000_000.0              # 25% of settle_hi
    # expected: 60M * 1.75 * 0.20 * 0.20 = 4.2M
    assert abs(w["expected_relator"] - 4_200_000.0) < 1.0


def test_size_line_readable():
    s = size_line(10_000_000.0)
    assert "$10,000,000/yr" in s and "screening estimates" in s
    assert "not computable" in size_line(0.0)


def test_missing_columns_skip_honest():
    df = pd.DataFrame({"npi": ["1"], "org_node_id": ["org:x"],
                       "net_paid": [5e6]})
    est = estimate_case_size(df)
    assert est.loc[0, "case_size_yr"] == 0.0
    assert est.loc[0, "case_size_basis"] == "none"
