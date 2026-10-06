"""Raw HCRIS A-8-1 / S-2 extraction on a synthetic SNF10FY zip (header-less CSVs)."""
import io
import zipfile

import pandas as pd

from src.ingest_cms.hcris_related_party import (a81_summary, load_hcris_year,
                                                related_party_rows)


def _zip(tmp_path):
    rpt = ("1001,4,365162,1528065737,1,01/01/2023,12/31/2023,05/30/2024,N,Y,,07001,3,06/01/2024,F,,,06/01/2024\n"
           "1002,4,105555,1999999999,1,01/01/2023,12/31/2023,05/30/2024,N,Y,,07001,3,06/01/2024,F,,,06/01/2024\n")
    alpha = ("1001,A810000,00100,00200,WV HEALTHCARE MANAGEMENT LLC\n"
             "1001,A810000,00100,00600,MANAGEMENT SERVICES\n"
             "1001,S200001,00100,00100,WESLEYAN VILLAGE\n"
             "1002,A810000,00100,00200,SOMEONE ELSE LLC\n")
    nmrc = ("1001,A810000,00100,00300,100\n"
            "1001,A810000,00100,00400,812345.5\n"
            "1001,A000000,00500,00100,999\n"
            "1002,A810000,00100,00400,5\n")
    p = tmp_path / "SNF10FY2023.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("snf10_2023_RPT.CSV", rpt)
        z.writestr("snf10_2023_ALPHA.CSV", alpha)
        z.writestr("snf10_2023_NMRC.CSV", nmrc)
    return p


def test_zip_loads_and_filters_by_ccn_and_state(tmp_path):
    rpt, cells = load_hcris_year(_zip(tmp_path))
    assert len(rpt) == 2 and len(cells) == 8
    oh = related_party_rows(rpt, cells, state="OH")
    assert set(oh["ccn"]) == {"365162"}
    assert "A000000" not in set(oh["worksheet"])             # only A-8-1 and S-2 kept
    assert (oh["npi"] == "1528065737").all()
    by_ccn = related_party_rows(rpt, cells, ccns={"105555"})
    assert set(by_ccn["ccn"]) == {"105555"} and len(by_ccn) == 2


def test_a81_summary_keeps_column_numbers_in_headers(tmp_path):
    rpt, cells = load_hcris_year(_zip(tmp_path))
    rows = related_party_rows(rpt, cells, state="OH")
    s = a81_summary(rows)
    assert len(s) == 1
    r = s.iloc[0]
    assert r["col_00200"] == "WV HEALTHCARE MANAGEMENT LLC"
    assert r["col_00600"] == "MANAGEMENT SERVICES"
    assert r["col_00400"] == "812345.5"


def test_extracted_folder_works_like_the_zip(tmp_path):
    p = _zip(tmp_path)
    d = tmp_path / "SNF10FY2023"
    d.mkdir()
    with zipfile.ZipFile(p) as z:
        for n in z.namelist():
            (d / n).write_bytes(z.read(n))
    rpt, cells = load_hcris_year(d)
    assert len(rpt) == 2
    assert related_party_rows(rpt, cells, state="FL")["ccn"].tolist() == ["105555", "105555"]
