"""
test_medicare_revocations.py — CMS Revoked Medicare Providers → exclusions schema.

The public HHS revocation file normalizes into the same exclusions schema as
LEIE/SAM: NPI canonicalized, name → name_key, revocation date → excl_date, bar
expiration → reinstate_date, and currently_active driven by whether the bar has
expired. Multi-NPI enrollments yield one exclusion row per NPI.
"""

from __future__ import annotations

import pandas as pd

from src.enforcement.medicare_revocations import (
    normalize_revocations, EXCLUSION_COLS)


def _raw():
    return pd.DataFrame([
        # an org with an active bar (expires in the future)
        {"ENRLMT_ID": "I20240101000001", "NPI": "1003000415",
         "ORGANIZATION_NAME": "Acme Health, LLC", "REVOCATION_EFCTV_DT": "2024-03-01",
         "REENROLLMENT_BAR_EXPRTN_DT": "2030-03-01", "REVOCATION_RSN": "Felony; Non-compliance"},
        # a person whose bar already expired (not currently active)
        {"ENRLMT_ID": "I20200101000002", "NPI": "1003000407",
         "LAST_NAME": "Smith", "FIRST_NAME": "John", "REVOCATION_EFCTV_DT": "2019-01-01",
         "REENROLLMENT_BAR_EXPRTN_DT": "2022-01-01", "REVOCATION_RSN": "Abuse of billing privileges"},
        # a bad/short NPI → dropped from the npi field but still has a name
        {"ENRLMT_ID": "I20240101000003", "NPI": "123",
         "ORGANIZATION_NAME": "Beta Clinic Inc", "REVOCATION_EFCTV_DT": "2025-06-01",
         "REENROLLMENT_BAR_EXPRTN_DT": "", "REVOCATION_RSN": ""},
    ])


def test_normalizes_to_exclusion_schema():
    out = normalize_revocations(_raw(), as_of="2026-06-16")
    assert list(out.columns) == EXCLUSION_COLS
    a = out.set_index("entity_name")
    # org name kept, NPI canonicalized, reason carried into excl_type
    assert a.loc["Acme Health, LLC", "npi"] == "1003000415"
    assert "medicare_revocation: Felony" in a.loc["Acme Health, LLC", "excl_type"]
    assert a.loc["Acme Health, LLC", "name_key"] == "ACME HEALTH"   # suffix stripped


def test_currently_active_tracks_bar_expiration():
    out = normalize_revocations(_raw(), as_of="2026-06-16").set_index("entity_name")
    assert out.loc["Acme Health, LLC", "currently_active"] == 1   # bar to 2030
    assert out.loc["Smith, John", "currently_active"] == 0        # bar expired 2022
    assert out.loc["Beta Clinic Inc", "currently_active"] == 1    # no expiration → active


def test_person_name_and_bad_npi_handling():
    out = normalize_revocations(_raw(), as_of="2026-06-16").set_index("entity_name")
    assert out.loc["Smith, John", "name_key"] == "SMITH JOHN"     # built from last,first
    # the 3-digit NPI is invalid → npi blanked, but the row survives on its name
    assert out.loc["Beta Clinic Inc", "npi"] == ""
    assert out.loc["Beta Clinic Inc", "name_key"] == "BETA CLINIC"
