"""
lead_export.py — the ranked, gated lead lists (Run 2 §A, complete).

Replaces the scratchpad lead scripts with the corrected §A design, in-repo:

  1. NO size exclusion. Big/complex providers stay in the pool (Run 1 wrongly
     dropped them to dodge the "big = intense" trap and lost the >$5M targets).
  2. Rank on SIZE-ADJUSTED anomaly only: the mean of the available peer-relative
     percentiles (the five v3 concepts + the two digital-twin residuals) with the
     signal-stack count as the primary key. Raw dollars NEVER enter the ranking —
     asserted, not promised.
  3. Gate AFTER ranking with the scheme-aware recovery gate (lead_gate): own
     billing for volume/price schemes, INFLUENCED dollars for ordering/kickback,
     ring-aggregate for ownership rings, facility program for facility schemes.
     The gate decides what surfaces; it never re-orders.

Outputs (CSV — investigative leads for human/counsel review, never accusations):
  top_new_leads.csv           not-excluded providers, ranked, gate-annotated
  excluded_adjacent_leads.csv the ranked leads that ALSO sit near a known
                              excluded party (operations corroboration — the
                              leakage-adjacent columns are fine HERE; they are
                              banned from training, not from casework)
  LEAD_EXPORT_REPORT.md       counts + gate pass rates + the ranking contract
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .lead_gate import apply_recovery_gate

RANK_COMPONENTS = ["concentration", "payment_intensity", "service_intensity",
                   "specialty_mismatch", "temporal",
                   "volume_residual", "billing_residual"]
DOLLAR_TOKENS = ("paid", "dollar", "cost", "allowed", "revenue", "payment_usd")
DISPLAY_COLS = ["npi", "org_node_id", "entity_type", "primary_taxonomy",
                "practice_state", "org_legal_name", "provider_name",
                "addr_city", "addr_zip", "net_paid",
                "n_concept_signals", "anomaly_contributing_concepts"]


def rank_leads(matrix: pd.DataFrame) -> pd.DataFrame:
    """Ranked copy of the matrix: signal-stack count first, then the mean of the
    available size-adjusted percentiles. Dollars never enter the key (asserted)."""
    components = [c for c in RANK_COMPONENTS if c in matrix.columns]
    assert components, "no ranking components present in the matrix"
    for c in components:
        assert not any(t in c.lower() for t in DOLLAR_TOKENS), \
            f"raw-dollar column {c!r} must never drive the ranking (Run 2 §A)"
    out = matrix.copy()
    vals = out[components].apply(pd.to_numeric, errors="coerce")
    with np.errstate(all="ignore"):
        out["rank_score"] = vals.mean(axis=1, skipna=True)
    out["rank_n_components"] = vals.notna().sum(axis=1)
    out["_stack"] = (pd.to_numeric(out["n_concept_signals"], errors="coerce").fillna(0)
                     if "n_concept_signals" in out.columns else 0)
    out = (out.sort_values(["_stack", "rank_score"], ascending=[False, False],
                           kind="stable")
           .drop(columns=["_stack"]).reset_index(drop=True))
    return out


def build_lead_lists(matrix: pd.DataFrame,
                     org_payments: pd.DataFrame | None = None,
                     influenced: pd.DataFrame | None = None,
                     threshold: float = 5_000_000.0,
                     top_n: int = 10_000) -> dict[str, pd.DataFrame]:
    """Rank → gate → split. Returns {"top_new": …, "excluded_adjacent": …}."""
    ranked = rank_leads(matrix)
    gated = apply_recovery_gate(ranked, org_payments=org_payments,
                                influenced=influenced, threshold=threshold)
    label = pd.to_numeric(gated.get("provider_on_exclusion"), errors="coerce"
                          ).fillna(0) if "provider_on_exclusion" in gated.columns \
        else pd.Series(0, index=gated.index)
    new = gated[label == 0].head(top_n)
    keep = [c for c in DISPLAY_COLS if c in gated.columns] + \
           ["rank_score", "rank_n_components", "expected_recovery",
            "gate_basis", "passes_gate"]
    adj_col = "within_2_hops_of_exclusion"
    adjacent = (new[pd.to_numeric(new[adj_col], errors="coerce").fillna(0) > 0]
                if adj_col in new.columns else new.iloc[0:0])
    return {"top_new": new[keep], "excluded_adjacent": adjacent[keep]}


def write_lead_report(lists: dict[str, pd.DataFrame], out_dir: Path,
                      threshold: float) -> None:
    new, adj = lists["top_new"], lists["excluded_adjacent"]
    passes = int(new["passes_gate"].sum()) if len(new) else 0
    lines = ["# LEAD_EXPORT_REPORT — ranked, gated lead lists (Run 2 §A)\n\n",
             "_Investigative leads for human/counsel review — never accusations. "
             "Ranking: signal-stack count, then the mean of the size-adjusted "
             "percentiles; raw dollars never enter the key (asserted in code). "
             "The recovery gate is scheme-aware (see OUTPUT_METHODOLOGY): "
             "orchestrators are gated on INFLUENCED dollars, rings at ring grain "
             "— the gate annotates and filters, it never re-ranks._\n\n",
             f"- top_new_leads.csv: {len(new):,} not-excluded ranked leads; "
             f"{passes:,} pass the ${threshold:,.0f} scheme-aware gate "
             f"({(passes / len(new) if len(new) else 0):.1%})\n",
             f"- excluded_adjacent_leads.csv: {len(adj):,} of them also sit within "
             "2 hops of a known excluded party (operations corroboration)\n",
             "- NO size exclusion was applied (Run 2 §A: big providers stay in; "
             "size decides what surfaces via the gate, never the rank)\n"]
    (out_dir / "LEAD_EXPORT_REPORT.md").write_text("".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", required=True, help="provider_features_for_model.parquet")
    ap.add_argument("--org-payments", default=None,
                    help="org exposure parquet (org_node_id, payments) from exposure.py")
    ap.add_argument("--threshold", type=float, default=5_000_000.0)
    ap.add_argument("--top-n", type=int, default=10_000)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    matrix = pd.read_parquet(args.matrix)
    org_pay = pd.read_parquet(args.org_payments) if args.org_payments else None
    lists = build_lead_lists(matrix, org_payments=org_pay,
                             threshold=args.threshold, top_n=args.top_n)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lists["top_new"].to_csv(out_dir / "top_new_leads.csv", index=False)
    lists["excluded_adjacent"].to_csv(out_dir / "excluded_adjacent_leads.csv", index=False)
    write_lead_report(lists, out_dir, args.threshold)
    print(f"wrote {len(lists['top_new']):,} leads "
          f"({int(lists['top_new']['passes_gate'].sum()):,} pass the gate) "
          f"+ {len(lists['excluded_adjacent']):,} excluded-adjacent → {out_dir}")


if __name__ == "__main__":
    main()
