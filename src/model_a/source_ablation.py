"""
source_ablation.py — does the EXTRA data beat the five core files? Measured.

Turns the "keep it simple vs use the broad data" argument into a run. Using the
same grouped-CV, cluster-bootstrap machinery as the network A/B that Travis
already trusts, it fits three kinds of model against a forward label and reports
deltas with confidence intervals:

  * CORE    — features derivable from the five core files only;
  * FULL    — core plus every extra source;
  * per-group MARGINAL — full minus one extra group, so the drop attributes the
    value to a specific source (open_payments, facility_quality, hcris, ...).

Decision rules are printed with the result: if FULL does not beat CORE by a CI
clear of zero, the extra data is not earning its keep on this label; and any
extra group whose leave-one-out drop is ~zero is a prune candidate. The honest
caveat is printed too: an exclusion-type forward label is blind to billing- and
quality-scheme fraud, so a null here for open_payments / facility_quality is
expected and the verdict for those groups defers to the DOJ label.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a.feature_partition import partition_features, full_feature_list
from src.model_a.network_ab import (_fit_predict, _grouped_split, top_decile_lift,
                                    pr_auc, roc_auc, _to_num)

RANDOM_STATE = 42
# groups that need the DOJ label to judge (exclusion-blind schemes)
_DOJ_DEFERRED = {"open_payments", "opioid", "facility_quality", "hcris", "saturation"}


def _numeric_frame(matrix: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    keep = [c for c in cols if c in matrix.columns]
    X = matrix[keep].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    return X


def _eval_set(matrix, y, cols, groups, splits, seeds):
    """Mean (over splits x seeds) top-decile lift / PR-AUC / ROC for one column set."""
    X = _numeric_frame(matrix, cols)
    lifts, prs, rocs = [], [], []
    for s in seeds:
        for k in range(splits):
            tr, te = _grouped_split(len(X), groups, seed=RANDOM_STATE + s * 17 + k)
            p = _fit_predict(X.iloc[tr].to_numpy(), y[tr], X.iloc[te].to_numpy(),
                             seed=RANDOM_STATE + s)
            lifts.append(top_decile_lift(y[te], p))
            prs.append(pr_auc(y[te], p))
            rocs.append(roc_auc(y[te], p))
    def _m(v):
        v = [x for x in v if x == x]
        return float(np.mean(v)) if v else float("nan")
    return {"top_decile_lift": _m(lifts), "pr_auc": _m(prs), "roc_auc": _m(rocs)}


def _group_index(g_te):
    """Group array → (order, starts, counts) for O(1) cluster resampling.

    ``np.isin(g_te, sample)`` on the raw (text) group ids was the bottleneck: a
    200-iteration loop each matching ~10^5 strings against ~10^5 strings, single
    threaded, turned a minutes-long CI into an hours-long one. Factorizing to
    integer codes once and slicing a presorted index makes every draw pure
    integer arithmetic. It also FIXES the resampling: set-membership silently
    dropped duplicate draws (a group picked twice contributed once), which is
    not a cluster bootstrap and understates the variance. Here a group drawn
    twice contributes its rows twice — matching ``network_ab._ab_once``.
    """
    codes, uniq = pd.factorize(pd.Series(g_te))
    order = np.argsort(codes, kind="stable")
    sorted_codes = codes[order]
    ids = np.arange(len(uniq))
    starts = np.searchsorted(sorted_codes, ids, side="left")
    counts = np.searchsorted(sorted_codes, ids, side="right") - starts
    return order, starts, counts


def _draw_cluster(order, starts, counts, pick):
    """Row indices for a bootstrap draw of groups ``pick`` (vectorized).

    Expands the ragged per-group slices without a Python loop: repeat each
    picked group's start offset by its size, then add a within-group counter.
    """
    sel = counts[pick]
    total = int(sel.sum())
    if total == 0:
        return np.empty(0, dtype=int)
    base = np.repeat(starts[pick], sel)
    within = np.arange(total) - np.repeat(np.cumsum(sel) - sel, sel)
    return order[base + within]


def _delta_ci(matrix, y, cols_a, cols_b, groups, splits, seeds, n_boot=200):
    """ROC delta (A minus B) with a cluster-bootstrap CI over test groups."""
    Xa, Xb = _numeric_frame(matrix, cols_a), _numeric_frame(matrix, cols_b)
    deltas = []
    for s in seeds:
        tr, te = _grouped_split(len(Xa), groups, seed=RANDOM_STATE + s)
        pa = _fit_predict(Xa.iloc[tr].to_numpy(), y[tr], Xa.iloc[te].to_numpy(), seed=RANDOM_STATE + s)
        pb = _fit_predict(Xb.iloc[tr].to_numpy(), y[tr], Xb.iloc[te].to_numpy(), seed=RANDOM_STATE + s)
        yte = y[te]
        g_te = (np.asarray(groups)[te] if groups is not None
                else np.arange(len(te)))
        order, starts, counts = _group_index(g_te)
        n_groups = len(counts)
        rng = np.random.RandomState(RANDOM_STATE + s)
        for _ in range(n_boot):
            pick = rng.randint(0, n_groups, size=n_groups)
            idx = _draw_cluster(order, starts, counts, pick)
            if len(idx) == 0 or _to_num(yte[idx]).sum() == 0:
                continue
            deltas.append(roc_auc(yte[idx], pa[idx]) - roc_auc(yte[idx], pb[idx]))
    deltas = [d for d in deltas if d == d]
    if not deltas:
        return {"delta": float("nan"), "lo": float("nan"), "hi": float("nan")}
    return {"delta": float(np.mean(deltas)),
            "lo": float(np.percentile(deltas, 2.5)),
            "hi": float(np.percentile(deltas, 97.5))}


def resolve_label(matrix: pd.DataFrame, label_col: str = "provider_on_exclusion",
                  future_label: pd.DataFrame | None = None):
    """(frame, y, label_name) for either the in-matrix label or a forward file.

    Shared by the holdout and out-of-fold harnesses so they can never disagree
    about what the label is. RAISES on a zero-positive label rather than training
    on all-negatives and printing a confident zero.
    """
    m = matrix.copy()
    m["npi"] = m["npi"].astype(str)
    if future_label is not None and len(future_label):
        fl = future_label.copy()
        fl["npi"] = fl["npi"].astype(str)
        # The prospective_label CSV is (npi, first_excl_date, ...,
        # is_prospective_positive, was_excluded_pre_cutoff, ...). Read the LABEL
        # correctly (the old "first non-npi column" grabbed first_excl_date, a
        # date string, and every provider collapsed to 0). Positives are
        # is_prospective_positive==1; providers already excluded at/before the
        # cutoff are DROPPED from the eval, not scored as negatives — the same
        # construction the network A/B uses.
        if "is_prospective_positive" in fl.columns:
            pos = set(fl.loc[_to_num(fl["is_prospective_positive"]) == 1, "npi"])
            drop = (set(fl.loc[_to_num(fl["was_excluded_pre_cutoff"]) == 1, "npi"])
                    if "was_excluded_pre_cutoff" in fl.columns else set())
            if drop:
                m = m[~m["npi"].isin(drop)].reset_index(drop=True)
            y = m["npi"].isin(pos).astype(int).to_numpy()
            label_name = "forward:is_prospective_positive"
        else:                                    # a plain (npi, 0/1) label file
            ycol = [c for c in fl.columns if c != "npi"][0]
            m = m.merge(fl[["npi", ycol]].rename(columns={ycol: "_y"}),
                        on="npi", how="left")
            y = _to_num(m["_y"])
            label_name = f"forward:{ycol}"
    else:
        y = _to_num(m[label_col])
        label_name = label_col
    if int(pd.Series(y).sum()) == 0:
        raise ValueError("forward label resolved to 0 positives — check the "
                         "future-label file columns (expected is_prospective_positive)")
    return m, np.asarray(y), label_name


def run_source_ablation(matrix: pd.DataFrame, manifest: dict,
                        label_col: str = "provider_on_exclusion",
                        future_label: pd.DataFrame | None = None,
                        splits: int = 3, seeds=(0, 1, 2)) -> dict:
    """Core vs full vs leave-one-extra-group-out on the given label.

    ``future_label`` (npi + a 1/0 column) overrides the in-matrix label with a
    held-out forward label, the honest evaluation. Returns a dict ready for
    to_markdown."""
    m, y, label_name = resolve_label(matrix, label_col, future_label)
    groups = (m["group_id"].to_numpy() if "group_id" in m.columns else None)

    part = partition_features(manifest)
    core = part["core"]
    full = full_feature_list(part)

    core_ev = _eval_set(m, y, core, groups, splits, seeds)
    full_ev = _eval_set(m, y, full, groups, splits, seeds)
    full_vs_core = _delta_ci(m, y, full, core, groups, splits, seeds)

    per_group = {}
    for grp, cols in part["extra"].items():
        without = [c for c in full if c not in set(cols)]
        per_group[grp] = {"n_features": len(cols),
                          "marginal": _delta_ci(m, y, full, without, groups, splits, seeds),
                          "doj_deferred": grp in _DOJ_DEFERRED}

    return {"label": label_name, "n": int(len(m)), "positives": int(y.sum()),
            "n_core": part["n_core"], "n_extra": part["n_extra"],
            "core": core_ev, "full": full_ev, "full_vs_core": full_vs_core,
            "per_group": per_group}


def unique_catches(y, p_full, p_core, frac: float = 0.10) -> dict:
    """Who does the FULL model catch in the review zone that CORE misses?

    Average deltas can wash out real complementarity: two models with identical
    top-decile lift can be catching DIFFERENT offenders, and for an origination
    engine a new catch is worth more than a shared one. This partitions the
    forward positives by which model's top decile they land in:

      both / full_only / core_only / neither

    ``full_only`` is the honest, per-provider version of "the broad data finds
    signal the core files do not": future-banned providers the full model puts
    in front of a reviewer while the core model leaves them buried. The count is
    symmetric — ``core_only`` is reported with equal prominence, so the analysis
    cannot be read as one-sided advocacy.
    """
    y = np.asarray(y)
    k = max(1, int(len(y) * frac))
    top_f = set(np.argsort(p_full)[::-1][:k])
    top_c = set(np.argsort(p_core)[::-1][:k])
    pos = np.flatnonzero(y == 1)
    both = sum(1 for i in pos if i in top_f and i in top_c)
    fo = sum(1 for i in pos if i in top_f and i not in top_c)
    co = sum(1 for i in pos if i in top_c and i not in top_f)
    neither = len(pos) - both - fo - co
    return {"frac": frac, "positives": int(len(pos)), "both": int(both),
            "full_only": int(fo), "core_only": int(co), "neither": int(neither),
            "jaccard_topk": float(len(top_f & top_c) / max(len(top_f | top_c), 1))}


# ---- out-of-fold harness (the powered version) -----------------------------
_OOF_METRICS = (("roc_auc", roc_auc), ("pr_auc", pr_auc),
                ("lift10", top_decile_lift))


def oof_predict(matrix, y, cols, groups, n_splits=5, seed=RANDOM_STATE):
    """Out-of-fold scores: every row predicted by a model that never saw it.

    The single 70/30 holdout wastes 70% of an already tiny positive class — with
    ~1k forward bans in 600k providers only ~300 positives reach the metric, and
    a ROC delta of a few thousandths cannot be resolved against that. Pooling
    out-of-fold predictions puts EVERY positive into the metric instead, which is
    what buys back the statistical power. Folds are grouped, so a provider's
    matched cluster never straddles the train/test boundary.
    """
    from sklearn.model_selection import GroupKFold, KFold
    X = _numeric_frame(matrix, cols)
    oof = np.full(len(X), np.nan)
    if groups is not None and pd.Series(groups).nunique() >= n_splits:
        it = GroupKFold(n_splits=n_splits).split(X, y, groups=groups)
    else:
        it = KFold(n_splits=n_splits, shuffle=True, random_state=seed).split(X)
    for tr, te in it:
        oof[te] = _fit_predict(X.iloc[tr].to_numpy(), y[tr],
                               X.iloc[te].to_numpy(), seed=seed)
    return oof


def _oof_delta_ci(y, pa, pb, groups, n_boot=400, seed=RANDOM_STATE):
    """Cluster-bootstrap CI on the A-minus-B delta for ALL THREE metrics.

    ROC alone is the wrong lens at a ~0.2% base rate: it averages over
    thresholds nobody will ever review. Top-decile lift is what the lead list
    actually experiences, so it gets an interval too.
    """
    g = np.asarray(groups) if groups is not None else np.arange(len(y))
    order, starts, counts = _group_index(g)
    n_groups = len(counts)
    rng = np.random.RandomState(seed)
    acc = {k: [] for k, _ in _OOF_METRICS}
    for _ in range(n_boot):
        pick = rng.randint(0, n_groups, size=n_groups)
        idx = _draw_cluster(order, starts, counts, pick)
        yb = y[idx]
        if len(idx) == 0 or yb.sum() == 0 or yb.sum() == len(yb):
            continue
        for k, f in _OOF_METRICS:
            d = f(yb, pa[idx]) - f(yb, pb[idx])
            if d == d:
                acc[k].append(d)
    out = {}
    for k, v in acc.items():
        if v:
            out[k] = {"delta": float(np.mean(v)),
                      "lo": float(np.percentile(v, 2.5)),
                      "hi": float(np.percentile(v, 97.5))}
        else:
            out[k] = {"delta": float("nan"), "lo": float("nan"), "hi": float("nan")}
    return out


def run_oof_ablation(matrix: pd.DataFrame, manifest: dict,
                     label_col: str = "provider_on_exclusion",
                     future_label: pd.DataFrame | None = None,
                     n_splits: int = 5, n_boot: int = 400,
                     per_group: bool = False) -> dict:
    """Core vs full on POOLED out-of-fold predictions — the powered ablation.

    Same question as ``run_source_ablation``, answered with every positive in the
    metric instead of the ~30% that land in one holdout, and with intervals on
    lift and PR-AUC as well as ROC.
    """
    m, y, label_name = resolve_label(matrix, label_col, future_label)
    groups = (m["group_id"].to_numpy() if "group_id" in m.columns else None)

    part = partition_features(manifest)
    core, full = part["core"], full_feature_list(part)

    p_core = oof_predict(m, y, core, groups, n_splits=n_splits)
    p_full = oof_predict(m, y, full, groups, n_splits=n_splits)
    scored = ~(np.isnan(p_core) | np.isnan(p_full))
    y_s, pc, pf = y[scored], p_core[scored], p_full[scored]
    g_s = groups[scored] if groups is not None else None

    res = {"label": label_name, "mode": "out-of-fold (pooled)",
           "n": int(len(m)), "positives": int(y.sum()),
           "n_scored": int(scored.sum()), "positives_scored": int(y_s.sum()),
           "n_splits": n_splits, "n_boot": n_boot,
           "n_core": part["n_core"], "n_extra": part["n_extra"],
           "core": {k: f(y_s, pc) for k, f in _OOF_METRICS},
           "full": {k: f(y_s, pf) for k, f in _OOF_METRICS},
           "full_vs_core": _oof_delta_ci(y_s, pf, pc, g_s, n_boot=n_boot),
           "complementarity": unique_catches(y_s, pf, pc)}

    if per_group:
        pg = {}
        for grp, cols in part["extra"].items():
            without = [c for c in full if c not in set(cols)]
            p_wo = oof_predict(m, y, without, groups, n_splits=n_splits)
            ok = scored & ~np.isnan(p_wo)
            pg[grp] = {
                "n_features": len(cols),
                "marginal": _oof_delta_ci(y[ok], p_full[ok], p_wo[ok],
                                          groups[ok] if groups is not None else None,
                                          n_boot=n_boot),
                "doj_deferred": grp in _DOJ_DEFERRED}
        res["per_group"] = pg
    return res


def oof_to_markdown(res: dict) -> str:
    def fmt(d):
        return (f"{d['delta']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}]")

    L = ["# SOURCE ABLATION (out-of-fold) — do the extra files beat the five core files?",
         "",
         f"- label: `{res['label']}`  |  mode: **{res['mode']}**, "
         f"{res['n_splits']} grouped folds, {res['n_boot']} bootstrap draws",
         f"- n={res['n']:,}  positives={res['positives']:,}  "
         f"(scored out-of-fold: {res['n_scored']:,} / {res['positives_scored']:,} positives)",
         f"- core features: {res['n_core']}  |  extra features: {res['n_extra']}",
         "",
         "| metric | core only | full | delta (full-core) [95% CI] |",
         "|---|--:|--:|---|"]
    for k, nm in (("roc_auc", "ROC-AUC"), ("pr_auc", "PR-AUC"),
                  ("lift10", "top-decile lift")):
        L.append(f"| {nm} | {res['core'][k]:.4f} | {res['full'][k]:.4f} | "
                 f"{fmt(res['full_vs_core'][k])} |")
    L.append("")
    lift = res["full_vs_core"]["lift10"]
    roc = res["full_vs_core"]["roc_auc"]
    verdicts = []
    for k, nm in (("roc_auc", "ROC-AUC"), ("lift10", "top-decile lift")):
        d = res["full_vs_core"][k]
        if d["lo"] > 0:
            verdicts.append(f"**{nm}: the extra data WINS** (CI clear of zero).")
        elif d["hi"] < 0:
            verdicts.append(f"**{nm}: the extra data HURTS** (CI below zero).")
        else:
            verdicts.append(f"**{nm}: no detectable difference** (CI spans zero: "
                            f"{fmt(d)}).")
    L += verdicts
    L.append("")
    L.append("_Read top-decile lift first. At this base rate ROC-AUC averages over "
             "thresholds nobody reviews, while lift is what the delivered lead list "
             "actually experiences. A CI spanning zero here means UNDECIDED, not "
             "'redundant' — check the interval width against the effect size you "
             "would care about before concluding anything._")
    L.append("")
    L.append("_An exclusion-type forward label is blind to billing- and quality-"
             "scheme fraud, so a null for the DOJ-deferred groups (kickback, opioid, "
             "facility quality, cost report, saturation) is expected by construction "
             "and their verdict waits for the DOJ case label._")
    comp = res.get("complementarity")
    if comp:
        L += ["", "## Complementarity — who catches whom (top decile)",
              "",
              f"Of {comp['positives']:,} forward positives:",
              "",
              "| caught by | count |",
              "|---|--:|",
              f"| both models | {comp['both']:,} |",
              f"| **full only** (broad data surfaces, core buries) | {comp['full_only']:,} |",
              f"| **core only** (core surfaces, broad data buries) | {comp['core_only']:,} |",
              f"| neither | {comp['neither']:,} |",
              "",
              f"Top-decile overlap (Jaccard): {comp['jaccard_topk']:.2f}. "
              "A `full_only` count meaningfully above `core_only` is per-provider "
              "evidence of NEW signal in the extra sources, even when the average "
              "metric deltas are flat: the models are catching different offenders. "
              "Near-symmetric counts mean the extra data reshuffles rather than "
              "adds — read that honestly._"]
    if res.get("per_group"):
        L += ["", "## Which extra source pulls weight (leave-one-group-out, out-of-fold)",
              "", "| extra group | features | marginal lift delta [95% CI] | note |",
              "|---|--:|---|---|"]
        for grp, g in sorted(res["per_group"].items(),
                             key=lambda kv: -kv[1]["marginal"]["lift10"]["delta"]):
            note = ("DOJ-label deferred (exclusion-blind)" if g["doj_deferred"]
                    else "judgeable on this label")
            L.append(f"| {grp} | {g['n_features']} | "
                     f"{fmt(g['marginal']['lift10'])} | {note} |")
    return "\n".join(L)


def to_markdown(res: dict) -> str:
    L = ["# SOURCE ABLATION — do the extra files beat the five core files?", ""]
    L.append(f"- label: `{res['label']}`  |  n={res['n']:,}  positives={res['positives']:,}")
    L.append(f"- core features: {res['n_core']}  |  extra features: {res['n_extra']}")
    L.append("")
    c, f = res["core"], res["full"]
    L.append("| model | ROC-AUC | PR-AUC | top-decile lift |")
    L.append("|---|--:|--:|--:|")
    L.append(f"| core only | {c['roc_auc']:.4f} | {c['pr_auc']:.4f} | {c['top_decile_lift']:.3f} |")
    L.append(f"| full | {f['roc_auc']:.4f} | {f['pr_auc']:.4f} | {f['top_decile_lift']:.3f} |")
    d = res["full_vs_core"]
    L.append("")
    L.append(f"**Full minus core, ROC-AUC delta: {d['delta']:+.4f} "
             f"[{d['lo']:+.4f}, {d['hi']:+.4f}].** "
             + ("The extra data beats core, CI clear of zero."
                if d["lo"] > 0 else
                "CI includes zero on this label: the extra data does not beat "
                "core here."))
    L.append("")
    L.append("## Which extra source pulls weight (leave-one-group-out)")
    L.append("")
    L.append("| extra group | features | marginal ROC delta [95% CI] | note |")
    L.append("|---|--:|---|---|")
    for grp, g in sorted(res["per_group"].items(),
                         key=lambda kv: -kv[1]["marginal"]["delta"]):
        mg = g["marginal"]
        note = ("DOJ-label deferred (exclusion-blind)" if g["doj_deferred"]
                else "judgeable on this label")
        L.append(f"| {grp} | {g['n_features']} | "
                 f"{mg['delta']:+.4f} [{mg['lo']:+.4f}, {mg['hi']:+.4f}] | {note} |")
    L.append("")
    L.append("_Decision rules. FULL beats CORE only if its delta CI clears zero. "
             "An extra group whose marginal CI includes zero is a prune candidate "
             "ON THIS LABEL. But an exclusion-type forward label is blind to "
             "billing- and quality-scheme fraud, so a null for the DOJ-deferred "
             "groups (kickback, opioid, facility quality, cost report, saturation) "
             "is expected and their verdict waits for the DOJ label. Groups NOT "
             "deferred that still null out are the real prune candidates._")
    return "\n".join(L)


def main() -> None:
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--future-label", default=None,
                    help="csv: npi + a 1/0 forward-label column (the honest eval)")
    ap.add_argument("--label-col", default="provider_on_exclusion")
    ap.add_argument("--splits", type=int, default=3)
    ap.add_argument("--oof", action="store_true",
                    help="POWERED mode: pooled out-of-fold predictions put every "
                         "positive into the metric (a single holdout keeps ~30%%) "
                         "and report CIs on lift + PR-AUC as well as ROC. Use this "
                         "when the label is rare and the holdout CI spans zero.")
    ap.add_argument("--per-group", action="store_true",
                    help="with --oof: also run leave-one-extra-group-out")
    ap.add_argument("--out", default="SOURCE_ABLATION.md")
    args = ap.parse_args()
    matrix = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text())
    fl = pd.read_csv(args.future_label, dtype=str) if args.future_label else None
    if args.oof:
        res = run_oof_ablation(matrix, manifest, label_col=args.label_col,
                               future_label=fl, n_splits=max(args.splits, 5),
                               per_group=args.per_group)
        Path(args.out).write_text(oof_to_markdown(res), encoding="utf-8")
        d = res["full_vs_core"]
        print(f"[source_ablation --oof] positives in metric: {res['positives_scored']:,}")
        print(f"  ROC delta  {d['roc_auc']['delta']:+.4f} "
              f"[{d['roc_auc']['lo']:+.4f}, {d['roc_auc']['hi']:+.4f}]")
        print(f"  lift delta {d['lift10']['delta']:+.4f} "
              f"[{d['lift10']['lo']:+.4f}, {d['lift10']['hi']:+.4f}]")
        print(f"  → {args.out}")
        return
    res = run_source_ablation(matrix, manifest, label_col=args.label_col,
                              future_label=fl, splits=args.splits)
    Path(args.out).write_text(to_markdown(res), encoding="utf-8")
    print(f"[source_ablation] full-vs-core ROC delta "
          f"{res['full_vs_core']['delta']:+.4f} "
          f"[{res['full_vs_core']['lo']:+.4f}, {res['full_vs_core']['hi']:+.4f}] "
          f"→ {args.out}")


if __name__ == "__main__":
    main()
