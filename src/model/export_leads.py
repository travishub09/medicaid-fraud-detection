"""
export_leads.py (model) — spreadsheet-ready company lead list from the model scores.

Takes the company-grain model scores (score.py output), applies the agreed lead
filters — minimum consolidated billing, then top-K by max constituent model
score — and writes one CSV. Companies whose rollup name is null (single-NPI
companies) get their name resolved from the highest-scoring constituent NPI's
org_legal_name; still-unresolved names fall back to "UNKNOWN NAME (NPI <npi>)".

Scores are uncalibrated ranking scores (leads for human review, never
determinations); the list is defined by SIZE (top-K), not by a score threshold.

Run (after score.py):
    python -m src.model.export_leads --top-k 5000 --min-net-paid 10000000
"""

import argparse
from pathlib import Path

import pandas as pd

from . import config
from .data import log, require

OUT_COLS = ["rank", "company_name", "company_model_score_max",
            "company_model_score_wmean", "company_net_paid", "n_npis",
            "n_npis_reliable", "n_leie_npis", "merge_confidence", "npi_list"]


def resolve_names(comp: pd.DataFrame, prov: pd.DataFrame) -> pd.DataFrame:
    nmap = pd.read_parquet(config.NPI_TO_COMPANY_MAP, columns=["npi", "company_id"])
    j = prov.merge(nmap, on="npi", how="inner", validate="1:1")
    j = j.sort_values("model_score", ascending=False)
    best = j.groupby("company_id", sort=False).agg(
        _best_name=("org_legal_name", "first"), _best_npi=("npi", "first"))
    npis = j.groupby("company_id", sort=False)["npi"].agg("|".join).rename("npi_list")
    out = comp.merge(best, on="company_id", how="left", validate="1:1") \
              .merge(npis, on="company_id", how="left", validate="1:1")
    # blank-string names (individual NPIs have no org_legal_name) count as missing
    for c in ("company_name", "_best_name"):
        out[c] = out[c].replace(r"^\s*$", pd.NA, regex=True)
    out["company_name"] = out["company_name"].fillna(out["_best_name"])
    out["company_name"] = out["company_name"].fillna(
        "UNKNOWN NAME (NPI " + out["_best_npi"].astype(str) + ")")
    return out.drop(columns=["_best_name", "_best_npi"])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--top-k", type=int, default=5000)
    p.add_argument("--min-net-paid", type=float, default=10_000_000.0)
    p.add_argument("--min-score", type=float, default=0.0,
                   help="floor on company_model_score_max (uncalibrated — pick "
                        "by inspecting the score distribution, not by analogy "
                        "to the unsupervised 0.70 bar)")
    p.add_argument("--out", type=str, default=str(
        config.MODEL_DATA_DIR / "model_leads_top5000_over10m.csv"))
    args = p.parse_args()

    log("[1/3] Loading company + provider model scores")
    comp = pd.read_parquet(config.SCORES_DIR / "company_model_scores.parquet")
    prov = pd.read_parquet(config.SCORES_DIR / "provider_model_scores.parquet",
                           columns=["npi", "model_score", "org_legal_name"])
    require("company_id unique", comp["company_id"].is_unique)

    log("[2/3] Filtering: billing >= threshold, reliable score present, top-K")
    eligible = comp[(comp["company_net_paid"] >= args.min_net_paid)
                    & (comp["company_model_score_max"] >= args.min_score)]
    log(f"    {len(eligible):,} companies >= ${args.min_net_paid:,.0f} with a "
        f"reliable score >= {args.min_score}")
    leads = eligible.sort_values(
        ["company_model_score_max", "company_net_paid"],
        ascending=False).head(args.top_k).copy()
    leads = resolve_names(leads, prov)
    leads["rank"] = range(1, len(leads) + 1)
    require("no unresolved company names", bool(leads["company_name"].notna().all()))
    require("every lead meets the billing floor",
            bool((leads["company_net_paid"] >= args.min_net_paid).all()))

    log("[3/3] Writing CSV")
    out_path = Path(args.out)
    leads[OUT_COLS].to_csv(out_path, index=False)
    log(f"    {len(leads):,} leads (score {leads['company_model_score_max'].min():.4f}"
        f"–{leads['company_model_score_max'].max():.4f}, "
        f"${leads['company_net_paid'].sum()/1e9:.1f}B total billing) → {out_path}")


if __name__ == "__main__":
    main()
