"""
hcris_affiliates.py — the affiliate-disclosure check for skilled nursing facilities.

Two facts the raw HCRIS cost report and PECOS ownership state separately, put side
by side for EVERY SNF (not one tip at a time):

  1. PECOS says who owns or manages the facility: organization owners (property
     companies, holding companies, management companies) with ownership or
     operational/managerial control.
  2. Worksheet A-8-1 of the Medicare cost report is where the facility must list
     every payment to a related organization (rent, management fees, services),
     filed under penalty of perjury.

A facility with organization owners/managers in PECOS and NO A-8-1 at all is a
disclosure gap: either it pays nothing to any affiliate (rare for a facility owned
through a property company) or the worksheet is incomplete. Wesleyan Village
(2026-10-06) was the only home in a 21-home group with that gap. This module asks
how common the gap is, and ranks every facility's affiliate share of expenses
within its state, so one tip's number has a peer context.

Inputs (all already on the operator's machine):
  --hcris-dir   folder of SNF10FY<year>.zip / SNF24FY<year>.zip (raw HCRIS)
  --ccn-to-npi  processed/ccn_to_npi.parquet            (ccn, npi)
  --graph       graph root holding edges/member_edges.parquet, edges/owned_by_edges.parquet,
                nodes/owner_nodes.parquet
Outputs: <out>.csv (one row per CCN x fiscal year) + <out>_summary.md.

Numbers are the facilities' own filings. A gap is a question for the state cost
report and a business-office insider, never a finding.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .hcris_related_party import load_hcris_year, related_party_rows

# worksheets and the cells read (SNF 2540-10 / 2540-24 HCRIS layout, verified on the
# 2021-2025 Ohio files): A-8-1 Part A total line, G-3 income statement, S-3 Part I.
WORKSHEETS = {"A81": "A-8-1 related organizations", "G3": "G-3 income statement",
              "S3000": "S-3 statistics"}
A81_TOTAL = ("A810001", "01000", "00400")      # amount included in Wkst A, total line
G3_OPEX = ("G300000", "00400", "00100")        # total operating expenses
G3_NET_REV = ("G300000", "00300", "00100")     # net patient revenue
S3_BEDS = ("S300001", "00100", "00100")
S3_DAYS = ("S300001", "00100", "00700")

MANAGING_ROLES = ("MANAG", "OPERATIONAL", "DIRECTOR", "OFFICER", "OWNERSHIP")


def _cell(g: pd.DataFrame, key: tuple) -> float:
    ws, line, col = key
    x = g[(g["worksheet"] == ws) & (g["line"] == line) & (g["column"] == col)]
    if not len(x):
        return np.nan
    try:
        return float(x["value"].iloc[0])
    except (TypeError, ValueError):
        return np.nan


def facility_year_table(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per CCN x fiscal year from the long-form cells: affiliate total,
    whether any A-8-1 cell exists, opex, net revenue, beds, days."""
    out = []
    for (ccn, fy), g in rows.groupby(["ccn", "fy_end"]):
        has_a81 = bool((g["worksheet"].astype(str).str.startswith("A81")).any())
        out.append({
            "ccn": ccn, "fy_end": fy, "year": str(fy)[-4:],
            "npi": next((v for v in g["npi"].astype(str) if v and v != "nan"), ""),
            "has_a81": has_a81,
            "affiliate_booked": _cell(g, A81_TOTAL) if has_a81 else 0.0,
            "opex": _cell(g, G3_OPEX), "net_patient_revenue": _cell(g, G3_NET_REV),
            "beds": _cell(g, S3_BEDS), "patient_days": _cell(g, S3_DAYS),
        })
    t = pd.DataFrame(out)
    if not len(t):
        return t
    t["affiliate_share_opex"] = (t["affiliate_booked"] / t["opex"]).where(t["opex"] > 0)
    t["state_code"] = t["ccn"].astype(str).str[:2]
    return t


def pecos_org_owners(ccn_to_npi: pd.DataFrame, member_edges: pd.DataFrame,
                     owned_by: pd.DataFrame, owner_nodes: pd.DataFrame) -> pd.DataFrame:
    """Per CCN: how many ORGANIZATION owners/managers PECOS lists, and their names."""
    x = ccn_to_npi[["ccn", "npi"]].astype(str)
    x["ccn"] = x["ccn"].str.zfill(6)
    m = member_edges[["src_id", "dst_id"]].astype(str)
    m["npi"] = m["src_id"].str.replace("provider:", "", regex=False)
    j = x.merge(m[["npi", "dst_id"]], on="npi", how="inner").rename(columns={"dst_id": "org"})
    e = owned_by[["src_id", "dst_id", "owner_role"]].astype(str)
    e["owner_key"] = e["dst_id"].str.replace("owner:", "", regex=False)
    o = owner_nodes[["owner_key", "owner_type", "owner_display_name"]].astype(str)
    je = e.merge(o, on="owner_key", how="left")
    is_org = je["owner_type"].str.upper().eq("O")
    role_ok = je["owner_role"].str.upper().apply(lambda r: any(k in r for k in MANAGING_ROLES))
    je = je[is_org & role_ok]
    jj = j.merge(je[["src_id", "owner_display_name"]], left_on="org", right_on="src_id", how="left")
    agg = jj.groupby("ccn").agg(
        n_org_owners=("owner_display_name", lambda s: int(s.dropna().nunique())),
        org_owner_names=("owner_display_name", lambda s: "; ".join(sorted(set(s.dropna()))[:6])),
    ).reset_index()
    return agg


def affiliate_disclosure(table: pd.DataFrame, owners: pd.DataFrame) -> pd.DataFrame:
    """Join, flag the gap, and rank affiliate share within state x year (one-sided)."""
    t = table.merge(owners, on="ccn", how="left")
    t["n_org_owners"] = t["n_org_owners"].fillna(0).astype(int)
    t["org_owner_names"] = t["org_owner_names"].fillna("")
    t["affiliate_undisclosed"] = (t["n_org_owners"] >= 1) & (~t["has_a81"])
    t["affiliate_share_pct_state"] = (
        t.groupby(["state_code", "year"])["affiliate_share_opex"]
         .rank(pct=True, method="max"))
    return t


def write_summary(t: pd.DataFrame, path: Path, focus_ccns: list[str] | None = None) -> None:
    L = ["# AFFILIATE DISCLOSURE CHECK (raw HCRIS A-8-1 vs PECOS organization owners)", "",
         "_Facilities' own filings, assembled for review. A gap is a question, not a finding._", ""]
    for (st, yr), g in t.groupby(["state_code", "year"]):
        n = len(g); gap = int(g["affiliate_undisclosed"].sum()); owned = int((g["n_org_owners"] >= 1).sum())
        L.append(f"- state {st}, FY{yr}: {n:,} SNF filings; {owned:,} with organization owners/managers in PECOS; "
                 f"{gap:,} of those file NO A-8-1 ({(gap / owned * 100) if owned else 0:.0f}%)")
    if focus_ccns:
        L += ["", "## Focus facilities", ""]
        f = t[t["ccn"].isin([c.zfill(6) for c in focus_ccns])].sort_values(["ccn", "year"])
        for _, r in f.iterrows():
            share = "" if pd.isna(r["affiliate_share_opex"]) else f"{r['affiliate_share_opex'] * 100:.1f}% of expenses"
            pct = "" if pd.isna(r["affiliate_share_pct_state"]) else f"(state percentile {r['affiliate_share_pct_state']:.2f})"
            L.append(f"- {r['ccn']} FY{r['year']}: A-8-1 filed={r['has_a81']}; affiliate booked "
                     f"${(r['affiliate_booked'] or 0):,.0f} {share} {pct}; PECOS org owners/managers: "
                     f"{r['n_org_owners']} [{r['org_owner_names']}]; undisclosed gap={r['affiliate_undisclosed']}")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hcris-dir", required=True)
    ap.add_argument("--ccn-to-npi", required=True)
    ap.add_argument("--graph", required=True, help="graph root (edges/, nodes/)")
    ap.add_argument("--state", default=None, help="two-letter state to keep (default: every SNF)")
    ap.add_argument("--focus", default="", help="comma-separated CCNs to detail in the summary")
    ap.add_argument("--out", required=True, help="CSV path; a _summary.md sits beside it")
    a = ap.parse_args()
    root = Path(a.hcris_dir)
    sources = sorted({q for pat in ("SNF*FY*.zip", "snf*fy*.zip") for q in root.glob(pat)})
    frames = []
    for src in sources:
        rpt, cells = load_hcris_year(src)
        rows = related_party_rows(rpt, cells, state=a.state, worksheets=WORKSHEETS)
        print(f"  {src.name}: {len(rpt):,} cost reports, {len(rows):,} cells")
        frames.append(facility_year_table(rows))
    table = pd.concat([f for f in frames if len(f)], ignore_index=True)
    g = Path(a.graph)
    owners = pecos_org_owners(pd.read_parquet(a.ccn_to_npi),
                              pd.read_parquet(g / "edges" / "member_edges.parquet"),
                              pd.read_parquet(g / "edges" / "owned_by_edges.parquet"),
                              pd.read_parquet(g / "nodes" / "owner_nodes.parquet"))
    t = affiliate_disclosure(table, owners)
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    t.to_csv(out, index=False)
    focus = [c.strip() for c in a.focus.split(",") if c.strip()]
    write_summary(t, out.with_name(out.stem + "_summary.md"), focus)
    print(f"affiliate disclosure: {t['ccn'].nunique():,} CCNs, {len(t):,} facility-years, "
          f"{int(t['affiliate_undisclosed'].sum()):,} undisclosed gaps -> {out}")


if __name__ == "__main__":
    main()
