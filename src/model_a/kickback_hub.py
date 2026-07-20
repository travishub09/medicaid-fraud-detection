"""
kickback_hub.py — flip the kickback signal from spokes to HUBS.

The dossier engine finds individual prescribers whose prescribing tracks the
money they take (the spokes). But in a kickback case the recoverable defendant
is usually the PAYER — the manufacturer or device company whose payments taint
every downstream claim across many prescribers, and which carries the balance
sheet. This module ranks those payers.

Given the flagged spokes (high kickback subscore / high payment-utilization
co-occurrence) and the Open Payments records, it groups by manufacturer and
scores each on: how many flagged spokes it pays, the dollars it pays them, how
concentrated its payments are on flagged versus ordinary recipients, and an
optional estimate of the tainted downstream billing sitting under it. The top
hubs become their own dossier, and they are the strongest ABM targets on the
board — company sales reps are historically the most successful relator class,
because the rep holds the payment spreadsheet.

    from src.model_a.kickback_hub import build_kickback_hubs
    hubs = build_kickback_hubs(op_raw, spokes)   # spokes: npi + optional weights

Output is leads and marketing audiences for counsel review, never an accusation
against any company or person.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.attempt_2.clean_data import canonicalize_series, _resolve_columns
from src.ingest_cms.openpayments import OP_COLS

_HUB_COLS = ["hub", "n_spokes", "total_to_spokes", "n_recipients",
             "spoke_recipient_share", "mean_spoke_corr",
             "est_downstream_tainted", "hub_score"]


def build_kickback_hubs(op_raw: pd.DataFrame, spokes: pd.DataFrame,
                        min_spokes: int = 2) -> pd.DataFrame:
    """One row per manufacturer/GPO that pays at least ``min_spokes`` flagged
    prescribers, ranked by a hub score.

    ``spokes`` needs an ``npi`` column; optional weight columns sharpen the
    score when present:
      * ``op_payment_utilization_corr`` — how tightly each spoke's prescribing
        tracks payments (0-1); averaged into ``mean_spoke_corr``.
      * ``suspect_dollars`` (or ``net_paid``) — used with the corr to estimate
        the tainted downstream billing a hub sits under, attributed to hubs in
        proportion to the dollars each paid that spoke.
    """
    if op_raw is None or not len(op_raw) or spokes is None or not len(spokes):
        return pd.DataFrame(columns=_HUB_COLS)

    resolved = _resolve_columns(list(op_raw.columns), OP_COLS)
    missing = [c for c in ["npi", "manufacturer", "amount"] if c not in resolved]
    if missing:
        raise ValueError(f"Open Payments file missing required columns {missing}; "
                         f"saw {list(op_raw.columns)[:12]}")
    op = op_raw.rename(columns={v: k for k, v in resolved.items()})
    op = op.assign(npi=canonicalize_series(op["npi"]))
    op = op[op["npi"].notna()].copy()
    op["manufacturer"] = op["manufacturer"].fillna("").astype(str).str.strip().str.upper()
    op["amount"] = pd.to_numeric(op["amount"], errors="coerce").fillna(0.0)
    op = op[op["manufacturer"] != ""]

    # recipient breadth per hub (context: a hub that pays everyone is less
    # interesting than one whose money concentrates on flagged prescribers)
    recipients = op.groupby("manufacturer")["npi"].nunique().rename("n_recipients")

    sp = spokes.copy()
    sp["npi"] = sp["npi"].astype(str)
    canon = canonicalize_series(sp["npi"])
    sp = sp.assign(npi=canon)[canon.notna()].drop_duplicates("npi")
    spoke_npis = set(sp["npi"])
    if not spoke_npis:
        return pd.DataFrame(columns=_HUB_COLS)

    corr = (pd.to_numeric(sp.get("op_payment_utilization_corr"), errors="coerce")
            if "op_payment_utilization_corr" in sp.columns else None)
    weight_col = next((c for c in ("suspect_dollars", "net_paid", "gross_paid")
                       if c in sp.columns), None)
    sp_weight = (pd.to_numeric(sp[weight_col], errors="coerce").fillna(0.0)
                 if weight_col else None)
    corr_by_npi = dict(zip(sp["npi"], corr)) if corr is not None else {}
    weight_by_npi = dict(zip(sp["npi"], sp_weight)) if sp_weight is not None else {}

    paid = op[op["npi"].isin(spoke_npis)].copy()
    if not len(paid):
        return pd.DataFrame(columns=_HUB_COLS)

    # spoke-level payment from each hub (sum across a hub's many payment rows)
    hub_spoke = (paid.groupby(["manufacturer", "npi"], as_index=False)["amount"].sum())
    # each spoke's TOTAL payments (across all hubs), for the tainted-billing split
    spoke_total_pay = hub_spoke.groupby("npi")["amount"].sum().to_dict()

    def _tainted(mfr_rows: pd.DataFrame) -> float:
        # a spoke's tainted billing ~= its suspect dollars * its payment-corr;
        # attribute to this hub by the hub's share of that spoke's total payments
        if not weight_by_npi:
            return float("nan")
        total = 0.0
        for npi_val, amt in zip(mfr_rows["npi"], mfr_rows["amount"]):
            wt = weight_by_npi.get(npi_val, 0.0)
            c = corr_by_npi.get(npi_val, 1.0)
            c = c if (c is not None and not np.isnan(c)) else 1.0
            denom = spoke_total_pay.get(npi_val, 0.0)
            share = (amt / denom) if denom > 0 else 0.0
            total += wt * c * share
        return float(total)

    rows = []
    for mfr, g in hub_spoke.groupby("manufacturer"):
        n_spokes = int(g["npi"].nunique())
        if n_spokes < min_spokes:
            continue
        total_to_spokes = float(g["amount"].sum())
        n_rec = int(recipients.get(mfr, n_spokes))
        share = n_spokes / n_rec if n_rec else float("nan")
        if corr_by_npi:
            cs = [corr_by_npi.get(n) for n in g["npi"]]
            cs = [c for c in cs if c is not None and not np.isnan(c)]
            mean_corr = float(np.mean(cs)) if cs else float("nan")
        else:
            mean_corr = float("nan")
        rows.append({
            "hub": mfr, "n_spokes": n_spokes,
            "total_to_spokes": round(total_to_spokes, 2),
            "n_recipients": n_rec, "spoke_recipient_share": round(share, 4),
            "mean_spoke_corr": round(mean_corr, 3) if not np.isnan(mean_corr) else None,
            "est_downstream_tainted": (round(_tainted(g), 2)
                                       if weight_by_npi else None),
        })
    if not rows:
        return pd.DataFrame(columns=_HUB_COLS)
    hubs = pd.DataFrame(rows)

    # hub score: reward breadth of flagged spokes, dollars, and concentration on
    # flagged recipients. Log-damp dollars so one huge payment doesn't dominate
    # count. All one-sided (only excess is suspicious).
    dollars = np.log1p(hubs["total_to_spokes"].clip(lower=0))
    hubs["hub_score"] = (hubs["n_spokes"]
                         * (1.0 + hubs["spoke_recipient_share"].fillna(0))
                         * (1.0 + dollars / dollars.max() if dollars.max() > 0 else 1.0))
    hubs["hub_score"] = hubs["hub_score"].round(2)
    return hubs.sort_values("hub_score", ascending=False)[_HUB_COLS].reset_index(drop=True)


def to_markdown(hubs: pd.DataFrame, n_spokes_total: int = 0) -> str:
    L = ["# KICKBACK HUBS — the payers behind the flagged prescribers", ""]
    L.append("_In a kickback case the recoverable defendant is usually the payer, "
             "not the prescriber: its money taints every downstream claim across "
             "many prescribers. This ranks manufacturers/GPOs by how many flagged "
             "prescribers they pay, the dollars, and how concentrated those "
             "payments are on flagged versus ordinary recipients._")
    L.append("")
    if not len(hubs):
        L.append("**No hub paid the minimum number of flagged prescribers.**")
        return "\n".join(L)
    if n_spokes_total:
        L.append(f"- Flagged prescribers (spokes) considered: **{n_spokes_total:,}**")
    L.append(f"- Hubs surfaced: **{len(hubs)}**")
    L.append("")
    L.append("| hub | flagged spokes paid | $ to spokes | spoke share of recipients | mean corr | est. downstream tainted |")
    L.append("|---|--:|--:|--:|--:|--:|")
    for r in hubs.head(25).itertuples():
        tainted = (f"${r.est_downstream_tainted:,.0f}"
                   if r.est_downstream_tainted is not None else "-")
        corr = f"{r.mean_spoke_corr}" if r.mean_spoke_corr is not None else "-"
        L.append(f"| {r.hub[:40]} | {r.n_spokes} | ${r.total_to_spokes:,.0f} | "
                 f"{r.spoke_recipient_share:.1%} | {corr} | {tainted} |")
    L.append("")
    L.append("_These are the two deliverables the hub view feeds: a payer-side "
             "dossier for counsel, and the strongest ABM targets on the board "
             "(the companies' own sales reps are the relator pool). Leads and "
             "audiences for counsel review, never an accusation against any "
             "company or person._")
    return "\n".join(L)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--open-payments", required=True,
                    help="Open Payments CSV (ideally the cut of flagged spokes)")
    ap.add_argument("--matrix", required=True,
                    help="provider matrix parquet (source of the flagged spokes)")
    ap.add_argument("--spoke-threshold", type=float, default=0.7,
                    help="min subscore_pharma_kickback to count as a spoke")
    ap.add_argument("--min-spokes", type=int, default=2)
    ap.add_argument("--out", default="KICKBACK_HUBS.md")
    ap.add_argument("--results", default=None, help="optional hubs parquet path")
    args = ap.parse_args()

    from src.attempt_2.clean_data import read_csv_text
    op = read_csv_text(args.open_payments)
    m = pd.read_parquet(args.matrix)
    m["npi"] = m["npi"].astype(str)
    if "subscore_pharma_kickback" not in m.columns:
        # degrade with a named reason, not a crash: fall back to the raw
        # co-occurrence if present, else there is no spoke signal to flip
        fallback = "op_payment_utilization_corr"
        if fallback in m.columns:
            print(f"[kickback_hub] matrix has no subscore_pharma_kickback — "
                  f"falling back to {fallback} >= {args.spoke_threshold}")
            m["subscore_pharma_kickback"] = pd.to_numeric(
                m[fallback], errors="coerce").fillna(0)
        else:
            print("[kickback_hub] matrix has neither subscore_pharma_kickback "
                  "nor op_payment_utilization_corr — no spoke signal; writing "
                  "an empty report")
            m["subscore_pharma_kickback"] = 0.0
    sub = pd.to_numeric(m["subscore_pharma_kickback"], errors="coerce").fillna(0)
    keep = ["npi"]
    for c in ("op_payment_utilization_corr", "suspect_dollars", "net_paid",
              "gross_paid", "expected_net_paid"):
        if c in m.columns:
            keep.append(c)
    spokes = m.loc[sub >= args.spoke_threshold, keep].copy()
    if "suspect_dollars" not in spokes.columns and \
            {"net_paid", "expected_net_paid"}.issubset(m.columns):
        spokes["suspect_dollars"] = (pd.to_numeric(m.loc[sub >= args.spoke_threshold, "net_paid"],
                                                    errors="coerce").fillna(0)
                                     - pd.to_numeric(m.loc[sub >= args.spoke_threshold, "expected_net_paid"],
                                                     errors="coerce").fillna(0))
    hubs = build_kickback_hubs(op, spokes, min_spokes=args.min_spokes)
    Path(args.out).write_text(to_markdown(hubs, len(spokes)), encoding="utf-8")
    if args.results:
        hubs.to_parquet(args.results, index=False)
    print(f"[kickback_hub] {len(spokes):,} spokes → {len(hubs)} hubs → {args.out}")


if __name__ == "__main__":
    main()
