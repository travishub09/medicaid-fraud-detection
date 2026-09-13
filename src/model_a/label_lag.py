"""
label_lag.py — was the fraud-tier forward label even VISIBLE at the freeze?

The Sept 2026 label experiment on the 2023-12 matrix inverted the
post-mortem's hypothesis: forward lift under the `hard` ruler (fraud
convictions, kickbacks, abuse of billing privileges) collapsed to ~1.7x with
ROC 0.51, while the old `all` ruler sat at 3.76x. Two readings compete:

  A. the model never learned fraud — its lift came from predicting the
     administrative / weak events that dominate both the training label and
     the old ruler;
  B. enforcement LAG — a conviction-based exclusion lands 2 to 6 years after
     the conduct, so by a 2023-12 freeze most convicted providers had already
     stopped billing and their frozen features describe a dormant NPI, not
     the fraud. The ruler is fair for administrative revocations (which
     follow the observable behaviour within months) and unfair for fraud.

This diagnostic separates them from the run's own files: for each tier of
each forward-label file it joins the positives to the frozen matrix and
reports how many are in the scored universe, how many were still billing in
the last six months before the freeze, how long before the freeze their
billing stopped, and the median months from last billing to exclusion
(the lag). If hard-tier positives are mostly dormant at the freeze, reading
B holds and the fix is an earlier rolling-origin freeze plus conduct-window
labels; if they were billing at the freeze and the model still cannot rank
them, reading A holds and the fix is a hard-tier TRAINING label.

    python -m src.model_a.label_lag --matrix <frozen>/provider_features_for_model.parquet \
        --labels <frozen>/future_bans_after_2023-12__all.csv \
                 <frozen>/future_bans_after_2023-12__hard.csv --cutoff 2023-12
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

_MATRIX_COLS = ["npi", "net_paid", "last_service_month", "first_service_month",
                "tenure_months", "n_active_months"]


def _ym(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str[:7], format="%Y-%m", errors="coerce")


def _months_between(a: pd.Series, b: pd.Series) -> pd.Series:
    return (b.dt.year - a.dt.year) * 12 + (b.dt.month - a.dt.month)


def load_matrix_slim(matrix_path: str | Path) -> pd.DataFrame:
    """Only the columns the diagnostic needs (the matrix is multi-GB)."""
    import pyarrow.parquet as pq
    names = pq.ParquetFile(matrix_path).schema_arrow.names
    cols = [c for c in _MATRIX_COLS if c in names]
    m = pd.read_parquet(matrix_path, columns=cols)
    m["npi"] = m["npi"].astype(str)
    return m


def lag_table(label: pd.DataFrame, matrix: pd.DataFrame, cutoff: str,
              recent_months: int = 6, dormant_months: int = 24) -> pd.DataFrame:
    """Per fca_tier (plus ALL): visibility of the positives at the freeze."""
    pos = label[pd.to_numeric(label["is_prospective_positive"], errors="coerce") == 1].copy()
    pos["npi"] = pos["npi"].astype(str)
    if "fca_tier" not in pos.columns:
        pos["fca_tier"] = "all"
    cut = pd.Timestamp(f"{cutoff[:7]}-01")
    j = pos.merge(matrix, on="npi", how="left", indicator=True)
    j["in_universe"] = j["_merge"] == "both"
    last = _ym(j.get("last_service_month", pd.Series(index=j.index, dtype=object)))
    excl = _ym(j["first_excl_date"])
    j["months_last_to_cutoff"] = _months_between(last, pd.Series(cut, index=j.index))
    j["months_last_to_excl"] = _months_between(last, excl)
    j["billing_recent"] = j["in_universe"] & (j["months_last_to_cutoff"] <= recent_months)
    j["dormant_at_freeze"] = j["in_universe"] & (j["months_last_to_cutoff"] > dormant_months)
    rows = []
    for tier, g in [("ALL", j)] + list(j.groupby("fca_tier")):
        u = g[g["in_universe"]]
        rows.append({
            "tier": tier,
            "positives": int(len(g)),
            "in_universe": int(g["in_universe"].sum()),
            "in_universe_share": float(g["in_universe"].mean()) if len(g) else 0.0,
            "billing_in_last_6mo_share": (float(u["billing_recent"].mean()) if len(u) else None),
            "dormant_24mo_share": (float(u["dormant_at_freeze"].mean()) if len(u) else None),
            "median_months_last_bill_to_freeze": (float(u["months_last_to_cutoff"].median())
                                                  if len(u) else None),
            "median_months_last_bill_to_exclusion": (float(u["months_last_to_excl"].median())
                                                     if len(u) else None),
            "median_net_paid": (float(pd.to_numeric(u.get("net_paid"), errors="coerce").median())
                                if len(u) and "net_paid" in u.columns else None),
        })
    return pd.DataFrame(rows)


def _f(v, fmt):
    return fmt.format(v) if v is not None and pd.notna(v) else "n/a"


def to_markdown(tables: dict[str, pd.DataFrame], cutoff: str) -> str:
    L = [f"# LABEL LAG — were the forward positives visible at the {cutoff} freeze?", "",
         "_For each ruler and tier: how many positives sit in the scored universe, "
         "how many were still billing in the six months before the freeze, how many "
         "had gone dormant (no billing for 24+ months before it), and the median "
         "months from last billing to the freeze and to the exclusion (the "
         "enforcement lag). A tier that is mostly dormant at the freeze cannot be "
         "ranked from frozen features no matter how good the model: the fix is an "
         "earlier freeze and conduct-window labels, not more features._", ""]
    for name, t in tables.items():
        L += [f"## {name}", "",
              "| tier | positives | in universe | billing in last 6 mo | dormant 24+ mo | "
              "median mo: last bill -> freeze | median mo: last bill -> exclusion | median paid |",
              "|---|--:|--:|--:|--:|--:|--:|--:|"]
        for _, r in t.iterrows():
            L.append(f"| {r['tier']} | {int(r['positives']):,} | {int(r['in_universe']):,} "
                     f"({r['in_universe_share']:.0%}) | {_f(r['billing_in_last_6mo_share'], '{:.0%}')} "
                     f"| {_f(r['dormant_24mo_share'], '{:.0%}')} "
                     f"| {_f(r['median_months_last_bill_to_freeze'], '{:.0f}')} "
                     f"| {_f(r['median_months_last_bill_to_exclusion'], '{:.0f}')} "
                     f"| {_f(r['median_net_paid'], '${:,.0f}')} |")
        L.append("")
    L += ["## Reading it", "",
          "- **hard tier mostly dormant / long lag** -> reading B (enforcement lag): the "
          "2023-12 freeze looks at convicted providers after they stopped; use a rolling-"
          "origin freeze 2 to 3 years earlier and DOJ conduct windows so the observation "
          "window overlaps the conduct.",
          "- **hard tier billing at the freeze, still unranked** -> reading A: the model "
          "learned administrative revocation, not fraud; retrain with a hard-tier "
          "training label (weak tier as weak supervision) before anything else.",
          "- Both can be true; the table says how much of each.", "",
          "_Leads for counsel review, never accusations; a label is a public integrity "
          "event, not a finding about any provider._"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--labels", nargs="+", required=True,
                    help="one or more forward-label CSVs (prospective_label output)")
    ap.add_argument("--cutoff", required=True, help="the freeze, YYYY-MM")
    ap.add_argument("--out", default=None, help="default: <matrix folder>/LABEL_LAG.md")
    args = ap.parse_args()
    m = load_matrix_slim(args.matrix)
    tables = {}
    for lp in args.labels:
        lab = pd.read_csv(lp, dtype=str)
        tables[Path(lp).name] = lag_table(lab, m, args.cutoff)
    out = Path(args.out) if args.out else Path(args.matrix).parent / "LABEL_LAG.md"
    out.write_text(to_markdown(tables, args.cutoff), encoding="utf-8")
    print(f"wrote {out}")
    for name, t in tables.items():
        print(f"== {name}")
        print(t.to_string(index=False))


if __name__ == "__main__":
    main()
