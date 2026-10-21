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


def test_big_state_headers_resolve_and_tolerant_csv_reads(tmp_path):
    from src.enforcement.state_terminations import resolve_fields, read_state_file
    ny = resolve_fields(['provider_name', 'license_num', 'npi_num', 'provider_type', 'exclusion_effective_date'])
    assert ny["org_name"] == "provider_name" and ny["npi"] == "npi_num" and ny["excl_date"] == "exclusion_effective_date"
    pa = resolve_fields(['ProviderName', 'LicenseNumber', 'Status', 'BeginDate', 'EndDate', 'CAO'])
    assert pa["org_name"] == "ProviderName" and pa["excl_date"] == "BeginDate" and pa["reinstate_date"] == "EndDate"
    tx = resolve_fields(['CompanyName', 'LastName', 'FirstName', 'MidInitial', 'Occupation', 'NPI', 'StartDate'])
    assert tx["org_name"] == "CompanyName" and tx["last_name"] == "LastName" and tx["middle_name"] == "MidInitial"
    fl = resolve_fields(['Provider', 'NPI Number', 'Provider Type', 'Sanction Date', 'Violation Code'])
    assert fl["org_name"] == "Provider" and fl["reason"] == "Violation Code"
    # a preamble line + an unbalanced quote: the sniffer rejects it, the tolerant reader does not
    p = tmp_path / "ca suspended-ineligible-list.csv"
    p.write_text('Suspended and Ineligible Provider List,,,\n'
                 'Provider Name,NPI,Effective Date,Reason\n'
                 '"ACME "CLINIC" INC",1234567893,2024-01-05,Medi-Cal fraud conviction\n'
                 'BETA LLC,1245319599,2024-02-01,License revoked\n', encoding="utf-8")
    df, fields, _ = read_state_file(p)
    assert len(df) == 2 and fields["org_name"] == "Provider Name" and fields["reason"] == "Reason"


def test_sorter_never_lists_warn_or_census_files(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    pd.DataFrame({"Company Name": ["X"], "Effective Date": ["2026-01-01"]}).to_csv(dl / "florida_warn_2026.csv", index=False)
    pd.DataFrame({"a": [1]}).to_excel(dl / "co-est2025-pop.xlsx", index=False)
    assert plan(dl, tmp_path / "dest", today=date(2026, 10, 21)) == []
