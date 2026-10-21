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


def test_html_table_with_an_xlsx_extension_reads(tmp_path):
    from src.enforcement.state_terminations import read_state_file
    p = tmp_path / "il Active Sanctions.xlsx"
    p.write_text("<html><body><table><tr><th>Provider Name</th><th>NPI</th><th>Sanction Date</th><th>Reason</th></tr>"
                 "<tr><td>ACME CLINIC</td><td>1234567893</td><td>2024-01-05</td><td>Medicaid fraud</td></tr>"
                 "<tr><td>BETA LLC</td><td>1245319599</td><td>2024-02-01</td><td>License revoked</td></tr>"
                 "</table></body></html>", encoding="utf-8")
    df, fields, sheet = read_state_file(p)
    assert sheet == "html" and len(df) == 2 and fields["org_name"] == "Provider Name" and fields["npi"] == "NPI"


def test_a_provider_roster_is_never_copied(tmp_path):
    dl, dest = tmp_path / "dl", tmp_path / "dest"
    dl.mkdir()
    pd.DataFrame({"Provider Name": ["ACME", "BETA"], "NPI": ["1234567893", "1245319599"],
                  "Provider Type": ["x", "y"], "City": ["a", "b"]}).to_csv(dl / "florida prw19000.csv", index=False)
    rows = plan(dl, dest, today=date(2026, 10, 21))
    assert rows and rows[0]["note"].startswith("NO DATE AND NO REASON")
    assert apply(rows, dl, dest) == []


def test_xlsx_sheet_xml_reads_without_the_style_records(tmp_path):
    from src.enforcement.state_terminations import _xlsx_raw
    p = tmp_path / "il Active Sanctions.xlsx"
    pd.DataFrame({"Provider Name": ["ACME CLINIC", "BETA LLC"], "NPI": ["1234567893", "1245319599"],
                  "Sanction Date": ["2024-01-05", "2024-02-01"], "Reason": ["Medicaid fraud", "License revoked"]}
                 ).to_excel(p, index=False)
    df = _xlsx_raw(p)                      # the raw path on a healthy workbook reads the same table
    assert df is not None and len(df) == 2 and list(df.columns)[:2] == ["Provider Name", "NPI"]
    assert df.iloc[0]["Reason"] == "Medicaid fraud"


def test_raw_xlsx_reader_takes_any_worksheet_name_and_namespace(tmp_path):
    import zipfile
    from src.enforcement.state_terminations import _xlsx_raw
    p = tmp_path / "odd.xlsx"
    ns = "http://purl.oclc.org/ooxml/spreadsheetml/main"             # strict OOXML, odd sheet name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}"/>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}"><si><t>Provider Name</t></si><si><t>NPI</t></si>'
                                            '<si><t>ACME CLINIC</t></si></sst>')
        z.writestr("xl/worksheets/Sanctions Sheet.xml",
                   f'<worksheet xmlns="{ns}"><sheetData>'
                   '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                   '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>1234567893</v></c></row>'
                   '</sheetData></worksheet>')
    df = _xlsx_raw(p)
    assert df is not None and list(df.columns) == ["Provider Name", "NPI"] and df.iloc[0]["NPI"] == "1234567893"


def test_sorter_skips_a_template(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    pd.DataFrame({"Provider Name": ["EXAMPLE"], "Termination Effective Date": ["2024-01-01"],
                  "Termination Reason": ["example"]}).to_excel(
        dl / "Florida Denied.Terminated.Suspended.Provider.Report.Template_12_18_2024.xlsx", index=False)
    assert plan(dl, tmp_path / "dest", today=date(2026, 10, 21)) == []


def test_loader_keeps_only_the_newest_edition_per_state(tmp_path):
    from src.enforcement.state_terminations import load_all, newest_editions
    d = tmp_path / "state_terminations"
    d.mkdir()
    row = {"Provider Name": "ACME CLINIC", "NPI": "1234567893", "Effective Date": "2024-01-05", "Reason": "Medicaid fraud"}
    pd.DataFrame([row]).to_csv(d / "GA_terminations_202609.csv", index=False)
    pd.DataFrame([row, dict(row, **{"Provider Name": "BETA LLC", "NPI": "1245319599"})]).to_csv(
        d / "GA_terminations_202610.csv", index=False)
    pd.DataFrame([row]).to_csv(d / "LA_terminations_202610.csv", index=False)
    keep, superseded = newest_editions(sorted(d.glob("*")))
    assert superseded == {"GA_terminations_202609.csv": "GA_terminations_202610.csv"}
    assert sorted(p.name for p in keep) == ["GA_terminations_202610.csv", "LA_terminations_202610.csv"]
    rows, log = load_all(d)
    assert len(rows) == 3                                   # 2 GA (October) + 1 LA, never the September GA row again
    assert any("superseded" in r["note"] for r in log)
