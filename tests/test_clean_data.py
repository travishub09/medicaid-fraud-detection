"""
test_clean_data.py — the shared attempt_2 core every stage depends on
(GAPS #8): NPI Luhn canonicalization, the name/address normalizers, header
resolution, and the env-overridable data root.

These are the single most-reused functions in the repo (rule 6: ONE normalizer);
a silent behavior change here corrupts every downstream join.
"""

from __future__ import annotations

import importlib

import pandas as pd

from src.attempt_2 import clean_data
from src.attempt_2.clean_data import (
    is_valid_npi, canonicalize_npi, canonicalize_series,
    _normalize_name, _standardize_address, _resolve_columns,
    BROAD_HCPCS_CODES,
)

VALID = "1003000415"      # Luhn-valid (the fixture's mill NPI)
INVALID = "1003000041"    # right shape, wrong check digit


# ----------------------------------------------------------- NPI handling ---

def test_is_valid_npi():
    assert is_valid_npi(VALID)
    assert not is_valid_npi(INVALID)
    assert not is_valid_npi("123")            # too short
    assert not is_valid_npi("abcdefghij")     # not digits
    assert not is_valid_npi(1003000415)       # not a string


def test_canonicalize_npi_strips_excel_and_prefixes():
    assert canonicalize_npi(f"'{VALID}") == VALID          # Excel apostrophe
    assert canonicalize_npi(f"NPI: {VALID}") == VALID      # label prefix
    assert canonicalize_npi(f"  {VALID}  ") == VALID       # whitespace
    assert canonicalize_npi(INVALID) is None               # bad check digit
    assert canonicalize_npi("") is None
    assert canonicalize_npi(None) is None


def test_canonicalize_series_matches_scalar():
    raw = pd.Series([VALID, f"'{VALID}", INVALID, "", None, "garbage"])
    out = canonicalize_series(raw)
    assert out.iloc[0] == VALID
    assert out.iloc[1] == VALID                # vectorized handles the apostrophe
    assert out.iloc[2] is None or pd.isna(out.iloc[2])
    assert out.iloc[5] is None or pd.isna(out.iloc[5])
    # scalar and vector paths must agree on every input
    for i, v in raw.items():
        scalar = canonicalize_npi(v)
        vector = out.iloc[i]
        assert (scalar == vector) or (scalar is None and pd.isna(vector))


# ------------------------------------------------------------ normalizers ---

def test_normalize_name_collapses_suffixes_and_punctuation():
    s = pd.Series(["Acme Health, LLC.", "ACME HEALTH LLC", "Smith, John Jr.",
                   "The Best Care Inc"])
    out = _normalize_name(s)
    assert out.iloc[0] == out.iloc[1] == "ACME HEALTH"     # the alias-merge key
    assert out.iloc[2] == "SMITH JOHN"                     # honorific stripped
    assert "INC" not in out.iloc[3]


def test_standardize_address_zip5_and_case():
    df = pd.DataFrame({"line1": ["100 Main St."], "city": ["austin"],
                       "state": ["tx"], "zip": ["78701-1234"]})
    key = _standardize_address(df)
    assert key.iloc[0] == "100 MAIN ST AUSTIN TX 78701"    # zip9 → zip5, upper
    # missing columns degrade gracefully, never raise
    key2 = _standardize_address(pd.DataFrame({"line1": ["1 A St"]}))
    assert key2.iloc[0] == "1 A ST"


def test_resolve_columns_normalized_first_wins():
    header = ["Rndrng_NPI", "HCPCS Cd", "TOT_SRVCS"]
    wanted = {"npi": ["RNDRNG_NPI", "npi"], "hcpcs": ["HCPCS_Cd"],
              "services": ["Tot_Srvcs"], "absent": ["NOT_THERE"]}
    resolved = _resolve_columns(header, wanted)
    assert resolved["npi"] == "Rndrng_NPI"        # case-insensitive
    assert resolved["hcpcs"] == "HCPCS Cd"        # space ≡ underscore
    assert resolved["services"] == "TOT_SRVCS"
    assert "absent" not in resolved               # missing stays missing


def test_broad_hcpcs_flags_personal_care():
    assert "T1019" in BROAD_HCPCS_CODES           # the personal-care unit problem


# ------------------------------------------------------------ config root ---

def test_data_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDICAID_DATA_ROOT", str(tmp_path))
    importlib.reload(clean_data)
    try:
        assert clean_data.DATA_ROOT == tmp_path
        assert clean_data.PRECLEAN_DIR == tmp_path / "preclean"
    finally:
        monkeypatch.delenv("MEDICAID_DATA_ROOT")
        importlib.reload(clean_data)              # restore the default for other tests
