"""
vintage_inspect.py — reach INSIDE every preclean file and settle its dates.

The EDITIONS worksheet asks the operator to fill in each undated file's data
period by hand. Most files can answer for themselves: they carry date columns
(service months, survey dates, processing stamps, measure ranges). This tool
opens every data file under preclean/, finds those columns, and streams their
MIN/MAX through DuckDB (so a 30 GB csv costs a scan, not RAM), producing:

  VINTAGE_INSPECT.md      per-file verdicts: period found inside / snapshot
                          stamp only / nothing dated / unreadable — plus the
                          right-file column check for every registry source
                          and the orphan list (files NO adapter reads)
  EDITIONS.csv            data_period auto-filled where the data answered
                          (operator-entered values are never overwritten)
  rename_plan.bat         reviewable renames adding the inferred period to
                          undated filenames — ONLY for files whose names are
                          not exact registry names (core files keep their
                          contract names; their period lives in EDITIONS).
                          Nothing is renamed until the operator runs the .bat.

The files still UNRESOLVED after this (no dates inside, none in the name) are
the true re-download list — the minimal set the operator must fetch fresh
with a dated name.

  python -m src.vintage_inspect --data-root C:/Users/treyr/OneDrive/Desktop/data
"""

from __future__ import annotations

import argparse
import re
import zipfile
from datetime import date as _date
from pathlib import Path

import pandas as pd

# columns that plausibly carry dates, by name
DATE_NAME_RE = re.compile(
    r"date|month|year|period|_dt$|updated|processing|survey|effective|"
    r"thru|through|start|end|asof|as_of|deactivat", re.IGNORECASE)
# formats tried, most specific first (DuckDB strptime patterns)
DATE_FMTS = ["%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%Y-%m", "%m/%d/%y", "%Y/%m/%d"]
YEAR_RE = re.compile(r"(?<!\d)(20[0-9]{2})(?!\d)")
# 8-digit YYYYMMDD stamps (e.g. 'Report 20260608.xlsx') — YEAR_RE can't see
# the year inside these because of its no-adjacent-digit guards
DATE8_RE = re.compile(r"(?<!\d)(20[0-9]{2})[01][0-9][0-3][0-9](?!\d)")

# sources whose canonical file NAMES are contracts (never auto-rename);
# code_legality is a built, reviewed reference other modules cite by path
PROTECTED_NAMES = {"spending.csv", "nppes.csv", "pecos.csv", "caught.csv",
                   "editions.csv", "code_legality.csv"}

# required columns per source (the runbook Part-6 table, machine-run).
# folder-key → (any-of-these-files description, [required header fragments])
REQUIRED_COLS = {
    "partb": ["Rndrng_NPI", "HCPCS_Cd", "Tot_Srvcs"],
    "partd": ["Prscrbr_NPI", "Tot_Clms", "Tot_Drug_Cst"],
    "dmepos": ["Rfrg_NPI", "HCPCS_Cd"],
    "opioid": ["Prscrbr_NPI", "Opioid_Tot_Clms"],
    "open_payments": ["Covered_Recipient_NPI",
                      "Total_Amount_of_Payment_USDollars"],
    "pos": ["PRVDR_NUM", "BED_CNT"],
    # '|' = any-of aliases: data.cms.gov flattened cost reports use Title Case
    "hcris": ["PRVDR_NUM|Provider CCN",
              "TOTAL_COSTS|Total Costs|Total Cost"],
    "nadac": ["NDC", "NADAC Per Unit"],
    "order_referring": ["NPI", "PARTB", "DME"],
    "docgraph": ["from_npi", "to_npi"],
    "nucc": ["Code", "Grouping"],
    "revocations": ["NPI"],
    "saturation": ["Type of Service"],
}
# facility folder holds several layouts; checked per-file by name hint
FACILITY_CHECKS = [
    (re.compile(r"pbj", re.I), ["PROVNUM", "WorkDate", "Hrs_RN"]),
    (re.compile(r"citat|deficien", re.I),
     ["CMS Certification Number (CCN)", "Survey Date"]),
    (re.compile(r"hospice", re.I),
     ["CMS Certification Number (CCN)", "Measure Code", "Score"]),
]


def _duck():
    import duckdb
    return duckdb.connect()


def _read_header(p: Path) -> list[str] | None:
    """Header columns for csv/xlsx/parquet; None when unreadable. Banner-row
    files (the AMA-copyright class) are probed a few rows deep."""
    try:
        if p.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq
            return list(pq.ParquetFile(p).schema_arrow.names)
        if p.suffix.lower() in (".xlsx", ".xls"):
            df = pd.read_excel(p, nrows=5)
            return [str(c) for c in df.columns]
        # csv/txt: find the first row that looks like a header (>=3 named cols)
        con = _duck()
        for skip in (0, 1, 2, 3, 4, 5, 8, 10):
            try:
                cols = con.execute(
                    f"SELECT * FROM read_csv_auto('{p.as_posix()}', "
                    f"skip={skip}, sample_size=200, encoding='latin-1') "
                    f"LIMIT 0").df().columns
            except Exception:
                continue
            named = [c for c in cols if not str(c).startswith("column")]
            if len(named) >= 3:
                return [str(c) for c in cols]
        # pandas fallback with delimiter sniffing — clears files DuckDB's
        # probe can't parse (odd quoting, uncommon separators)
        try:
            df = pd.read_csv(p, nrows=5, sep=None, engine="python",
                             encoding="latin-1", dtype=str)
            named = [c for c in df.columns
                     if not str(c).startswith("Unnamed")]
            if len(named) >= 3:
                return [str(c) for c in df.columns]
        except Exception:
            pass
        return None
    except Exception:
        return None


def _date_minmax(p: Path, cols: list[str]) -> dict[str, tuple[str, str]]:
    """MIN/MAX of every date-named column, streamed via DuckDB try_strptime.
    Returns {col: (min, max)} for columns where any format parsed."""
    cand = [c for c in cols if DATE_NAME_RE.search(str(c))]
    if not cand or p.suffix.lower() not in (".csv", ".txt", ".parquet"):
        # xlsx are small: pandas fallback
        if cand and p.suffix.lower() in (".xlsx", ".xls"):
            try:
                df = pd.read_excel(p, usecols=cand)
                out = {}
                for c in cand:
                    d = pd.to_datetime(df[c], errors="coerce")
                    if d.notna().any():
                        out[c] = (str(d.min().date()), str(d.max().date()))
                return out
            except Exception:
                return {}
        return {}
    con = _duck()
    src = (f"read_parquet('{p.as_posix()}')" if p.suffix.lower() == ".parquet"
           else f"read_csv_auto('{p.as_posix()}', sample_size=1000, "
                f"encoding='latin-1', ignore_errors=true)")
    out: dict[str, tuple[str, str]] = {}
    for c in cand[:8]:                       # cap the per-file scan width
        cq = str(c).replace('"', '""')
        exprs = " , ".join(
            f"min(try_strptime(CAST(\"{cq}\" AS VARCHAR), '{f}')) , "
            f"max(try_strptime(CAST(\"{cq}\" AS VARCHAR), '{f}'))"
            for f in DATE_FMTS)
        try:
            row = con.execute(f"SELECT {exprs} FROM {src}").fetchone()
        except Exception:
            continue
        pairs = [(row[i], row[i + 1]) for i in range(0, len(row), 2)
                 if row[i] is not None]
        if pairs:
            lo = min(x[0] for x in pairs)
            hi = max(x[1] for x in pairs)
            out[str(c)] = (str(lo)[:10], str(hi)[:10])
    return out


def _zip_peek(p: Path) -> list[str]:
    try:
        with zipfile.ZipFile(p) as z:
            return z.namelist()[:10]
    except Exception:
        return []


def _required_check(rel: str, cols: list[str] | None) -> tuple[str, list[str]]:
    """(source_key, missing_required) for a file, by its folder / name."""
    parts = rel.replace("\\", "/").split("/")
    folder = parts[0] if len(parts) > 1 else ""
    fname = parts[-1].lower()
    if folder == "facility":
        for pat, req in FACILITY_CHECKS:
            if pat.search(parts[-1]):
                missing = [r for r in req if cols is None or r not in cols]
                return f"facility/{pat.pattern}", missing
        return "", []
    req = REQUIRED_COLS.get(folder)
    if folder == "pos" and ("clia" in fname or "iqies" in fname):
        req = ["PRVDR_NUM"]        # lab / iQIES facility classes: no beds
    if folder == "nucc" and "crosswalk" in fname:
        return "", []              # the crosswalk has its own layout
    if not req:
        return "", []
    # normalize '_' vs ' ' so Type of Service matches type_of_service
    def _norm(s):
        return re.sub(r"[_\s]+", " ", str(s)).strip().lower()
    low = {_norm(c) for c in (cols or [])}
    missing = [r for r in req
               if not any(_norm(a) in low for a in r.split("|"))]
    return folder, missing


def inspect_tree(data_root: Path) -> list[dict]:
    pre = Path(data_root) / "preclean"
    rows = []
    exts = {".csv", ".txt", ".xlsx", ".xls", ".zip", ".parquet"}
    for p in sorted(pre.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in exts \
                or p.name == "EDITIONS.csv":
            continue
        rel = str(p.relative_to(pre)).replace("\\", "/")
        held = "_hold" in rel or rel.endswith(".bak")
        # 8-digit YYYYMMDD stamps in filenames count as dated too
        name_years = YEAR_RE.findall(p.name) + DATE8_RE.findall(p.name)
        rec = {"file": rel, "size_mb": round(p.stat().st_size / 1e6, 1),
               "held": held, "year_in_name": "/".join(name_years),
               "columns": None, "date_cols": {}, "zip_inner": [],
               "inferred_period": "", "verdict": "", "source_check": "",
               "missing_required": []}
        if p.suffix.lower() == ".zip":
            rec["zip_inner"] = _zip_peek(p)
            inner_years = sorted({y for n in rec["zip_inner"]
                                  for y in (YEAR_RE.findall(n)
                                            + DATE8_RE.findall(n))})
            if inner_years:
                rec["inferred_period"] = "/".join(inner_years)
                rec["verdict"] = "PERIOD FROM INNER FILENAMES"
            else:
                rec["verdict"] = ("named" if name_years
                                  else "UNRESOLVED (zip, no dated inner names)")
        else:
            cols = _read_header(p)
            rec["columns"] = cols
            if cols:
                rec["date_cols"] = _date_minmax(p, cols)
                src, missing = _required_check(rel, cols)
                rec["source_check"], rec["missing_required"] = src, missing
            if rec["date_cols"]:
                his = [v[1] for v in rec["date_cols"].values()]
                los = [v[0] for v in rec["date_cols"].values()]
                rec["inferred_period"] = f"{min(los)} to {max(his)}"
                rec["verdict"] = "PERIOD FOUND INSIDE"
            elif name_years:
                rec["verdict"] = "named"
            elif cols is None:
                rec["verdict"] = "UNREADABLE"
            else:
                rec["verdict"] = "UNRESOLVED (no dates inside or in name)"
        rows.append(rec)
    return rows


def write_outputs(rows: list[dict], data_root: Path) -> Path:
    pre = Path(data_root) / "preclean"

    # 1. auto-fill EDITIONS data_period where blank and the data answered
    ed = pre / "EDITIONS.csv"
    n_filled = 0
    if ed.exists():
        table = pd.read_csv(ed, dtype=str).fillna("")
        by_file = {r["file"]: r for r in rows}
        for i, r in table.iterrows():
            rec = by_file.get(str(r["file"]))
            if rec and not str(r.get("data_period", "")).strip() \
                    and rec["inferred_period"]:
                table.at[i, "data_period"] = rec["inferred_period"]
                table.at[i, "notes"] = (str(r.get("notes", "")) +
                                        " [auto: from data]").strip()
                n_filled += 1
        table.to_csv(ed, index=False)

    # 2. reviewable rename plan for undated, non-protected, resolvable files
    plan = ["@echo off", "REM rename_plan.bat — REVIEW before running.",
            "REM Adds the inferred period to undated filenames. Core contract",
            "REM names (Spending/NPPES/PECOS/Caught) are never renamed."]
    n_ren = 0
    for r in rows:
        if r["held"] or r["year_in_name"] or not r["inferred_period"] \
                or r["verdict"] != "PERIOD FOUND INSIDE":
            continue
        base = r["file"].split("/")[-1]
        if base.lower() in PROTECTED_NAMES:
            continue
        hi_year = (YEAR_RE.findall(r["inferred_period"]) or [""])[-1]
        if not hi_year:
            continue
        # a max-date years in the future is an expiration/sentinel column,
        # not a data vintage — a '_thru2036' suffix would mislead
        if int(hi_year) > _date.today().year + 1:
            continue
        stem, ext = base.rsplit(".", 1)
        newname = f"{stem}_thru{hi_year}.{ext}"
        old = str(pre / r["file"]).replace("/", "\\")
        plan.append(f'ren "{old}" "{newname}"')
        n_ren += 1
    (pre / "rename_plan.bat").write_text("\r\n".join(plan) + "\r\n",
                                         encoding="utf-8")

    # 3. the report
    L = ["# VINTAGE INSPECT — what every file says about its own dates", ""]
    order = {"UNRESOLVED (no dates inside or in name)": 0,
             "UNRESOLVED (zip, no dated inner names)": 0, "UNREADABLE": 1,
             "PERIOD FOUND INSIDE": 2, "PERIOD FROM INNER FILENAMES": 2,
             "named": 3}
    rows_s = sorted(rows, key=lambda r: (order.get(r["verdict"], 9),
                                         r["file"]))
    unresolved = [r for r in rows_s if r["verdict"].startswith("UNRESOLVED")
                  and not r["held"]]
    L.append(f"_{len(rows)} files inspected; "
             f"{sum(1 for r in rows if r['verdict'] == 'PERIOD FOUND INSIDE')} "
             f"answered from their own data; {len(unresolved)} remain "
             f"UNRESOLVED (the true re-download list); EDITIONS auto-filled "
             f"for {n_filled}; {n_ren} rename(s) proposed in rename_plan.bat._")
    L.append("")
    L.append("## THE TRUE RE-DOWNLOAD LIST (nothing inside or outside dates these)")
    L.append("")
    if unresolved:
        for r in unresolved:
            L.append(f"- `{r['file']}` ({r['size_mb']} MB)"
                     + (f" — zip contains: {', '.join(r['zip_inner'][:4])}"
                        if r["zip_inner"] else ""))
    else:
        L.append("**None — every live file is dated by name or by content.**")
    L.append("")
    L.append("## Right-file column check (runbook Part 6, machine-run)")
    L.append("")
    bad = [r for r in rows_s if r["missing_required"] and not r["held"]]
    if bad:
        for r in bad:
            L.append(f"- WRONG-FILE? `{r['file']}` ({r['source_check']}): "
                     f"missing required {', '.join(r['missing_required'])}")
    else:
        L.append("All checked sources carry their required columns.")
    L.append("")
    L.append("## Orphans — files no adapter folder claims (discovery candidates)")
    L.append("")
    known = set(REQUIRED_COLS) | {"facility", "hcpcs_time", "reference",
                                  "opensanctions", "nppes_deactivation",
                                  "hrsa_340b", "owners", "owners_prior",
                                  "hud", "usps", "state_licensing",
                                  "preclusion", "dmf", "cms_priority", "mue",
                                  "reassignment", "census", "sam", "warn"}
    for r in rows_s:
        top = r["file"].split("/")[0]
        if "/" in r["file"] and top not in known and "_hold" not in top:
            L.append(f"- `{r['file']}` — folder `{top}` is not a wired source; "
                     "if this data is useful, it is scheme-discovery input")
    L.append("")
    L.append("## Full inventory")
    L.append("")
    L.append("| file | MB | verdict | inferred period | date columns used |")
    L.append("|---|--:|---|---|---|")
    for r in rows_s:
        held = " (hold)" if r["held"] else ""
        dc = "; ".join(f"{k} [{v[0]}→{v[1]}]"
                       for k, v in list(r["date_cols"].items())[:3])
        L.append(f"| {r['file']}{held} | {r['size_mb']} | {r['verdict']} "
                 f"| {r['inferred_period']} | {dc} |")
    out = pre / "VINTAGE_INSPECT.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"[vintage_inspect] {len(rows)} files -> {out}")
    print(f"[vintage_inspect] EDITIONS auto-filled: {n_filled}; renames "
          f"proposed: {n_ren}; TRUE re-download list: {len(unresolved)}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--data-root", required=True)
    args = ap.parse_args()
    rows = inspect_tree(Path(args.data_root))
    write_outputs(rows, Path(args.data_root))


if __name__ == "__main__":
    main()
