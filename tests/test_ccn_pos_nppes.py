"""
test_ccn_pos_nppes.py — CCN↔NPI bridge by joining the POS facility file to NPPES.

When neither file pairs both keys (PECOS=NPI-no-CCN, POS=CCN-no-NPI, NPPES uses
only type 01/05 so the type-06 path is empty), bridge two ways and union:
(1) Medicaid vendor number (POS mdcd_vndr_num == an NPPES type-05 identifier), and
(2) normalized facility name + ZIP5. Uses the real column names from Trey's files.
"""

from __future__ import annotations

import pandas as pd

from src.ingest_cms.ccn_npi_crosswalk import (crosswalk_from_pos_nppes,
                                              load_nppes_for_ccn)


def _pos():
    # real POS headers from Trey's file
    return pd.DataFrame({
        "prvdr_num": ["010001", "455028"],
        "fac_name": ["ACME HOSPITAL", "BAYOU HOSPICE LLC"],
        "st_adr": ["1 MAIN ST", "9 OAK RD"],
        "city_name": ["AUSTIN", "HOUSTON"],
        "state_cd": ["TX", "TX"],
        "zip_cd": ["78701", "77002"],
        "mdcd_vndr_num": ["MD-555", ""],         # facility 1 has a Medicaid vendor #
    })


def _facilities_and_medicaid():
    # NPPES-derived frames: facility 2 matches by name+ZIP; facility 1 by Medicaid #
    from src.entity_graph.resolve_entities import norm_org_name
    facilities = pd.DataFrame({
        "npi": ["1003000100", "1003000126"],
        "name_key": [norm_org_name("Different Name Inc"), norm_org_name("BAYOU HOSPICE LLC")],
        "zip5": ["78701", "77002"],
        "addr_key": ["", ""],
    })
    medicaid = pd.DataFrame({"npi": ["1003000100"], "mdcd": ["MD-555"]})
    return facilities, medicaid


def test_join_recovers_both_match_paths():
    fac, med = _facilities_and_medicaid()
    xw = crosswalk_from_pos_nppes(_pos(), fac, med).set_index("ccn")
    # facility 1 (010001) recovered via the Medicaid vendor number
    assert xw.loc["010001", "npi"] == "1003000100"
    assert xw.loc["010001", "match_source"] == "medicaid_vendor"
    # facility 2 (455028) recovered via normalized name + ZIP
    assert xw.loc["455028", "npi"] == "1003000126"
    assert xw.loc["455028", "match_source"] == "name_zip"


def test_load_nppes_for_ccn_reads_selected_columns(tmp_path):
    # a miniature NPPES file with the real wide-format headers
    nppes = pd.DataFrame({
        "NPI": ["1003000126"],
        "Provider Organization Name (Legal Business Name)": ["BAYOU HOSPICE LLC"],
        "Provider First Line Business Practice Location Address": ["9 OAK RD"],
        "Provider Business Practice Location Address City Name": ["HOUSTON"],
        "Provider Business Practice Location Address State Name": ["TX"],
        "Provider Business Practice Location Address Postal Code": ["77002-1234"],
        "Other Provider Identifier_1": ["MD-777"],
        "Other Provider Identifier Type Code_1": ["05"],
        "Other Provider Identifier Issuer_1": ["TX"],
    })
    p = tmp_path / "NPPES.csv"
    nppes.to_csv(p, index=False)
    fac, med = load_nppes_for_ccn(str(p))
    assert fac.iloc[0]["zip5"] == "77002"
    assert fac.iloc[0]["name_key"]                       # normalized, non-empty
    assert med.to_dict("records") == [{"npi": "1003000126", "mdcd": "MD-777"}]


def test_empty_when_nothing_matches():
    fac = pd.DataFrame({"npi": ["1003000100"], "name_key": ["zzz"], "zip5": ["00000"],
                        "addr_key": [""]})
    med = pd.DataFrame(columns=["npi", "mdcd"])
    xw = crosswalk_from_pos_nppes(_pos(), fac, med)
    assert len(xw) == 0 and list(xw.columns) == ["ccn", "npi", "match_source"]
