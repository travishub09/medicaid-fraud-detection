"""
scheme_eval.py — test each source against the FRAUD TYPE it was built for.

The leave-one-group-out ablation asks every source to predict generic
exclusions — a job most sources weren't designed for, on one shared thin
label, which is why its per-source ranking wobbles between runs. This is the
sharper instrument: the DOJ/MFCU case label is SCHEME-TYPED, so each source
gets the question it exists to answer —

  does Open Payments help detect the KICKBACK cases?
  does HCRIS help detect the COST-REPORT cases?
  does the opioid file help detect the PRESCRIBING cases?

For each scheme family with enough case-labeled providers in the matrix:
  * positives = providers whose ``fraud_scheme`` matches the family;
  * the eval universe DROPS other-scheme case positives and non-matching
    exclusion positives (fraud-ish rows must not sit in the negative pool);
  * three OOF score sets, each trained on THIS scheme's label: CORE, FULL,
    and FULL-minus-the-mapped-source;
  * cluster-bootstrap CIs on (full - core) — does the bundle help here —
    and (full - without_source) — does the source built for this scheme
    carry it.

Positives per scheme are small (dozens to a few hundred), so intervals are
wide by nature; the report says so rather than hiding it. Runtime is hours
(many LGBM fits on the full matrix) — an overnight-class job.

  python -m src.model_a.scheme_eval --matrix .../provider_features_for_model.parquet \
      --manifest .../feature_manifest.json --out SCHEME_EVAL.md
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a.feature_partition import partition_features, full_feature_list
from src.model_a.source_ablation import (oof_predict, _oof_delta_ci,
                                         _OOF_METRICS)

# source -> (regex over the pipe-joined fraud_scheme tags, plain description).
# Free-text tolerant: the harvest lets agents use their own words when the
# suggested categories don't fit.
SOURCE_SCHEME_MAP = {
    "open_payments": (r"kickback|inducement|referral payment",
                      "kickback-type cases"),
    "opioid": (r"opioid|prescrib|pill|controlled substance",
               "unlawful-prescribing cases"),
    "hcris": (r"cost report", "cost-report cases"),
    "facility_quality": (r"worthless|neglect|substandard|grossly inadequate",
                         "worthless-services cases"),
    "hrsa_340b": (r"340b|contract pharmac", "340B / contract-pharmacy cases"),
    "medicare_puf": (r"upcod|billing_fraud|billing fraud|services_not_rendered|"
                     r"services not rendered|ghost|unnecessary|medical_necessity|"
                     r"medical necessity",
                     "billing-scheme cases (upcoding / not-rendered / necessity)"),
    "other_extra": (r"\bdme\b|equipment|durable medical",
                    "DME-equipment cases"),
}

# source -> columns defining the FAIR COMPARISON COHORT: providers with any
# real exposure to what the source measures. Without this the test collapses
# to "can you tell scheme-adjacent providers from the whole world" (opioid
# cases vs a universe of non-prescribers scores a fake 10x; hospital billing
# settlements vs an individual-provider universe scores below chance). The
# sharp question is bad-vs-comparable: positives judged against their own
# kind. A row qualifies when ANY listed column is numeric and > 0.
SOURCE_COHORT_COLS = {
    "open_payments": ["op_total_dollars"],
    "opioid": ["opioid_claims", "opioid_claim_share"],
    "hcris": ["hcris_cost_anomaly"],
    "facility_quality": ["deficiency_count", "pbj_understaffing",
                         "hospice_live_discharge_rate"],
    "hrsa_340b": ["contract_pharmacy_concentration"],
    "medicare_puf": ["total_allowed", "total_services"],
    "other_extra": ["dme_high_cost_item_share", "dme_code_concentration"],
}


def _to_num(v):
    return pd.to_numeric(pd.Series(v), errors="coerce").fillna(0).to_numpy()


def run_scheme_eval(matrix: pd.DataFrame, manifest: dict,
                    min_pos: int = 30, n_splits: int = 3,
                    n_boot: int = 400) -> dict:
    """Per-scheme source evaluation. Returns a dict ready for to_markdown."""
    if "fraud_scheme" not in matrix.columns:
        return {"error": "matrix has no fraud_scheme column — rebuild with "
                         "--case-db so the case labels ride along."}
    part = partition_features(manifest)
    core = part["core"]
    full = full_feature_list(part)
    extra = part["extra"]

    scheme = matrix["fraud_scheme"].fillna("").astype(str)
    any_case = scheme.str.strip() != ""
    excl = _to_num(matrix.get("provider_on_exclusion", 0)) == 1
    groups_all = (matrix["group_id"].astype(str).to_numpy()
                  if "group_id" in matrix.columns else None)

    results, skipped = {}, {}
    for source, (pat, desc) in SOURCE_SCHEME_MAP.items():
        if source not in extra:
            skipped[source] = "source group not present in this manifest"
            continue
        is_pos = scheme.str.contains(pat, case=False, regex=True)
        # fair-cohort restriction: judge positives against their own kind
        cohort = pd.Series(False, index=matrix.index)
        cohort_cols = [c for c in SOURCE_COHORT_COLS.get(source, [])
                       if c in matrix.columns]
        for cc in cohort_cols:
            cohort |= pd.Series(_to_num(matrix[cc]) > 0, index=matrix.index)
        if not cohort_cols:
            cohort[:] = True
        n_pos_all = int(is_pos.sum())
        is_pos = is_pos & cohort
        n_pos = int(is_pos.sum())
        if n_pos < min_pos:
            skipped[source] = (f"only {n_pos} case-labeled positives inside "
                               f"the comparison cohort ({n_pos_all} overall; "
                               f"< {min_pos})")
            continue
        # eval universe: this scheme's positives + the unlabeled pool WITHIN
        # the cohort. Other case positives and non-matching exclusion
        # positives are fraud-ish — they may not sit in the negative pool.
        keep = (is_pos | (~any_case & ~excl)) & cohort
        m = matrix.loc[keep.to_numpy()].reset_index(drop=True)
        y = is_pos.loc[keep.to_numpy()].astype(int).to_numpy()
        g = groups_all[keep.to_numpy()] if groups_all is not None else None

        without = [c for c in full if c not in set(extra[source])]
        p_core = oof_predict(m, y, core, g, n_splits=n_splits)
        p_full = oof_predict(m, y, full, g, n_splits=n_splits)
        p_wo = oof_predict(m, y, without, g, n_splits=n_splits)

        abs_scores = {}
        for tag, p in (("core", p_core), ("full", p_full), ("without", p_wo)):
            abs_scores[tag] = {k: float(f(y, p)) for k, f in _OOF_METRICS}
        n_aff = int((matrix.loc[is_pos.to_numpy(), "label_basis"]
                     .astype(str) == "affiliated_individual").sum()) \
            if "label_basis" in matrix.columns else None
        results[source] = {
            "description": desc,
            "n_pos": n_pos,
            "n_pos_all": n_pos_all,
            "n_pos_affiliated": n_aff,
            "n_eval": int(len(m)),
            "cohort_cols": cohort_cols,
            "n_source_features": len(extra[source]),
            "abs": abs_scores,
            "full_vs_core": _oof_delta_ci(y, p_full, p_core, g, n_boot=n_boot),
            "source_marginal": _oof_delta_ci(y, p_full, p_wo, g, n_boot=n_boot),
        }
    return {"results": results, "skipped": skipped,
            "n_core": len(core), "n_full": len(full),
            "min_pos": min_pos, "n_splits": n_splits}


def to_markdown(res: dict) -> str:
    L = ["# SCHEME-STRATIFIED SOURCE EVAL — each source vs its own fraud type",
         ""]
    if res.get("error"):
        return "\n".join(L + [f"**{res['error']}**"])
    L.append("_The sharp version of the which-sources-matter question: instead "
             "of asking every source to predict generic exclusions, each source "
             "is tested on the scheme-typed case label it was procured to "
             "detect. Positives per scheme are small, so read the intervals, "
             "not the point estimates._")
    for source, r in res["results"].items():
        L.append("")
        L.append(f"## {source} → {r['description']}")
        cohort_note = (
            f" | cohort: any of {', '.join(r['cohort_cols'])} > 0"
            if r.get("cohort_cols") else "")
        L.append(f"- positives: {r['n_pos']:,}"
                 + (f" (of {r['n_pos_all']:,} overall)"
                    if r.get("n_pos_all", r["n_pos"]) != r["n_pos"] else "")
                 + (f" | {r['n_pos_affiliated']:,} via affiliation broadcast "
                    "(weak basis: worked at a settling org)"
                    if r.get("n_pos_affiliated") else "")
                 + f" | eval universe: {r['n_eval']:,} | source features: "
                 f"{r['n_source_features']}{cohort_note}")
        if r["n_pos"] < 150:
            L.append(f"- _CAUTION: {r['n_pos']} positives is thin — bootstrap "
                     "intervals can collapse to [0, 0] at this count; treat "
                     "the verdict as provisional._")
        L.append("")
        L.append("| metric | core | full | without source | full-core [95% CI] "
                 "| source marginal [95% CI] |")
        L.append("|---|--:|--:|--:|---|---|")
        for k, name in (("lift10", "top-decile lift"), ("pr_auc", "PR-AUC"),
                        ("roc_auc", "ROC-AUC")):
            fc = r["full_vs_core"].get(k, {})
            sm = r["source_marginal"].get(k, {})
            L.append(
                f"| {name} | {r['abs']['core'].get(k, float('nan')):.4f} "
                f"| {r['abs']['full'].get(k, float('nan')):.4f} "
                f"| {r['abs']['without'].get(k, float('nan')):.4f} "
                f"| {fc.get('delta', float('nan')):+.4f} "
                f"[{fc.get('lo', float('nan')):+.4f}, {fc.get('hi', float('nan')):+.4f}] "
                f"| {sm.get('delta', float('nan')):+.4f} "
                f"[{sm.get('lo', float('nan')):+.4f}, {sm.get('hi', float('nan')):+.4f}] |")
        sm = r["source_marginal"].get("lift10", {})
        lo = sm.get("lo", float("nan"))
        verdict = ("EARNS ITS KEEP on its own scheme (marginal CI clear of zero)"
                   if lo == lo and lo > 0 else
                   "not yet resolved on its own scheme (CI includes zero)")
        L.append("")
        L.append(f"**{verdict}.**")
    if res.get("skipped"):
        L.append("")
        L.append("## Skipped")
        L.append("")
        for s, why in res["skipped"].items():
            L.append(f"- {s}: {why}")
        L.append("")
        L.append("_A skipped source is a harvest gap, not a verdict — more case "
                 "rows of its scheme type unlock it._")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--min-pos", type=int, default=30)
    ap.add_argument("--splits", type=int, default=3)
    ap.add_argument("--out", default="SCHEME_EVAL.md")
    args = ap.parse_args()
    matrix = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text())
    res = run_scheme_eval(matrix, manifest, min_pos=args.min_pos,
                          n_splits=args.splits)
    Path(args.out).write_text(to_markdown(res), encoding="utf-8")
    n = len(res.get("results", {}))
    print(f"[scheme_eval] {n} sources evaluated, "
          f"{len(res.get('skipped', {}))} skipped -> {args.out}")


if __name__ == "__main__":
    main()
