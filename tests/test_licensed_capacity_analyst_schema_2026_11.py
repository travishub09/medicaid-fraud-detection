"""The analyst's rosters (2026-11-07): the server's first load skipped 18 of 43 files because
their headers were not in the alias lists. Three header families, each planted here:
the normalised Arizona export (AccountName / Street / Capacity / LicenseNumber / Type, used
for AZ, ME, NH and VA), the Missouri adult-day file (ADCNAME / MAXCAP / ADCADDRESSCENTER),
and the Texas HHSC workbooks (a one-cell banner row above 'Facility Name' / 'Total Licensed
Capacity' / 'Physical Address ...')."""
import pandas as pd

from src.ingest_cms.licensed_capacity import load_roster, merge_rosters

AZ_HEADER = ["rundate", "Bureau", "Type", "Subtype", "ExternalFacilityID", "AccountName", "LicenseNumber",
             "EffectiveDate", "ExpirationDate", "IssueDate", "Street", "City", "State", "Zip", "County",
             "Capacity", "Phone", "CCN", "Longitude", "Latitude"]


def _az_row(name, street, city, zip_, cap, typ="Nursing Care Institution", lic="NCI123"):
    return ["2026-11-05", "LTC", typ, "Skilled", "FAC1", name, lic, "2026-01-01", "2027-01-01", "2010-01-01",
            street, city, "AZ", zip_, "Maricopa", cap, "6025550100", "", "-112.0", "33.4"]


def test_normalised_arizona_schema_loads_for_every_state_that_uses_it(tmp_path):
    rows = [_az_row("DESERT CARE CENTER", "100 MAIN ST", "PHOENIX", "85001", "120"),
            _az_row("MESA GROUP HOME", "2 ELM ST", "MESA", "85201", "10", typ="Behavioral Health", lic="BH9")]
    for st in ("AZ", "NH", "VA", "ME"):
        p = tmp_path / f"{st}_nursing_home.csv"
        pd.DataFrame(rows, columns=AZ_HEADER).to_csv(p, index=False)
        r = load_roster(p, st).set_index("facility_name")
        assert r.loc["DESERT CARE CENTER", "capacity"] == 120
        assert r.loc["DESERT CARE CENTER", "addr_line1"] == "100 MAIN ST"
        assert r.loc["DESERT CARE CENTER", "zip"] == "85001"
        assert r.loc["DESERT CARE CENTER", "license_number"] == "NCI123"
        assert r.loc["DESERT CARE CENTER", "license_type"] == "Nursing Care Institution"
        assert r.loc["DESERT CARE CENTER", "state"] == st
        assert r.loc["MESA GROUP HOME", "addr_key"] != ""


def test_sober_living_without_a_street_is_name_match_only(tmp_path):
    p = tmp_path / "AZ_sober_living_home.csv"
    pd.DataFrame({"rundate": ["2026-11-05"], "Bureau": ["BH"], "Type": ["Sober Living Home"], "Subtype": [""],
                  "ExternalFacilityID": ["S1"], "AccountName": ["QUIET HOUSE"], "LicenseNumber": ["SL1"],
                  "EffectiveDate": [""], "ExpirationDate": [""], "IssueDate": [""], "City": ["TUCSON"],
                  "TotalCapacity": ["8"], "Phone": [""], "CCN": [""]}).to_csv(p, index=False)
    r = load_roster(p, "AZ").iloc[0]
    assert r["capacity"] == 8 and r["addr_key"] == "" and r["name_key"] != ""


def test_missouri_adult_day_headers_resolve(tmp_path):
    p = tmp_path / "MO_adult_day_care.csv"
    pd.DataFrame({"REGION": ["1"], "ADCFACID": ["ADC77"], "MAXCAP": ["45"], "ADCNAME": ["SUNRISE ADULT DAY"],
                  "ADCADDRESSCENTER": ["9 OAK AVE"], "ADCCITY": ["ST LOUIS"], "ADCCOUNTY": ["ST LOUIS"],
                  "ADCZIP": ["63101"], "ADCPHONE": [""], "ADCOPEN": ["7:00"], "ADCCLOSE": ["17:00"],
                  "ADC DAYS OF OPERATION": ["M-F"], "ADCHOURSSAT": [""], "ADCDIRECTOR": [""],
                  "PROVIDERNAME": ["SUNRISE LLC"], "PROVIDERADDRESS": [""], "PROVIDERADDRESS2": [""],
                  "PROVIDERCITY": [""], "PROVIDERZIP": [""], "ALZUNIT": ["N"]}).to_csv(p, index=False)
    r = load_roster(p, "MO").iloc[0]
    assert r["capacity"] == 45 and r["facility_name"] == "SUNRISE ADULT DAY"
    assert r["addr_line1"] == "9 OAK AVE" and r["city"] == "ST LOUIS" and r["zip"] == "63101"
    assert r["facility_id"] == "ADC77" and r["addr_key"] != ""


def test_texas_workbook_banner_row_is_skipped(tmp_path, capsys):
    p = tmp_path / "TX_nursing_facilities.xlsx"
    header = ["Facility Name", "Facility ID", "Program Type", "State Region", "County", "Service  Type",
              "License No", "Physical Address", "Physical Address CITY", "Physical Address State",
              "Physical Address Zipcode", "Total Licensed Capacity", "Licensed Only Beds", "Owner_"]
    banner = ["Texas Health and Human Services Commission\nDirectory of Nursing Facilities with an Active "
              "License as of 10/29/2026"] + [None] * (len(header) - 1)
    rows = [["AVIR AT ELKHART", "005034", "Nursing", "04", "ANDERSON", "MEDICARE/MEDICAID", "312857",
             "214 JONES RD", "ELKHART", "TX", "75839", 98, 0, "214 JONES RD OPCO LLC"],
            ["DOGWOOD TRAILS", "102438", "Assisted Living", "04", "ANDERSON", "TYPE B", "312868",
             "1625 W SPRING STREET", "PALESTINE", "TEXAS", "75803", 56, 56, "OPCO LLC"]]
    pd.DataFrame([banner, header] + rows).to_excel(p, index=False, header=False)
    r = load_roster(p, "TX").set_index("facility_name")
    assert r.loc["AVIR AT ELKHART", "capacity"] == 98
    assert r.loc["AVIR AT ELKHART", "addr_line1"] == "214 JONES RD" and r.loc["AVIR AT ELKHART", "zip"] == "75839"
    assert r.loc["AVIR AT ELKHART", "license_number"] == "312857"
    assert r.loc["AVIR AT ELKHART", "license_type"] == "Nursing"          # Program Type, not Service Type
    assert r.loc["DOGWOOD TRAILS", "capacity"] == 56
    assert "header found on row 2" in capsys.readouterr().out


def test_merge_rosters_stacks_the_three_families(tmp_path):
    pd.DataFrame([_az_row("DESERT CARE CENTER", "100 MAIN ST", "PHOENIX", "85001", "120")],
                 columns=AZ_HEADER).to_csv(tmp_path / "AZ_nursing_home.csv", index=False)
    pd.DataFrame({"REGION": ["1"], "ADCFACID": ["A1"], "MAXCAP": ["20"], "ADCNAME": ["DAY ONE"],
                  "ADCADDRESSCENTER": ["1 A ST"], "ADCCITY": ["KANSAS CITY"], "ADCZIP": ["64101"]}
                 ).to_csv(tmp_path / "MO_adult_day_care.csv", index=False)
    m = merge_rosters(tmp_path)
    assert set(m["state"]) == {"AZ", "MO"} and len(m) == 2
