"""
company_list.py — the org-grain list for the cross-model overlap test.

Travis's product is a screened COMPANY list (his score cutoff, a billed-dollar
floor). To compare lists as sets, ours has to exist at the same grain. This
rolls the provider matrix up to organizations:

  1. score every provider OUT-OF-FOLD with the full feature set against the
     widened exclusion label (the same model family the ablation validated —
     each provider scored by a model that never trained on it);
  2. aggregate to the org: max provider score, dollar-weighted mean score,
     provider count, total billed;
  3. percentile-normalize to [0, 1] so any cutoff ("keep score > 0.9") is
     meaningful without knowing our score's raw scale;
  4. name the top scheme drivers per org — no bare composite leaves this
     module (rule 5).

Output: one CSV, one row per org, sorted by score. Apply any cutoffs
downstream. A modelling artifact for cross-model comparison — leads context
for review, never an accusation.

  python -m src.model_a.company_list --matrix provider_features_for_model.parquet \
      --manifest feature_manifest.json --out company_list.csv
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a.feature_partition import partition_features, full_feature_list
from src.model_a.network_ab import _fit_predict, _to_num

RANDOM_STATE = 42


# --- label policy for the PRODUCTION scorer ---------------------------------
# The in-matrix PU label (provider_on_exclusion) is ~75% administrative and
# weak events (Sept 2026 tier audit), so a scorer trained on it ranks
# "likely to be revoked", not "fraud-shaped". A policy of hard / hard+weak
# rebuilds the training label from the exclusion nodes' statutes via
# fca_relevance without a matrix rebuild. Set once per process (CLI flag);
# every scorer call site honours it and says so in its log line.
_LABEL_POLICY: dict = {"tier": "all", "exclusion_nodes": None, "cutoff": None}


def set_label_policy(tier: str = "all", exclusion_nodes: str | Path | None = None,
                     cutoff: str | None = None) -> None:
    tier = (tier or "all").lower()
    if tier not in ("all", "hard", "hard+weak"):
        raise ValueError(f"unknown label tier {tier!r}")
    _LABEL_POLICY.update(tier=tier, exclusion_nodes=exclusion_nodes, cutoff=cutoff)


def label_policy() -> dict:
    return dict(_LABEL_POLICY)


# --- training policy for the PRODUCTION scorer (v9, 2026-10-10) ---------------
# The matrix's manifest is the contract (training_policy.n_seeds /
# broadcast_rows / group_policy, written by the export); a CLI may override the
# first two for an A/B on the SAME matrix (set_training_policy). None = manifest.
#   n_seeds         LightGBM fits averaged per fold (seed bagging: unties the head,
#                   raises top-50 stability)
#   broadcast_rows  "weight": org_billing / affiliated rows train at label_weight
#                   (the per-case budget); "mask": rows whose ONLY positive
#                   evidence is a broadcast case tier are held out of training
#                   folds and scored out-of-fold (the bag-label reading: the
#                   case says "someone at this org", not "this member")
BROADCAST_BASES = ("org_billing", "affiliated_individual")
_TRAINING_POLICY: dict = {"n_seeds": None, "broadcast": None}


def set_training_policy(n_seeds: int | None = None, broadcast: str | None = None) -> None:
    if broadcast is not None and broadcast not in ("weight", "mask"):
        raise ValueError(f"unknown broadcast policy {broadcast!r} (weight | mask)")
    if n_seeds is not None and int(n_seeds) < 1:
        raise ValueError("n_seeds must be >= 1")
    _TRAINING_POLICY.update(n_seeds=(int(n_seeds) if n_seeds is not None else None),
                            broadcast=broadcast)


def training_policy(manifest: dict | None) -> dict:
    """The effective policy: CLI override, else the manifest, else v8 defaults."""
    tp = (manifest or {}).get("training_policy") or {}
    n_seeds = _TRAINING_POLICY["n_seeds"] or int(tp.get("n_seeds") or 1)
    broadcast = _TRAINING_POLICY["broadcast"] or str(tp.get("broadcast_rows") or "weight")
    return {"n_seeds": max(1, n_seeds), "broadcast": broadcast,
            "group_policy": str(tp.get("group_policy") or "org")}


def broadcast_only_mask(matrix: pd.DataFrame) -> np.ndarray:
    """Rows whose only positive evidence is a broadcast case tier (label_basis
    org_billing / affiliated_individual and no label source beyond doj_case)."""
    if "label_basis" not in matrix.columns:
        return np.zeros(len(matrix), dtype=bool)
    bc = matrix["label_basis"].fillna("").astype(str).isin(BROADCAST_BASES)
    src = (matrix["exclusion_label_sources"] if "exclusion_label_sources" in matrix.columns
           else pd.Series("", index=matrix.index)).fillna("").astype(str)
    only_doj = src.str.split(";").apply(lambda xs: {x for x in xs if x} <= {"doj_case"})
    return (bc & only_doj).to_numpy(dtype=bool)


def case_cluster_groups(org_ids: pd.Series, case_ids: pd.Series | None) -> tuple[pd.Series, int]:
    """The fold key (v9): org_node_id, except that every organization sharing a
    DOJ case with another organization is merged into one cluster
    ('case_cluster:<smallest org id>'). A chain settling under twenty enrolled
    names used to have fifteen in training and five in the test fold, so the
    scorer 'found' organizations it had already seen. Returns (group_id,
    n_clusters)."""
    org = org_ids.fillna("").astype(str)
    if case_ids is None:
        return org, 0
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for o, cs in zip(org, case_ids.fillna("").astype(str)):
        if not o or not cs.strip():
            continue
        for c in cs.split(";"):
            c = c.strip()
            if c:
                union(o, "case:" + c)
    orgs_by_root: dict[str, set] = {}
    for o in set(org[org != ""]):
        if o in parent:
            orgs_by_root.setdefault(find(o), set()).add(o)
    label = {r: "case_cluster:" + min(s) for r, s in orgs_by_root.items() if len(s) > 1}
    out = [label.get(find(o), o) if (o and o in parent) else o for o in org]
    return pd.Series(out, index=org_ids.index), len(label)


def default_exclusion_nodes(run_dir: str | Path, data_root: str | Path | None = None) -> Path:
    """Where this run's exclusion nodes live: the as-of graph for a frozen run
    (manifest graph_substrate.asof), the current graph otherwise."""
    import os
    root = Path(data_root or os.environ.get("MEDICAID_DATA_ROOT",
                                            str(Path.home() / "Desktop/data")))
    asof = None
    man = Path(run_dir) / "feature_manifest.json"
    if man.exists():
        try:
            mj = json.loads(man.read_text(encoding="utf-8"))
            asof = mj.get("asof_cutoff") or (mj.get("graph_substrate") or {}).get("asof")
        except Exception:
            asof = None
    if asof:
        p = root / f"graph_asof_{asof}" / "nodes" / "exclusion_nodes.parquet"
        if p.exists():
            return p
    return root / "graph" / "nodes" / "exclusion_nodes.parquet"


def tier_label(matrix: pd.DataFrame, exclusion_nodes: str | Path | pd.DataFrame,
               keep: str = "hard", cutoff: str | None = None) -> np.ndarray:
    """0/1 per matrix row: the NPI has an exclusion record in a kept FCA tier
    (dated on/before ``cutoff`` when given; undated records count for the
    current-day scorer, never for a frozen one)."""
    from .fca_relevance import classify_series, tier_mask
    e = exclusion_nodes if isinstance(exclusion_nodes, pd.DataFrame) \
        else pd.read_parquet(exclusion_nodes)
    e = e.copy()
    e["npi"] = e.get("npi").astype("string").str.strip()
    e = e[e["npi"].notna() & (e["npi"] != "") & (e["npi"].str.lower() != "none")]
    tiers = classify_series(e.get("excl_type", pd.Series("", index=e.index)).fillna("").astype(str))
    e = e[tier_mask(tiers["fca_tier"], keep).values]
    if cutoff:
        dt = pd.to_datetime(e.get("excl_date"), errors="coerce")
        # the freeze instant is the cutoff month's FIRST day everywhere else (the as-of
        # graph keeps excl_date <= it; the forward label is strictly after it); a
        # month-END here made late-month bans both a training positive and a forward
        # positive (the one-month boundary leak)
        e = e[dt.notna() & (dt <= pd.Timestamp(f"{cutoff[:7]}-01"))]
    pos = set(e["npi"].astype(str))
    y = matrix["npi"].astype(str).isin(pos).to_numpy()
    # DOJ case labels (settlements: the only label institutions generate) live
    # in the matrix's label provenance, not in the exclusion nodes; they are
    # hard by construction (fca_relevance tiers 'doj' hard). The frozen export
    # already time-boxes them to the run's freeze, so no date filter here.
    if "exclusion_label_sources" in matrix.columns:
        prov = matrix["exclusion_label_sources"].fillna("").astype(str)
        doj = prov.str.split(";").apply(lambda xs: "doj_case" in xs).to_numpy()
        y = y | doj
    return y.astype(int)


def _training_label(matrix: pd.DataFrame, label_col: str) -> np.ndarray:
    """The scorer's y under the current label policy (see set_label_policy)."""
    pol = _LABEL_POLICY
    if pol["tier"] == "all" or not pol.get("exclusion_nodes"):
        return _to_num(matrix[label_col])
    y = tier_label(matrix, pol["exclusion_nodes"], keep=pol["tier"],
                   cutoff=pol.get("cutoff"))
    base = int(_to_num(matrix[label_col]).sum()) if label_col in matrix.columns else 0
    n_doj = 0
    if "exclusion_label_sources" in matrix.columns:
        n_doj = int(matrix["exclusion_label_sources"].fillna("").astype(str)
                    .str.split(";").apply(lambda xs: "doj_case" in xs).sum())
    print(f"[company_list] training label policy '{pol['tier']}': "
          f"{int(y.sum()):,} positives ({n_doj:,} of them DOJ-case; in-matrix "
          f"{label_col}: {base:,})")
    if y.sum() < 50:
        raise ValueError(f"label policy '{pol['tier']}' leaves {int(y.sum())} "
                         f"positives in this matrix — too few to train; use "
                         f"'hard+weak' or check --exclusion-nodes")
    return y


def training_weights(matrix: pd.DataFrame) -> np.ndarray | None:
    """The scorer's sample weights: the matrix's ``label_weight`` column (the
    per-case fan-out budget from case_labels), NaN read as 1.0 and clipped to
    [0, 1]; None when the column is absent so older matrices train unweighted."""
    if "label_weight" not in matrix.columns:
        return None
    w = (pd.to_numeric(matrix["label_weight"], errors="coerce").fillna(1.0)
         .clip(0.0, 1.0).to_numpy(dtype=float))
    n_dil = int((w < 1.0).sum())
    if n_dil:
        print(f"[company_list] sample weights: {n_dil:,} broadcast case positives "
              f"down-weighted by the per-case budget (min weight {w.min():.3f})")
    return w


def fold_assignments(y, groups, n_splits: int = 5):
    """The scorer's folds: StratifiedGroupKFold (2026-10-09; balanced on positives so
    one fold's model does not run hot: the v6 / v7 diagnostics found fold 1 supplying
    33% of the top 1% against 20% expected) when the groups and positives allow,
    GroupKFold otherwise, plain shuffled KFold without groups. Deterministic."""
    from sklearn.model_selection import GroupKFold, KFold, StratifiedGroupKFold
    y = np.asarray(y)
    if groups is not None and pd.Series(groups).nunique() > n_splits:
        if 0 < int(y.sum()) and int(y.sum()) >= n_splits and int((y == 0).sum()) >= n_splits:
            return list(StratifiedGroupKFold(n_splits=n_splits, shuffle=False).split(np.zeros(len(y)), y, groups))
        return list(GroupKFold(n_splits=n_splits).split(np.zeros(len(y)), y, groups))
    return list(KFold(n_splits=n_splits, shuffle=True, random_state=RANDOM_STATE).split(np.zeros(len(y))))


def calibrate_oof(matrix: pd.DataFrame, scores: np.ndarray, label_col: str | None = None,
                  n_splits: int = 5) -> np.ndarray:
    """Cross-fitted isotonic calibration of out-of-fold scores against the training
    label: the calibrator for fold k is fitted on the OTHER folds' (score, y) pairs,
    so every row's probability comes from a map that never saw it. Returns
    P(training positive | score) per row, the sort key the organization rollup uses
    for P(any member) (model_a/calibration.py holds the fitter)."""
    from .calibration import fit_calibrator
    label_col = label_col or ("provider_on_exclusion" if "provider_on_exclusion" in matrix.columns
                              else "provider_on_leie")
    y = _training_label(matrix, label_col) if label_col in matrix.columns else np.zeros(len(matrix), int)
    y = np.asarray(y).astype(int)
    groups = matrix["group_id"].to_numpy() if "group_id" in matrix.columns else None
    s = np.nan_to_num(np.asarray(scores, dtype=float), nan=0.0)
    out = np.full(len(s), float(y.mean()) if len(y) else 0.0)
    if y.sum() < 5:
        return out
    for tr, te in fold_assignments(y, groups, n_splits):
        if y[tr].sum() == 0:
            continue
        out[te] = fit_calibrator(s[tr], y[tr]).predict(s[te])
    return out


def oof_scores(matrix: pd.DataFrame, manifest: dict,
               label_col: str = "provider_on_exclusion",
               n_splits: int = 5) -> np.ndarray:
    """Out-of-fold probability for every provider, full feature set MINUS
    every label-adjacent column.

    The PU label here is "already on an exclusion list", and several features
    are BUILT FROM the exclusion lists (fraud-proximity PageRank,
    excluded-owner flags, the ownership-integrity subscore). Trained on this
    label they are the answer key: the model saturates to exact 0/1 and the
    ranking collapses to "contains an excluded provider" (the 538k-way tie
    caught on 2026-07-30). The forward-label ablation can keep them; this
    scorer must not."""
    from sklearn.model_selection import GroupKFold, KFold
    from src.model_a.network_ab import LABEL_ADJACENT_NET

    # the matrix's label is whatever the manifest says it is (provider_on_exclusion
    # when the widened label was built, provider_on_leie otherwise); a hard-coded
    # column name crashed the pursuit dossier on any matrix without the widened label
    if label_col not in matrix.columns:
        for cand in (manifest.get("label"), "provider_on_leie"):
            if cand and cand in matrix.columns:
                label_col = cand
                break
        else:
            raise KeyError(f"no label column in the matrix (looked for {label_col}, "
                           f"{manifest.get('label')}, provider_on_leie)")
    banned = set(LABEL_ADJACENT_NET) \
        | set(manifest.get("leakage_adjacent", []) or []) \
        | set(manifest.get("leakage_hard", []) or [])
    cols = [c for c in full_feature_list(partition_features(manifest))
            if c in matrix.columns and c not in banned]
    # mechanical leak screen: the ban list is curated, but any single column
    # that near-perfectly separates the PU label on its own IS the label in
    # disguise, whatever it's called. Drop it and say so.
    y_train = _training_label(matrix, label_col)
    y_screen = y_train
    if y_screen.sum() > 0 and y_screen.sum() < len(matrix):
        from sklearn.metrics import roc_auc_score
        leaked = []
        for c in list(cols):
            v = pd.to_numeric(matrix[c], errors="coerce").fillna(0.0)
            if v.nunique() <= 1:
                continue
            auc = roc_auc_score(y_screen, v.to_numpy())
            if max(auc, 1 - auc) > 0.985:
                leaked.append((c, round(max(auc, 1 - auc), 4)))
                cols.remove(c)
        if leaked:
            print("[company_list] auto-dropped label-leaking feature(s): "
                  + ", ".join(f"{c} (single-column AUC {a})"
                              for c, a in leaked))
    # NATIVE missing values (2026-10-08): a missing percentile means "no adequate
    # peer group", a missing delta "no history", a zero-vector embedding "no
    # neighbourhood". fillna(0) read every one of them as the most benign value a
    # real provider can take (the smallest mid-rank percentile is 0.5/n) and threw
    # away the scheme engine's skip-missing design. LightGBM routes NaN on its own.
    X = matrix[cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y = y_train
    # MASK, never NEGATE (2026-10-08 label audit): a provider the in-matrix label
    # calls positive but the tier policy does not keep (a license action, a loan
    # default, a derivative exclusion, an allegation) is AMBIGUOUS, not clean. It
    # used to train as y=0 — the most contaminated negatives available. Such rows
    # are held out of every training fold and still scored out-of-fold.
    ambiguous = ((_to_num(matrix[label_col]) > 0) & (np.asarray(y) == 0)) \
        if label_col in matrix.columns else np.zeros(len(matrix), dtype=bool)
    ambiguous = np.asarray(ambiguous, dtype=bool)
    if ambiguous.any():
        print(f"[company_list] {int(ambiguous.sum()):,} listed-but-not-kept providers masked "
              f"from training (scored, never negatives)")
    groups = (matrix["group_id"].to_numpy()
              if "group_id" in matrix.columns else None)
    # PER-CASE BUDGET (2026-10-10): the export's label_weight dilutes the
    # broadcast DOJ positives of a case that fans out past the budget (the top 5
    # cases supplied 64% of v7's DOJ positives), so no single settlement teaches
    # the model what fraud looks like. 1.0 everywhere else; absent column = no
    # weighting (v8 and earlier matrices train exactly as before).
    w = training_weights(matrix)
    pol = training_policy(manifest)
    if pol["broadcast"] == "mask":
        bmask = broadcast_only_mask(matrix) & (np.asarray(y) == 1)
        if bmask.any():
            print(f"[company_list] broadcast policy 'mask': {int(bmask.sum()):,} broadcast-only "
                  f"case positives held out of training folds (scored out-of-fold)")
        ambiguous = ambiguous | bmask
    from src.model_a.network_ab import SCORER_PARAMS
    print(f"[company_list] scorer: LightGBM {', '.join(f'{k}={v}' for k, v in SCORER_PARAMS.items())}")
    print(f"[company_list] training policy: seeds={pol['n_seeds']}, broadcast={pol['broadcast']}, "
          f"groups={pol['group_policy']}")
    scores = np.zeros(len(matrix))
    for k, (tr, te) in enumerate(fold_assignments(y, groups, n_splits)):
        tr = tr[~ambiguous[tr]]
        acc = np.zeros(len(te))
        for s in range(pol["n_seeds"]):                # seed bagging (v9)
            acc += _fit_predict(X[tr], y[tr], X[te], seed=RANDOM_STATE + k + 1000 * s,
                                sample_weight=(w[tr] if w is not None else None))
        scores[te] = acc / pol["n_seeds"]
    # collapse guard (hard-fail, rule #2): a real probability ranking over a
    # large universe has thousands of distinct values; exact-0/1 saturation
    # or a giant single tie means a leaky feature or empty X reached the
    # model, and shipping that ranking would be worse than shipping nothing.
    if len(matrix) > 10_000:
        distinct = int(np.unique(np.round(scores, 12)).size)
        modal_share = float(pd.Series(scores).value_counts(normalize=True).iat[0])
        if distinct < 500 or modal_share > 0.95:
            raise ValueError(
                f"score collapse: {distinct} distinct scores, modal share "
                f"{modal_share:.1%} — a leaky or empty feature set reached "
                f"the scorer; refusing to write a broken ranking")
    return scores


def rollup(matrix: pd.DataFrame, scores: np.ndarray,
           top_k_schemes: int = 3, drop_excluded: bool = False,
           probs: np.ndarray | None = None) -> pd.DataFrame:
    """Provider scores → one row per org with named drivers.

    ``drop_excluded`` removes providers already ON the exclusion label before
    aggregation — a PURSUIT list must not be headed by the already-caught (the
    model perfectly re-finding label positives is validation, not a lead; the
    2026-08-02 chase list's top 25 was exactly that: exact-1.0 solo excluded
    providers billing $0-$50k). The org keeps its non-excluded members, and an
    ``n_excluded_members`` column preserves the corroboration context."""
    m = matrix.copy()
    m["_score"] = scores
    m["_prob"] = (np.nan_to_num(np.asarray(probs, dtype=float), nan=0.0) if probs is not None
                  else np.nan)
    n_excl = None
    if drop_excluded and "provider_on_exclusion" in m.columns:
        excl = pd.to_numeric(m["provider_on_exclusion"],
                             errors="coerce").fillna(0).astype(int)
        org_key = "org_node_id" if "org_node_id" in m.columns else "group_id"
        n_excl = (m.loc[excl == 1].groupby(m.loc[excl == 1][org_key])
                  .size() if excl.any() else pd.Series(dtype=int))
        m = m.loc[excl == 0]
    m["_billed"] = pd.to_numeric(
        m.get("net_paid", m.get("gross_paid", 0)), errors="coerce").fillna(0.0)
    org_col = "org_node_id" if "org_node_id" in m.columns else "group_id"
    sub_cols = [c for c in m.columns if c.startswith("subscore_")]

    def _best_name(g):
        # try each name column in turn, ignoring blanks — a rollup row must
        # never ship nameless (blank org_name rows reached a funder file)
        for c in ("org_display_name", "org_name", "provider_name"):
            if c in g.columns:
                vals = g[c].dropna().astype(str).str.strip()
                vals = vals[vals != ""]
                if len(vals):
                    return str(vals.mode().iat[0])
        return ""

    def _agg(g):
        w = g["_billed"].to_numpy()
        s = g["_score"].to_numpy()
        wmean = float(np.average(s, weights=w)) if w.sum() > 0 else float(s.mean())
        row = {
            "org_name": _best_name(g) or f"UNNAMED ORGANIZATION ({g.name})",
            "n_providers": int(len(g)),
            "total_billed": float(w.sum()),
            "score_max": float(s.max()),
            "score_dollar_weighted": wmean,
        }
        if probs is not None:
            # P(any member is a positive) from calibrated member probabilities,
            # treated as independent; the 2026-10-08 diagnostics: a dollar-weighted
            # MEAN of raw scores put hospitals and no-basis rows in the first 50
            pr = np.clip(g["_prob"].to_numpy(dtype=float), 0.0, 1.0)
            row["score_p_any"] = float(1.0 - np.prod(1.0 - pr))
            row["score_p_max"] = float(pr.max()) if len(pr) else 0.0
        if sub_cols:
            means = {c: pd.to_numeric(g[c], errors="coerce").mean()
                     for c in sub_cols}
            top = sorted(means, key=lambda c: -(means[c] if means[c] == means[c]
                                                else -1))[:top_k_schemes]
            row["top_schemes"] = "|".join(
                c.replace("subscore_", "") for c in top
                if means[c] == means[c] and means[c] > 0)
        return pd.Series(row)

    out = m.groupby(org_col, dropna=False).apply(_agg).reset_index()
    out = out.rename(columns={org_col: "org_id"})
    if n_excl is not None:
        out["n_excluded_members"] = out["org_id"].map(n_excl).fillna(0).astype(int)
    # percentile-normalize the headline score so ANY cutoff is meaningful.
    # SIGNAL SORTS, DOLLARS FILTER (operator decision 2026-08-04): rank by
    # the max-score percentile, then dollar-weighted score (still signal) —
    # dollars only break EXACT signal ties, so a $35 solo never sits above
    # a $1M org at the same score, but billing size never outranks signal.
    out["score_pct"] = out["score_max"].rank(pct=True)
    return out.sort_values(
        ["score_pct", "score_dollar_weighted", "total_billed"],
        ascending=False).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--min-billed", type=float, default=0.0,
                    help="drop orgs below this total billed (0 keeps all; the "
                         "comparer can apply their own floor)")
    ap.add_argument("--keep-excluded", action="store_true",
                    help="keep providers already on the exclusion label in the "
                         "rollup (default drops them: a chase list must not be "
                         "headed by the already-caught)")
    ap.add_argument("--out", default="company_list.csv")
    args = ap.parse_args()

    matrix = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text())
    print(f"[company_list] scoring {len(matrix):,} providers out-of-fold ...",
          flush=True)
    scores = oof_scores(matrix, manifest)
    print("[company_list] rolling up to orgs ...", flush=True)
    out = rollup(matrix, scores, drop_excluded=not args.keep_excluded)
    if args.min_billed > 0:
        out = out[out["total_billed"] >= args.min_billed]
    out.to_csv(args.out, index=False)
    big = int((out["total_billed"] >= 10_000_000).sum())
    print(f"[company_list] {len(out):,} orgs -> {args.out} "
          f"({big:,} with $10M+ billed; score_pct is percentile 0-1, "
          "apply cutoffs downstream)")


if __name__ == "__main__":
    main()
