"""
case_labels.py — scheme-typed, time-boxed positives from DOJ/qui tam outcomes.

The exclusion-list label is a binary, untyped, untimed flag. A *settlement* carries
far more: the scheme ("upcoding"), the dollars, and — critically — the **conduct
window** ("from 2015 through 2019"). This module turns the enforcement case DB into
per-NPI labels that say *what* and *when*, which is what makes two things possible:

  * SCHEME-STRATIFIED evaluation — measure lift within each scheme family, so a
    model can't hide by only learning LEIE-flavored fraud;
  * OUT-OF-TIME training — train on features dated BEFORE ``conduct_start`` (the
    Pillar-1 leakage fix, usable today even before the full bitemporal store).

Resolution is org-grain and conservative (reuses ``resolve_settled_orgs`` — only
*successful* enforcement with a recovery or an intervention), then broadcast to the
org's member NPIs. These are the strongest positives we have (prosecuted fraud),
folded into the widened label with source ``doj_case``.
"""

from __future__ import annotations

import re

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name
from src.model_a.lookalikes import resolve_settled_orgs

_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
DEFAULT_LOOKBACK = 4         # conduct typically precedes announcement; conservative


def extract_conduct_window(text: str, announced_date: str = "",
                           lookback: int = DEFAULT_LOOKBACK) -> tuple:
    """(start_year, end_year) of the conduct period from the case text.

    Takes the span of plausible years mentioned (≤ the announcement year); falls
    back to a lookback window ending at the announcement year when the text names
    no period. Returns (None, None) when nothing is determinable."""
    ann_year = None
    s = str(announced_date or "")
    if len(s) >= 4 and s[:4].isdigit():
        ann_year = int(s[:4])
    cap = ann_year or 2026
    years = sorted({int(y) for y in _YEAR.findall(str(text or "")) if 1995 <= int(y) <= cap})
    if years:
        return years[0], years[-1]
    if ann_year:
        return ann_year - lookback, ann_year
    return None, None


def _fuzzy_settled(org_nodes: pd.DataFrame, case_db: pd.DataFrame,
                   already: set, threshold: float) -> pd.DataFrame:
    """Second-pass FUZZY defendant↔org matching (Run 2 §F) for cases the exact
    name-key join missed — 'ACME HEALTH SERVICES LLC' vs 'ACME HEALTH SERVICES'.
    difflib on normalized keys, conservative threshold, same output shape as
    resolve_settled_orgs. Only cases with a real recovery."""
    import difflib
    cols = ["org_node_id", "matched_case_id"]
    name_col = next((c for c in ("org_name", "name", "entity_name")
                     if c in org_nodes.columns), None)
    def_col = next((c for c in ("defendant", "defendant_name", "defendants")
                    if c in case_db.columns), None)
    if not name_col or not def_col or "case_id" not in case_db.columns:
        return pd.DataFrame(columns=cols)
    orgs = org_nodes[["org_node_id", name_col]].dropna().copy()
    orgs["key"] = orgs[name_col].astype(str).map(norm_org_name)
    orgs = orgs[orgs["key"].str.len() >= 8]           # short keys over-match
    org_keys = orgs["key"].unique().tolist()
    amount = pd.to_numeric(case_db.get("amount_usd"), errors="coerce").fillna(0.0)
    live = case_db[(amount > 0) & ~case_db["case_id"].astype(str).isin(already)]
    rows = []
    for r in live.itertuples():
        dkey = norm_org_name(str(getattr(r, def_col, "") or ""))
        if len(dkey) < 8:
            continue
        hit = difflib.get_close_matches(dkey, org_keys, n=1, cutoff=threshold)
        if hit:
            for oid in orgs.loc[orgs["key"] == hit[0], "org_node_id"]:
                rows.append({"org_node_id": str(oid),
                             "matched_case_id": str(r.case_id)})
    return pd.DataFrame(rows, columns=cols)


def build_case_labels(case_db: pd.DataFrame, org_nodes: pd.DataFrame,
                      npi_to_org: pd.DataFrame,
                      fuzzy_threshold: float | None = 0.92,
                      medicaid_only: bool = False,
                      asof_cutoff: str | None = None) -> pd.DataFrame:
    """Per-NPI DOJ-case labels: npi, fraud_label, fraud_scheme, conduct_start,
    conduct_end, case_ids, amount_usd, label_source. One row per NPI (a provider in
    multiple cases takes the union of schemes, the widest window, summed dollars).

    ``asof_cutoff`` (YYYY-MM, frozen runs): a case is a leakage-safe positive only
    when its conduct STARTED on/before the cutoff year. Cases whose fraud began
    after the freeze, or whose conduct window is unknown, are dropped from a
    frozen label — otherwise the model would be graded on conduct it could not
    have seen. Current-day runs pass None and keep every resolved case.

    ``fuzzy_threshold`` adds a conservative difflib second pass for defendants the
    exact name-key join missed (None disables — Run 2 §F). ``medicaid_only`` keeps
    only cases whose text mentions Medicaid (the §F filter for the Medicaid label)."""
    cols = ["npi", "fraud_label", "fraud_scheme", "conduct_start", "conduct_end",
            "case_ids", "amount_usd", "label_source"]
    if case_db is None or not len(case_db) or org_nodes is None or not len(org_nodes):
        return pd.DataFrame(columns=cols)
    if medicaid_only:
        text = (case_db.get("summary", pd.Series("", index=case_db.index)).fillna("")
                .astype(str) + " "
                + case_db.get("title", pd.Series("", index=case_db.index)).fillna("")
                .astype(str))
        case_db = case_db[text.str.contains("medicaid", case=False)]
        if not len(case_db):
            return pd.DataFrame(columns=cols)
    settled = resolve_settled_orgs(org_nodes, case_db)        # org_node_id, matched_case_id, …
    if fuzzy_threshold is not None:
        extra = _fuzzy_settled(org_nodes, case_db,
                               set(settled["matched_case_id"].astype(str))
                               if len(settled) else set(),
                               threshold=fuzzy_threshold)
        if len(extra):
            settled = (pd.concat([settled, extra], ignore_index=True)
                       .drop_duplicates(["org_node_id", "matched_case_id"]))
    if not len(settled):
        return pd.DataFrame(columns=cols)

    cases = case_db.drop_duplicates("case_id").set_index("case_id")
    xw = npi_to_org[["npi", "org_node_id"]].astype(str)
    org_to_npis: dict[str, list[str]] = {}
    for r in xw.itertuples():
        org_to_npis.setdefault(r.org_node_id, []).append(r.npi)

    rows = []
    for s in settled.itertuples():
        cid = str(s.matched_case_id)
        if cid not in cases.index:
            continue
        c = cases.loc[cid]
        scheme = str(c.get("scheme") or "unknown")
        start, end = extract_conduct_window(c.get("summary", ""), str(c.get("announced_date", "")))
        amt = pd.to_numeric(pd.Series([c.get("amount_usd")]), errors="coerce").iloc[0]
        for npi in org_to_npis.get(str(s.org_node_id), []):
            rows.append({"npi": npi, "fraud_scheme": scheme, "conduct_start": start,
                         "conduct_end": end, "case_ids": cid,
                         "amount_usd": float(amt) if pd.notna(amt) else 0.0})
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)

    if asof_cutoff:
        cut_year = int(str(asof_cutoff)[:4])
        n_before = len(df)
        df = df[df["conduct_start"].notna()
                & (pd.to_numeric(df["conduct_start"], errors="coerce") <= cut_year)]
        if not len(df):
            return pd.DataFrame(columns=cols)

    out_rows = []
    for npi, g in df.groupby("npi"):
        starts = g["conduct_start"].dropna()
        ends = g["conduct_end"].dropna()
        out_rows.append({
            "npi": npi, "fraud_label": 1,
            "fraud_scheme": ";".join(sorted(set(g["fraud_scheme"]))),
            "conduct_start": int(starts.min()) if len(starts) else None,
            "conduct_end": int(ends.max()) if len(ends) else None,
            "case_ids": ";".join(sorted(set(g["case_ids"]))),
            "amount_usd": float(g["amount_usd"].sum()),
            "label_source": "doj_case",
        })
    return pd.DataFrame(out_rows, columns=cols)


def main() -> None:
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case-db", required=True, help="case DB csv/parquet")
    ap.add_argument("--graph-dir", required=True, help="entity-graph output dir")
    ap.add_argument("--out", required=True, help="output case_labels parquet")
    args = ap.parse_args()
    g = Path(args.graph_dir)
    case_db = (pd.read_csv(args.case_db, dtype=str) if args.case_db.endswith(".csv")
               else pd.read_parquet(args.case_db))
    org_nodes = pd.read_parquet(g / "nodes" / "org_nodes.parquet")
    npi_to_org = pd.read_parquet(g / "npi_to_org.parquet")
    labels = build_case_labels(case_db, org_nodes, npi_to_org)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    labels.to_parquet(args.out, index=False)
    print(f"Wrote {args.out} — {len(labels):,} NPIs labeled from DOJ cases "
          f"({labels['fraud_scheme'].nunique() if len(labels) else 0} scheme types)")


if __name__ == "__main__":
    main()
