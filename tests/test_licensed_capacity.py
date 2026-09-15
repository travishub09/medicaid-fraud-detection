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


def test_ny_hfis_pair_joins_beds_to_addresses_on_facility_id(tmp_path: Path, capsys):
    # General Information: addresses, no capacity -> the link table
    _write(tmp_path, "NY_facilities.csv", pd.DataFrame({
        "Facility ID": ["101", "102", "103"],
        "Facility Name": ["Sunrise Adult Home", "Metro Hospital", "Empty Site"],
        "Short Description": ["ACF", "HOSP", "ACF"], "Description": ["Adult Home", "Hospital", "Adult Home"],
        "Facility Address 1": ["10 Main St", "1 Hospital Dr", "3 Nowhere Ln"],
        "Facility City": ["Albany", "Buffalo", "Utica"], "Facility State": ["NY", "NY", "NY"],
        "Facility Zip Code": ["12207", "14203", "13501"], "Operator Name": ["Op A", "Op B", "Op C"]}))
    # Certification Information: long format, BED + SERVICE rows per Facility ID
    _write(tmp_path, "NY_certification.csv", pd.DataFrame({
        "Facility ID": ["101", "101", "101", "102", "102", "999"],
        "Facility Name": ["Sunrise Adult Home"] * 3 + ["Metro Hospital"] * 2 + ["Orphan Beds"],
        "Attribute Type": ["BED", "BED", "SERVICE", "BED", "SERVICE", "BED"],
        "Attribute Value": ["Adult Home Beds", "Assisted Living Program", "Adult Day Care", "Medical/Surgical",
                            "Emergency Department", "Adult Home Beds"],
        "Measure Value": ["40", "20", "1", "250", "1", "12"]}))
    r = merge_rosters(tmp_path)
    by = r.set_index("facility_id")
    assert by.loc["101", "capacity"] == 60 and by.loc["101", "addr_key"] == "10 MAIN ST ALBANY NY 12207"
    assert by.loc["101", "license_type"] == "Adult Home"
    assert by.loc["102", "capacity"] == 250 and by.loc["102", "city"] == "Buffalo"
    assert "103" not in by.index                                 # no beds -> not a roster row
    assert by.loc["999", "capacity"] == 12 and by.loc["999", "addr_key"] == ""   # beds without a directory row still count by name
    assert "address directory for NY" in capsys.readouterr().out


def test_nj_open_data_headers_resolve(tmp_path: Path):
    p = _write(tmp_path, "NJ_acute_care.csv", pd.DataFrame({
        "facid": ["7001"], "facility_type": ["General Acute Care Hospital"], "lic": ["NJ-1"],
        "licensed_name": ["Garden State Medical Center"], "address": ["100 Route 1"], "fac_addr_2": [""],
        "fac_city": ["Newark"], "fac_st": ["NJ"], "zip": ["07102"], "county": ["Essex"],
        "hospbedtotal": ["312"], "licensed_owner": ["GSMC LLC"]}))
    r = load_roster(p, "nj")
    row = r.iloc[0]
    assert row["capacity"] == 312 and row["addr_key"] == "100 ROUTE 1 NEWARK NJ 07102"
    assert row["license_type"] == "General Acute Care Hospital" and row["license_number"] == "NJ-1"


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
