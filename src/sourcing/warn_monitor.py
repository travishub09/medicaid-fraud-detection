"""
warn_monitor.py — WARN layoff notices → canonical orgs → surge-timing leads.

Why WARN (docs/platform/02 + 07): a free, timed, *involuntary*-departure cohort
signal — one of the strongest propensity predictors — and its power multiplies
when the WARN'd employer is also Model-A-flagged. The 6–18-month post-departure
window is the outreach sweet spot, so a WARN notice is fundamentally a *timer*.

What this does:
  1. normalize raw WARN postings (state files differ; column map below);
  2. resolve employer names to canonical Organization nodes via the same
     ``norm_org_name`` key the entity-graph resolver uses (name + aliases);
  3. cross-reference against Model A's ERV ranking → org-level SURGE LEADS
     ("flagged org just had an involuntary-departure event, window opens now");
  4. route unmatched employers to an unmatched bucket — never silently dropped
     (they may match once the org universe grows).

Output is org-level timing signal for campaign planning — not a person list.
Person-level use (rosters × WARN) is Model B territory and gated on people-data.

Run:
    python -m src.sourcing.warn_monitor --warn preclean/warn/warn_TX.csv \
        --graph-dir ~/Desktop/data/graph --erv ~/Desktop/data/model_a/erv_ranked.parquet \
        --out ~/Desktop/data/sourcing
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name

# canonical name → candidate headers across state WARN postings
WARN_COLS = {
    "employer": ["COMPANY", "COMPANY NAME", "EMPLOYER", "EMPLOYER NAME", "BUSINESS NAME", "Company",
                 "JOB_SITE_NAME", "Job Site Name", "Name of Company", "Business"],
    "state": ["STATE", "State"],
    "notice_date": ["NOTICE_DATE", "NOTICE DATE", "WARN RECEIVED DATE", "Date Received", "Received Date",
                    "Date of Notice", "Notice Received", "NOTICE DATE RECEIVED", "Date Notice Received"],
    "layoff_date": ["LAYOFF_DATE", "LAYOFF DATE", "EFFECTIVE DATE", "Layoff Date", "LayOff_Date",
                    "Layoff/Closure Date", "Effective_Date", "Layoff Effective Date", "Closure Date"],
    "employees": ["EMPLOYEES", "NUMBER OF EMPLOYEES", "AFFECTED WORKERS", "No. Of Employees",
                  "TOTAL_LAYOFF_NUMBER", "# of Employees", "Employees Affected", "Number Affected",
                  "No. of Workers", "Affected Employees", "Total Employees"],
}

# the operator's save name is warn_<ST>_<yyyymm>.<csv|xlsx>; the state code in it
# fills the state column when the posting itself carries none (most do not)
_STATE_IN_NAME = re.compile(r"warn[_-]([A-Za-z]{2})[_-]", re.IGNORECASE)


# keyword fallback when no exact header matches (North Carolina 2026: employer =
# "WARN Notice: WARN Notice Name", count = "Number affected at this location")
WARN_KEYWORDS = {
    "employer": ["company", "employer", "business", "notice name", "job site", "firm name"],
    "employees": ["affected", "employee", "workers", "layoff number", "layoff_number", "headcount"],
    "notice_date": ["notice date", "date of notice", "date received", "received date", "notice_date"],
    "layoff_date": ["effective", "layoff date", "layoff_date", "closure date", "separation"],
    "state": [],
}


def _resolve(columns) -> dict:
    lower = {str(c).lower().strip(): c for c in columns}
    resolved = {}
    for canon, candidates in WARN_COLS.items():
        for cand in candidates:
            if cand.lower().strip() in lower:
                resolved[canon] = lower[cand.lower().strip()]
                break
        if canon in resolved:
            continue
        for kw in WARN_KEYWORDS.get(canon, []):
            hit = next((orig for low, orig in lower.items() if kw in low
                        and orig not in resolved.values()), None)
            if hit is not None:
                resolved[canon] = hit
                break
    return resolved


def read_warn_file(path) -> pd.DataFrame:
    """One state posting, csv or xlsx, as raw strings. State sites export Excel
    with a banner row or two above the header (California, Rhode Island), so an
    xlsx is probed up to eight rows deep for the row that carries an employer
    column. A `state` column is added from the filename (warn_CA_202610.xlsx)
    when the sheet has none."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        # every sheet, header probed up to eight rows deep: California's workbook
        # opens with an introduction sheet and keeps the postings on sheet 3
        # ("Detailed WARN Report"); Rhode Island puts a title row above the header
        raw = None
        try:
            sheets = pd.ExcelFile(p).sheet_names
        except Exception as e:
            raise ValueError(f"WARN file {p.name}: cannot open the workbook ({e})")
        for sheet in sheets:
            for skip in range(0, 9):
                try:
                    cand = pd.read_excel(p, sheet_name=sheet, dtype=str, header=skip)
                except Exception:
                    break
                if "employer" in _resolve(cand.columns) and len(cand):
                    raw = cand
                    break
            if raw is not None:
                break
        if raw is None:
            raise ValueError(f"WARN file {p.name}: no sheet carries an employer column in its "
                             f"first 9 rows (sheets: {', '.join(sheets)}); paste its first lines")
    else:
        from src.attempt_2.clean_data import read_csv_text
        raw = read_csv_text(str(p))
        if "employer" not in _resolve(raw.columns):
            for skip in range(1, 9):               # csv exports with a title row
                cand = read_csv_text(str(p), skiprows=skip)
                if "employer" in _resolve(cand.columns):
                    raw = cand
                    break
    if "state" not in _resolve(raw.columns):
        m = _STATE_IN_NAME.search(p.name)
        if m:
            raw = raw.copy()
            raw["STATE"] = m.group(1).upper()
    raw = raw.copy()
    raw["source_file"] = p.name
    return raw


def load_warn(path) -> pd.DataFrame:
    """A single posting or a folder of warn_*.csv / warn_*.xlsx postings ->
    one canonical frame (normalize_warn per file, concatenated)."""
    p = Path(path)
    files = sorted(list(p.glob("warn_*.csv")) + list(p.glob("warn_*.xlsx")) + list(p.glob("warn_*.xls"))) \
        if p.is_dir() else [p]
    if not files:
        raise FileNotFoundError(f"no warn_*.csv / warn_*.xlsx under {p}")
    frames = []
    for f in files:
        w = normalize_warn(read_warn_file(f))
        w["source_file"] = f.name
        frames.append(w)
    return pd.concat(frames, ignore_index=True)

SURGE_WINDOW_OPEN_MONTHS = 6     # outreach sweet spot opens ~6 months post-departure
SURGE_WINDOW_CLOSE_MONTHS = 18   # and fades by ~18


def normalize_warn(raw: pd.DataFrame) -> pd.DataFrame:
    """Map a raw WARN posting to the canonical schema; parse dates; keep strings."""
    resolved = _resolve(raw.columns)
    if "employer" not in resolved:
        raise ValueError(f"WARN file missing an employer column; saw {list(raw.columns)}")

    out = pd.DataFrame()
    out["employer_raw"] = raw[resolved["employer"]].fillna("").astype(str).str.strip()
    out["state"] = (raw[resolved["state"]].fillna("").astype(str).str.strip().str.upper()
                    if "state" in resolved else "")
    for d in ["notice_date", "layoff_date"]:
        out[d] = (pd.to_datetime(raw[resolved[d]], errors="coerce")
                  if d in resolved else pd.NaT)
    out["employees"] = (pd.to_numeric(raw[resolved["employees"]], errors="coerce")
                        if "employees" in resolved else pd.NA)
    out["employer_key"] = out["employer_raw"].map(norm_org_name)
    return out[out["employer_key"] != ""].reset_index(drop=True)


def _org_name_index(org_nodes: pd.DataFrame) -> tuple[dict[str, str], set[str]]:
    """employer_key → org_node_id over canonical names AND every alias.

    Returns ``(index, ambiguous_keys)``: a key claimed by more than one distinct
    org is AMBIGUOUS — matching it silently to the first org would pin a layoff
    on the wrong company, so the matcher flags those rows for human review
    instead (the decision-band principle).
    """
    idx: dict[str, str] = {}
    ambiguous: set[str] = set()
    for r in org_nodes.itertuples():
        names = {str(getattr(r, "org_name", "") or "")}
        names.update(a.strip() for a in str(getattr(r, "aliases", "") or "").split(";"))
        for n in names:
            k = norm_org_name(n)
            if not k:
                continue
            prev = idx.setdefault(k, str(r.org_node_id))
            if prev != str(r.org_node_id):
                ambiguous.add(k)
    return idx, ambiguous


def match_warn_to_orgs(warn: pd.DataFrame, org_nodes: pd.DataFrame
                       ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split normalized WARN rows into (matched-with-org_node_id, unmatched).

    Matched rows carry ``match_ambiguous`` (1 = the employer name maps to more
    than one org; kept and matched to the first, but flagged — verify before
    acting on it)."""
    idx, ambiguous = _org_name_index(org_nodes)
    warn = warn.copy()
    warn["org_node_id"] = warn["employer_key"].map(idx)
    warn["match_ambiguous"] = warn["employer_key"].isin(ambiguous).astype(int)
    matched = warn[warn["org_node_id"].notna()].reset_index(drop=True)
    unmatched = warn[warn["org_node_id"].isna()] \
        .drop(columns=["org_node_id", "match_ambiguous"]).reset_index(drop=True)
    return matched, unmatched


def surge_leads(matched: pd.DataFrame, erv_ranked: pd.DataFrame,
                top_fraction: float = 0.25,
                asof: pd.Timestamp | None = None) -> pd.DataFrame:
    """WARN events at orgs in the top ERV fraction → surge leads with the window.

    A lead is ACTIVE when `asof` falls inside [layoff + 6mo, layoff + 18mo];
    before that it is PENDING (schedule the campaign), after, EXPIRED.
    """
    if not len(matched) or not len(erv_ranked):
        return pd.DataFrame(columns=["org_node_id", "org_name", "erv_rank", "erv",
                                     "scheme_hypothesis", "layoff_date", "employees",
                                     "window_opens", "window_closes", "window_status"])
    asof = asof or pd.Timestamp.now()
    k = max(1, int(len(erv_ranked) * top_fraction))
    top = erv_ranked.nsmallest(k, "erv_rank")[
        ["org_node_id", "org_name", "erv_rank", "erv", "scheme_hypothesis"]]

    leads = matched.merge(top, on="org_node_id", how="inner")
    anchor = leads["layoff_date"].fillna(leads["notice_date"])
    leads["window_opens"] = anchor + pd.DateOffset(months=SURGE_WINDOW_OPEN_MONTHS)
    leads["window_closes"] = anchor + pd.DateOffset(months=SURGE_WINDOW_CLOSE_MONTHS)
    leads["window_status"] = "pending"
    leads.loc[(asof >= leads["window_opens"]) & (asof <= leads["window_closes"]),
              "window_status"] = "active"
    leads.loc[asof > leads["window_closes"], "window_status"] = "expired"
    cols = ["org_node_id", "org_name", "erv_rank", "erv", "scheme_hypothesis",
            "layoff_date", "employees", "window_opens", "window_closes", "window_status"]
    return leads[cols].sort_values(["window_status", "erv_rank"]).reset_index(drop=True)


def warn_summary(warn: pd.DataFrame) -> str:
    """Per posting file: rows, state(s), date ranges, employees, and how many rows
    carry no usable date (those never get an outreach window)."""
    L = ["file | rows | state | notice dates | layoff dates | employees | rows without a date"]
    for f, g in warn.groupby("source_file", sort=True):
        def rng(col):
            d = g[col].dropna()
            return f"{d.min().date()} to {d.max().date()}" if len(d) else "none"
        nodate = int((g["notice_date"].isna() & g["layoff_date"].isna()).sum())
        emp = pd.to_numeric(g["employees"], errors="coerce").sum()
        L.append(f"{f} | {len(g):,} | {','.join(sorted(set(g['state'].astype(str))) or ['?'])} | "
                 f"{rng('notice_date')} | {rng('layoff_date')} | {int(emp) if pd.notna(emp) else 0:,} | {nodate:,}")
    anchor = pd.Timestamp.today().normalize()
    live = warn[(warn["layoff_date"].fillna(warn["notice_date"]) >= anchor - pd.DateOffset(months=SURGE_WINDOW_CLOSE_MONTHS))]
    L.append(f"rows whose outreach window is still open or ahead (departure within the last "
             f"{SURGE_WINDOW_CLOSE_MONTHS} months): {len(live):,} of {len(warn):,}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--warn", required=True,
                    help="a raw WARN posting (csv or xlsx, any state's format) or the folder "
                         "preclean/warn/ holding warn_<ST>_<yyyymm>.csv|xlsx files")
    ap.add_argument("--graph-dir", default=None)
    ap.add_argument("--erv", default=None, help="erv_ranked.parquet from src.model_a")
    ap.add_argument("--out", default="/tmp/sourcing_out")
    ap.add_argument("--summary", action="store_true",
                    help="only read the postings and print, per file: rows, state, the "
                         "notice-date and layoff-date ranges, employees; no graph needed")
    args = ap.parse_args()

    if args.summary:
        print(warn_summary(load_warn(args.warn)))
        return
    if not (args.graph_dir and args.erv):
        ap.error("--graph-dir and --erv are required (or pass --summary)")
    org_nodes = pd.read_parquet(Path(args.graph_dir) / "nodes" / "org_nodes.parquet")
    erv = pd.read_parquet(args.erv)
    warn = load_warn(args.warn)
    matched, unmatched = match_warn_to_orgs(warn, org_nodes)
    leads = surge_leads(matched, erv)

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    matched.to_parquet(out / "warn_matched.parquet", index=False)
    unmatched.to_parquet(out / "warn_unmatched.parquet", index=False)
    leads.to_parquet(out / "warn_surge_leads.parquet", index=False)
    print(f"WARN rows: {len(warn)} | matched: {len(matched)} | "
          f"unmatched: {len(unmatched)} | surge leads: {len(leads)}")


if __name__ == "__main__":
    main()
