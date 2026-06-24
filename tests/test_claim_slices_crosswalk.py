"""
test_claim_slices_crosswalk.py — the data-unlock builders.

Covers the CCN→NPI crosswalk (the missing link for facility/HCRIS/POS schemes)
and the two derived claim slices (NDC-level and referral-bearing) that
drug_outlier and dme_ring need. Each builder must: resolve varied vintage column
names, canonicalize IDs (NPI Luhn / CCN zero-pad), and FAIL LOUD (naming the
missing field) when handed a file that can't support the slice.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.ingest_cms.ccn_npi_crosswalk import build_ccn_npi_crosswalk
from src.ingest_cms.claim_slices import build_ndc_claims, build_referred_claims

# valid Luhn NPIs (pass the shared canonicalize_series check)
NPI_A, NPI_B = "1003000126", "1003000134"


def test_ccn_crosswalk_resolves_and_canonicalizes():
    raw = pd.DataFrame({
        "PRVDR_NUM": ["12345", "455123", ""],      # all-digit → zero-pad to 6
        "NPI": [NPI_A, NPI_B, NPI_A],
    })
    xw = build_ccn_npi_crosswalk(raw)
    assert list(xw.columns) == ["ccn", "npi"]
    assert "012345" in set(xw["ccn"])              # zero-padded CCN
    assert xw["npi"].isin([NPI_A, NPI_B]).all()    # only valid NPIs survive
    assert not xw.duplicated().any()               # deduplicated


def test_ccn_crosswalk_fails_loud_on_wrong_file():
    with pytest.raises(ValueError, match="ccn"):
        build_ccn_npi_crosswalk(pd.DataFrame({"hcpcs": ["X"], "paid": [1]}))


def test_ndc_claims_slice():
    raw = pd.DataFrame({
        "prscrbr_npi": [NPI_A, NPI_B],
        "ndc": ["00071015523", "00002323730"],
        "units_reimbursed": ["30", "60"],
        "medicaid_amount_reimbursed": ["100.5", "200"],
    })
    out = build_ndc_claims(raw)
    assert set(out.columns) == {"billing_npi", "ndc", "units", "billed_cost"}
    assert out["units"].sum() == 90.0
    assert out["billing_npi"].tolist() == [NPI_A, NPI_B]


def test_referred_claims_slice_drops_invalid_referrer():
    raw = pd.DataFrame({
        "npi": [NPI_A, NPI_B],
        "ordering_npi": [NPI_B, "notanpi"],        # second referrer invalid → dropped
        "total_paid": ["500", "900"],
    })
    out = build_referred_claims(raw)
    assert set(out.columns) == {"billing_npi", "referring_npi", "total_paid"}
    assert len(out) == 1
    assert out.iloc[0]["referring_npi"] == NPI_B


def test_claim_slice_fails_loud_when_too_thin():
    # a by-HCPCS spending file has no NDC / referring NPI — must reject, not empty
    thin = pd.DataFrame({"billing_npi": [NPI_A], "hcpcs_code": ["T1019"],
                         "total_paid": ["100"]})
    with pytest.raises(ValueError, match="ndc"):
        build_ndc_claims(thin)
    with pytest.raises(ValueError, match="referring_npi"):
        build_referred_claims(thin)
