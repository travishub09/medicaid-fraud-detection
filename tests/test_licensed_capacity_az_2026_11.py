"""The Arizona DD group-home roster (the analyst, 2026-11-04): the capacity column
carries the statutory maximum (6, A.R.S. 36-551) on every row and the agency's own
figure sits in capacity_adhs for the homes it lists (mostly 3). The agency's number is
the licence; the statute is a ceiling the rest keep."""
import pandas as pd

from src.ingest_cms.licensed_capacity import load_roster


def test_agency_capacity_column_overrides_the_statutory_ceiling(tmp_path, capsys):
    p = tmp_path / "AZ_dd_group_homes.csv"
    pd.DataFrame({
        "facility_name": ["DESERT HOME A", "DESERT HOME B", "DESERT HOME C"],
        "street_address": ["1 MAIN ST", "2 MAIN ST", "3 MAIN ST"],
        "city": ["PHOENIX", "MESA", "TUCSON"], "zip": ["85001", "85201", "85701"],
        "license_type": ["DD GROUP HOME"] * 3, "license_number": ["GH1", "GH2", "GH3"],
        "capacity": ["6", "6", "6"],                       # statutory maximum on every row
        "capacity_adhs": ["3", "", "4"],                   # the agency's own figure where it lists one
        "licensee_operator": ["DESERT HOME A", "DESERT HOME B", "DESERT HOME C"],
    }).to_csv(p, index=False)
    r = load_roster(p, "AZ").set_index("facility_name")
    assert r.loc["DESERT HOME A", "capacity"] == 3
    assert r.loc["DESERT HOME B", "capacity"] == 6             # no agency figure: the ceiling stays
    assert r.loc["DESERT HOME C", "capacity"] == 4
    out = capsys.readouterr().out
    assert "capacity_adhs" in out and "2 of 3 rows" in out


def test_roster_without_an_agency_column_is_unchanged(tmp_path):
    p = tmp_path / "AZ_adult_day.csv"
    pd.DataFrame({"facility_name": ["DAY A"], "street_address": ["9 ELM ST"], "city": ["YUMA"], "zip": ["85364"],
                  "license_type": ["ADULT DAY"], "capacity": ["40"]}).to_csv(p, index=False)
    assert load_roster(p, "AZ").iloc[0]["capacity"] == 40
