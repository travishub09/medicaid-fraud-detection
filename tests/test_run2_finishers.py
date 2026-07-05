"""
test_run2_finishers.py — the last Run-2 plan builds:
  §E smoking-gun time attributes; §F DOJ fuzzy matching + Medicaid filter.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.smoking_gun_timeline import billing_after_cutoff
from src.model_a.case_labels import build_case_labels


def test_timeline_months_dollars_and_strictly_after():
    spend = pd.DataFrame({
        "billing_npi": ["1003000126"] * 4 + ["1003000134"],
        "service_month": ["2023-05", "2023-06", "2023-06", "2023-08", "2023-01"],
        "total_paid": [100.0, 200.0, 50.0, 300.0, 999.0],
    })
    cuts = pd.DataFrame({"npi": ["1003000126", "1003000134"],
                         "cutoff_date": ["2023-05-15", "2023-06-01"]})
    tl = billing_after_cutoff(spend, cuts, "excl").set_index("npi")
    r = tl.loc["1003000126"]
    # the cutoff MONTH itself (2023-05) is excluded; 06 and 08 count
    assert r["excl_months_after"] == 2
    assert r["excl_paid_after"] == 550.0
    assert r["excl_first_after"] == "2023-06" and r["excl_last_after"] == "2023-08"
    # banned-but-stopped provider: zero months, zero dollars — present, not absent
    r2 = tl.loc["1003000134"]
    assert r2["excl_months_after"] == 0 and r2["excl_paid_after"] == 0.0


def test_timeline_earliest_cutoff_wins_on_relist():
    spend = pd.DataFrame({"billing_npi": ["1003000126"],
                          "service_month": ["2023-07"], "total_paid": [500.0]})
    cuts = pd.DataFrame({"npi": ["1003000126", "1003000126"],
                         "cutoff_date": ["2023-09-01", "2023-03-01"]})  # re-listed
    tl = billing_after_cutoff(spend, cuts, "excl").set_index("npi")
    assert tl.loc["1003000126", "excl_months_after"] == 1   # after the EARLIEST ban


def _org_nodes():
    return pd.DataFrame({
        "org_node_id": ["org:acme", "org:other"],
        "org_name": ["ACME HEALTH SERVICES LLC", "TOTALLY DIFFERENT CARE"],
    })


def _case_db():
    return pd.DataFrame({
        "case_id": ["c1", "c2"],
        "defendant": ["ACME HEALTH SERVICES", "UNRELATED HOSPITAL SYSTEM"],
        "amount_usd": ["1000000", "2000000"],
        "scheme": ["upcoding", "kickback"],
        "summary": ["Acme settled Medicaid upcoding claims from 2019 through 2021",
                    "A Medicare-only matter"],
        "announced_date": ["2022-05-01", "2022-06-01"],
        "intervened": ["1", "1"],
    })


def test_fuzzy_matching_catches_llc_suffix_variants():
    xw = pd.DataFrame({"npi": ["1003000126"], "org_node_id": ["org:acme"]})
    out = build_case_labels(_case_db(), _org_nodes(), xw, fuzzy_threshold=0.90)
    assert len(out) == 1 and out.iloc[0]["npi"] == "1003000126"
    assert out.iloc[0]["fraud_scheme"] == "upcoding"
    assert out.iloc[0]["conduct_start"] == 2019 and out.iloc[0]["conduct_end"] == 2021
    # without fuzzy, the LLC-suffix variant is missed (documents the gain)
    strict = build_case_labels(_case_db(), _org_nodes(), xw, fuzzy_threshold=None)
    acme_exact = strict[strict["npi"] == "1003000126"]
    assert len(acme_exact) == 0 or "c1" not in str(acme_exact.get("case_ids", ""))


def test_medicaid_only_filter():
    xw = pd.DataFrame({"npi": ["1003000126"], "org_node_id": ["org:acme"]})
    out = build_case_labels(_case_db(), _org_nodes(), xw,
                            fuzzy_threshold=0.90, medicaid_only=True)
    # c1 mentions Medicaid → kept; a Medicare-only case could never label anyone here
    assert len(out) == 1 and "c1" in out.iloc[0]["case_ids"]
