"""ohio_cost_report: a planted two-year filing (the ODM 02524N layout as pdftotext renders it)."""
import pandas as pd
from src.ingest_cms import ohio_cost_report as ocr


def _report(year, q1_x_before_yes, fee, rp_int, home_office, officers, contracts):
    q1 = ("X      Yes                      No      If Yes, complete item 2" if q1_x_before_yes
          else "        Yes              X        No    If Yes, complete item 2")
    cover = f"""4.1                                                      Schedule A
                                                                              Ohio Department of Medicaid
                                                              MEDICAID NURSING FACILITY COST REPORT
     Type of Cost Report Filing. (Please check one of the following)
       X     4.1 Year-End                                        4.3 Change of Operator
Provider Name            (DBA)                                   National Provider Identifier                    Medicaid Provider Number                           CMS Certification Number
Planted Village                                                  1234567893                                                                                           36-5162
Address(1)               1 Main St
                                                                                     From:            01/01/{year}
Zip Code                 44035                                   County           LORAIN                      Through:         12/31/{year}
                                                                                  Name Of Legal Entity Planted Village LLC
 For Profit
                                        NAME AND ADDRESS OF OWNER OF REAL ESTATE
           Corporation (1.3)                                          Name                      Planted Propco LLC
"""
    ho = f"""4.1   Schedule A
CHAIN HOME OFFICE/CERTIFICATION BY OFFICER OF PROVIDER
1. Name Of Home Office As reported To The IRS                    {home_office}                                            Federal Tax ID Number                XX-XXXXXXX
    1.       X    Joint venture / Partnership                                  3.            Managed / Related
    2.            Operated / Related                                           4.            Wholly Owned
"""
    sched = f"""4.1   Schedule C
11. Consulting and Management Fees-Direct               6210                              0               0            0               0 1.0000                0
  45. Consulting & Mgmt. Fees - Ancillary/Support             7610                     {fee:,}           {fee:,}              0       {fee:,}      1.0000       {fee:,}
  64. Home Office Costs - Ancillary/Support **                7695              0             0                 0             0             0      1.0000             0
 7. Total patient days (from Schedule A-1, line 13, column 11)                                                                                                      16,636.50
 9. Total costs (total of Schedule B-1 line 5, Schedule B-2,line                                                           10,046,288
11. Home Office Costs - capital **                      8090                         0                  0                      0          1.0000                    0
 37. Related Party Loans - Interest Non-Allowable(to Sch E-1, line 3)                 2430                {rp_int:,}                  0
"""
    offs = "\n".join(f" {n}                                                                                                                 {t}" for n, t in officers)
    cons = "\n".join(f" {n}                                             {a:,}                               {s}" for n, a, s in contracts)
    c3 = f"""4.1   Schedule C-3
                                 COST OF SERVICES FROM RELATED PARTIES
  1. In the amount of costs to be reimbursed by the Ohio Medicaid Program, are any costs included which are a result of transactions with a related party? *
{q1}
  2. Does this cost report include payments to related parties in excess of the costs to the related party?
        Yes              X        No    If Yes, complete the table below
 ODM 02524N (REV. 1/2025)
\f4.1   Schedule C-3
                                 COST OF SERVICES FROM RELATED PARTIES
 3. List each individual, partner, related corporation, or related LLC which owns, in whole or in part, any mortgage or deed of trust, of the facility or of any property or asset of the provider.
  Name                     Title/Position (if applicable)     % Ownership
  4. List all persons performing the duties of officer, director or equivalence (President, VP, Secretary, or other related positions).
      Note: Social Security numbers are not required for non-profit or governmental facilities.
  Name                                                                                       Social Security Number                                   Job Title(if applicable)
{offs}
 5. List all other facilities that have related ownership as set forth in Section 5111.20 of the ORC.
                                Provider Name                                      Provider Number              Number of Beds
 ODM 02524N (REV. 1/2025)
\f4.1   Schedule C-3
                                 COST OF SERVICES FROM RELATED PARTIES
 6. Has any director, officer, manager, employee, individual or organization having a direct or indirect ownership interest of 5%
  Title XIX (Medicaid), or Title XX of the Social Security Act as amended?
        Yes              X        No    If yes, list names below:
 7. Has any individual currently under contract with the provider or related party organization been employed in a managerial, accounting,
  the Ohio Department of Aging, the Ohio Department of Commerce, or the Ohio Industrial Commission within the previous twelve months?
        Yes              X        No    If yes, list names below:
  8. List all contracts in effect during the cost report period for which the imputed value or cost of goods or service from any individual or organization is ten
      thousand dollars or more in a twelve month period.
                                  Contractor Name                                             Contract Amount                                                                     Goods or Services Provided
{cons}
 ODM 02524N (REV. 1/2025)
"""
    return "\f".join([cover, ho, sched, c3])


def test_two_years_parse_and_the_flags_fire(tmp_path):
    y1 = _report(2023, False, 210_967, 1_239_907, "", [("Pat Example", "President")], [("Mercy Physicians", 26_688, "Medical Director")])
    y2 = _report(2024, True, 274_901, 0, "Planted Home Office", [("Pat Example", "President")], [("Mercy Physicians", 26_688, "Medical Director")])
    other = _report(2024, True, 100_000, 0, "Planted Home Office", [("Pat Exmaple", "SVP")], [("Katone Consulting", 84_000, "Operation Management")]) \
        .replace("Planted Village", "Other Village").replace("36-5162", "36-6158").replace("1234567893", "1234567801")
    (tmp_path / "a.txt").write_text(y1 + "\f" + y2, encoding="utf-8")
    (tmp_path / "b.txt").write_text(other, encoding="utf-8")
    rows = ocr.parse_file(tmp_path / "a.txt") + ocr.parse_file(tmp_path / "b.txt")
    assert len(rows) == 3
    r23, r24, ro = rows
    assert r23["ccn"] == "365162" and r23["year"] == "2023" and r23["npi"] == "1234567893"
    assert r23["q1_related_party_costs"] == "no" and r24["q1_related_party_costs"] == "yes"
    assert r23["fee_7610"] == 210_967 and r23["rp_loan_interest_nonallowable_2430"] == 1_239_907
    assert r24["home_office_name"] == "Planted Home Office" and r24["home_office_affiliation"].startswith("Joint venture")
    assert r23["officers"] == "Pat Example (President)" and r23["q8_contracts_n"] == 1
    assert r23["total_patient_days"] == 16_636.5 and r23["total_costs"] == 10_046_288
    df = ocr.grade(pd.DataFrame(rows))
    g = df.set_index(["ccn", "year"])
    assert bool(g.loc[("365162", "2023"), "flag_rp_denied_with_rp_loan_interest"])
    assert bool(g.loc[("365162", "2023"), "flag_mgmt_fees_without_named_contractor"])
    assert bool(g.loc[("365162", "2024"), "flag_home_office_declared_no_cost"])
    # the same officer, typed two ways on two facilities, is shared; Q5 blank -> the flag
    assert int(g.loc[("365162", "2024"), "officers_shared_with_other_facilities"]) == 1
    assert bool(g.loc[("365162", "2024"), "flag_related_facilities_blank"])
    assert bool(g.loc[("365162", "2024"), "flag_contracts_copied_forward"])
    assert not bool(g.loc[("366158", "2024"), "flag_mgmt_fees_without_named_contractor"])
    md = ocr.to_markdown(df)
    assert "Planted Village" in md and "rp_denied_with_rp_loan_interest" in md
