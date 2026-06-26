"""
test_nppes_deactivation_native.py — load the native CMS Deactivated NPI Report.

CMS ships the Deactivated NPI Report as a .zip containing an Excel file (NPI +
NPPES Deactivation Date) with a title/notice banner above the header row.
``load_deactivation`` must unzip in memory, find the real header row, and hand a
clean frame to ``deactivated_npis`` — so the raw download drops in as-is.
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

pytest.importorskip("openpyxl")


def _report_xlsx_bytes() -> bytes:
    """An Excel report with a 2-row banner, then NPI / NPPES Deactivation Date."""
    body = pd.DataFrame({"NPI": ["1003000100", "1003000126", "bad"],
                         "NPPES Deactivation Date": ["2021-03-01", "2022-07-15", ""]})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        pd.DataFrame({"NPPES Deactivated NPI Report — generated 2024": []}).to_excel(
            xl, sheet_name="Deactivated", index=False)
        body.to_excel(xl, sheet_name="Deactivated", index=False, startrow=2)
    return buf.getvalue()


def test_load_deactivation_from_zip_of_xlsx(tmp_path):
    from src.ingest_cms.nppes_deactivation import load_deactivation, deactivated_npis
    zp = tmp_path / "NPPES_Deactivated_NPI_Report_060124.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("Deactivated_NPI_Report.xlsx", _report_xlsx_bytes())

    raw = load_deactivation(str(zp))
    assert "NPI" in raw.columns and "NPPES Deactivation Date" in raw.columns
    deact, quarantined = deactivated_npis(raw)
    assert set(deact["npi"]) == {"1003000100", "1003000126"}    # valid NPIs only
    assert quarantined == 1                                     # the "bad" id
    assert deact.set_index("npi").loc["1003000100", "deactivation_date"] \
        == pd.Timestamp("2021-03-01")


def test_load_deactivation_csv_passthrough(tmp_path):
    from src.ingest_cms.nppes_deactivation import load_deactivation, deactivated_npis
    p = tmp_path / "deactivation.csv"
    pd.DataFrame({"NPI": ["1003000100"],
                  "Deactivation Date": ["2020-01-01"]}).to_csv(p, index=False)
    deact, _ = deactivated_npis(load_deactivation(str(p)))
    assert list(deact["npi"]) == ["1003000100"]
