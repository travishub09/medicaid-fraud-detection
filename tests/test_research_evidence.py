"""
test_research_evidence.py — the reviewed-research → label-schema bridge.

A messy state termination list becomes exclusion-schema rows (with quarantine,
never silent drops), and a state MFCU sweep becomes case-DB rows that merge
without clobbering the federal feed.
"""

from __future__ import annotations

import pandas as pd

from src.enforcement.research_evidence import (load_state_exclusions,
                                               load_mfcu_cases,
                                               merge_into_case_db)
from src.enforcement.opensanctions import EXCLUSION_COLS


def test_state_exclusions_normalize_and_quarantine():
    raw = pd.DataFrame({
        "Provider Name": ["SMITH", "ACME HOME CARE LLC", ""],
        "First Name": ["JOHN", "", ""],
        "NPI Number": ["1588799746", "", "not-an-npi"],
        "Termination Type": ["fraud conviction", "billing termination", "x"],
        "Effective Date": ["2024-03-01", "2023-11-15", ""],
    })
    excl, quarantine = load_state_exclusions(raw, "ri")
    assert list(excl.columns) == EXCLUSION_COLS
    assert len(excl) == 2 and len(quarantine) == 1        # nameless+bad-NPI row kept aside
    byname = excl.set_index("entity_name")
    assert byname.loc["SMITH, JOHN", "npi"] == "1588799746"
    assert byname.loc["SMITH, JOHN", "excl_type"] == "state_term_ri:fraud conviction"
    assert byname.loc["ACME HOME CARE LLC", "npi"] == ""   # name-key row still usable
    assert (excl["currently_active"] == 1).all()
    assert str(excl["excl_date"].dt.year.tolist()) == "[2024, 2023]"


def test_mfcu_cases_classify_and_merge(tmp_path):
    raw = pd.DataFrame({
        "Defendant": ["EVERGREEN HOSPICE LLC", "DR EXAMPLE"],
        "Date": ["2025-06-01", "2024-01-15"],
        "Settlement Amount": ["$1,200,000", "300000"],
        "Description": [
            "paid kickbacks for hospice referrals of patients not terminally ill",
            "billed for services not rendered at a pain clinic prescribing opioids"],
    })
    cases = load_mfcu_cases(raw, "oh")
    assert len(cases) == 2
    c0 = cases.set_index("defendant_name").loc["EVERGREEN HOSPICE LLC"]
    assert c0["jurisdiction"] == "OH MFCU"
    assert "kickback" in c0["scheme"]
    assert c0["sector"] == "hospice"
    assert float(c0["amount_usd"]) == 1_200_000.0

    # merge: existing (federal) row with the same case_id is never overwritten
    csv = tmp_path / "cases.csv"
    federal = cases.head(1).copy().astype(str)
    federal["summary"] = "RICH FEDERAL PARSE"
    federal.to_csv(csv, index=False)
    merged = merge_into_case_db(cases, csv)
    assert len(merged) == 2
    kept = merged.set_index("case_id").loc[str(cases.iloc[0]["case_id"]), "summary"]
    assert kept == "RICH FEDERAL PARSE"
