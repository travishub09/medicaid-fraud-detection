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
from pathlib import Path

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name

# canonical name → candidate headers across state WARN postings
WARN_COLS = {
    "employer": ["COMPANY", "COMPANY NAME", "EMPLOYER", "BUSINESS NAME", "Company"],
    "state": ["STATE", "State"],
    "notice_date": ["NOTICE_DATE", "NOTICE DATE", "WARN RECEIVED DATE", "Date Received"],
    "layoff_date": ["LAYOFF_DATE", "LAYOFF DATE", "EFFECTIVE DATE", "Layoff Date"],
    "employees": ["EMPLOYEES", "NUMBER OF EMPLOYEES", "AFFECTED WORKERS", "No. Of Employees"],
}

SURGE_WINDOW_OPEN_MONTHS = 6     # outreach sweet spot opens ~6 months post-departure
SURGE_WINDOW_CLOSE_MONTHS = 18   # and fades by ~18


def normalize_warn(raw: pd.DataFrame) -> pd.DataFrame:
    """Map a raw WARN posting to the canonical schema; parse dates; keep strings."""
    resolved = {}
    lower = {c.lower().strip(): c for c in raw.columns}
    for canon, candidates in WARN_COLS.items():
        for cand in candidates:
            if cand.lower().strip() in lower:
                resolved[canon] = lower[cand.lower().strip()]
                break
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--warn", required=True, help="raw WARN CSV (any state's format)")
    ap.add_argument("--graph-dir", required=True)
    ap.add_argument("--erv", required=True, help="erv_ranked.parquet from src.model_a")
    ap.add_argument("--out", default="/tmp/sourcing_out")
    args = ap.parse_args()

    org_nodes = pd.read_parquet(Path(args.graph_dir) / "nodes" / "org_nodes.parquet")
    erv = pd.read_parquet(args.erv)
    warn = normalize_warn(pd.read_csv(args.warn, dtype=str))
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
