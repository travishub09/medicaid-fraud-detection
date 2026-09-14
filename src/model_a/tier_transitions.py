"""
tier_transitions.py — is a revocation an early marker of fraud?

The question behind the FCA tiers: providers revoked for what looks like
paperwork (non-compliance, failure to report) or for a phantom site (not
operational on an on-site review) — do they later show up with a
fraud-tier event (a conviction, a CIA breach, abuse of billing
privileges, a DOJ case)? If they do at a meaningful rate, a revocation is
an early marker of the conduct we pursue and belongs higher in the tier
table; if they do not, it is regulatory trouble and stays where it is.

Reads the exclusion nodes (every record with its date and excl_type), sorts
each NPI's records in time, and for each FIRST-event basis reports: how many
NPIs, how many later carry a hard-tier record, and the median months to it.

    python -m src.model_a.tier_transitions --exclusion-nodes graph/nodes/exclusion_nodes.parquet
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .fca_relevance import classify_series


def transitions(nodes: pd.DataFrame, min_group: int = 20) -> pd.DataFrame:
    e = nodes.copy()
    e["npi"] = e.get("npi").astype("string").str.strip()
    e = e[e["npi"].notna() & (e["npi"] != "") & (e["npi"].str.lower() != "none")].copy()
    e["dt"] = pd.to_datetime(e.get("excl_date"), errors="coerce")
    e = e[e["dt"].notna()].copy()
    if "excl_type" not in e.columns:
        e["excl_type"] = ""
    tiers = classify_series(e["excl_type"].fillna("").astype(str))
    e["tier"] = tiers["fca_tier"].values
    e["basis"] = tiers["fca_basis"].values
    e = e.sort_values(["npi", "dt"])
    first = e.groupby("npi").first()
    hard = e[e["tier"] == "hard"].groupby("npi")["dt"].min()
    first["first_dt"] = first["dt"]
    first["later_hard_dt"] = first.index.map(hard)
    first["later_hard"] = first["later_hard_dt"].notna() & (first["later_hard_dt"] > first["first_dt"])
    first["months_to_hard"] = ((first["later_hard_dt"].dt.year - first["first_dt"].dt.year) * 12
                               + (first["later_hard_dt"].dt.month - first["first_dt"].dt.month))
    rows = []
    for (tier, basis), g in first.groupby(["tier", "basis"]):
        if len(g) < min_group:
            continue
        lh = g[g["later_hard"]]
        rows.append({"first_event_tier": tier, "first_event_basis": basis,
                     "npis": int(len(g)), "later_hard_event": int(len(lh)),
                     "later_hard_share": float(len(lh) / len(g)),
                     "median_months_to_hard": (float(lh["months_to_hard"].median())
                                               if len(lh) else None)})
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(["first_event_tier", "later_hard_share"],
                              ascending=[True, False])
    return out


def to_markdown(t: pd.DataFrame, source: str) -> str:
    L = ["# TIER TRANSITIONS — does a first event of each kind precede a fraud-tier event?", "",
         f"_Source: `{source}`. Each NPI's earliest dated record is its first event; "
         "'later hard' counts NPIs that carry a hard-tier record (conviction, CIA/settlement "
         "breach, abuse of billing privileges, false enrollment, DOJ case) AFTER it. A high "
         "share for a 'weak' or 'excluded' basis says that basis is an early marker of the "
         "conduct we pursue and should move up the tier table._", "",
         "| first event tier | basis | NPIs | later hard-tier event | share | median months |",
         "|---|---|--:|--:|--:|--:|"]
    for _, r in t.iterrows():
        mm = f"{r['median_months_to_hard']:.0f}" if pd.notna(r["median_months_to_hard"]) else "n/a"
        L.append(f"| {r['first_event_tier']} | {r['first_event_basis']} | {r['npis']:,} "
                 f"| {r['later_hard_event']:,} | {r['later_hard_share']:.1%} | {mm} |")
    L += ["", "_Leads for counsel review, never accusations; a label is a public integrity "
          "event, not a finding about any provider._"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exclusion-nodes", required=True,
                    help="graph/nodes/exclusion_nodes.parquet (the FULL graph, not as-of)")
    ap.add_argument("--out", default=None, help="default: <nodes folder>/TIER_TRANSITIONS.md")
    ap.add_argument("--min-group", type=int, default=20)
    args = ap.parse_args()
    p = Path(args.exclusion_nodes)
    nodes = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, dtype=str)
    t = transitions(nodes, min_group=args.min_group)
    out = Path(args.out) if args.out else p.parent / "TIER_TRANSITIONS.md"
    out.write_text(to_markdown(t, p.name), encoding="utf-8")
    print(f"wrote {out}")
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
