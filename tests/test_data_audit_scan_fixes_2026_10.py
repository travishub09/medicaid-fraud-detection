"""The first real scan (2026-10-21, VM) and the scanner classes it taught: the
deactivation edition picker, adapter-probe hooks, the sibling-file rule, any-of
critical groups, the POS lab exemption, two-column reference tables."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd

from src import data_audit as da
from src.ingest_cms.nppes_deactivation import edition_of, pick_deactivation_file


def _deact_zip(p: Path, stamp: str, banner: str) -> None:
    df = pd.DataFrame([[banner, None], ["NPI", "NPPES Deactivation Date"], ["1234567893", "06/01/2026"]])
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(f"NPPES Deactivated NPI Report {stamp}.xlsx", buf.getvalue())


def test_deactivation_edition_and_newest_pick(tmp_path):
    d = tmp_path / "nppes_deactivation"
    d.mkdir()
    _deact_zip(d / "NPPES_Deactivated_NPI_Report_060826_V2.zip", "20260608", "NPPES Deactivated Records as of Jun 08 2026")
    _deact_zip(d / "NPPES_Deactivated_NPI_Report_091426_V2.zip", "20260914", "NPPES Deactivated Records as of Sep 14 2026")
    _deact_zip(d / "deactivation_20260608.zip", "20260608", "NPPES Deactivated Records as of Jun 08 2026")
    assert edition_of(d / "NPPES_Deactivated_NPI_Report_091426_V2.zip") == "2026-09-14"
    assert edition_of(d / "deactivation_20260608.zip") == "2026-06-08"
    # banner only (no stamp anywhere in the names)
    _deact_zip(d / "report.zip", "latest", "NPPES Deactivated Records as of Oct 05 2026")
    assert edition_of(d / "report.zip") == "2026-10-05"
    picked, ed, listing = pick_deactivation_file(d)
    assert picked.name == "report.zip" and ed == "2026-10-05" and len(listing) == 4
    (d / "report.zip").unlink()
    picked, ed, _ = pick_deactivation_file(d)
    assert picked.name == "NPPES_Deactivated_NPI_Report_091426_V2.zip" and ed == "2026-09-14"


def test_scanner_reads_banner_zip_through_the_adapter(tmp_path):
    pre = tmp_path / "preclean" / "nppes_deactivation"
    pre.mkdir(parents=True)
    _deact_zip(pre / "NPPES_Deactivated_NPI_Report_091426_V2.zip", "20260914", "NPPES Deactivated Records as of Sep 14 2026")
    rep = da.run(tmp_path, None, None, counts=False, only={"nppes_deactivation"})
    s = rep["sources"][0]
    f = rep["files"][0]
    assert s["contract"] == "OK", s["issues"]
    assert f["resolved"] == {"npi": "NPI", "deactivation_date": "NPPES Deactivation Date"}
    assert "adapter's own loader" in f["note"]


def test_sibling_file_is_listed_not_graded(tmp_path):
    d = tmp_path / "preclean" / "fda_ndc"
    d.mkdir(parents=True)
    (d / "product.txt").write_text("PRODUCTID\tPRODUCTNDC\tPROPRIETARYNAME\tNONPROPRIETARYNAME\tLABELERNAME\tSUBSTANCENAME\tDEASCHEDULE\nx\t0001-0001\tA\tb\tACME\ts\t\n")
    (d / "package.txt").write_text("PRODUCTID\tPRODUCTNDC\tNDCPACKAGECODE\tPACKAGEDESCRIPTION\nx\t0001-0001\t0001-0001-01\tbox\n")
    rep = da.run(tmp_path, None, None, counts=False, only={"fda_ndc"})
    s = rep["sources"][0]
    assert s["contract"] == "OK", s["issues"]
    notes = {f["file"]: f.get("note", "") for f in rep["files"]}
    assert notes["package.txt"].startswith("not this adapter")


def test_hospice_date_range_satisfies_end_date_and_pos_lab_file_is_exempt(tmp_path):
    fac = tmp_path / "preclean" / "facility"
    fac.mkdir(parents=True)
    (fac / "Hospice_Provider_Nov2023.csv").write_text(
        "CMS Certification Number (CCN),Measure Code,Measure Name,Score,Measure Date Range\n"
        "010001,H_001_01_OBSERVED,x,12.3,04/01/2022 - 03/31/2023\n")
    pos = tmp_path / "preclean" / "pos"
    pos.mkdir()
    (pos / "POS_File_CLIA_Q4_2023.csv").write_text("PRVDR_NUM,GNRL_FAC_TYPE_CD,STATE_CD\n01D000,22,AL\n")
    (pos / "POS_File_Hospital_Non_Hospital_Facilities_Q4_2023.csv").write_text("PRVDR_NUM,BED_CNT,GNRL_FAC_TYPE_CD,STATE_CD\n010001,20,01,AL\n")
    rep = da.run(tmp_path, "2023-12", None, counts=False, only={"hospice", "pos"})
    by = {s["key"]: s for s in rep["sources"]}
    assert by["hospice"]["contract"] == "OK", by["hospice"]["issues"]
    assert by["pos"]["contract"] == "OK", by["pos"]["issues"]
    assert sorted(by["pos"]["picked"]) == ["POS_File_CLIA_Q4_2023.csv", "POS_File_Hospital_Non_Hospital_Facilities_Q4_2023.csv"]


def test_two_column_reference_table_and_warn_keyword_headers(tmp_path):
    ht = tmp_path / "preclean" / "hcpcs_time"
    ht.mkdir(parents=True)
    (ht / "hcpcs_minutes_2023.csv").write_text("hcpcs,minutes\n99213,15\n")
    w = tmp_path / "preclean" / "warn"
    w.mkdir()
    (w / "warn_NC_202610.csv").write_text(
        "County,Warn Number,Date of Notice,Date Received by NC,Effective Date,WARN Notice: WARN Notice Name,"
        "WARN notice type,Type of layoff or closure,Number affected at this location,Address 1,City\n"
        "Wake,1,01/07/2026,01/08/2026,03/01/2026,Acme Health LLC,WARN,Closure,63,1 Main,Raleigh\n")
    rep = da.run(tmp_path, None, None, counts=False, only={"hcpcs_time", "warn"})
    by = {s["key"]: s for s in rep["sources"]}
    assert not by["hcpcs_time"]["issues"], by["hcpcs_time"]["issues"]
    assert by["warn"]["contract"] == "OK", by["warn"]["issues"]


def test_rescan_rules(tmp_path):
    """The 2021-12 re-scan (bundle 348): hospice mirrors pick_edition (the earliest release
    after the freeze is the right one), the overshoot check reads only the adapter's date
    column (POS certification expiries run to 2027 by nature), and the date cache."""
    fac = tmp_path / "preclean" / "facility"
    fac.mkdir(parents=True)
    (fac / "Hospice_Provider_Feb2022.csv").write_text(
        "CMS Certification Number (CCN),Measure Code,Measure Name,Score,Start Date,End Date\n"
        "010001,H_001,x,1.0,01/01/2021,12/31/2021\n")
    (fac / "Hospice_Provider_Nov2023.csv").write_text(
        "CMS Certification Number (CCN),Measure Code,Measure Name,Score,Measure Date Range\n"
        "010001,H_001,x,1.0,04/01/2022 - 03/31/2023\n")
    pos = tmp_path / "preclean" / "pos"
    pos.mkdir()
    (pos / "POS_File_CLIA_Q4_2021.csv").write_text("PRVDR_NUM,GNRL_FAC_TYPE_CD,STATE_CD,CRTFCTN_DT,TRMNTN_EXPRTN_DT\n01D000,22,AL,2015-01-01,2027-03-26\n")
    rep = da.run(tmp_path, "2021-12", None, counts=False, only={"hospice", "pos"})
    by = {s["key"]: s for s in rep["sources"]}
    assert by["hospice"]["picked"] == ["Hospice_Provider_Feb2022.csv"] and by["hospice"]["freeze"].startswith("OK")
    assert not by["pos"]["issues"], by["pos"]["issues"]
    # the cache: the second run reads the dates without probing
    da._DATE_CACHE.clear()
    da.run(tmp_path, "2021-12", None, counts=False, only={"pos"})
    assert da._DATE_CACHE
    rep2 = da.run(tmp_path, "2021-12", None, counts=False, only={"pos"})
    assert any("cache" in f.get("note", "") for f in rep2["files"])
