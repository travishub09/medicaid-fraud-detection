"""test_licensed_capacity.py — state facility rosters -> licensed capacity keyed
like the graph (name_key / addr_key), joined to NPIs by address then name, and
merged with POS beds in the capacity check."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.ingest_cms.licensed_capacity import capacity_by_npi, load_roster, merge_rosters
from src.model_a.capacity import merge_licensed


def _write(tmp_path: Path, name: str, df: pd.DataFrame) -> Path:
    p = tmp_path / name
    df.to_csv(p, index=False)
    return p


def test_load_roster_resolves_common_headers_and_keys(tmp_path: Path):
    p = _write(tmp_path, "RI_hospice.csv", pd.DataFrame({
        "Facility Name": ["Ocean State Hospice House", "Empty Row", "Zero Beds Home"],
        "Address": ["10 Main St.", "", "5 Elm Ave"], "City": ["Providence", "", "Warwick"],
        "Zip Code": ["02903-1234", "", "02886"], "License Type": ["Hospice", "", "Group Home"],
        "Licensed Capacity": ["24 beds", "", "0"]}))
    r = load_roster(p, "ri")
    assert len(r) == 1
    row = r.iloc[0]
    assert row["state"] == "RI" and row["capacity"] == 24 and row["zip"] == "02903"
    assert row["addr_key"] == "10 MAIN ST PROVIDENCE RI 02903"
    assert row["name_key"] and row["source_file"] == "RI_hospice.csv"


def test_merge_rosters_state_prefix_or_folder_and_skips_bad_files(tmp_path: Path, capsys):
    _write(tmp_path, "FL_adult_day.csv", pd.DataFrame({"Provider Name": ["Sunny Days ADC"], "Street": ["1 A St"],
                                                       "City": ["Miami"], "Zip": ["33101"], "Capacity": ["60"]}))
    (tmp_path / "ME").mkdir()
    _write(tmp_path / "ME", "group_homes.csv", pd.DataFrame({"Name": ["Casa Home 1"], "Address": ["7 Warren Ave"],
                                                            "City": ["Portland"], "Zip": ["04103"], "Beds": ["4"]}))
    _write(tmp_path, "notes.csv", pd.DataFrame({"foo": [1]}))
    _write(tmp_path, "TX_bad.csv", pd.DataFrame({"Facility Name": ["x"], "Nothing": ["y"]}))
    r = merge_rosters(tmp_path)
    assert sorted(r["state"]) == ["FL", "ME"]
    out = capsys.readouterr().out
    assert "notes.csv" in out and "TX_bad.csv" in out


def test_capacity_by_npi_address_then_name(tmp_path: Path):
    roster = pd.DataFrame({
        "state": ["FL", "FL", "FL", "ME"], "facility_name": ["A", "A", "B", "C"],
        "name_key": ["SUNNY DAYS", "SUNNY DAYS", "OTHER PLACE", "CASA"],
        "addr_line1": "", "city": "", "zip": "",
        "addr_key": ["1 A ST MIAMI FL 33101", "1 A ST MIAMI FL 33101", "2 B ST MIAMI FL 33102", "7 WARREN AVE PORTLAND ME 04103"],
        "license_type": ["adult day", "adult day", "adult day", "group home"],
        "license_number": "", "status": "", "capacity": [60, 80, 30, 4], "source_file": "x"})
    pdim = pd.DataFrame({"npi": ["1000000001", "1000000002", "1000000003"],
                         "addr_key": ["1 A ST MIAMI FL 33101", "", ""],
                         "name_key": ["SUNNY DAYS", "CASA", "NOBODY"], "addr_state": ["FL", "ME", "ME"]})
    m = capacity_by_npi(roster, pdim).set_index("npi")
    assert m.loc["1000000001", "licensed_capacity"] == 80 and m.loc["1000000001", "match_basis"] == "address"
    assert m.loc["1000000002", "licensed_capacity"] == 4 and m.loc["1000000002", "match_basis"] == "name+state"
    assert "1000000003" not in m.index


def test_merge_licensed_takes_max_and_labels_source():
    beds = pd.Series({"1": 100.0, "2": float("nan"), "3": float("nan")})
    lic = pd.DataFrame({"npi": ["1", "2"], "licensed_capacity": [120, 16]})
    out, src = merge_licensed(beds, lic)
    assert out["1"] == 120 and src["1"] == "pos+licensed"
    assert out["2"] == 16 and src["2"] == "licensed"
    assert out["3"] != out["3"] and src["3"] == ""
