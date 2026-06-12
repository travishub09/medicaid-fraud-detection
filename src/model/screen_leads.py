"""
screen_leads.py (model) — apply the validated institutional false-positive
screens to the model lead list.

Reuses classify/normalize/taxonomy_code from attempt_2's build_final_leads.py
verbatim (government / tribal / public-academic / national-nonprofit / hospital
name keywords + hospital and FQHC taxonomy codes) so the model list gets the
SAME screening as the unsupervised FinalLeads list. The lead CSV carries no
specialty column, so each company's taxonomy is taken from its dominant
(highest net_paid) constituent NPI — the same "specialty" semantics
finalize_tracker uses.

Quarantine, never silently delete: removed rows go to an audit CSV with the
matched category and keyword.

Run (after export_leads.py):
    python -m src.model.screen_leads
"""

import argparse
from collections import Counter
from pathlib import Path

import pandas as pd

from ..attempt_2.leads.build_final_leads import classify, normalize, taxonomy_code
from . import config
from .data import log, require


def dominant_taxonomy(npi_list: pd.Series) -> pd.Series:
    """Per lead, the primary_taxonomy of its highest-billing constituent NPI."""
    prov = pd.read_parquet(config.SCORES_DIR / "provider_model_scores.parquet",
                           columns=["npi", "primary_taxonomy", "net_paid"])
    best = (prov.sort_values("net_paid", ascending=False)
                .drop_duplicates("npi").set_index("npi"))
    out = []
    for npis in npi_list:
        members = best.loc[[n for n in str(npis).split("|") if n in best.index]]
        out.append("" if members.empty
                   else str(members.sort_values("net_paid", ascending=False)
                            ["primary_taxonomy"].iloc[0] or ""))
    return pd.Series(out, index=npi_list.index)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in", dest="in_csv", type=str, default=str(
        config.MODEL_DATA_DIR / "model_leads_top5000_over10m.csv"))
    args = p.parse_args()
    in_path = Path(args.in_csv)

    log("[1/3] Loading model lead list + dominant constituent taxonomies")
    leads = pd.read_csv(in_path, dtype={"npi_list": str})
    leads["specialty"] = dominant_taxonomy(leads["npi_list"])

    log("[2/3] Classifying against the build_final_leads screens")
    cats, toks = [], []
    for nm, sp in zip(leads["company_name"], leads["specialty"]):
        c, t = classify(normalize(nm), taxonomy_code(sp))
        cats.append(c)
        toks.append(t)
    removed = pd.Series([c is not None for c in cats], index=leads.index)
    kept = leads.loc[~removed].copy()
    audit = leads.loc[removed].copy()
    audit["removed_category"] = [c for c in cats if c is not None]
    audit["matched"] = [t for c, t in zip(cats, toks) if c is not None]
    require("kept + removed covers every lead", len(kept) + len(audit) == len(leads))

    log("[3/3] Writing screened list + removal audit")
    out_csv = in_path.with_name(in_path.stem + "_screened.csv")
    audit_csv = in_path.with_name(in_path.stem + "_removed_audit.csv")
    kept.to_csv(out_csv, index=False)
    audit.to_csv(audit_csv, index=False)
    for label, n in Counter(audit["removed_category"]).most_common():
        log(f"    removed {label:<20} {n:>5}")
    log(f"    total removed {int(removed.sum()):,} | kept {len(kept):,} "
        f"(${kept['company_net_paid'].sum()/1e9:.1f}B) → {out_csv}")


if __name__ == "__main__":
    main()
