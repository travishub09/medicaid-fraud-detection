"""
test_nppes_primary_taxonomy.py — the primary-taxonomy switch must be honored.

NPPES lists up to 15 taxonomy slots per NPI; the true primary is the slot whose
"Primary Taxonomy Switch" is Y, which is often NOT slot 1 (slot order is
enrollment history). Found live: a dossier target whose slot 1 was the 2012
student code 390200000X while the Y-flagged primary was Internal Medicine —
slot-1 selection mislabeled the provider and, at scale, corrupts every
taxonomy-keyed peer group.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import clean_nppes


def _write_nppes(path, rows):
    cols = ["NPI", "Entity Type Code",
            "Provider Organization Name (Legal Business Name)",
            "Provider Last Name (Legal Name)", "Provider First Name",
            "Healthcare Provider Taxonomy Code_1",
            "Healthcare Provider Primary Taxonomy Switch_1",
            "Healthcare Provider Taxonomy Code_2",
            "Healthcare Provider Primary Taxonomy Switch_2",
            "Provider First Line Business Practice Location Address",
            "Provider Business Practice Location Address City Name",
            "Provider Business Practice Location Address State Name",
            "Provider Business Practice Location Address Postal Code",
            "NPI Deactivation Date", "NPI Reactivation Date"]
    pd.DataFrame(rows, columns=cols).to_csv(path, index=False)


def test_switch_flagged_primary_beats_slot_one(tmp_path):
    # the live case: slot 1 = student (switch N), slot 2 = internal med (switch Y)
    src = tmp_path / "nppes.csv"
    _write_nppes(src, [
        ["1255694451", "1", "", "SUHAIL", "MOHAMMAD",
         "390200000X", "N", "207R00000X", "Y",
         "130 W KINGSBRIDGE RD", "BRONX", "NY", "10468", "", ""],
        # control: primary genuinely in slot 1
        ["1588799746", "1", "", "VEAL", "LAURA",
         "235Z00000X", "Y", "", "",
         "3904 BRYAN AVE NW", "ALBUQUERQUE", "NM", "87114", "", ""],
    ])
    clean_nppes(str(src), tmp_path)
    out = pd.read_parquet(tmp_path / "identity.parquet").set_index("npi")
    assert out.loc["1255694451", "taxonomy_code"] == "207R00000X"   # not the slot-1 student code
    assert out.loc["1588799746", "taxonomy_code"] == "235Z00000X"


def test_slot_one_fallback_when_no_switch_columns(tmp_path):
    # trimmed exports without switch columns keep the old slot-1 behavior
    src = tmp_path / "nppes_trim.csv"
    pd.DataFrame([{
        "NPI": "1255694451", "Entity Type Code": "1",
        "Provider Last Name (Legal Name)": "SUHAIL",
        "Provider First Name": "MOHAMMAD",
        "Healthcare Provider Taxonomy Code_1": "390200000X",
        "Provider First Line Business Practice Location Address": "130 W KINGSBRIDGE RD",
        "Provider Business Practice Location Address City Name": "BRONX",
        "Provider Business Practice Location Address State Name": "NY",
        "Provider Business Practice Location Address Postal Code": "10468",
    }]).to_csv(src, index=False)
    clean_nppes(str(src), tmp_path)
    out = pd.read_parquet(tmp_path / "identity.parquet").set_index("npi")
    assert out.loc["1255694451", "taxonomy_code"] == "390200000X"
