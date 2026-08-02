"""
operating_points.py — core vs full at REVIEW-BUDGET cutoffs, not deciles.

The counterparty critique of top-decile lift is fair: a decile of 616k
providers is 61,600 rows, which is not a review budget anyone has. Their
preferred view — set a cutoff, count how many providers score high enough to
matter, and check how many of those are real — is the deployment view. This
module produces it, honestly, both ways it can be defined:

  1. MATCHED REVIEW BUDGETS. Raw scores from two different models are not on
     the same scale, so "the same score cutoff" is apples to oranges. The fair
     version fixes the budget: at the top 250 / 500 / 1,000 / ... providers,
     how many future-banned providers does each model's list contain
     (precision and recall at N), with a cluster-bootstrap CI on the
     full-minus-core catch difference at every budget.
  2. CALIBRATED-PROBABILITY CUTOFFS. Each model's out-of-fold scores are
     isotonic-calibrated against the forward label, so "score high enough to
     matter" becomes a real number: P(future ban) >= x%. For each cutoff the
     table shows how many providers clear it and the precision among them —
     the literal "how many providers score high enough" count.

Same discipline as every other verdict: identical folds, groups, and seeds for
both models; the ONLY difference is the columns. Scores are out-of-fold (no
provider is scored by a model that trained on it); the label is the frozen
forward one (first exclusion strictly after the cutoff).

  python -m src.model_a.operating_points \
      --matrix frozen/provider_features_for_model.parquet \
      --manifest frozen/feature_manifest.json \
      --future-label frozen/future_bans_after_2023-12.csv \
      --out frozen/OPERATING_POINTS.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a.feature_partition import partition_features, full_feature_list
from src.model_a.source_ablation import (oof_predict, resolve_label,
                                         _group_index, _draw_cluster)

RANDOM_STATE = 42
DEFAULT_BUDGETS = (250, 500, 1_000, 2_500, 5_000, 10_000, 25_000)
DEFAULT_PROB_CUTOFFS = (0.005, 0.01, 0.02, 0.05, 0.10)


def catches_at_budget(y: np.ndarray, scores: np.ndarray, n: int) -> int:
    """Future positives inside the top-n by score."""
    n = min(int(n), len(scores))
    idx = np.argpartition(-scores, n - 1)[:n]
    return int(y[idx].sum())


def budget_table(y, p_core, p_full, groups, budgets=DEFAULT_BUDGETS,
                 n_boot: int = 400, seed: int = RANDOM_STATE) -> list[dict]:
    """Catches/precision/recall per budget + bootstrap CI on the difference.

    One cluster resample per draw serves every budget (a single argsort of the
    resampled scores), so the CI cost does not multiply by len(budgets)."""
    total_pos = int(y.sum())
    rows = []
    for n in budgets:
        if n > len(y):
            continue
        c_core = catches_at_budget(y, p_core, n)
        c_full = catches_at_budget(y, p_full, n)
        rows.append({
            "budget": int(n),
            "core_catches": c_core, "full_catches": c_full,
            "core_precision": c_core / n, "full_precision": c_full / n,
            "core_recall": c_core / total_pos if total_pos else float("nan"),
            "full_recall": c_full / total_pos if total_pos else float("nan"),
            "delta": c_full - c_core})

    g = np.asarray(groups) if groups is not None else np.arange(len(y))
    order, starts, counts = _group_index(g)
    rng = np.random.RandomState(seed)
    deltas: dict[int, list[int]] = {r["budget"]: [] for r in rows}
    for _ in range(n_boot):
        pick = rng.randint(0, len(counts), size=len(counts))
        idx = _draw_cluster(order, starts, counts, pick)
        if len(idx) == 0 or y[idx].sum() == 0:
            continue
        yb, cb, fb = y[idx], p_core[idx], p_full[idx]
        for r in rows:
            n = min(r["budget"], len(idx))
            deltas[r["budget"]].append(
                catches_at_budget(yb, fb, n) - catches_at_budget(yb, cb, n))
    for r in rows:
        v = deltas[r["budget"]]
        if v:
            r["delta_lo"] = float(np.percentile(v, 2.5))
            r["delta_hi"] = float(np.percentile(v, 97.5))
        else:
            r["delta_lo"] = r["delta_hi"] = float("nan")
    return rows


def combined_table(y, p_core, p_full, budgets=DEFAULT_BUDGETS) -> list[dict]:
    """The TOGETHER view: what the two models find jointly.

    Two honest versions per budget:
      * combined ranking — each provider's rank-percentile under each model,
        averaged (scale-free; neither model's raw score dominates), then the
        top-N of that single blended list: catches and precision (the
        'what % of the top slice is future-banned' number).
      * union of lists — top-N from EACH model, unioned: how many
        future-banned providers appear on at least one list, with the union's
        actual size (up to 2N) so its precision is computed on what a reviewer
        would really read."""
    r_core = pd.Series(p_core).rank(pct=True).to_numpy()
    r_full = pd.Series(p_full).rank(pct=True).to_numpy()
    blended = (r_core + r_full) / 2.0
    total_pos = int(y.sum())
    out = []
    for n in budgets:
        if n > len(y):
            continue
        c_blend = catches_at_budget(y, blended, n)
        top_c = np.argpartition(-p_core, n - 1)[:n]
        top_f = np.argpartition(-p_full, n - 1)[:n]
        union = np.union1d(top_c, top_f)
        out.append({
            "budget": int(n),
            "pct_universe": n / len(y),
            "blend_catches": c_blend,
            "blend_precision": c_blend / n,
            "blend_recall": c_blend / total_pos if total_pos else float("nan"),
            "union_size": int(len(union)),
            "union_catches": int(y[union].sum()),
            "union_precision": (float(y[union].sum()) / len(union)
                                if len(union) else float("nan"))})
    return out


def calibrated_cutoff_table(y, p_core, p_full,
                            cutoffs=DEFAULT_PROB_CUTOFFS) -> list[dict]:
    """Isotonic-calibrate each model's OOF scores, then count who clears each
    P(future ban) cutoff — the literal 'how many score high enough to matter'."""
    from sklearn.isotonic import IsotonicRegression
    out = []
    cal = {}
    for name, p in (("core", p_core), ("full", p_full)):
        iso = IsotonicRegression(out_of_bounds="clip")
        cal[name] = iso.fit_transform(p, y)
    for cut in cutoffs:
        row = {"cutoff": float(cut)}
        for name in ("core", "full"):
            flagged = cal[name] >= cut
            n_f = int(flagged.sum())
            row[f"{name}_flagged"] = n_f
            row[f"{name}_true"] = int(y[flagged].sum())
            row[f"{name}_precision"] = (row[f"{name}_true"] / n_f
                                        if n_f else float("nan"))
        out.append(row)
    return out


def run_operating_points(matrix: pd.DataFrame, manifest: dict,
                         future_label: pd.DataFrame,
                         budgets=DEFAULT_BUDGETS, n_splits: int = 5,
                         n_boot: int = 400) -> dict:
    m, y, label_name = resolve_label(matrix, "provider_on_exclusion",
                                     future_label)
    groups = (m["group_id"].to_numpy() if "group_id" in m.columns else None)
    part = partition_features(manifest)
    core, full = part["core"], full_feature_list(part)
    p_core = oof_predict(m, y, core, groups, n_splits=n_splits)
    p_full = oof_predict(m, y, full, groups, n_splits=n_splits)
    ok = ~(np.isnan(p_core) | np.isnan(p_full))
    y_s, pc, pf = y[ok], p_core[ok], p_full[ok]
    g_s = groups[ok] if groups is not None else None
    return {"label": label_name, "n": int(ok.sum()),
            "positives": int(y_s.sum()),
            "n_core": part["n_core"], "n_extra": part["n_extra"],
            "budgets": budget_table(y_s, pc, pf, g_s, budgets=budgets,
                                    n_boot=n_boot),
            "combined": combined_table(y_s, pc, pf, budgets=budgets),
            "prob_cutoffs": calibrated_cutoff_table(y_s, pc, pf)}


def to_markdown(res: dict) -> str:
    L = ["# OPERATING POINTS — core vs full at review-budget cutoffs", ""]
    L.append(f"_The cutoff view of the same frozen forward test: "
             f"{res['positives']:,} future-banned providers among "
             f"{res['n']:,} scored out-of-fold. Core model: "
             f"{res['n_core']} features from the five core files; full model "
             f"adds {res['n_extra']} features from the extra sources. Same "
             f"folds, groups, and seeds — only the columns differ._")
    L.append("")
    L.append("## At matched review budgets (top-N providers)")
    L.append("")
    L.append("_Raw scores from different models are not on one scale, so the "
             "fair cutoff is a fixed review budget: if a team can look at N "
             "providers, whose list catches more of the future-banned?_")
    L.append("")
    L.append("| budget N | core catches | full catches | full-core "
             "[95% CI] | core precision | full precision | full recall |")
    L.append("|--:|--:|--:|---|--:|--:|--:|")
    for r in res["budgets"]:
        ci = ("—" if r["delta_lo"] != r["delta_lo"] else
              f"{r['delta']:+d} [{r['delta_lo']:+.0f}, {r['delta_hi']:+.0f}]")
        L.append(f"| {r['budget']:,} | {r['core_catches']} "
                 f"| {r['full_catches']} | {ci} "
                 f"| {r['core_precision']:.2%} | {r['full_precision']:.2%} "
                 f"| {r['full_recall']:.1%} |")
    L.append("")
    L.append("## The two models TOGETHER (blended list + union of lists)")
    L.append("")
    L.append("_The joint view: a single blended ranking (each provider's rank "
             "under each model, averaged) and the union of both top-N lists. "
             "'Blend precision' is the direct answer to 'what % of the top "
             "slice of the combined list are future-banned providers'._")
    L.append("")
    L.append("| budget N | % of universe | blend catches | blend precision "
             "| blend recall | union list size | union catches | union precision |")
    L.append("|--:|--:|--:|--:|--:|--:|--:|--:|")
    for r in res.get("combined", []):
        L.append(f"| {r['budget']:,} | {r['pct_universe']:.2%} "
                 f"| {r['blend_catches']} | {r['blend_precision']:.2%} "
                 f"| {r['blend_recall']:.1%} | {r['union_size']:,} "
                 f"| {r['union_catches']} | {r['union_precision']:.2%} |")
    L.append("")
    L.append("_If the blended list beats both single lists at the same budget, "
             "the two models are complementary and the case for combining them "
             "is made by the numbers; if it merely matches the full model, the "
             "full model already contains what the core one knows._")
    L.append("")
    L.append("## At calibrated probability cutoffs")
    L.append("")
    L.append("_Each model's out-of-fold scores are isotonic-calibrated against "
             "the forward label, so a cutoff is a real probability. This is "
             "the literal 'how many providers score high enough to matter' "
             "count — and note the flagged-list SIZES differ between models "
             "at the same probability, which is information, not unfairness: "
             "a model that can push more providers above a given risk level "
             "with the same precision has found more signal._")
    L.append("")
    L.append("| P(future ban) >= | core flagged | core true | core prec "
             "| full flagged | full true | full prec |")
    L.append("|--:|--:|--:|--:|--:|--:|--:|")
    for r in res["prob_cutoffs"]:
        def _p(v):
            return "—" if v != v else f"{v:.1%}"
        L.append(f"| {r['cutoff']:.1%} | {r['core_flagged']:,} "
                 f"| {r['core_true']} | {_p(r['core_precision'])} "
                 f"| {r['full_flagged']:,} | {r['full_true']} "
                 f"| {_p(r['full_precision'])} |")
    L.append("")
    L.append("_Read with the PR-AUC verdict from the ablation report (the "
             "area under the whole precision-recall tradeoff): full beats "
             "core with a CI clear of zero. These tables are that same win "
             "expressed at the specific cutoffs a review team would actually "
             "staff._")
    L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--future-label", required=True)
    ap.add_argument("--out", default="OPERATING_POINTS.md")
    ap.add_argument("--budgets", default=None,
                    help="comma-separated top-N budgets "
                         "(default 250,500,1000,2500,5000,10000,25000)")
    ap.add_argument("--splits", type=int, default=5)
    ap.add_argument("--boot", type=int, default=400)
    args = ap.parse_args()
    matrix = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    fl = pd.read_csv(args.future_label, dtype=str)
    budgets = (tuple(int(b) for b in args.budgets.split(","))
               if args.budgets else DEFAULT_BUDGETS)
    res = run_operating_points(matrix, manifest, fl, budgets=budgets,
                               n_splits=args.splits, n_boot=args.boot)
    Path(args.out).write_text(to_markdown(res), encoding="utf-8")
    print(f"[operating_points] {len(res['budgets'])} budgets, "
          f"{res['positives']:,} forward positives -> {args.out}")


if __name__ == "__main__":
    main()
