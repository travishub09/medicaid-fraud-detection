"""
test_dossier_build.py — one NPI → a counsel-grade dossier from the packs.
"""

from __future__ import annotations

import pandas as pd

from src.model_a.dossier_build import build_npi_dossier


def _pack():
    return pd.DataFrame([{
        "npi": "1588799746", "provider_name": "VEAL, LAURA",
        "primary_taxonomy": "235Z00000X", "entity_type": "1",
        "addr_city": "ALBUQUERQUE", "practice_state": "NM",
        "net_paid": 31_018_097.0, "expected_net_paid": 4_922_631.0,
        "tenure_months": 84, "org_member_count": 1, "n_distinct_hcpcs": 4,
        "consistency_flags": 3,
        "subscore_facility_code_billing": 0.95,
        "subscore_ownership_integrity": 0.9,     # non-headline, must be skipped
        "subscore_specialty_mismatch": 0.8,
    }])


def _monthly():
    return pd.DataFrame({
        "npi": ["1588799746"] * 3,
        "month": ["2018-01", "2020-01", "2024-12"],
        "paid": [367_258.0, 500_000.0, 606_014.0],
        "claim_lines": [900, 1000, 1100],
    })


def _codes():
    return pd.DataFrame({
        "npi": ["1588799746", "1588799746"],
        "hcpcs": ["T2016", "H2021"],
        "paid": [26_549_233.0, 3_341_248.0],
        "claim_lines": [70_330, 5_414],
    })


def test_dossier_has_all_sections_and_money():
    md = build_npi_dossier("1588799746", _pack(), _monthly(), _codes())
    for section in ("Target dossier", "The money", "The pattern",
                    "innocent explanations", "Public-disclosure screen",
                    "What a relator would know", "Damages frame"):
        assert section in md
    # suspect dollars = net - expected = 26,095,466; break-even ~0.38%
    assert "$26,095,466" in md
    assert "0.38%" in md
    # top code appears with its claim-line count
    assert "T2016" in md and "70,330" in md
    # the leakage-adjacent subscore is NOT the headline pattern
    assert "ownership-integrity concern**" not in md.split("The money")[0]
    # disclaimer present
    assert "Nothing here is a finding or accusation" in md


def test_registry_and_innocent_fill_when_provided():
    reg = {"registry_flag": "TAXONOMY_MISMATCH", "registry_taxonomy": "207R00000X",
           "registry_taxonomy_desc": "Internal Medicine", "registry_status": "A",
           "registry_last_updated": "2016-02-22"}
    inn = {"result": "This could be a real residential agency billing under one NPI."}
    md = build_npi_dossier("1588799746", _pack(), _monthly(), _codes(),
                           registry=reg, innocent=inn)
    assert "TAXONOMY_MISMATCH" in md and "Internal Medicine" in md
    assert "residential agency" in md
    assert "Pending: run the innocent-explanation audit" not in md


def test_unknown_npi_is_graceful():
    md = build_npi_dossier("9999999999", _pack())
    assert "not found" in md.lower()
