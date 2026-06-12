"""
test_enforcement.py — item 3: the DOJ case database and derived sector priors.
"""

from __future__ import annotations

import pandas as pd

from src.enforcement import parse_press_release, build_case_db, derive_sector_priors
from src.model_a.sector_priors import sector_prior_series

HOSPICE_RELEASE = """
Acme Hospice Care LLC has agreed to pay $12.5 million to resolve allegations
under the False Claims Act that it knowingly submitted claims for hospice
patients who were not eligible for the hospice benefit. The settlement resolves
a qui tam lawsuit filed by a former admissions nurse; the United States
intervened in the action, filed in the Middle District of Florida.
"""

DME_RELEASE = """
Brace Depot Inc. will pay $3 million to settle allegations that it paid
kickbacks to telemedicine companies in exchange for orders of durable medical
equipment. The government declined to intervene and the whistleblower pursued
the case.
"""


def test_parse_press_release_extracts_fields():
    case = parse_press_release(HOSPICE_RELEASE, source_url="https://doj/x",
                               defendant_name="Acme Hospice Care LLC")
    assert case["amount_usd"] == 12_500_000.0
    assert case["sector"] == "hospice"
    assert case["scheme"] == "eligibility"
    assert case["qui_tam"] == 1
    assert case["intervened"] == 1
    assert case["jurisdiction"] == "Middle District of Florida"
    assert case["defendant_name_key"] == "ACME HOSPICE CARE"   # graph join key


def test_parse_declined_kickback_case():
    case = parse_press_release(DME_RELEASE, defendant_name="Brace Depot Inc.")
    assert case["sector"] == "dme"
    assert case["scheme"] == "kickback"
    assert case["intervened"] == 0
    assert case["amount_usd"] == 3_000_000.0


def test_build_case_db_validates_and_dedupes():
    a = parse_press_release(HOSPICE_RELEASE, source_url="https://doj/x",
                            defendant_name="Acme Hospice Care LLC")
    db = build_case_db([a, a, parse_press_release(DME_RELEASE,
                                                  defendant_name="Brace Depot Inc.")])
    assert len(db) == 2                                    # deduped on case_id
    assert set(db.columns) >= {"defendant_name_key", "sector", "amount_usd"}


def test_derived_priors_replace_placeholders():
    # 3 hospice cases incl. the big one, 1 dme → hospice gets the max multiplier
    rows = [parse_press_release(HOSPICE_RELEASE, source_url=f"u{i}",
                                defendant_name=f"Hospice {i}") for i in range(3)]
    rows.append(parse_press_release(DME_RELEASE, source_url="u9",
                                    defendant_name="Brace Depot Inc."))
    priors = derive_sector_priors(build_case_db(rows), max_multiplier=1.6)
    assert priors["hospice"] == 1.6
    assert 1.0 < priors["dme"] < priors["hospice"]
    assert priors["default"] == 1.0

    # and they flow into Model A's prior series as an override
    s = sector_prior_series(pd.Series(["251G00000X", "207Q00000X"]), priors=priors)
    assert s.iloc[0] == 1.6 and s.iloc[1] == 1.0


def test_empty_case_db_is_neutral():
    priors = derive_sector_priors(build_case_db([]))
    assert priors == {"default": 1.0}
