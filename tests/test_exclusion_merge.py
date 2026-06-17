"""
test_exclusion_merge.py — revoked-providers union into the exclusions table.

`merge_exclusion_sources` unions the HHS Revoked Medicare Providers CSV (when
present) into the LEIE-built exclusions, preserving the schema and de-duplicating
on identity + action so the graph picks up revoked-provider nodes automatically.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.ingest.integrate import merge_exclusion_sources

# the exact column shape build_exclusions emits
LEIE_COLS = ["npi", "excl_date", "reinstate_date", "excl_type",
             "currently_active", "entity_name", "name_key"]


def _leie():
    return pd.DataFrame([
        {"npi": "1003000209", "excl_date": pd.Timestamp("2020-01-01"),
         "reinstate_date": pd.NaT, "excl_type": "1128a1", "currently_active": 1,
         "entity_name": "EXCLUDED, JOHN", "name_key": "EXCLUDED JOHN"},
    ])[LEIE_COLS]


def test_no_revocations_file_is_noop(tmp_path):
    leie = _leie()
    out = merge_exclusion_sources(leie, str(tmp_path / "missing.csv"))
    assert out.equals(leie)               # unchanged when the file isn't there


def test_revocations_unioned_and_schema_preserved(tmp_path):
    rev_csv = tmp_path / "revoked_providers.csv"
    pd.DataFrame([
        {"ENRLMT_ID": "I1", "NPI": "1003000415", "ORGANIZATION_NAME": "Acme Health LLC",
         "REVOCATION_EFCTV_DT": "2024-03-01", "REENROLLMENT_BAR_EXPRTN_DT": "2030-03-01",
         "REVOCATION_RSN": "Felony"},
    ]).to_csv(rev_csv, index=False)

    out = merge_exclusion_sources(_leie(), str(rev_csv))
    assert list(out.columns) == LEIE_COLS          # schema preserved/aligned
    assert len(out) == 2                            # LEIE row + revoked row
    rev = out[out["npi"] == "1003000415"].iloc[0]
    assert rev["name_key"] == "ACME HEALTH"
    assert "medicare_revocation" in rev["excl_type"]
    assert rev["currently_active"] == 1


def test_dedup_on_identity_and_action(tmp_path):
    # same NPI + same normalized action already in LEIE → not double-counted
    rev_csv = tmp_path / "revoked_providers.csv"
    pd.DataFrame([
        {"NPI": "1003000209", "LAST_NAME": "Excluded", "FIRST_NAME": "John",
         "REVOCATION_EFCTV_DT": "2024-05-01", "REENROLLMENT_BAR_EXPRTN_DT": "2027-05-01",
         "REVOCATION_RSN": "Other"},
    ]).to_csv(rev_csv, index=False)
    out = merge_exclusion_sources(_leie(), str(rev_csv))
    # different excl_type/date than the LEIE row → it's a distinct action, kept
    assert len(out) == 2
    assert (out["npi"] == "1003000209").sum() == 2
