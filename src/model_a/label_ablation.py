"""
label_ablation.py — which LABEL sources earn their keep? Measured, like features.

Every run ablates FEATURE sources (source_ablation). Nobody has ever ablated the
LABEL. The training label ``provider_on_exclusion`` is a union — LEIE + the
OpenSanctions 50-state lists + SAM + Medicare revocations + DOJ case labels
(the Manus harvest) + deactivation/death smoking guns — and each provider
carries per-source provenance in ``exclusion_label_sources``. That provenance
makes the question answerable instead of arguable: retrain with one source's
positives removed, score out-of-fold, and measure the change in FORWARD
top-decile lift against the same frozen future-bans label every other verdict
uses.

Reading a ``minus_<src>`` row:
  delta CI entirely ABOVE zero   → dropping the source HELPED: it is noise on
                                   this eval; candidate to remove from the label.
  delta CI entirely BELOW zero   → the source is contributing forward signal;
                                   keep it.
  CI spans zero                  → no measurable effect either way at this
                                   sample size — keep by default (more positives
                                   rarely hurt a PU label) but stop arguing
                                   about it.

Design guards:
  * Features are the SCREENED set (label-adjacent + leakage hard/adjacent
    banned, same policy as company_list). This matters more here than anywhere:
    fraud-proximity PageRank is seeded by the very exclusion lists being
    ablated, so leaving it in would feed the dropped source back in through a
    feature and flatten every delta toward zero.
  * All variants share ONE feature set and ONE fold assignment — the only thing
    that changes between rows is the training label, so the delta is
    attributable to the label and nothing else.
  * Pre-cutoff-excluded providers train (they are the positives) but are
    DROPPED from the eval, exactly as prospective_label/network_ab construct it.
  * Overlap guard: a provider positive in a variant's TRAINING label who is
    also a FORWARD positive (possible for doj_case, whose rows are not
    time-boxed) hands that variant the answer key. Overlaps are counted per
    variant, and when any exist the lift is ALSO reported with those providers
    removed from the eval — the number to trust.

  python -m src.model_a.label_ablation \
      --matrix frozen/provider_features_for_model.parquet \
      --manifest frozen/feature_manifest.json \
      --future-label frozen/future_bans_after_2023-12.csv \
      --out frozen/LABEL_ABLATION.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a.feature_partition import partition_features, full_feature_list
from src.model_a.network_ab import (LABEL_ADJACENT_NET, _fit_predict, _to_num,
                                    top_decile_lift, pr_auc, roc_auc)
from src.model_a.source_ablation import _numeric_frame, _oof_delta_ci

RANDOM_STATE = 42
MIN_SOURCE_POSITIVES = 25       # below this a minus-variant cannot resolve


def _source_sets(matrix: pd.DataFrame) -> dict[str, np.ndarray]:
    """Boolean mask per label source from the provenance column."""
    if "exclusion_label_sources" not in matrix.columns:
        raise ValueError("matrix has no exclusion_label_sources column — "
                         "rebuild the export before ablating the label")
    prov = matrix["exclusion_label_sources"].fillna("").astype(str)
    split = prov.str.split(";")
    names = sorted({s for xs in split for s in xs if s})
    return {name: split.apply(lambda xs, n=name: n in xs).to_numpy()
            for name in names}


def label_variants(matrix: pd.DataFrame,
                   only: set[str] | None = None) -> dict[str, dict]:
    """Training-label variants: the shipped union, minus each source, and the
    LEIE-only floor. A provider labeled by two sources stays positive when one
    is dropped — removal means removing EVIDENCE, not providers."""
    y_all = _to_num(matrix["provider_on_exclusion"]).astype(int)
    masks = _source_sets(matrix)
    variants: dict[str, dict] = {
        "all": {"y": y_all,
                "note": "the shipped union label (baseline)"}}
    for src, mask in sorted(masks.items()):
        n_src = int(mask.sum())
        if n_src < MIN_SOURCE_POSITIVES:
            continue
        others = np.zeros(len(matrix), dtype=bool)
        for o, om in masks.items():
            if o != src:
                others |= om
        y = (y_all.astype(bool) & (others | ~mask)).astype(int)
        variants[f"minus_{src}"] = {
            "y": y, "note": f"drop the {n_src:,} {src}-labeled positives "
                            f"(kept where another source also labels them)"}
    if "leie" in masks:
        variants["leie_only"] = {
            "y": masks["leie"].astype(int),
            "note": "the original single-source label — the floor everything "
                    "widened from"}
    if only:
        variants = {k: v for k, v in variants.items()
                    if k == "all" or k in only}
    return variants


def _screened_features(matrix: pd.DataFrame, manifest: dict,
                       y_screen: np.ndarray) -> tuple[list[str], list]:
    """company_list's feature policy: ban label-adjacent + manifest leakage,
    then the mechanical single-column AUC screen (vs the UNION label, once, so
    every variant trains on the identical column set)."""
    from sklearn.metrics import roc_auc_score
    banned = set(LABEL_ADJACENT_NET) \
        | set(manifest.get("leakage_adjacent", []) or []) \
        | set(manifest.get("leakage_hard", []) or [])
    cols = [c for c in full_feature_list(partition_features(manifest))
            if c in matrix.columns and c not in banned]
    leaked = []
    if 0 < int(y_screen.sum()) < len(y_screen):
        for c in list(cols):
            v = pd.to_numeric(matrix[c], errors="coerce").fillna(0.0)
            if v.nunique() <= 1:
                continue
            try:
                auc = roc_auc_score(y_screen, v.to_numpy())
            except ValueError:
                continue
            if max(auc, 1 - auc) > 0.985:
                leaked.append((c, round(max(auc, 1 - auc), 4)))
                cols.remove(c)
    return cols, leaked


def _forward_eval(matrix: pd.DataFrame, future_label: pd.DataFrame):
    """(y_fwd, eval_mask) on the FULL matrix — pre-cutoff-excluded providers
    stay in training but never in the metric."""
    npi = matrix["npi"].astype(str)
    fl = future_label.copy()
    fl["npi"] = fl["npi"].astype(str)
    if "is_prospective_positive" not in fl.columns:
        raise ValueError("future-label file lacks is_prospective_positive")
    pos = set(fl.loc[_to_num(fl["is_prospective_positive"]) == 1, "npi"])
    pre = (set(fl.loc[_to_num(fl["was_excluded_pre_cutoff"]) == 1, "npi"])
           if "was_excluded_pre_cutoff" in fl.columns else set())
    y_fwd = npi.isin(pos).to_numpy().astype(int)
    eval_mask = ~npi.isin(pre).to_numpy()
    if int(y_fwd[eval_mask].sum()) == 0:
        raise ValueError("0 forward positives inside the eval universe — "
                         "wrong future-label file?")
    return y_fwd, eval_mask


def _oof_scores_trained_on(matrix, y_train, cols, groups,
                           n_splits=5, seed=RANDOM_STATE):
    """Out-of-fold scores where the TRAINING label is a variant, not the eval
    label. Folds are grouped and IDENTICAL across variants (same seed/groups),
    so two variants' scores for one provider come from models trained on the
    same rows with different labels — the delta isolates the label."""
    from sklearn.model_selection import GroupKFold, KFold
    X = _numeric_frame(matrix, cols)
    oof = np.full(len(X), np.nan)
    if groups is not None and pd.Series(groups).nunique() >= n_splits:
        it = GroupKFold(n_splits=n_splits).split(X, y_train, groups=groups)
    else:
        it = KFold(n_splits=n_splits, shuffle=True,
                   random_state=seed).split(X)
    for tr, te in it:
        if int(y_train[tr].sum()) == 0:      # a fold with no positives can't fit
            continue
        oof[te] = _fit_predict(X.iloc[tr].to_numpy(), y_train[tr],
                               X.iloc[te].to_numpy(), seed=seed)
    return oof


def run_label_ablation(matrix: pd.DataFrame, manifest: dict,
                       future_label: pd.DataFrame,
                       n_splits: int = 5, n_boot: int = 400,
                       only: set[str] | None = None) -> dict:
    m = matrix.reset_index(drop=True)
    m["npi"] = m["npi"].astype(str)
    y_fwd, eval_mask = _forward_eval(m, future_label)
    variants = label_variants(m, only=only)
    groups = (m["group_id"].to_numpy() if "group_id" in m.columns else None)
    cols, leaked = _screened_features(m, manifest, variants["all"]["y"])

    res: dict = {"n": int(len(m)), "n_features": len(cols),
                 "screened_out": leaked, "n_splits": n_splits,
                 "n_boot": n_boot,
                 "eval_n": int(eval_mask.sum()),
                 "eval_positives": int(y_fwd[eval_mask].sum()),
                 "variants": {}}

    scores: dict[str, np.ndarray] = {}
    for name, v in variants.items():
        y_tr = np.asarray(v["y"])
        p = _oof_scores_trained_on(m, y_tr, cols, groups, n_splits=n_splits)
        scores[name] = p
        ok = eval_mask & ~np.isnan(p)
        ye, pe = y_fwd[ok], p[ok]
        overlap = int((y_tr.astype(bool) & (y_fwd == 1) & eval_mask).sum())
        row = {"note": v["note"], "train_positives": int(y_tr.sum()),
               "overlap_with_forward": overlap,
               "top_decile_lift": top_decile_lift(ye, pe),
               "pr_auc": pr_auc(ye, pe), "roc_auc": roc_auc(ye, pe)}
        if overlap:
            attacked = ok & ~(y_tr.astype(bool) & (y_fwd == 1))
            row["lift_overlap_removed"] = top_decile_lift(
                y_fwd[attacked], p[attacked])
        res["variants"][name] = row

    base = scores["all"]
    for name in res["variants"]:
        if name == "all":
            continue
        p = scores[name]
        ok = eval_mask & ~np.isnan(p) & ~np.isnan(base)
        g_ok = groups[ok] if groups is not None else None
        res["variants"][name]["delta_vs_all"] = _oof_delta_ci(
            y_fwd[ok], p[ok], base[ok], g_ok, n_boot=n_boot)
    return res


def _verdict(name: str, d: dict | None) -> str:
    if not d:
        return "—"
    lift = d.get("lift10", {})
    lo, hi = lift.get("lo"), lift.get("hi")
    if lo is None or lo != lo:
        return "unresolved"
    if name.startswith("minus_"):
        src = name[len("minus_"):]
        if lo > 0:
            return (f"dropping {src} HELPED — it is noise on this eval; "
                    f"candidate to remove")
        if hi < 0:
            return f"{src} is contributing forward signal — keep it"
        return f"no measurable effect from {src} — keep by default"
    if lo > 0:
        return "beats the union label on this eval"
    if hi < 0:
        return "the union label beats this — widening earned its keep"
    return "indistinguishable from the union label"


def to_markdown(res: dict) -> str:
    L = ["# LABEL ABLATION — which label sources earn their keep", ""]
    L.append(f"_Same frozen features, same folds, same forward eval "
             f"({res['eval_positives']:,} forward positives in "
             f"{res['eval_n']:,} eval providers); the ONLY thing that changes "
             f"per row is which sources' positives the model trains on. "
             f"{res['n_features']} screened features, {res['n_splits']} "
             f"grouped folds, {res['n_boot']} cluster-bootstrap draws._")
    L.append("")
    if res.get("screened_out"):
        L.append("Screened out as single-column leaks (AUC > 0.985 vs the "
                 "union label): "
                 + ", ".join(f"`{c}` ({a})" for c, a in res["screened_out"]))
        L.append("")
    L.append("| variant | train pos | fwd overlap | lift | lift Δ vs all "
             "[95% CI] | verdict |")
    L.append("|---|--:|--:|--:|---|---|")
    for name, r in res["variants"].items():
        d = r.get("delta_vs_all", {}).get("lift10") \
            if r.get("delta_vs_all") else None
        dtxt = ("—" if not d or d["delta"] != d["delta"] else
                f"{d['delta']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]")
        lift = r["top_decile_lift"]
        ltxt = "—" if lift != lift else f"{lift:.2f}×"
        if r.get("lift_overlap_removed") is not None:
            lo_r = r["lift_overlap_removed"]
            ltxt += f" ({lo_r:.2f}× overlap-removed)"
        L.append(f"| {name} | {r['train_positives']:,} "
                 f"| {r['overlap_with_forward']} | {ltxt} | {dtxt} "
                 f"| {_verdict(name, r.get('delta_vs_all'))} |")
    L.append("")
    L.append("_Notes: `minus_<src>` keeps a provider positive when another "
             "source also labels them — a source is removed as EVIDENCE, not "
             "as providers. `fwd overlap` counts training positives that are "
             "also forward positives (possible for doj_case, which is not "
             "time-boxed); when nonzero, trust the overlap-removed lift. "
             "Variant rows never gate a release by themselves — fold the "
             "conclusion into the next pre-registration's label choice and "
             "let the standing gates judge that run._")
    L.append("")
    for name, r in res["variants"].items():
        L.append(f"- **{name}**: {r['note']}")
    L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--future-label", required=True)
    ap.add_argument("--out", default="LABEL_ABLATION.md")
    ap.add_argument("--splits", type=int, default=5)
    ap.add_argument("--boot", type=int, default=400)
    ap.add_argument("--variants", default=None,
                    help="comma-separated variant names to run (besides 'all', "
                         "always run), e.g. 'minus_doj_case,minus_opensanctions'"
                         " — default runs every source with enough positives")
    args = ap.parse_args()
    matrix = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    fl = pd.read_csv(args.future_label, dtype=str)
    only = ({v.strip() for v in args.variants.split(",") if v.strip()}
            if args.variants else None)
    res = run_label_ablation(matrix, manifest, fl, n_splits=args.splits,
                             n_boot=args.boot, only=only)
    Path(args.out).write_text(to_markdown(res), encoding="utf-8")
    print(f"[label_ablation] {len(res['variants'])} variants on "
          f"{res['eval_positives']:,} forward positives -> {args.out}")


if __name__ == "__main__":
    main()
