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
        ug = np.unique(g_te)
        rng = np.random.RandomState(RANDOM_STATE + s)
        for _ in range(n_boot):
            samp = rng.choice(ug, size=len(ug), replace=True)
            mask = np.isin(g_te, samp)
            if _to_num(yte[mask]).sum() == 0:
                continue
            deltas.append(roc_auc(yte[mask], pa[mask]) - roc_auc(yte[mask], pb[mask]))
    deltas = [d for d in deltas if d == d]
    if not deltas:
        return {"delta": float("nan"), "lo": float("nan"), "hi": float("nan")}
    return {"delta": float(np.mean(deltas)),
            "lo": float(np.percentile(deltas, 2.5)),
            "hi": float(np.percentile(deltas, 97.5))}


def run_source_ablation(matrix: pd.DataFrame, manifest: dict,
                        label_col: str = "provider_on_exclusion",
                        future_label: pd.DataFrame | None = None,
                        splits: int = 3, seeds=(0, 1, 2)) -> dict:
    """Core vs full vs leave-one-extra-group-out on the given label.

    ``future_label`` (npi + a 1/0 column) overrides the in-matrix label with a
    held-out forward label, the honest evaluation. Returns a dict ready for
    to_markdown."""
    m = matrix.copy()
    m["npi"] = m["npi"].astype(str)
    if future_label is not None and len(future_label):
        fl = future_label.copy()
        fl["npi"] = fl["npi"].astype(str)
        ycol = [c for c in fl.columns if c != "npi"][0]
        m = m.merge(fl[["npi", ycol]].rename(columns={ycol: "_y"}), on="npi", how="left")
        y = _to_num(m["_y"])
        label_name = f"forward:{ycol}"
    else:
        y = _to_num(m[label_col])
        label_name = label_col
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
    ap.add_argument("--out", default="SOURCE_ABLATION.md")
    args = ap.parse_args()
    matrix = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text())
    fl = pd.read_csv(args.future_label, dtype=str) if args.future_label else None
    res = run_source_ablation(matrix, manifest, label_col=args.label_col,
                              future_label=fl, splits=args.splits)
    Path(args.out).write_text(to_markdown(res), encoding="utf-8")
    print(f"[source_ablation] full-vs-core ROC delta "
          f"{res['full_vs_core']['delta']:+.4f} "
          f"[{res['full_vs_core']['lo']:+.4f}, {res['full_vs_core']['hi']:+.4f}] "
          f"→ {args.out}")


if __name__ == "__main__":
    main()
