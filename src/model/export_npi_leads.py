"""
export_npi_leads.py (model) — provider-grain lead list straight from the model
scores: NO company rollup.

Same process as the company list (export_leads + screen_leads) at the NPI
grain: filter to reliable scores, a billing floor, and a score floor; rank by
model score; then apply the validated institutional false-positive screens
(name keywords + hospital/FQHC taxonomy, reused verbatim from attempt_2's
build_final_leads). Screening uses each NPI's own org_legal_name and
primary_taxonomy — individual providers carry no org name, so they can only be
screened by taxonomy. Removed rows are quarantined to an audit CSV.

Writes <out>, <out>_screened.csv and <out>_removed_audit.csv.

Run (after score.py):
    python -m src.model.export_npi_leads --min-net-paid 5000000 --min-score 0.9
"""

import argparse
from collections import Counter
from pathlib import Path

import pandas as pd

from ..attempt_2.leads.build_final_leads import classify, normalize, taxonomy_code
from . import config
from .data import log, require

OUT_COLS = ["rank", "npi", "provider_name", "model_score", "net_paid",
            "entity_type", "practice_state", "primary_taxonomy", "segment",
            "provider_on_leie"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--min-net-paid", type=float, default=5_000_000.0)
    p.add_argument("--min-score", type=float, default=0.9,
                   help="floor on model_score (uncalibrated ranking score)")
    p.add_argument("--out", type=str, default=str(
        config.MODEL_DATA_DIR / "model_npi_leads_score090_over5m.csv"))
    args = p.parse_args()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log("[1/3] Loading provider model scores")
    prov = pd.read_parquet(config.SCORES_DIR / "provider_model_scores.parquet")
    require("npi unique", prov["npi"].is_unique)

    log("[2/3] Filtering: reliable score, billing + score floors")
    leads = prov[prov["score_reliable"]
                 & (prov["net_paid"] >= args.min_net_paid)
                 & (prov["model_score"] >= args.min_score)].copy()
    log(f"    {len(leads):,} NPIs with reliable score >= {args.min_score} "
        f"and net_paid >= ${args.min_net_paid:,.0f}")
    leads = leads.sort_values(["model_score", "net_paid"], ascending=False)
    leads["rank"] = range(1, len(leads) + 1)
    blank = leads["org_legal_name"].fillna("").str.strip().eq("")
    label = pd.Series("UNKNOWN NAME", index=leads.index).where(
        leads["entity_type"] != "1", "INDIVIDUAL PROVIDER")
    leads["provider_name"] = leads["org_legal_name"].where(
        ~blank, label + " (NPI " + leads["npi"] + ")")
    require("every lead has a name", bool(leads["provider_name"].notna().all()))

    log("[3/3] Screening + writing")
    cats, toks = [], []
    for nm, tax in zip(leads["org_legal_name"].fillna(""), leads["primary_taxonomy"]):
        c, t = classify(normalize(nm), taxonomy_code(tax))
        cats.append(c)
        toks.append(t)
    removed = pd.Series([c is not None for c in cats], index=leads.index)
    kept = leads.loc[~removed, OUT_COLS]
    audit = leads.loc[removed, OUT_COLS].copy()
    audit["removed_category"] = [c for c in cats if c is not None]
    audit["matched"] = [t for c, t in zip(cats, toks) if c is not None]
    require("kept + removed covers every lead", len(kept) + len(audit) == len(leads))

    kept.to_csv(out_path.with_name(out_path.stem + "_screened.csv"), index=False)
    audit.to_csv(out_path.with_name(out_path.stem + "_removed_audit.csv"), index=False)
    leads[OUT_COLS].to_csv(out_path, index=False)
    for label, n in Counter(audit["removed_category"]).most_common():
        log(f"    removed {label:<20} {n:>5}")
    log(f"    kept {len(kept):,} (${kept['net_paid'].sum()/1e9:.1f}B; "
        f"{int(kept['provider_on_leie'].sum())} on-LEIE) → "
        f"{out_path.with_name(out_path.stem + '_screened.csv')}")


if __name__ == "__main__":
    main()
