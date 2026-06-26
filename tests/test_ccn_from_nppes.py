"""
test_ccn_from_nppes.py — CCN↔NPI bridge from NPPES Other Provider Identifier fields.

When neither PECOS (NPI, no CCN) nor POS (CCN, no NPI) carries both keys, the
crosswalk falls back to NPPES: institutional NPIs record their Medicare CCN as an
Other Provider Identifier with Type Code 06 (Medicare OSCAR/Certification).
"""

from __future__ import annotations

import pandas as pd

from src.ingest_cms.ccn_npi_crosswalk import (build_ccn_npi_crosswalk,
                                              crosswalk_from_nppes)


def _nppes():
    # NPPES-shaped: an NPI + two Other Provider Identifier slots with type codes.
    # NPI 1 has a Medicare CCN (type 06) in slot 2; NPI 2 lists only a Medicaid id.
    return pd.DataFrame({
        "NPI": ["1003000100", "1003000126"],
        "Other Provider Identifier_1": ["XYZ123", "MD999"],
        "Other Provider Identifier Type Code_1": ["01", "05"],
        "Other Provider Identifier State_1": ["AK", "OH"],
        "Other Provider Identifier_2": ["450123", ""],
        "Other Provider Identifier Type Code_2": ["06", ""],
    })


def test_extracts_type06_ccn_and_pairs_with_npi():
    xw = crosswalk_from_nppes(_nppes())
    assert list(xw.columns) == ["ccn", "npi"]
    # only the type-06 identifier becomes a CCN, paired with its row's NPI
    assert xw.to_dict("records") == [{"ccn": "450123", "npi": "1003000100"}]


def test_build_autofalls_back_to_nppes_when_no_ccn_column():
    # the same enrollment-shaped frame the user hit (NPI present, no CCN column),
    # but carrying NPPES OPI fields → the builder bridges via type-06 automatically
    xw = build_ccn_npi_crosswalk(_nppes())
    assert len(xw) == 1 and xw.iloc[0]["ccn"] == "450123"


def test_direct_pair_still_preferred():
    direct = pd.DataFrame({"PRVDR_NUM": ["010001"], "NPI": ["1003000100"]})
    xw = build_ccn_npi_crosswalk(direct)
    assert xw.to_dict("records") == [{"ccn": "010001", "npi": "1003000100"}]


def test_loud_error_when_neither_path_possible():
    import pytest
    bad = pd.DataFrame({"prvdr_num": ["010001"], "fac_name": ["X"]})   # CCN, no NPI, no OPI
    with pytest.raises(ValueError, match="no recognizable npi"):
        build_ccn_npi_crosswalk(bad)
