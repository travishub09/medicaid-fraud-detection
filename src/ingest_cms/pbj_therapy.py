"""
pbj_therapy.py — the public check behind a resident's "did not get OT".

A nursing-home resident's own transaction ledger (the facility's billing
record, obtained by the resident) lists therapy sessions by date, code and
units. When the resident says the sessions never happened, the one PUBLIC
record that can contradict the ledger on a given DAY is the facility's own
Payroll Based Journal: every SNF reports to CMS, per calendar day, the hours
worked by each discipline, therapists included (the "Daily Non-Nurse Staffing"
file on data.cms.gov: Hrs_OT, Hrs_OTasst, Hrs_PT, Hrs_PTasst, Hrs_SLP ...).

The logic is deliberately narrow. PBJ hours are FACILITY-WIDE, so a resident's
two 15-minute units on a day when the home reported eight OT hours proves
nothing either way. A session on a day when the home reported ZERO hours of
that discipline is the contradiction: the facility told CMS no occupational
therapist worked that day and told the payer that one treated this resident.
That is the only verdict this module calls; everything else is context (hours
per resident-day, weekend pattern, days with no therapy at all).

Inputs
  --pbj-dir / --files   the Non-Nurse Staffing CSVs (one per quarter; the
                        2025Q1..Q3 editions for the Wesleyan dates). The file
                        is read through DuckDB with a WHERE on the CCN, so a
                        600 MB quarter costs seconds and no RAM: safe on the
                        laptop, unlike anything that touches the spending fact.
  --ccn                 the facility's CMS Certification Number
  --sessions            a CSV with columns date, discipline (ot/pt/slp), units,
                        transcribed from the resident's ledger. It is a
                        resident's record: it lives under
                        <data_root>/enforcement/pi_reports/, never in git.
Output: Markdown (stdout or --out) + the per-session verdict CSV beside it.

Leads for counsel, never a finding: a zero-hours day can be a PBJ reporting
error, a therapist on a contract the home failed to submit, or a date-entry
slip on the ledger. The report lists those innocent explanations first.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns
from src.attempt_2.duck import connect as _duck_connect

DISCIPLINES = ("ot", "pt", "slp")

# total-hours columns per discipline; the _emp / _ctr suffixed columns are the
# employee / contract SPLIT of the same hours (components, never added twice)
_THERAPY_PATTERNS = {
    "ot": re.compile(r"^hrs_ot(asst|aide)?$", re.I),
    "pt": re.compile(r"^hrs_pt(asst|aide)?$", re.I),
    "slp": re.compile(r"^hrs_slp(asst|aide)?$|^hrs_speech", re.I),
}
_COMPONENT = re.compile(r"_(emp|ctr)$", re.I)

KEY_COLS = {
    "ccn": ["PROVNUM", "provnum", "CMS Certification Number (CCN)",
            "cms_certification_number_ccn", "CCN", "ccn"],
    "workdate": ["WorkDate", "WORKDATE", "workdate", "work_date"],
    "census": ["MDScensus", "MDSCENSUS", "mdscensus"],
}

INNOCENT_EXPLANATIONS = [
    "PBJ reporting error: the home omitted a contract therapist's hours for the day (PBJ audits find this; ask for the therapy company's own timesheets)",
    "Ledger date slip: the session happened on an adjacent day and was keyed to the wrong date (the therapy note carries the real date)",
    "Group / concurrent session logged by a therapist whose hours were reported under another job code",
    "The resident's own account is from memory; the signed therapy note and the MDS Section O minutes are the documents that settle it",
]


def discover_therapy_columns(header: list[str]) -> dict[str, list[str]]:
    """discipline -> the hours columns to SUM for it (totals; components only
    when no total exists for that role)."""
    out: dict[str, list[str]] = {}
    for disc, pat in _THERAPY_PATTERNS.items():
        totals = [c for c in header if pat.match(c.strip())]
        if totals:
            out[disc] = totals
            continue
        comps = [c for c in header if _COMPONENT.search(c) and pat.match(_COMPONENT.sub("", c.strip()))]
        if comps:
            out[disc] = comps
    return out


def _header(con, path: str) -> list[str]:
    rows = con.execute(
        "DESCRIBE SELECT * FROM read_csv_auto(?, all_varchar=true, encoding='latin-1')", [path]
    ).fetchall()
    return [r[0] for r in rows]


def facility_days(files: list[str | Path], ccn: str, start: str | None = None,
                  end: str | None = None, con=None) -> pd.DataFrame:
    """Per-day therapy hours for ONE CCN across the given PBJ files.

    Columns: date, census, ot_hours, pt_hours, slp_hours, <disc>_per_100 (hours
    per 100 residents), weekday, is_weekend, source_file. Files whose header
    carries no therapy column at all (the nurse file) are skipped by name."""
    con = con or _duck_connect()
    ccn = str(ccn).strip().zfill(6)
    frames = []
    for f in files:
        f = str(f)
        hdr = _header(con, f)
        keys = _resolve_columns(hdr, KEY_COLS)
        ther = discover_therapy_columns(hdr)
        if "ccn" not in keys or "workdate" not in keys:
            print(f"[pbj_therapy] skipped {Path(f).name}: no CCN / WorkDate column")
            continue
        if not ther:
            print(f"[pbj_therapy] skipped {Path(f).name}: no therapy hours columns (the nurse file?)")
            continue
        sel = [f'"{keys["ccn"]}" AS ccn', f'"{keys["workdate"]}" AS workdate']
        sel.append(f'TRY_CAST("{keys["census"]}" AS DOUBLE) AS census' if "census" in keys else "NULL AS census")
        for disc in DISCIPLINES:
            cols = ther.get(disc)
            if cols:
                sel.append(" + ".join(f'COALESCE(TRY_CAST("{c}" AS DOUBLE), 0)' for c in cols) + f" AS {disc}_hours")
            else:
                sel.append(f"NULL AS {disc}_hours")
        q = (f"SELECT {', '.join(sel)} FROM read_csv_auto(?, all_varchar=true, encoding='latin-1') "
             f"WHERE lpad(trim(\"{keys['ccn']}\"), 6, '0') = ?")
        df = con.execute(q, [f, ccn]).df()
        df["source_file"] = Path(f).name
        print(f"[pbj_therapy] {Path(f).name}: {len(df)} days for CCN {ccn}; therapy columns "
              + ", ".join(f"{d}={ther[d]}" for d in ther))
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["date", "census", "ot_hours", "pt_hours", "slp_hours", "source_file"])
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["workdate"].astype(str).str.strip().str[:8], format="%Y%m%d", errors="coerce")
    bad = out["date"].isna()
    if bad.any():          # a non-numeric date format on another edition
        out.loc[bad, "date"] = pd.to_datetime(out.loc[bad, "workdate"], errors="coerce")
    out = out.dropna(subset=["date"]).drop(columns=["workdate"]).sort_values("date")
    out = out.drop_duplicates("date", keep="last").reset_index(drop=True)
    if start:
        out = out[out["date"] >= pd.Timestamp(start)]
    if end:
        out = out[out["date"] <= pd.Timestamp(end)]
    for disc in DISCIPLINES:
        h = out[f"{disc}_hours"]
        out[f"{disc}_per_100"] = (h / out["census"] * 100).where(out["census"] > 0)
    out["weekday"] = out["date"].dt.day_name()
    out["is_weekend"] = out["date"].dt.dayofweek >= 5
    return out.reset_index(drop=True)


def compare_sessions(days: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    """One row per ledger session with the facility's reported hours of that
    discipline on that day and a verdict:
      no_hours_reported   the home told CMS 0 hours of the discipline that day
      day_not_reported    no PBJ row for the day (the home submitted nothing)
      hours_reported      hours exist facility-wide (says nothing about this resident)
    """
    s = sessions.copy()
    s["date"] = pd.to_datetime(s["date"], errors="coerce")
    s["discipline"] = s["discipline"].astype(str).str.strip().str.lower()
    s["units"] = pd.to_numeric(s.get("units", 1), errors="coerce").fillna(1)
    unknown = sorted(set(s["discipline"]) - set(DISCIPLINES))
    if unknown:
        raise ValueError(f"sessions discipline must be one of {DISCIPLINES}; got {unknown}")
    d = days.set_index("date") if len(days) else pd.DataFrame()
    rows = []
    for _, r in s.iterrows():
        hrs = None
        reported = r["date"] in d.index if len(d) else False
        if reported:
            hrs = d.at[r["date"], f"{r['discipline']}_hours"]
        if not reported:
            verdict = "day_not_reported"
        elif pd.isna(hrs):
            verdict = "day_not_reported"
        elif float(hrs) <= 0:
            verdict = "no_hours_reported"
        else:
            verdict = "hours_reported"
        rows.append({**r.to_dict(), "facility_hours": hrs,
                     "census": d.at[r["date"], "census"] if reported else None,
                     "weekday": r["date"].day_name() if pd.notna(r["date"]) else "",
                     "verdict": verdict})
    return pd.DataFrame(rows)


def summarize(days: pd.DataFrame, cmp: pd.DataFrame | None) -> dict:
    out: dict = {"n_days": int(len(days))}
    for disc in DISCIPLINES:
        h = days[f"{disc}_hours"] if len(days) else pd.Series(dtype=float)
        c = days["census"] if len(days) else pd.Series(dtype=float)
        out[f"{disc}_days_zero"] = int((h.fillna(0) <= 0).sum()) if len(h) else 0
        out[f"{disc}_hours_total"] = float(h.fillna(0).sum()) if len(h) else 0.0
        out[f"{disc}_hours_per_resident_day"] = (
            float(h.fillna(0).sum() / c.fillna(0).sum()) if len(c) and c.fillna(0).sum() > 0 else None)
        if len(days):
            wk = days.loc[~days["is_weekend"], f"{disc}_hours"].fillna(0)
            we = days.loc[days["is_weekend"], f"{disc}_hours"].fillna(0)
            out[f"{disc}_weekday_mean"] = float(wk.mean()) if len(wk) else None
            out[f"{disc}_weekend_mean"] = float(we.mean()) if len(we) else None
    if cmp is not None and len(cmp):
        out["n_sessions"] = int(len(cmp))
        out["sessions_no_hours"] = int((cmp["verdict"] == "no_hours_reported").sum())
        out["sessions_day_not_reported"] = int((cmp["verdict"] == "day_not_reported").sum())
        out["sessions_hours_reported"] = int((cmp["verdict"] == "hours_reported").sum())
        out["sessions_weekend"] = int(cmp["date"].dt.dayofweek.ge(5).sum())
    return out


def to_markdown(ccn: str, days: pd.DataFrame, cmp: pd.DataFrame | None, summ: dict) -> str:
    L = [f"# PBJ therapy hours — CCN {ccn}", ""]
    if not len(days):
        L.append("No PBJ rows for this CCN in the files given (wrong CCN, wrong quarter, or the nurse file instead of the non-nurse file).")
        return "\n".join(L) + "\n"
    L.append(f"Window {days['date'].min().date()} to {days['date'].max().date()}: {summ['n_days']} reported days.")
    L.append("")
    L.append("| discipline | hours total | hours per resident-day | days at zero | weekday mean | weekend mean |")
    L.append("|---|---:|---:|---:|---:|---:|")
    def _n(v, fmt):
        return "" if v is None else format(v, fmt)
    for disc in DISCIPLINES:
        L.append(f"| {disc.upper()} | {summ[disc + '_hours_total']:.1f} | "
                 f"{_n(summ.get(disc + '_hours_per_resident_day'), '.3f')} | {summ[disc + '_days_zero']} | "
                 f"{_n(summ.get(disc + '_weekday_mean'), '.1f')} | "
                 f"{_n(summ.get(disc + '_weekend_mean'), '.1f')} |")
    if cmp is not None and len(cmp):
        L += ["", "## Ledger sessions against the facility's reported hours", "",
              f"{summ['n_sessions']} sessions: **{summ['sessions_no_hours']} on a day the home reported ZERO hours "
              f"of that discipline**, {summ['sessions_day_not_reported']} on a day with no PBJ row, "
              f"{summ['sessions_hours_reported']} on a day with hours reported (facility-wide; says nothing about this resident). "
              f"{summ['sessions_weekend']} on a weekend.", "",
              "| date | weekday | discipline | units | facility hours | census | verdict |", "|---|---|---|---:|---:|---:|---|"]
        for _, r in cmp.sort_values("date").iterrows():
            fh = "" if r["facility_hours"] is None or pd.isna(r["facility_hours"]) else f"{float(r['facility_hours']):.2f}"
            cs = "" if r["census"] is None or pd.isna(r["census"]) else f"{float(r['census']):.0f}"
            L.append(f"| {r['date'].date()} | {r['weekday']} | {r['discipline'].upper()} | {int(r['units'])} | {fh} | {cs} | {r['verdict']} |")
    L += ["", "## Innocent explanations to rule out first", ""]
    L += [f"- {e}" for e in INNOCENT_EXPLANATIONS]
    L += ["", "## Daily hours", "", "| date | weekday | census | OT | PT | SLP |", "|---|---|---:|---:|---:|---:|"]
    for _, r in days.iterrows():
        def _f(v):
            return "" if pd.isna(v) else f"{float(v):.2f}"
        L.append(f"| {r['date'].date()} | {r['weekday']} | {_f(r['census'])} | {_f(r['ot_hours'])} | {_f(r['pt_hours'])} | {_f(r['slp_hours'])} |")
    L.append("")
    L.append("Source: CMS Payroll Based Journal, Daily Non-Nurse Staffing (the facility's own submission). "
             "A resident's ledger is a resident's record and stays under enforcement/pi_reports/.")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbj-dir", default=None, help="folder of PBJ Non-Nurse Staffing CSVs (every *.csv is tried; nurse files skip themselves)")
    ap.add_argument("--files", default=None, help="comma-separated CSV paths instead of --pbj-dir")
    ap.add_argument("--ccn", required=True)
    ap.add_argument("--start", default=None, help="YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD")
    ap.add_argument("--sessions", default=None, help="CSV: date, discipline (ot/pt/slp), units")
    ap.add_argument("--out", default=None, help="Markdown path; the verdict CSV sits beside it")
    args = ap.parse_args()
    if args.files:
        files = [p.strip() for p in args.files.split(",") if p.strip()]
    elif args.pbj_dir:
        files = sorted(str(p) for p in Path(args.pbj_dir).glob("*.csv"))
    else:
        ap.error("give --pbj-dir or --files")
    if not files:
        ap.error("no CSV files found")
    days = facility_days(files, args.ccn, args.start, args.end)
    cmp = None
    if args.sessions:
        cmp = compare_sessions(days, pd.read_csv(args.sessions, dtype=str))
    summ = summarize(days, cmp)
    md = to_markdown(str(args.ccn).zfill(6), days, cmp, summ)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        days.to_csv(out.with_suffix(".days.csv"), index=False)
        if cmp is not None:
            cmp.to_csv(out.with_suffix(".sessions.csv"), index=False)
        print(f"[pbj_therapy] wrote {out}")
    else:
        print(md)
    if cmp is not None and len(cmp):
        print(f"[pbj_therapy] {summ['n_sessions']} sessions: {summ['sessions_no_hours']} on zero-hours days, "
              f"{summ['sessions_day_not_reported']} on unreported days, {summ['sessions_hours_reported']} with hours reported")


if __name__ == "__main__":
    main()
