"""
test_pbj_therapy_2026_10.py — the PBJ therapy-hours check behind a resident's
"did not get OT": column discovery (totals, never the emp/ctr components twice),
the per-CCN DuckDB filter, the three session verdicts, the nurse file skipping
itself, and the CLI end to end.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd


def _nonnurse_csv(path: Path) -> Path:
    rows = []
    for ccn in ("365162", "365999"):
        for d, ot, pt in [("20250902", 6.5, 4.0), ("20250903", 0.0, 3.0), ("20250906", 0.0, 0.0), ("20250908", 2.0, 0.0)]:
            rows.append({"PROVNUM": ccn, "PROVNAME": "HOME " + ccn, "STATE": "OH", "CY_Qtr": "2025Q3",
                         "WorkDate": d, "MDScensus": 100, "Hrs_Admin": 8.0,
                         "Hrs_OT": ot if ccn == "365162" else 9.0, "Hrs_OT_emp": ot, "Hrs_OT_ctr": 0.0,
                         "Hrs_OTasst": 1.0 if d == "20250908" else 0.0,
                         "Hrs_PT": pt, "Hrs_PT_emp": pt, "Hrs_PT_ctr": 0.0, "Hrs_PTasst": 0.0,
                         "Hrs_SLP": 1.5})
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _nurse_csv(path: Path) -> Path:
    pd.DataFrame({"PROVNUM": ["365162"], "WorkDate": ["20250902"], "MDScensus": [100],
                  "Hrs_RN": [10.0], "Hrs_LPN": [20.0], "Hrs_CNA": [60.0]}).to_csv(path, index=False)
    return path


def test_discover_totals_not_components():
    from src.ingest_cms.pbj_therapy import discover_therapy_columns
    got = discover_therapy_columns(["PROVNUM", "Hrs_OT", "Hrs_OT_emp", "Hrs_OT_ctr", "Hrs_OTasst",
                                    "Hrs_PT_emp", "Hrs_PT_ctr", "Hrs_SLP", "Hrs_RN"])
    assert got["ot"] == ["Hrs_OT", "Hrs_OTasst"]           # totals only
    assert got["pt"] == ["Hrs_PT_emp", "Hrs_PT_ctr"]       # components when no total exists
    assert got["slp"] == ["Hrs_SLP"]


def test_facility_days_and_verdicts(tmp_path):
    from src.ingest_cms.pbj_therapy import compare_sessions, facility_days, summarize
    f = _nonnurse_csv(tmp_path / "PBJ_Daily_Non-Nurse_Staffing_2025Q3.csv")
    n = _nurse_csv(tmp_path / "pbj_daily_staffing_2025Q3.csv")
    days = facility_days([f, n], "365162")
    assert len(days) == 4 and (days["ot_hours"].tolist() == [6.5, 0.0, 0.0, 3.0])   # OT + OTasst on 9/8; other CCN excluded
    assert days.loc[days["date"] == pd.Timestamp("2025-09-06"), "is_weekend"].item()
    sessions = pd.DataFrame({"date": ["2025-09-02", "2025-09-03", "2025-09-04", "2025-09-08"],
                             "discipline": ["ot", "ot", "ot", "pt"], "units": [1, 2, 1, 1]})
    cmp = compare_sessions(days, sessions)
    assert cmp["verdict"].tolist() == ["hours_reported", "no_hours_reported", "day_not_reported", "no_hours_reported"]
    s = summarize(days, cmp)
    assert s["sessions_no_hours"] == 2 and s["sessions_day_not_reported"] == 1 and s["ot_days_zero"] == 2
    assert abs(s["ot_hours_per_resident_day"] - 9.5 / 400) < 1e-9


def test_cli_end_to_end(tmp_path):
    f = _nonnurse_csv(tmp_path / "nonnurse.csv")
    sess = tmp_path / "sessions.csv"
    pd.DataFrame({"date": ["2025-09-03"], "discipline": ["ot"], "units": [2]}).to_csv(sess, index=False)
    out = tmp_path / "rep" / "PBJ_THERAPY.md"
    r = subprocess.run([sys.executable, "-m", "src.ingest_cms.pbj_therapy", "--files", str(f), "--ccn", "365162",
                        "--sessions", str(sess), "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    md = out.read_text()
    assert "ZERO hours" in md and "no_hours_reported" in md and "Innocent explanations" in md
    assert (tmp_path / "rep" / "PBJ_THERAPY.sessions.csv").exists()


def test_ccn_lookup_rows(tmp_path):
    from src.ingest_cms.ccn_lookup import rows_for_ccn
    pd.DataFrame({"CMS Certification Number (CCN)": ["365162", "36 5162", "365999"],
                  "Deficiency Tag Number": ["0641", "0636", "0641"]}).to_csv(tmp_path / "NH_HealthCitations_Oct2026.csv", index=False)
    pd.DataFrame({"Provider Name": ["A"]}).to_csv(tmp_path / "no_ccn.csv", index=False)
    got = rows_for_ccn(tmp_path / "NH_HealthCitations_Oct2026.csv", "365162")
    assert len(got) == 2 and set(got["Deficiency Tag Number"]) == {"0641", "0636"}
    assert rows_for_ccn(tmp_path / "no_ccn.csv", "365162") is None
    r = subprocess.run([sys.executable, "-m", "src.ingest_cms.ccn_lookup", "--dir", str(tmp_path), "--ccn", "365162"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "NH_HealthCitations_Oct2026.csv — 2 rows" in r.stdout


def test_zip_download_is_read(tmp_path):
    import zipfile
    from src.ingest_cms.pbj_therapy import facility_days
    csv = _nonnurse_csv(tmp_path / "inner.csv")
    z = tmp_path / "PBJ_Daily_Non_Nurse_Staffing_Q3_2025.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(csv, "PBJ_Daily_Non_Nurse_Staffing_Q3_2025.csv")
    days = facility_days([z], "365162", tmp_dir=tmp_path / "_extracted")
    assert len(days) == 4 and (tmp_path / "_extracted" / "PBJ_Daily_Non_Nurse_Staffing_Q3_2025.csv").exists()
    r = subprocess.run([sys.executable, "-m", "src.ingest_cms.pbj_therapy", "--pbj-dir", str(tmp_path), "--ccn", "365162"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "reported days" in r.stdout


def test_utf8_and_latin1_editions_both_read(tmp_path):
    from src.ingest_cms.pbj_therapy import facility_days
    base = pd.read_csv(_nonnurse_csv(tmp_path / "base.csv"), dtype=str)
    base["PROVNAME"] = "HOME é 2025"                       # a multibyte UTF-8 name (the 2025 editions)
    base.to_csv(tmp_path / "utf8.csv", index=False, encoding="utf-8")
    base["PROVNAME"] = "HOME é 2021"
    base.to_csv(tmp_path / "latin1.csv", index=False, encoding="latin-1")   # a CP1252-style byte that fails UTF-8
    assert len(facility_days([tmp_path / "utf8.csv"], "365162")) == 4
    assert len(facility_days([tmp_path / "latin1.csv"], "365162")) == 4


def test_missing_discipline_reads_not_in_file(tmp_path):
    from src.ingest_cms.pbj_therapy import facility_days, summarize, to_markdown
    raw = pd.read_csv(_nonnurse_csv(tmp_path / "base.csv"), dtype=str).drop(columns=["Hrs_SLP"])
    raw.to_csv(tmp_path / "noslp.csv", index=False)
    days = facility_days([tmp_path / "noslp.csv"], "365162")
    s = summarize(days, None)
    assert s["slp_in_file"] is False and s["ot_in_file"] is True
    md = to_markdown("365162", days, None, s)
    assert "| SLP | not in file |" in md
