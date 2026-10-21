"""data_audit: headers graded against the adapters' own column maps, editions against the freeze."""
from __future__ import annotations

from pathlib import Path

from src import data_audit as da


def _tree(tmp_path: Path) -> Path:
    pre = tmp_path / "preclean"
    for d in ("partb", "pos", "nadac", "facility"):
        (pre / d).mkdir(parents=True)
    (pre / "caught.csv").write_text(
        "LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,GENERAL,SPECIALTY,UPIN,NPI,DOB,ADDRESS,CITY,STATE,ZIP,"
        "EXCLTYPE,EXCLDATE,REINDATE,WAIVERDATE,WVRSTATE\n"
        "DOE,JOHN,,,MD,SURG,,1234567893,19600101,2 B ST,NY,NY,10001,1128b4,20220115,0,0,\n")
    (pre / "partb" / "partb_2019.csv").write_text(
        "Rndrng_NPI,HCPCS_Cd,Tot_Srvcs,Tot_Benes,Avg_Mdcr_Alowd_Amt\n1234567893,99213,10,5,50\n")
    (pre / "pos" / "POS_File_Q4_2021.csv").write_text("PRVDR_NUM,BED_CNT,GNRL_FAC_TYPE_CD,STATE_CD\n010001,20,01,AL\n")
    (pre / "pos" / "POS_File_Q1_2023.csv").write_text("PRVDR_NUM,BED_CNT,GNRL_FAC_TYPE_CD,STATE_CD\n010001,22,01,AL\n")
    # a renamed column: the adapter's map knows no 'Drug'
    (pre / "nadac" / "nadac_2021.csv").write_text("Drug,Price Per Unit,Effective_Date\nX,1.0,2021-01-01\n")
    (pre / "facility" / "pbj_daily_staffing_2021Q4.csv").write_text(
        "PROVNUM,STATE,WorkDate,MDScensus,Hrs_RN,Hrs_LPN,Hrs_CNA\n010001,AL,20211001,50,8,4,12\n")
    return tmp_path


def test_contract_and_freeze_verdicts(tmp_path):
    root = _tree(tmp_path)
    rep = da.run(root, "2021-12", None, counts=True, only={"leie", "partb", "pos", "nadac", "pbj", "nppes"})
    by = {s["key"]: s for s in rep["sources"]}
    assert by["nppes"]["contract"] == "MISSING" and "no file on disk" in by["nppes"]["issues"]
    assert by["leie"]["contract"] == "OK" and by["leie"]["freeze"] == "OK (row-filtered)"
    assert by["partb"]["contract"] == "OK"
    # the vintage picker: Q4 2021 read, Q1 2023 skipped under the 2021-12 freeze
    assert by["pos"]["picked"] == ["POS_File_Q4_2021.csv"] and by["pos"]["skipped"] == ["POS_File_Q1_2023.csv"]
    # the renamed NADAC column is caught against NADAC_COLS, not a hand-written table
    assert by["nadac"]["contract"] == "FIELDS MISSING" and any("ndc" in i for i in by["nadac"]["issues"])
    assert by["pbj"]["contract"] == "OK"
    leie = [f for f in rep["files"] if f["source"] == "leie"][0]
    assert leie["rows"] == 1 and leie["resolved"]["excl_date"] == "EXCLDATE"
    assert leie["date_range"]["EXCLDATE"] == ("2022-01-15", "2022-01-15")
    md = da.render(rep)
    assert "FIELDS MISSING" in md and "POS_File_Q1_2023.csv" in md


def test_resolve_contract_is_tolerant():
    cols = {"ccn": ["PRVDR_NUM", "CMS Certification Number (CCN)"], "beds": ["BED_CNT"]}
    r = da.resolve_contract(["prvdr num", "Bed_Cnt"], cols)
    assert r == {"ccn": "prvdr num", "beds": "Bed_Cnt"}
    assert da.resolve_contract(None, cols) == {}
