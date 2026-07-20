"""
test_kickback_hub.py — flip flagged prescriber-spokes to ranked payer-hubs.
"""

from __future__ import annotations

import pandas as pd

from src.model_a.kickback_hub import build_kickback_hubs, to_markdown


def _op():
    # ACME pays three flagged spokes and one ordinary recipient;
    # BETA pays one flagged spoke; GAMMA pays only ordinary recipients.
    rows = [
        ("1588799746", "ACME PHARMA", 50000, "DRUGX"),
        ("1033308556", "ACME PHARMA", 40000, "DRUGX"),
        ("1255694451", "ACME PHARMA", 30000, "DRUGX"),
        ("1999999984", "ACME PHARMA", 10000, "DRUGX"),   # ordinary recipient
        ("1588799746", "BETA DEVICES", 20000, "DEVY"),    # only one flagged spoke
        ("1999999984", "GAMMA INC", 5000, "DRUGZ"),       # no flagged spokes
    ]
    return pd.DataFrame(rows, columns=[
        "Covered_Recipient_NPI",
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name",
        "Total_Amount_of_Payment_USDollars",
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1"])


def _spokes():
    return pd.DataFrame({
        "npi": ["1588799746", "1033308556", "1255694451"],
        "op_payment_utilization_corr": [0.9, 0.8, 0.95],
        "suspect_dollars": [1_000_000, 2_000_000, 500_000],
    })


def test_hub_ranks_broadest_payer_first():
    hubs = build_kickback_hubs(_op(), _spokes(), min_spokes=2)
    # only ACME pays >= 2 flagged spokes; BETA (1) and GAMMA (0) drop out
    assert list(hubs["hub"]) == ["ACME PHARMA"]
    row = hubs.iloc[0]
    assert row["n_spokes"] == 3
    assert row["total_to_spokes"] == 120000.0
    assert row["n_recipients"] == 4           # 3 flagged + 1 ordinary
    assert abs(row["spoke_recipient_share"] - 0.75) < 1e-6
    assert abs(row["mean_spoke_corr"] - (0.9 + 0.8 + 0.95) / 3) < 1e-3


def test_downstream_tainted_estimate_splits_by_payment_share():
    hubs = build_kickback_hubs(_op(), _spokes(), min_spokes=2).set_index("hub")
    # spoke 1588799746 is paid by BOTH ACME (50k) and BETA (20k), so ACME gets
    # only its 50/70 share of that spoke's exposure; the other two spokes are
    # ACME-only. Exposure = suspect * corr * (hub's share of the spoke's pay).
    expected = (1_000_000 * 0.9 * (50000 / 70000)
                + 2_000_000 * 0.8 * 1.0
                + 500_000 * 0.95 * 1.0)
    assert abs(hubs.loc["ACME PHARMA", "est_downstream_tainted"] - expected) < 1.0


def test_no_weights_still_ranks():
    hubs = build_kickback_hubs(_op(), pd.DataFrame({"npi": [
        "1588799746", "1033308556", "1255694451"]}), min_spokes=2)
    assert hubs.iloc[0]["n_spokes"] == 3
    assert hubs.iloc[0]["est_downstream_tainted"] is None
    assert "ACME" in to_markdown(hubs, 3)


def test_min_spokes_gate():
    # raise the gate above what any hub meets → empty
    hubs = build_kickback_hubs(_op(), _spokes(), min_spokes=4)
    assert not len(hubs)
