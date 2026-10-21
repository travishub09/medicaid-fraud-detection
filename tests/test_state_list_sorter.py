"""The state-list sorter (2026-10-21): Downloads -> preclean/state_terminations/<ST>_terminations_<yyyymm>."""
from __future__ import annotations

from datetime import date

import pandas as pd

from src.enforcement.state_list_sorter import apply, edition_of, plan, state_of


def test_state_detected_from_prefix_name_or_agency_token():
    assert state_of("PA_Pennsylvania_DHS_Medchk_2026-10-21.csv") == "PA"
    assert state_of("oh_ExclusionSuspensionList_10_15_2026-3.xlsx") == "OH"
    assert state_of("il Active Sanctions.xlsx") == "IL"
    assert state_of("NY SOMIGExclusionsList.xlsx") == "NY"
    assert state_of("ca suspended-ineligible-list-september-2026.csv") == "CA"
    assert state_of("tx TexasExclusionsData_2026_10_21.xls") == "TX"
    assert state_of("ExclusionSuspensionList_10_15_2026-3 (2).xlsx") == "OH"
    assert state_of("bundle355.bundle") == "" and state_of("HHA_Cost_Report_2021.zip") == ""


def test_edition_from_name_else_today():
    t = date(2026, 10, 21)
    assert edition_of("PA_x_2026-10-21.csv", t) == "202610"
    assert edition_of("ca suspended-ineligible-list-september-2026.csv", t) == "202609"
    assert edition_of("oh_ExclusionSuspensionList_10_15_2026-3.xlsx", t) == "202610"
    assert edition_of("NY SOMIGExclusionsList.xlsx", t) == "202610"


def test_plan_checks_headers_collapses_duplicates_and_apply_copies(tmp_path):
    dl, dest = tmp_path / "dl", tmp_path / "data" / "preclean" / "state_terminations"
    dl.mkdir()
    good = pd.DataFrame({"NPI": ["1234567893"], "Provider Name": ["ACME CLINIC"],
                         "Effective Date": ["2024-01-05"], "Reason": ["Medicaid fraud conviction"]})
    good.to_csv(dl / "PA_Medicheck_2026-10-21.csv", index=False)
    good.to_csv(dl / "PA_Medicheck_2026-10-21 (1).csv", index=False)           # a browser duplicate
    pd.DataFrame({"Column A": ["x"], "Column B": ["y"]}).to_csv(dl / "NY SOMIGExclusionsList.csv", index=False)
    pd.DataFrame({"Provider Name": ["B CLINIC"], "Effective Date": ["2024-02-01"]}).to_excel(
        dl / "il Active Sanctions.xlsx", index=False)
    (dl / "notes.csv").write_text("a,b\n1,2\n")
    rows = plan(dl, dest, today=date(2026, 10, 21))
    by = {r["file"]: r for r in rows}
    assert "notes.csv" not in by
    assert by["PA_Medicheck_2026-10-21.csv"]["target"] == "PA_terminations_202610.csv"
    assert by["PA_Medicheck_2026-10-21.csv"]["reason_col"] == "Reason"
    assert by["PA_Medicheck_2026-10-21 (1).csv"]["note"].startswith("duplicate of")
    assert by["NY SOMIGExclusionsList.csv"]["note"].startswith("NO NAME COLUMN")
    assert "no reason column" in by["il Active Sanctions.xlsx"]["note"]
    done = apply(rows, dl, dest)
    names = sorted(p.name for p in done)
    assert names == ["IL_terminations_202610.xlsx", "PA_terminations_202610.csv"]
    assert (dl / "PA_Medicheck_2026-10-21.csv").exists()                         # the download stays
