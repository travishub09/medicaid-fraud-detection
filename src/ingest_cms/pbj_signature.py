"""
pbj_signature.py — does a nursing home's reported staffing carry the signature of a
schedule or of a payroll? (2026-11-04, from the analyst's Ohio read)

Every SNF reports to CMS, per calendar day, the hours every nursing role worked (the
Payroll Based Journal daily nurse staffing file). The hours feed the staffing star and
the overall rating, and a state's quality payment where it reads staffing. The analyst
compared twelve Ohio homes against all 920 in one quarter and found two shapes: homes
in the bottom 5 percent of hours per resident-day with a fifth of their hours from
agency staff, and ONE home whose reported hours sit at the Ohio average while
inspectors cited it four times for too few staff. Average hours beside repeated
findings of too few staff is a contradiction between two of the facility's own filings;
the way to tell an overstated PBJ from a badly distributed one is inside the file.

Fabricated hours leave a signature a payroll does not:
  identical_day_share   days whose total nurse hours equal the previous day's (to 0.01 h)
  round_share           days whose total is a whole number of hours (a payroll sums
                        minutes; a schedule is typed in whole shifts)
  weekend_ratio         weekend hours per resident-day over weekday (a real roster thins
                        at weekends; a typed one does not)
  census_corr           correlation of daily total hours with the daily census over the
                        quarter (staffing follows the census; a typed roster ignores it)
  cv_hprd               coefficient of variation of daily hours per resident-day
                        (smoothness; the analyst's "steadier than 96 percent of homes")
plus the level facts: hprd (total nurse hours per resident-day), rn_hprd, agency_share
(contract hours over total), and the census. Every metric is ranked against every home
in the same STATE and quarter (one-sided percentiles: the share of homes that look more
like a payroll), so a home reads as "smoother than 96 percent" on the state's own
distribution, and the quarters on disk give the before-and-after around an ownership
change (--events: ccn, date, label).

    python -m src.ingest_cms.pbj_signature --pbj-dir <data_root>/preclean/facility \
        --pbj-dir <data_root>/preclean/facility_current_hold --state OH \
        --ccns 365xxx,366xxx --out <folder>  [--events events.csv]

Reads every pbj_daily_staffing_*.csv (or .zip) through DuckDB with a WHERE on the
state, so a 600 MB quarter costs seconds. Innocent explanations are printed first:
a small, stable home has little day-to-day variation; a home with a fixed shift
pattern and salaried staff shows round totals honestly; a quarter with a census under
20 makes every ratio noisy; PBJ audits find omitted contract hours as often as padded
ones. Leads for counsel, never a finding; the test of a padded PBJ is the payroll and
the time clock, which a records request or a former scheduler supplies.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .pbj_therapy import _csv_encoding, _duck_connect, _header, _materialize

KEY = {
    "ccn": ["PROVNUM", "provnum", "CMS Certification Number (CCN)", "cms_certification_number_ccn", "CCN", "ccn"],
    "state": ["STATE", "State", "state"],
    "workdate": ["WorkDate", "WORKDATE", "workdate", "work_date"],
    "census": ["MDScensus", "MDSCENSUS", "mdscensus"],
}
# the nursing roles whose TOTAL columns sum to total nurse staffing (CMS's own definition:
# RN director / admin / staff, LPN admin / staff, aides, trainees, medication aides);
# the _emp / _ctr suffixes are the employee / contract split of the same hours
_ROLE = re.compile(r"^hrs_(rndon|rnadmin|rn|lpnadmin|lpn|cna|natrn|medaide)$", re.I)
_RN = re.compile(r"^hrs_(rndon|rnadmin|rn)$", re.I)
_CTR = re.compile(r"^hrs_(rndon|rnadmin|rn|lpnadmin|lpn|cna|natrn|medaide)_ctr$", re.I)

METRICS = ["hprd", "rn_hprd", "agency_share", "identical_day_share", "round_share",
           "weekend_ratio", "census_corr", "cv_hprd"]
# the direction in which a home looks MORE like a typed schedule / less like a payroll
# (the percentile is the share of the state's homes that look LESS like that)
FABRICATION_DIRECTION = {"identical_day_share": 1, "round_share": 1, "weekend_ratio": 1,
                         "census_corr": -1, "cv_hprd": -1, "agency_share": 1, "hprd": -1, "rn_hprd": -1}
MIN_CENSUS = 20
MIN_DAYS = 60

INNOCENT = [
    "a small, stable home: few residents and a fixed roster give little day-to-day variation honestly",
    "salaried staff on fixed shifts: whole-hour totals are what a shift schedule produces when nobody clocks minutes",
    "a quarter with a census under 20 or fewer than 60 reported days: every ratio is noise",
    "PBJ audits find omitted contract hours as often as padded ones; an agency's own invoices settle the agency share",
    "a home that staffs to a fixed minimum rather than to the census (a policy, not a fabrication)",
    "the citation for insufficient staff may concern ONE shift or unit while the daily total was adequate",
]


def _resolve(header: list[str]) -> dict:
    from src.attempt_2.clean_data import _resolve_columns
    return _resolve_columns(header, KEY)


def facility_days(files: list[str | Path], state: str, con=None, tmp_dir=None) -> pd.DataFrame:
    """Per (ccn, date) for every home in ``state`` across the given PBJ nurse files:
    census, total nurse hours, RN hours, contract hours, quarter (from the file's
    CY_Qtr or the date). Files without the nursing role columns are skipped by name."""
    con = con or _duck_connect()
    frames = []
    for f in files:
        path = _materialize(str(f), tmp_dir)
        enc = _csv_encoding(con, path)
        header = _header(con, path, enc)
        keys = _resolve(header)
        roles = [c for c in header if _ROLE.match(c.strip())]
        if not roles or not {"ccn", "workdate", "census"} <= set(keys):
            print(f"[pbj_signature] skipped {Path(path).name}: no nursing-role columns (the non-nurse file?)")
            continue
        rn = [c for c in header if _RN.match(c.strip())]
        ctr = [c for c in header if _CTR.match(c.strip())]
        num = lambda c: f"COALESCE(TRY_CAST(\"{c}\" AS DOUBLE), 0)"   # noqa: E731
        tot = " + ".join(num(c) for c in roles)
        rn_sql = " + ".join(num(c) for c in rn) if rn else "0"
        ctr_sql = " + ".join(num(c) for c in ctr) if ctr else "NULL"
        qcol = next((c for c in header if c.strip().lower() in ("cy_qtr", "cyqtr", "quarter")), None)
        q_sql = f'"{qcol}"' if qcol else "NULL"
        where = f"upper(trim(\"{keys['state']}\")) = '{state.upper()}'" if "state" in keys else "TRUE"
        df = con.execute(f"""
            SELECT lpad(trim(CAST("{keys['ccn']}" AS VARCHAR)), 6, '0') AS ccn,
                   COALESCE(TRY_CAST("{keys['workdate']}" AS DATE),
                            CAST(TRY_STRPTIME(trim("{keys['workdate']}"), '%Y%m%d') AS DATE),
                            CAST(TRY_STRPTIME(trim("{keys['workdate']}"), '%m/%d/%Y') AS DATE)) AS date,
                   TRY_CAST("{keys['census']}" AS DOUBLE) AS census,
                   {tot} AS hours, {rn_sql} AS rn_hours, {ctr_sql} AS ctr_hours,
                   {q_sql} AS quarter
            FROM read_csv_auto(?, all_varchar=true, encoding='{enc}')
            WHERE {where}""", [path]).df()
        df["source_file"] = Path(path).name
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["ccn", "date", "census", "hours", "rn_hours", "ctr_hours", "quarter", "source_file"])
    out = pd.concat(frames, ignore_index=True).dropna(subset=["date"])
    out["date"] = pd.to_datetime(out["date"])
    q = out["quarter"].fillna("").astype(str).str.strip()
    derived = out["date"].dt.year.astype(str) + "Q" + out["date"].dt.quarter.astype(str)
    out["quarter"] = np.where(q.eq(""), derived, q)
    return out.sort_values(["ccn", "date"]).reset_index(drop=True)


def _metrics(g: pd.DataFrame) -> dict:
    g = g.sort_values("date")
    census = g["census"].fillna(0)
    hours = g["hours"].fillna(0)
    days = int(len(g))
    tot_census = float(census.sum())
    hprd = float(hours.sum() / tot_census) if tot_census > 0 else np.nan
    rn_hprd = float(g["rn_hours"].fillna(0).sum() / tot_census) if tot_census > 0 else np.nan
    ctr = g["ctr_hours"]
    agency = float(ctr.fillna(0).sum() / hours.sum()) if ctr.notna().any() and hours.sum() > 0 else np.nan
    prev = hours.shift(1)
    identical = float(((hours.round(2) == prev.round(2)) & (hours > 0)).sum() / max(days - 1, 1))
    rnd = float(((hours > 0) & (np.isclose(hours, hours.round(0), atol=0.005))).sum() / max(days, 1))
    daily_hprd = (hours / census.replace(0, np.nan))
    wk = g["date"].dt.weekday
    we, wd = daily_hprd[wk >= 5], daily_hprd[wk < 5]
    weekend_ratio = float(we.mean() / wd.mean()) if len(we) and len(wd) and wd.mean() > 0 else np.nan
    if days > 2 and hours.std() > 0 and census.std() > 0:
        corr = float(np.corrcoef(hours, census)[0, 1])
    elif days > 2 and census.std() > 0:
        corr = 0.0                       # hours never moved while the census did: fully decoupled
    else:
        corr = np.nan
    cv = float(daily_hprd.std() / daily_hprd.mean()) if daily_hprd.notna().sum() > 2 and daily_hprd.mean() > 0 else np.nan
    return {"days": days, "mean_census": float(census.mean()), "hprd": hprd, "rn_hprd": rn_hprd,
            "agency_share": agency, "identical_day_share": identical, "round_share": rnd,
            "weekend_ratio": weekend_ratio, "census_corr": corr, "cv_hprd": cv}


def quarter_metrics(days: pd.DataFrame) -> pd.DataFrame:
    """One row per (ccn, quarter) with the METRICS, `assessable` (census and days floors)
    and, per metric, `<m>__state_pct` = the share of the state's assessable homes that
    look LESS like a typed schedule on that metric (FABRICATION_DIRECTION)."""
    if days is None or not len(days):
        return pd.DataFrame(columns=["ccn", "quarter", "days", "mean_census", "assessable"] + METRICS)
    rows = []
    for (ccn, q), g in days.groupby(["ccn", "quarter"]):
        m = _metrics(g)
        m.update({"ccn": ccn, "quarter": q})
        rows.append(m)
    out = pd.DataFrame(rows)
    out["assessable"] = (out["mean_census"] >= MIN_CENSUS) & (out["days"] >= MIN_DAYS)
    for m in METRICS:
        out[f"{m}__state_pct"] = np.nan
        for q, g in out[out["assessable"]].groupby("quarter"):
            v = g[m].dropna()
            if len(v) < 10:
                continue
            d = FABRICATION_DIRECTION[m]
            ranked = (v * d).rank(pct=True, method="average")
            out.loc[ranked.index, f"{m}__state_pct"] = ranked.values
    return out.sort_values(["ccn", "quarter"]).reset_index(drop=True)


def signature_score(qm: pd.DataFrame) -> pd.Series:
    """Mean of the four fabrication-signature percentiles (identical days, round totals,
    weekend flatness, census decoupling) where at least three are present; NaN otherwise."""
    cols = [f"{m}__state_pct" for m in ("identical_day_share", "round_share", "weekend_ratio", "census_corr")]
    have = qm[cols].notna().sum(axis=1)
    return qm[cols].mean(axis=1).where(have >= 3)


def before_after(qm: pd.DataFrame, events: pd.DataFrame | None) -> pd.DataFrame:
    """Per event (ccn, date, label): the mean of each metric over the quarters before and
    after the event date, for the quarters on disk."""
    cols = ["ccn", "label", "date", "quarters_before", "quarters_after"] + [f"{m}_before" for m in METRICS] + [f"{m}_after" for m in METRICS]
    if events is None or not len(events) or not len(qm):
        return pd.DataFrame(columns=cols)
    qstart = pd.to_datetime(qm["quarter"].str.replace(r"(\d{4})Q(\d)", lambda m: f"{m.group(1)}-{int(m.group(2)) * 3 - 2:02d}-01", regex=True), errors="coerce")
    rows = []
    for e in events.itertuples():
        d = pd.to_datetime(e.date, errors="coerce")
        sub = qm[qm["ccn"].astype(str) == str(e.ccn).zfill(6)]
        qs = qstart[sub.index]
        before, after = sub[qs < d], sub[qs >= d]
        row = {"ccn": str(e.ccn).zfill(6), "label": getattr(e, "label", ""), "date": str(d.date()) if pd.notna(d) else "",
               "quarters_before": int(len(before)), "quarters_after": int(len(after))}
        for m in METRICS:
            row[f"{m}_before"] = float(before[m].mean()) if len(before) else np.nan
            row[f"{m}_after"] = float(after[m].mean()) if len(after) else np.nan
        rows.append(row)
    return pd.DataFrame(rows, columns=cols)


def to_markdown(qm: pd.DataFrame, ccns: list[str], state: str, files: list[str], ba: pd.DataFrame | None = None,
                names: dict | None = None) -> str:
    names = names or {}
    L = ["# PBJ SIGNATURE: does the reported staffing look like a payroll or a typed schedule?", "",
         f"_State {state}; files: {', '.join(files)}. Every metric is ranked against every assessable home in the "
         f"state and quarter (census >= {MIN_CENSUS}, >= {MIN_DAYS} days); a percentile is the share of homes that look "
         "LESS like a typed schedule on it. `signature` = the mean of the four fabrication percentiles. A high "
         "signature is a reason to put the PBJ against the payroll and the time clock, never a finding._", "",
         "**Innocent explanations to rule out first.** " + "; ".join(INNOCENT) + ".", ""]
    tgt = qm[qm["ccn"].isin([c.zfill(6) for c in ccns])].copy()
    if not len(tgt):
        L.append("_None of the requested CCNs appear in the files read._")
        return "\n".join(L) + "\n"
    tgt["signature"] = signature_score(tgt)
    L += ["| ccn | home | quarter | days | census | hprd | st pct | RN hprd | agency | identical days | round | weekend ratio | census corr | cv | signature |",
          "|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    f = lambda v, p=2: "" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{p}f}"   # noqa: E731
    pc = lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.0%}"       # noqa: E731
    for r in tgt.sort_values(["ccn", "quarter"]).itertuples():
        L.append(f"| {r.ccn} | {names.get(r.ccn, '')[:34]} | {r.quarter} | {r.days} | {f(r.mean_census, 0)} | {f(r.hprd)} | "
                 f"{pc(r.hprd__state_pct)} | {f(r.rn_hprd)} | {pc(r.agency_share) if not np.isnan(r.agency_share) else ''} "
                 f"({pc(r.agency_share__state_pct)}) | {pc(r.identical_day_share)} ({pc(r.identical_day_share__state_pct)}) | "
                 f"{pc(r.round_share)} ({pc(r.round_share__state_pct)}) | {f(r.weekend_ratio)} ({pc(r.weekend_ratio__state_pct)}) | "
                 f"{f(r.census_corr)} ({pc(r.census_corr__state_pct)}) | {f(r.cv_hprd)} ({pc(r.cv_hprd__state_pct)}) | "
                 f"{pc(r.signature)}{'' if r.assessable else ' (not assessable)'} |")
    L += ["", "_Columns in parentheses are the state percentile for that quarter. hprd = total nurse hours per "
          "resident-day (st pct = share of homes with MORE hours). The level facts (hprd, RN, agency) are the "
          "worthless-services shape; the four signature columns are the fabrication shape; a home high on BOTH "
          "is the one to put against payroll first._"]
    if ba is not None and len(ba):
        L += ["", "## Before and after (the quarters on disk around each event)", "",
              "| ccn | event | date | quarters before / after | hprd before -> after | agency before -> after | identical days before -> after | signature inputs before -> after |",
              "|---|---|---|--:|--:|--:|--:|--:|"]
        for r in ba.itertuples():
            L.append(f"| {r.ccn} | {r.label} | {r.date} | {r.quarters_before} / {r.quarters_after} | "
                     f"{f(r.hprd_before)} -> {f(r.hprd_after)} | {pc(r.agency_share_before)} -> {pc(r.agency_share_after)} | "
                     f"{pc(r.identical_day_share_before)} -> {pc(r.identical_day_share_after)} | "
                     f"wk {f(r.weekend_ratio_before)} -> {f(r.weekend_ratio_after)}, corr {f(r.census_corr_before)} -> {f(r.census_corr_after)} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbj-dir", action="append", default=[], help="folder(s) of PBJ daily NURSE staffing files (repeatable)")
    ap.add_argument("--files", default="", help="comma-separated csv / zip paths instead of --pbj-dir")
    ap.add_argument("--state", required=True, help="two-letter state: the peer distribution")
    ap.add_argument("--ccns", default="", help="comma-separated CCNs to report")
    ap.add_argument("--ccns-file", default="", help="csv with a ccn column (and optional name column)")
    ap.add_argument("--events", default="", help="csv with ccn, date, label: before / after per event")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--tmp-dir", default=None)
    a = ap.parse_args()
    files: list[str] = [p for p in a.files.split(",") if p.strip()]
    for d in a.pbj_dir:
        files += [str(p) for p in sorted(Path(d).glob("pbj_daily_staffing*")) if p.suffix.lower() in (".csv", ".zip")]
        files += [str(p) for p in sorted(Path(d).glob("PBJ_Daily_Nurse_Staffing*")) if p.suffix.lower() in (".csv", ".zip")]
    files = sorted(set(files))
    if not files:
        raise SystemExit("no PBJ nurse staffing files found (pbj_daily_staffing_*.csv under --pbj-dir)")
    ccns = [c.strip().zfill(6) for c in a.ccns.split(",") if c.strip()]
    names: dict = {}
    if a.ccns_file:
        t = pd.read_csv(a.ccns_file, dtype=str)
        col = next(c for c in t.columns if c.lower() in ("ccn", "provnum", "provider_number"))
        ccns += [str(c).strip().zfill(6) for c in t[col].dropna()]
        ncol = next((c for c in t.columns if "name" in c.lower()), None)
        if ncol:
            names = dict(zip(t[col].astype(str).str.zfill(6), t[ncol].astype(str)))
    ccns = sorted(set(ccns))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    days = facility_days(files, a.state, tmp_dir=a.tmp_dir)
    print(f"[pbj_signature] {len(days):,} facility-days for {days['ccn'].nunique():,} {a.state} homes over {len(files)} file(s)")
    qm = quarter_metrics(days)
    qm["signature"] = signature_score(qm)
    qm.to_csv(out / "pbj_signature_state.csv", index=False)
    events = pd.read_csv(a.events, dtype=str) if a.events else None
    ba = before_after(qm, events)
    md = to_markdown(qm, ccns, a.state, [Path(f).name for f in files], ba, names)
    (out / "PBJ_SIGNATURE.md").write_text(md, encoding="utf-8")
    qm[qm["ccn"].isin(ccns)].to_csv(out / "pbj_signature_targets.csv", index=False)
    if len(ba):
        ba.to_csv(out / "pbj_before_after.csv", index=False)
    print(f"[pbj_signature] {len(qm):,} home-quarters ranked; {int(qm['ccn'].isin(ccns).sum())} target rows -> {out / 'PBJ_SIGNATURE.md'}")


if __name__ == "__main__":
    main()
