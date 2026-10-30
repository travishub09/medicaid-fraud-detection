"""ohio_cost_report: read Ohio Medicaid nursing-facility cost reports (ODM 02524N, the PDF the
Department releases under a public-records request) into one row per facility-year, and grade the
related-party answers against the numbers on the same report.

Input is the text of the PDF (``pdftotext -layout``; the CLI runs it when given PDFs). A file
usually carries several years back to back; each starts at a Schedule A cover page. Per report:

  identity      provider name, NPI, Medicaid provider number, CMS certification number, period,
                type of control, legal entity, owner of the real estate
  home office   the chain home-office name declared on Schedule A page 2 and the affiliation box
  Schedule C-3  Q1 "are any costs the result of transactions with a related party" (yes/no),
                Q2 payments to related parties above their cost (yes/no), the officers and
                directors (name + title), Q3 mortgage holders, Q5 the other facilities with
                related ownership (count), Q6 / Q7 (yes/no), Q8 contracts of $10,000+
  amounts       6210 consulting & management fees (direct care), 6230 home-office (direct),
                7610 consulting & management fees (ancillary/support), 7695 home-office
                (ancillary/support), 8090 home-office (capital), 2420 / 2430 related-party loan
                interest (allowable / non-allowable), 5510 management-services revenue,
                5620 related-party interest income, Schedule A total patient days, Schedule A-3
                total costs

The grade is a set of FLAGS, each a contradiction INSIDE the filing or between filings of one
group (never a conclusion about anyone):

  rp_denied_with_management_fees   Q1 = No while a consulting / management fee is booked and a
                                   home office is declared or a management company is in Q8
  rp_denied_with_rp_loan_interest  Q1 = No while Schedule E books related-party loan interest
  related_facilities_blank         Q5 lists no related facility while the same officers sign
                                   another report in the set (the ORC 5111.20 question)
  home_office_declared_no_cost     a home office is declared and every home-office cost line is 0
  contracts_copied_forward         the Q8 contract table is identical to the prior year's

The certification on the form cites 42 CFR 455.18; a false answer on a certified cost report is a
per-se fact for counsel, so every flag names the schedule, the line and the year. Work product:
the outputs live under <data_root>/enforcement/, never git.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

import pandas as pd

AMOUNT_LINES = {
    "fee_6210": r"Consulting and Management Fees-Direct\s+6210\s+([\d,()-]+)",
    "homeoffice_6230": r"Home Office Costs/Direct Care[^\n]*?6230\s+([\d,()-]+)",
    "fee_7610": r"Consulting & Mgmt\. Fees - Ancillary/Support\s+7610\s+([\d,()-]+)",
    "homeoffice_7695": r"Home Office Costs - Ancillary/Support \*\*\s+7695\s+([\d,()-]+)",
    "homeoffice_8090": r"Home Office Costs - capital \*\*\s+8090\s+([\d,()-]+)",
    "rp_loan_interest_allowable_2420": r"Related Party Loans - Interest Allowable\s+2420\s+([\d,()-]+)",
    "rp_loan_interest_nonallowable_2430": r"Related Party Loans - Interest Non-Allowable[^\n]*?2430\s+([\d,()-]+)",
    "mgmt_services_revenue_5510": r"Management Services\s+5510\s+([\d,()-]+)",
    "rp_interest_income_5620": r"Interest Income - Related Party Revenue\s+5620\s+([\d,()-]+)",
    "total_patient_days": r"7\. Total patient days[^\n]*?\)\s+([\d,.]+)",
    "total_costs": r"9\. Total costs[^\n]*?\s([\d,()-]{4,})\s*$",
}
MGMT_WORDS = re.compile(r"manage|mgmt|consult|holding|realty|propert|propco|home office|administrat", re.I)
LEGAL_TAIL = re.compile(r"[,.]?\s*\b(LLC|L\.L\.C\.|INC|INC\.|CORP|CO|LTD|LP|LLP|PC|PLLC)\b\.?$", re.I)


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip().replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _checkbox(line: str, labels=("Yes", "No")) -> str:
    """The box precedes its label on the layout line ('X   Yes      No' = Yes; 'Yes   X   No' = No):
    the label whose start is the first to the RIGHT of the X wins."""
    xs = [m.start() for m in re.finditer(r"(?<![A-Za-z])X(?![A-Za-z])", line)]
    if not xs:
        return ""
    pos = {lab: [m.start() for m in re.finditer(r"\b" + lab + r"\b", line)] for lab in labels}
    for x in xs:
        best, bestd = "", None
        for lab, ps in pos.items():
            for p in ps:
                if p > x and (bestd is None or p - x < bestd):
                    best, bestd = lab, p - x
        if best:
            return best.lower()
    return ""


def split_reports(text: str) -> list[str]:
    """One block per cost report: a file carries several years, each starting at the Schedule A
    cover page ("MEDICAID NURSING FACILITY COST REPORT" + "Type of Cost Report Filing")."""
    pages = text.split("\f")
    blocks, cur = [], []
    for p in pages:
        if "MEDICAID NURSING FACILITY COST REPORT" in p and "Type of Cost Report Filing" in p:
            if cur:
                blocks.append("\f".join(cur))
            cur = [p]
        elif cur:
            cur.append(p)
    if cur:
        blocks.append("\f".join(cur))
    return blocks


def _section(text: str, start: str, end_pats: tuple[str, ...]) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    seg = text[i + len(start):]
    nl = seg.find("\n")
    seg = seg[nl + 1:] if nl >= 0 else ""          # the rest of the header line is never a row
    ends = [seg.find(e) for e in end_pats if seg.find(e) >= 0]
    return seg[: min(ends)] if ends else seg


def _rows(seg: str, skip_header_words=("Name", "Provider Name", "Contractor Name", "Note:", "Social Security", "(1)")) -> list[list[str]]:
    rows = []
    for l in seg.splitlines():
        s = l.strip()
        if not s or any(s.startswith(w) for w in skip_header_words) or s.startswith("ODM 02524N") or s.startswith("*"):
            continue
        parts = [p.strip() for p in re.split(r"\s{2,}", s) if p.strip()]
        if parts:
            rows.append(parts)
    return rows


def parse_report(block: str) -> dict:
    r: dict = {}
    m = re.search(r"Provider Name\s+\(DBA\)[^\n]*\n\s*\n?([^\n]+)", block)
    line = m.group(1) if m else ""
    parts = [p for p in re.split(r"\s{2,}", line.strip()) if p]
    r["provider_name"] = parts[0] if parts else ""
    r["npi"] = next((p for p in parts if re.fullmatch(r"\d{10}", p)), "")
    r["medicaid_number"] = next((p for p in parts if re.fullmatch(r"\d{7}", p)), "")
    r["ccn"] = next((p.replace("-", "") for p in parts if re.fullmatch(r"\d{2}-\d{4}", p)), "")
    fm = re.search(r"From:\s+(\d{1,2}/\d{1,2}/\d{4})", block)
    tm = re.search(r"Through:\s+(\d{1,2}/\d{1,2}/\d{4})", block)
    r["period_from"] = fm.group(1) if fm else ""
    r["period_through"] = tm.group(1) if tm else ""
    r["year"] = r["period_through"][-4:] if r["period_through"] else ""
    le = re.search(r"Name Of Legal Entity\s+([^\n]+)", block)
    r["legal_entity"] = le.group(1).strip() if le else ""
    ro = re.search(r"NAME AND ADDRESS OF OWNER OF REAL ESTATE[^\n]*\n[^\n]*?Name\s+([^\n]+)", block)
    r["real_estate_owner"] = ro.group(1).strip() if ro else ""
    r["for_profit"] = "For Profit" in block.split("\f")[0]
    ft = re.search(r"X\s+4\.\d\s+(Year-End|New Facility|Change of Operator|Final|Amended)", block)
    r["filing_type"] = ft.group(1) if ft else ""
    ho = re.search(r"1\. Name Of Home Office As reported To The IRS\s+([^\n]*?)(?:\s{2,}Federal Tax ID Number.*)?$", block, re.M)
    hon = (ho.group(1).strip() if ho else "")
    r["home_office_name"] = "" if hon.startswith("Federal") else hon
    aff = ""
    for lab in ("Joint venture / Partnership", "Managed / Related", "Leased", "Operated / Related", "Wholly Owned", "Other (Specify)"):
        for l in block.splitlines():
            if lab in l and re.search(r"X\s+(\d\.\s+)?" + re.escape(lab[:10]), l):
                aff = lab
        if aff:
            break
    if not aff:
        for l in block.splitlines():
            if ("Joint venture" in l or "Operated / Related" in l) and re.search(r"(?<![A-Za-z])X(?![A-Za-z])", l):
                # the box precedes the label: take the label that follows the X
                after = l[l.index("X") + 1:]
                for lab in ("Joint venture / Partnership", "Managed / Related", "Leased", "Operated / Related", "Wholly Owned", "Other"):
                    if lab in after:
                        aff = lab
                        break
                if aff:
                    break
    r["home_office_affiliation"] = aff
    for k, pat in AMOUNT_LINES.items():
        mm = re.search(pat, block, re.M)
        r[k] = _num(mm.group(1)) if mm else None
    # Schedule C-3 = the pages whose header carries the title (the contract list on its last page
    # must never run into the balance sheet that follows)
    c3_pages = [p for p in block.split("\f") if "COST OF SERVICES FROM RELATED PARTIES" in p]
    c3 = "\n".join(c3_pages)
    q1 = re.search(r"1\. In the amount of costs[^\n]*\n([^\n]*Yes[^\n]*No[^\n]*)", c3)
    r["q1_related_party_costs"] = _checkbox(q1.group(1)) if q1 else ""
    q2 = re.search(r"2\. Does this cost report include payments[^\n]*\n([^\n]*Yes[^\n]*No[^\n]*)", c3)
    r["q2_payments_above_cost"] = _checkbox(q2.group(1)) if q2 else ""
    q6 = re.search(r"Social Security Act as amended\?\s*\n([^\n]*Yes[^\n]*No[^\n]*)", c3)
    r["q6_convicted"] = _checkbox(q6.group(1)) if q6 else ""
    q7 = re.search(r"within the previous twelve months\?\s*\n([^\n]*Yes[^\n]*No[^\n]*)", c3)
    r["q7_former_state_staff"] = _checkbox(q7.group(1)) if q7 else ""
    off = _section(c3, "4. List all persons performing", ("5. List all other facilities",))
    officers = []
    for parts in _rows(off, ("Name", "Note:", "Social Security")):
        if len(parts) >= 2 and not parts[0].startswith("Job Title"):
            officers.append((parts[0], parts[-1]))
        elif len(parts) == 1 and len(parts[0].split()) >= 2 and not parts[0].startswith("Job"):
            officers.append((parts[0], ""))
    r["officers"] = "; ".join(f"{n} ({t})" if t else n for n, t in officers)
    r["officer_keys"] = "|".join(sorted(set(re.sub(r"[^A-Z ]", "", n.upper()).strip() for n, _ in officers)))
    mort = _section(c3, "3. List each individual, partner, related corporation", ("4. List all persons",))
    mrows = [p for p in _rows(mort, ("Name", "Note:", "(All individuals")) if not p[0].startswith("(All")]
    r["q3_mortgage_holders"] = "; ".join(" / ".join(p[:3]) for p in mrows)
    rel = _section(c3, "5. List all other facilities that have related ownership", ("6. Has any director", "* FOR FURTHER"))
    rel += _section(c3, "5. Continued", ("6. Has any director",))
    # two facilities per row (left and right columns): name, provider number (often blank), beds
    relnames = []
    for parts in _rows(rel, ("Provider Name", "Note:")):
        if not any(re.fullmatch(r"[\d,-]+", x) for x in parts):
            continue
        relnames += [x for x in parts if not re.fullmatch(r"[\d,.-]+", x) and len(x) > 3]
    r["q5_related_facilities_n"] = len(relnames)
    r["q5_related_facilities"] = "; ".join(relnames)
    con = "\n".join(_section(p, "8. List all contracts", ("ODM 02524N",)) + "\n" + _section(p, "8. Continued", ("ODM 02524N",))
                    for p in c3_pages if "8. List all contracts" in p or "8. Continued" in p)
    con = "\n".join(l for l in con.splitlines() if "thousand dollars or more" not in l)
    crows = []
    for parts in _rows(con, ("Contractor Name", "thousand dollars", "8. Continued", "Note:")):
        amt = next((p for p in parts if re.fullmatch(r"[\d,]+", p)), None)
        if amt and len(parts) >= 2:
            name = parts[0]
            svc = parts[-1] if parts[-1] != amt else ""
            crows.append((name, _num(amt) or 0.0, svc))
    r["q8_contracts_n"] = len(crows)
    r["q8_contracts"] = "; ".join(f"{n} ${a:,.0f} {s}".strip() for n, a, s in crows)
    r["q8_contracts_key"] = "|".join(sorted(f"{n.upper()}:{a:.0f}" for n, a, s in crows))
    r["q8_management_contracts"] = "; ".join(f"{n} ${a:,.0f} {s}".strip() for n, a, s in crows if MGMT_WORDS.search(n + " " + s))
    return r


def parse_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rows = []
    for b in split_reports(text):
        r = parse_report(b)
        r["source_file"] = path.name
        rows.append(r)
    return rows


def grade(df: pd.DataFrame) -> pd.DataFrame:
    """The flags, each a contradiction inside the filing or between filings of the set."""
    df = df.copy()
    fees = df[["fee_6210", "fee_7610"]].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    hocost = df[["homeoffice_6230", "homeoffice_7695", "homeoffice_8090"]].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    rpint = df[["rp_loan_interest_allowable_2420", "rp_loan_interest_nonallowable_2430"]].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    ho = df["home_office_name"].fillna("").astype(str).str.strip() != ""
    mgmt = df["q8_management_contracts"].fillna("").astype(str).str.strip() != ""
    no = df["q1_related_party_costs"].eq("no")
    df["management_fees"] = fees
    df["home_office_costs"] = hocost
    df["rp_loan_interest"] = rpint
    df["flag_rp_denied_with_management_fees"] = no & (fees > 0) & (ho | mgmt)
    # a consulting / management fee of $10,000+ with no management contractor on the $10,000+ list
    df["flag_mgmt_fees_without_named_contractor"] = (fees >= 10_000) & ~mgmt
    df["flag_rp_denied_with_rp_loan_interest"] = no & (rpint > 0)
    df["flag_home_office_declared_no_cost"] = ho & (hocost == 0)
    # shared officers across different facilities in the set
    keys = df["officer_keys"].fillna("").astype(str)
    fac = df["ccn"].fillna("").astype(str)
    from difflib import SequenceMatcher

    def _same(a: str, b: str) -> bool:        # a name typed two ways on two forms (LAHANSKY / LAHASKY)
        return a == b or SequenceMatcher(None, a, b).ratio() >= 0.85
    shared = []
    for i, k in enumerate(keys):
        names = [x for x in k.split("|") if x]
        hit = set()
        for j, k2 in enumerate(keys):
            if fac.iloc[j] == fac.iloc[i]:
                continue
            for n in names:
                if any(_same(n, m) for m in k2.split("|") if m):
                    hit.add(n)
        shared.append(len(hit))
    df["officers_shared_with_other_facilities"] = shared
    df["flag_related_facilities_blank"] = df["q5_related_facilities_n"].fillna(0).eq(0) & (pd.Series(shared, index=df.index) > 0)
    df["flag_no_officers_listed"] = df["officer_keys"].fillna("").astype(str).eq("")
    # contracts copied forward: identical key to the previous year of the same facility
    df = df.sort_values(["ccn", "year"])
    prev = df.groupby("ccn")["q8_contracts_key"].shift(1)
    df["flag_contracts_copied_forward"] = (df["q8_contracts_key"].fillna("") != "") & (df["q8_contracts_key"] == prev)
    flag_cols = [c for c in df.columns if c.startswith("flag_")]
    df["n_flags"] = df[flag_cols].sum(axis=1)
    return df


def to_markdown(df: pd.DataFrame) -> str:
    L = ["# OHIO MEDICAID COST REPORTS: related-party answers against the numbers", "",
         "_One row per facility-year from the ODM 02524N filings released under the public-records request. Every "
         "flag is a contradiction inside a certified filing (42 CFR 455.18 certification on the form) or between "
         "filings of one group; leads for counsel's review, never a finding about anyone._", ""]
    L.append(f"Reports read: {len(df)} facility-years, {df['ccn'].nunique()} facilities, years "
             f"{', '.join(sorted(set(df['year'].astype(str))))}.")
    L.append("")
    flag_cols = [c for c in df.columns if c.startswith("flag_")]
    for c in flag_cols:
        L.append(f"- {c.replace('flag_', '')}: {int(df[c].sum())} facility-years")
    L.append("")
    L.append("## Per facility (latest year first)")
    L.append("")
    L.append("| facility (CCN) | year | home office (affiliation) | Q1 related-party costs | Q5 related facilities | officers shared with other facilities | mgmt fees | home-office cost lines | related-party loan interest | total costs | flags |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in df.sort_values(["ccn", "year"], ascending=[True, False]).itertuples():
        flags = ", ".join(c.replace("flag_", "") for c in flag_cols if getattr(r, c))
        L.append(f"| {r.provider_name} ({r.ccn}) | {r.year} | {r.home_office_name or 'none'} ({r.home_office_affiliation or ''}) | "
                 f"{r.q1_related_party_costs or '?'} | {int(r.q5_related_facilities_n or 0)} | {int(r.officers_shared_with_other_facilities)} | "
                 f"${(r.management_fees or 0):,.0f} | ${(r.home_office_costs or 0):,.0f} | ${(r.rp_loan_interest or 0):,.0f} | "
                 f"${(r.total_costs or 0):,.0f} | {flags} |")
    L.append("")
    L.append("## Officers and directors by facility (Schedule C-3 question 4; names as filed on the public cost report)")
    L.append("")
    for ccn, g in df.sort_values("year").groupby("ccn"):
        last = g.iloc[-1]
        L.append(f"- {last['provider_name']} (CCN {ccn}, {last['year']}): {last['officers'] or 'none listed'}")
    L.append("")
    L.append("## Management and consulting contracts named in question 8 (the $10,000+ list)")
    L.append("")
    for r in df.sort_values(["provider_name", "year"]).itertuples():
        if r.q8_management_contracts:
            L.append(f"- {r.provider_name} {r.year}: {r.q8_management_contracts}")
    L.append("")
    L.append("## Reading the flags")
    L.append("")
    L.append("- rp_denied_with_management_fees: the filing says no cost comes from a related party (C-3 Q1 = No) while it books consulting / management fees (lines 6210 / 7610) and declares a chain home office or names a management company among its contracts. Innocent reading: the manager is unrelated by ownership. Check: who owns the management company.")
    L.append("- rp_denied_with_rp_loan_interest: Q1 = No while Schedule E lines 2420 / 2430 carry related-party loan interest; the related party exists on the same filing.")
    L.append("- related_facilities_blank: question 5 (facilities with related ownership, ORC 5111.20) is empty while the same officers sign another facility's report in this set.")
    L.append("- home_office_declared_no_cost: a home office is declared on Schedule A while lines 6230 / 7695 / 8090 are zero; the home office's cost reached the facility some other way (a management fee, a lease) or not at all.")
    L.append("- contracts_copied_forward: the $10,000+ contract table is identical to the prior year's, amounts included; a table carried forward rather than reported.")
    L.append("- no_officers_listed: question 4 (officers and directors) is empty on a certified filing.")
    L.append("- mgmt_fees_without_named_contractor: consulting / management fees of $10,000 or more are booked (lines 6210 / 7610) and question 8, which must list every contract of $10,000 or more, names no management or consulting contractor; who was paid is the records question.")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True, nargs="+", help="PDF or pdftotext .txt files (or folders of them)")
    ap.add_argument("--out", required=True, help="output folder (ohio_cost_reports.csv + OHIO_COST_REPORTS.md)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for p in a.inp:
        pp = Path(p)
        files += sorted(pp.glob("*.pdf")) + sorted(pp.glob("*.txt")) if pp.is_dir() else [pp]
    rows = []
    txt_dir = out / "_text"
    for f in files:
        if f.suffix.lower() == ".pdf":
            if not shutil.which("pdftotext"):
                raise SystemExit("pdftotext is not installed; convert the PDFs first")
            txt_dir.mkdir(exist_ok=True)
            t = txt_dir / (f.stem + ".txt")
            subprocess.run(["pdftotext", "-layout", str(f), str(t)], check=True)
            f = t
        rows += parse_file(f)
    df = pd.DataFrame(rows)
    before = len(df)
    df = df.drop_duplicates(subset=["ccn", "period_from", "period_through", "filing_type"], keep="first")
    if len(df) < before:
        print(f"[ohio_cost_report] {before - len(df)} duplicate report(s) dropped (the same facility-year in two files)")
    df = grade(df)
    df.to_csv(out / "ohio_cost_reports.csv", index=False)
    (out / "OHIO_COST_REPORTS.md").write_text(to_markdown(df), encoding="utf-8")
    print(f"[ohio_cost_report] {len(df)} facility-years from {len(files)} files; flags: "
          + ", ".join(f"{c.replace('flag_', '')} {int(df[c].sum())}" for c in df.columns if c.startswith("flag_")))
    print(f"[ohio_cost_report] -> {out / 'OHIO_COST_REPORTS.md'}")


if __name__ == "__main__":
    main()
