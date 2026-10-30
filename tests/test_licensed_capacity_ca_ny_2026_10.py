"""Josh's two rosters (2026-10-29): the California CCLD 'Community Care Licensing
Facilities' export (RES_STREET_ADDR / RES_CITY / RES_ZIP_CODE, a numeric TYPE beside
FAC_TYPE_DESC, FAC_NBR) and the New York 'Adult Care Facility Directory' (Number of
Beds, Certificate Number). Before this the CA addresses resolved to nothing, so every
California row would have missed the NPI address join."""
import pandas as pd

from src.ingest_cms.licensed_capacity import load_roster


def test_california_ccld_headers_resolve(tmp_path):
    p = tmp_path / "CA_adult_senior_care.csv"
    pd.DataFrame({
        "FAC_LATITUDE": ["0.0"], "FAC_LONGITUDE": ["0.0"], "FAC_NBR": ["198320732"], "TYPE": ["735"],
        "PROGRAM_TYPE": ["ADULT AND SENIOR"], "STATUS": ["3"], "CLIENT_SERVED": ["940"],
        "CAPACITY": ["5"], "NAME": ["PACIFIC INTEGRITY HOME HEALTH   "],
        "RES_STREET_ADDR": ["1544 W 215TH ST   "], "RES_CITY": ["TORRANCE  "], "RES_STATE": ["CA"],
        "RES_ZIP_CODE": ["90501"], "FAC_PHONE_NBR": ["7145986010"], "FAC_CO_NBR": ["19"],
        "COUNTY": [""], "FAC_DO_DESC": ["EL SEGUNDO ASC"], "FAC_TYPE_DESC": ["ADULT RESIDENTIAL   "],
        "ObjectId": ["21"], "x": ["0"], "y": ["0"],
    }).to_csv(p, index=False)
    r = load_roster(p, "CA")
    row = r.iloc[0]
    assert row["capacity"] == 5
    assert row["addr_line1"] == "1544 W 215TH ST" and row["city"] == "TORRANCE" and row["zip"] == "90501"
    assert row["addr_key"] != ""
    assert row["license_type"].strip() == "ADULT RESIDENTIAL"      # the description, not the code 735
    assert row["license_number"] == "198320732"


def test_new_york_adult_care_directory_headers_resolve(tmp_path):
    p = tmp_path / "NY_adult_care_facilities.csv"
    pd.DataFrame({
        "Facility Name": ["Ashton Place"], "Address": ["190 Ashton Court"], "County": ["Ontario"],
        "City": ["Clifton Springs"], "Zip": ["14432"], "Phone": [""], "Fax": [""], "Facility ID": ["14916"],
        "Operator": ["Clifton Park Associates, LLC"], "Certificate Number": ["530-S-005"],
        "Number of Beds": ["60"], "Assisted Living Program Beds": [""],
        "Assisted Living Residence (ALR) Beds": [""], "Enhanced ALR Beds": [""],
        "Special Needs ALR Beds": [""], "Type": ["Enriched Housing Program"], "Classification": ["LLC"],
        "Regional Office": ["Western Regional Office - Buffalo"], "Location": [""],
    }).to_csv(p, index=False)
    r = load_roster(p, "NY")
    row = r.iloc[0]
    assert row["capacity"] == 60 and row["facility_id"] == "14916"
    assert row["license_number"] == "530-S-005"
    assert row["license_type"] == "Enriched Housing Program"
    assert row["addr_key"].startswith("190 ASHTON COURT")


def test_operator_capacity_takes_the_licensee_name_sum_when_wider():
    """Three six-bed homes under one brand name; the NPI's address matches one of them.
    The operator's capacity is the three homes (18), basis address+name."""
    from src.ingest_cms.licensed_capacity import capacity_by_npi
    roster = pd.DataFrame({
        "state": ["CA"] * 3 + ["CA"], "name_key": ["RIGHT CHOICE SENIOR LIVING"] * 3 + ["OTHER HOME"],
        "addr_key": ["1 A ST X CA 90001", "2 B ST X CA 90001", "3 C ST X CA 90001", "9 Z ST X CA 90001"],
        "capacity": [6, 6, 6, 40], "license_type": ["RESIDENTIAL-ELDERLY"] * 4,
    })
    pdim = pd.DataFrame({"npi": ["1234567893", "1111111116"],
                         "name_key": ["RIGHT CHOICE SENIOR LIVING", "OTHER HOME"],
                         "addr_key": ["1 A ST X CA 90001", "9 Z ST X CA 90001"], "addr_state": ["CA", "CA"]})
    out = capacity_by_npi(roster, pdim).set_index("npi")
    assert out.loc["1234567893", "licensed_capacity"] == 18
    assert out.loc["1234567893", "match_basis"] == "address+name"
    assert out.loc["1234567893", "n_sites"] == 3
    assert out.loc["1111111116", "licensed_capacity"] == 40 and out.loc["1111111116", "match_basis"] == "address"


def test_county_programme_is_a_government_name():
    from src.model_a.theory_screens import is_government_name
    assert is_government_name("SHASTA COUNTY ALCOHOL AND DRUG PROGRAMS")
    assert is_government_name("LOS ANGELES COUNTY DEPARTMENT OF PUBLIC HEALTH")
    assert not is_government_name("RIGHT CHOICE SENIOR LIVING LLC")
    assert not is_government_name("COUNTY LINE HOME CARE LLC")
