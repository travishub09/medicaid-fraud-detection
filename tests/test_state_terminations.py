"""State Medicaid termination lists -> exclusions schema (enforcement.state_terminations).

Synthetic workbooks only (rule 7). Covers: the header-row hunt under a banner, the
per-state column aliases seen on the real Southeast files (GA, KY, MS, NC, SC, TN, AL),
NPI validation, the name-only match to NPPES within a state, and the FCA tiering of the
reason text (fraud-shaped = hard; license / derivative / bare 'terminated' = weak).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.enforcement.state_terminations import (EXCLUSION_COLS, load_all, normalize_state_list,
                                                read_state_file, report, resolve_fields)
from src.model_a.fca_relevance import classify_series, fca_tier

NPI_A, NPI_B, NPI_C = "1003000415", "1003000407", "1003000308"   # Luhn-valid


def _write_xlsx(path: Path, rows: list[list], banner: int = 0, sheet: str = "Sheet1"):
    pad = [[None] * len(rows[0])] * banner
    pd.DataFrame(pad + rows).to_excel(path, index=False, header=False, sheet_name=sheet)


@pytest.fixture()
def in_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state_terminations"
    d.mkdir()
    # Georgia: banner rows, LAST/FIRST/BUSINESS NAME, SANCDATE, NPI, no reason
    _write_xlsx(d / "GA_terminations_202609.xlsx", [
        ["LAST NAME", "FIRST NAME", "MIDDLE NAME", "BUSINESS NAME", "GENERAL", "STATE", "SANCDATE", "NPI"],
        ["DOE", "JANE", "", "", "", "GA", "2023-05-01", NPI_A],
        ["", "", "", "ACME HOME HEALTH LLC", "", "GA", "2021-02-15", "123"],
    ], banner=2)
    # Kentucky: reason + timeframe
    _write_xlsx(d / "KY_terminations_202609.xlsx", [
        ["First Name", "Last Name or Practice Name", "NPI", "License", "Effective Date",
         "Reason for Term/Exclusion", "Timeframe of Term/Exclusion"],
        ["Bob", "Smith", NPI_B, "L1", "2022-09-01", "Medicaid Fraud Conviction", "Permanent"],
        ["", "Beta Pharmacy", NPI_C, "", "2024-01-10", "Failure to revalidate", "Until reenrolled"],
    ], banner=1)
    # South Carolina: one combined name column and an Excluded/Terminated flag
    _write_xlsx(d / "SC_terminations_202609.xlsx", [
        ["Individual//Entity", "NPI", "City", "State", "Zip", "Last Known Profession/Provider Type",
         "Excluded/Terminated", "Action Date"],
        ["GAMMA LABS INC", NPI_A, "Columbia", "SC", "29201", "Laboratory", "Terminated", "2020-06-30"],
    ], banner=2)
    # Alabama: name-only under a merged banner, suspension date column
    _write_xlsx(d / "AL_terminations_202609.xlsx", [
        ["INDIVIDUALS EXCLUDED FROM PARTICIPATING", None, None],
        [None, None, None],
        ["NAME OF PROVIDER", "SUSPENSION", "SUSPENSION.1"],
        ["JONES, MARY", "2019-03-01", "INDEFINITE"],
        ["HOMECARE", "2019-03-01", "INDEFINITE"],          # generic key: never matched
    ])
    # Florida template: skipped by name
    _write_xlsx(d / "FL_smmc_dst_202609.xlsx", [["Field Name", "Description"], ["x", "y"]])
    return d


@pytest.fixture()
def provider_dim() -> pd.DataFrame:
    return pd.DataFrame({
        "npi": ["1234567893", NPI_C, "1467463562"],
        "provider_name": ["JONES, MARY", "BETA PHARMACY", "JONES, MARY"],
        "addr_state": ["AL", "KY", "TX"],           # the TX namesake must not be matched
    })


def test_resolve_fields_covers_the_real_headers():
    f = resolve_fields(["EXCLUDED ENTITY", "NPI/ATYPICAL ID (EXCLUDED)", "EFFECTIVE DATE",
                        "REASON FOR EXCLUSION  ", "CITY", "STATE", "ZIP CODE", "OWNERSHIP"])
    assert f["org_name"] == "EXCLUDED ENTITY" and f["npi"].startswith("NPI/ATYPICAL")
    assert f["excl_date"] == "EFFECTIVE DATE" and f["reason"].startswith("REASON")
    f = resolve_fields(["#", "Provider Entity \n(Org/Ind)", "Provider Organization Name\n (Applicable for Organizations Only)",
                        "Provider First Name\n(Applicable for Individuals Only)", "Provider Last Name\n(Applicable for Individuals Only)",
                        "NPI", "Termination Effective Date", "Termination End Date", "Termination Reason", "Sanction Type"])
    assert f["org_name"].startswith("Provider Organization") and f["last_name"].startswith("Provider Last")
    assert f["excl_date"] == "Termination Effective Date" and f["reinstate_date"] == "Termination End Date"
    assert f["reason"] == "Termination Reason"


def test_load_all_normalizes_every_state(in_dir, provider_dim):
    rows, log = load_all(in_dir, provider_dim)
    assert set(EXCLUSION_COLS) <= set(rows.columns)
    by = rows.set_index("entity_name")
    # GA: person name assembled, NPI kept; bad NPI dropped to blank but the org row survives
    assert by.loc["DOE, JANE", "npi"] == NPI_A and by.loc["DOE, JANE", "state"] == "GA"
    assert by.loc["ACME HOME HEALTH LLC", "npi"] == ""
    # KY: reason carried into excl_type
    assert "Medicaid Fraud Conviction" in by.loc["Smith, Bob", "excl_type"]
    # SC: combined name column, flag as the reason
    assert by.loc["GAMMA LABS INC", "excl_type"].startswith("state_term:SC:Terminated")
    # AL: name-only, matched to the AL namesake (not the TX one) and flagged as a name match
    al = by.loc["JONES, MARY"]
    assert al["npi"] == "1234567893" and al["match_basis"] == "name_match" and al["excl_type"].endswith("|name_match")
    # the generic-token name is never matched
    assert by.loc["HOMECARE", "npi"] == "" and by.loc["HOMECARE", "match_basis"] == "unmatched"
    # the Florida template was skipped, not loaded
    assert not any(r.get("state") == "FL" and r.get("rows", 0) > 0 for r in log)
    assert any("template" in r["note"] for r in log if r["file"].startswith("FL_"))
    # dates parsed
    assert pd.Timestamp(by.loc["DOE, JANE", "excl_date"]) == pd.Timestamp("2023-05-01")


def test_tiers_from_the_reason_text(in_dir, provider_dim):
    rows, _ = load_all(in_dir, provider_dim)
    t = classify_series(rows["excl_type"])
    tier = dict(zip(rows["entity_name"], t["fca_tier"]))
    assert tier["Smith, Bob"] == "hard"              # fraud conviction
    assert tier["Beta Pharmacy"] == "weak"           # failure to revalidate
    assert tier["GAMMA LABS INC"] == "weak"          # bare 'Terminated'
    assert tier["DOE, JANE"] == "weak"               # no reason at all
    assert tier["JONES, MARY"] == "weak"             # name-matched, no reason


@pytest.mark.parametrize("excl_type,tier", [
    ("state_term:TN:Credible allegation of fraud", "hard"),
    ("state_term:MS:Kickback scheme [Physician]", "hard"),
    ("state_term:NC:OIG Exclusion", "weak"),
    ("state_term:KY:License revoked by board|name_match", "weak"),
    ("state_term:SC:Patient abuse", "weak"),
    ("state_term:GA:unspecified", "weak"),
    ("state_term:AL:Failure to return overpayment", "weak"),
])
def test_state_term_tier_rules(excl_type, tier):
    assert fca_tier(excl_type)[0] == tier


def test_report_lists_unmapped_reasons(in_dir, provider_dim):
    rows, log = load_all(in_dir, provider_dim)
    rows.loc[rows["entity_name"] == "GAMMA LABS INC", "excl_type"] = "state_term:SC:Something the table never saw"
    md = report(rows, log)
    assert "## Files" in md and "| GA |" in md
    assert "Something the table never saw" in md


def test_read_state_file_picks_the_list_sheet(tmp_path):
    p = tmp_path / "TN_terminations_202609.xlsx"
    with pd.ExcelWriter(p) as w:
        pd.DataFrame({"Notes": ["see next sheet"]}).to_excel(w, sheet_name="Cover", index=False)
        pd.DataFrame({"Last Name": ["A"] * 3, "First Name": ["B"] * 3, "NPI": [NPI_A] * 3,
                      "Effective Date": ["2024-01-01"] * 3, "Reason": ["fraud"] * 3}).to_excel(w, sheet_name="Terminated", index=False)
    df, fields, sheet = read_state_file(p)
    assert sheet == "Terminated" and fields["npi"] == "NPI" and len(df) == 3


def test_sentinel_and_far_dates_never_overflow_on_concat():
    """A '2999-12-31' open-ended reinstatement (KY/SC style) parsed by dateutil lands in a
    coarser unit and blew up pd.concat with OutOfBoundsDatetime; sentinels become NaT
    and every frame is datetime64[ns]."""
    from src.enforcement.state_terminations import _dates
    a = normalize_state_list(
        pd.DataFrame({"NPI": ["1234567893"], "Provider Name": ["ACME CLINIC"],
                      "Termination Date": ["03/01/2021"], "Reinstatement Date": ["2999-12-31"],
                      "Reason": ["fraud"]}),
        {"npi": "NPI", "org_name": "Provider Name", "excl_date": "Termination Date",
         "reinstate_date": "Reinstatement Date", "reason": "Reason"}, "KY", "ky.xlsx")
    b = normalize_state_list(
        pd.DataFrame({"NPI": ["1234567893"], "Provider Name": ["BETA LLC"],
                      "Termination Date": ["2020-06-15 00:00:00"], "Reinstatement Date": [""],
                      "Reason": ["abuse of billing privileges"]}),
        {"npi": "NPI", "org_name": "Provider Name", "excl_date": "Termination Date",
         "reinstate_date": "Reinstatement Date", "reason": "Reason"}, "SC", "sc.xlsx")
    both = pd.concat([a, b], ignore_index=True)
    assert str(both["reinstate_date"].dtype) == "datetime64[ns]"
    assert both["reinstate_date"].isna().all()
    assert both["currently_active"].tolist() == [1, 1]
    assert both["excl_date"].dt.year.tolist() == [2021, 2020]
    got = _dates(pd.Series(["9999-12-31", "44927", "12/31/1899", "Jan 5, 2022", None]))
    assert got.isna().tolist() == [True, False, True, False, True]
    assert got.iloc[1].year == 2023 and got.iloc[3].month == 1
