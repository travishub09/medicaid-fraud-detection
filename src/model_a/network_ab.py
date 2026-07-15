"""
network_ab.py — is the network (graph) family REAL signal, or size in disguise?

The PI dig showed that graph features like ``within_2_hops_of_exclusion`` and
``related_party_density`` scale with organization size and corporate complexity:
national chains and hospitals score high automatically. So a naive "network-in vs
network-out" A/B can show lift that is really a size/sector confound, not causal
structure. This harness runs the A/B the honest way — twice:

  FULL population   train with vs without the network family; report top-decile
                    lift + PR-AUC on a grouped holdout. (The headline Travis saw.)

  MATCHED set       the same A/B restricted to a size/taxonomy/state-matched
                    case-control set (``case_control.match_cohorts``). Here every
                    case sits beside clean peers of the SAME size and specialty, so
                    a network win CANNOT come from size. This is the decisive test.

Verdict logic:
  * matched-set delta > 0 with a CI clear of zero  → network adds real, size-
    independent signal; keep it (under the out-of-time split, since it is
    leakage-adjacent).
  * full delta > 0 but matched delta ~ 0           → the network lift was size;
    drop the family or size-normalize it before trusting it.
  * neither positive                                → network family adds nothing
    measurable here.

Run AFTER rebuilding the graph with the generic-collision fix, on the frozen
(as-of) matrix, optionally scoring a forward label:
  python -m src.model_a.network_ab --matrix provider_features_for_model.parquet \
      --manifest feature_manifest.json --out NETWORK_AB_REPORT.md
  # forward eval: --future-label future_bans_after_2023-12.csv
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

RANDOM_STATE = 42
# A matched-set AUC at/above this with AND without the network family means the
# controls are too easy (or the matrix leaks): both models are perfect, so there
# is no headroom to measure the network contribution. Verdict = SATURATED, not KEEP.
CEILING_AUC = 0.98
# KEEP also requires the matched ROC-AUC delta to clear this, not just clear zero:
# a +0.0001 win at the ceiling is noise, not signal.
MIN_KEEP_DELTA = 0.005
# Network features that are near-copies of the exclusion LABEL: an excluded
# provider is trivially within 2 hops of its own exclusion node, so these win
# on an in-time matrix by reading off the answer, not by learning structure.
# On an in-time (non-forward) run they are LEAKAGE; the trustworthy verdict comes
# from the STRUCTURAL features with these removed. On a forward run they are fair.
LABEL_ADJACENT_NET = {
    "within_2_hops_of_exclusion", "has_excluded_owner", "graph_fraud_proximity",
    "facility_has_excluded_owner_high", "facility_has_excluded_owner_probable",
    "subscore_ownership_integrity",
}
NETWORK_NAMED = {
    "within_2_hops_of_exclusion", "shell_score", "related_party_density",
    "related_party_density_norm", "has_excluded_owner",
    "facility_has_excluded_owner_high", "facility_has_excluded_owner_probable",
    "subscore_ownership_integrity",
}


def network_cols(cols) -> list[str]:
    """Present columns that belong to the network/graph family."""
    return [c for c in cols if str(c).startswith("graph_") or c in NETWORK_NAMED]


def _trainable(matrix: pd.DataFrame, manifest: dict) -> list[str]:
    fams = (manifest.get("raw_feature_cols", []) + manifest.get("peerpct_cols", [])
            + manifest.get("subscore_cols", []) + manifest.get("leakage_adjacent", []))
    hard = set(manifest.get("leakage_hard", []))
    out, seen = [], set()
    for c in fams:
        if c in seen or c in hard or c not in matrix.columns:
            continue
        if pd.api.types.is_numeric_dtype(matrix[c]) or matrix[c].dtype == bool:
            out.append(c)
            seen.add(c)
    return out


def feature_sets(matrix: pd.DataFrame, manifest: dict):
    """(without_network, with_network, network_only) trainable column lists."""
    allf = _trainable(matrix, manifest)
    net = [c for c in network_cols(allf)]
    without = [c for c in allf if c not in set(net)]
    return without, allf, net


# ---- metrics -------------------------------------------------------------
def _to_num(y):
    return pd.to_numeric(pd.Series(y), errors="coerce").fillna(0).to_numpy()


def top_decile_lift(y_true, scores, frac: float = 0.10) -> float:
    y = _to_num(y_true)
    base = y.mean()
    if base <= 0 or len(y) < 10:
        return float("nan")
    k = max(1, int(len(y) * frac))
    top = np.argsort(scores)[::-1][:k]
    return float(y[top].mean() / base)


def pr_auc(y_true, scores) -> float:
    from sklearn.metrics import average_precision_score
    y = _to_num(y_true)
    if y.sum() == 0 or y.sum() == len(y):
        return float("nan")
    return float(average_precision_score(y, scores))


def roc_auc(y_true, scores) -> float:
    from sklearn.metrics import roc_auc_score
    y = _to_num(y_true)
    if y.sum() == 0 or y.sum() == len(y):
        return float("nan")
    return float(roc_auc_score(y, scores))


def _fit_predict(Xtr, ytr, Xte, n_estimators=200, seed=RANDOM_STATE):
    from lightgbm import LGBMClassifier
    clf = LGBMClassifier(n_estimators=n_estimators, num_leaves=15,
                         random_state=seed, verbose=-1)
    clf.fit(Xtr, ytr)
    return clf.predict_proba(Xte)[:, 1]


def _grouped_split(n, groups, test_size=0.3, seed=RANDOM_STATE):
    from sklearn.model_selection import GroupShuffleSplit, ShuffleSplit
    if groups is not None and pd.Series(groups).nunique() > 3:
        gss = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
        return next(gss.split(np.zeros(n), groups=groups))
    ss = ShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
    return next(ss.split(np.zeros(n)))


_METRICS = (("pr_auc", pr_auc), ("roc_auc", roc_auc), ("lift10", top_decile_lift))


def _ab_once(frame, y, with_net, without, groups, test_groups_for_boot,
             n_boot=200, n_splits=5):
    """Train with vs without network across SEVERAL grouped splits; pool the
    delta distribution. A single split + i.i.d. row bootstrap understated the
    variance two ways (no split-to-split/model-stochasticity term, and rows
    within a matched cluster are dependent) and made KEEP anti-conservative —
    so: multiple seeded splits, and the within-split bootstrap resamples GROUPS
    (a case moves with its matched controls), not rows."""
    yv = _to_num(y)
    per_split = {"with": [], "without": []}
    deltas = {k: [] for k, _ in _METRICS}
    n_te_all, pos_te_all = [], []
    boot_per_split = max(20, n_boot // max(n_splits, 1))
    for s in range(max(1, n_splits)):
        tr, te = _grouped_split(len(frame), groups, seed=RANDOM_STATE + s)
        scores = {}
        for tag, cols in (("with", with_net), ("without", without)):
            Xtr = frame.iloc[tr][cols].fillna(0.0).to_numpy()
            Xte = frame.iloc[te][cols].fillna(0.0).to_numpy()
            scores[tag] = _fit_predict(Xtr, yv[tr], Xte, seed=RANDOM_STATE + s)
        yte = yv[te]
        n_te_all.append(len(te))
        pos_te_all.append(int(yte.sum()))
        for tag in ("with", "without"):
            per_split[tag].append({k: f(yte, scores[tag]) for k, f in _METRICS})
        # split-level delta contributes the between-split variance term
        for k, f in _METRICS:
            d = f(yte, scores["with"]) - f(yte, scores["without"])
            if d == d:
                deltas[k].append(d)
        # cluster bootstrap: resample GROUPS in the test set, not rows
        rng = np.random.default_rng(RANDOM_STATE + 1000 + s)
        gte = (pd.Series(groups).iloc[te].to_numpy()
               if groups is not None else None)
        if gte is not None and pd.Series(gte).nunique() > 3:
            uniq = pd.unique(gte)
            gidx = {u: np.flatnonzero(gte == u) for u in uniq}

            def _draw():
                pick = rng.choice(uniq, size=len(uniq), replace=True)
                return np.concatenate([gidx[u] for u in pick])
        else:
            m = len(yte)

            def _draw():
                return rng.integers(0, m, m)
        for _ in range(boot_per_split):
            idx = _draw()
            yb = yte[idx]
            if yb.sum() == 0 or yb.sum() == len(yb):
                continue
            for k, f in _METRICS:
                d = f(yb, scores["with"][idx]) - f(yb, scores["without"][idx])
                if d == d:
                    deltas[k].append(d)
    res = {tag: {k: float(np.nanmean([r[k] for r in per_split[tag]]))
                 for k, _ in _METRICS} for tag in ("with", "without")}
    ci = {}
    for k, v in deltas.items():
        v = [x for x in v if x == x]
        if v:
            ci[k] = {"delta": float(np.mean(v)),
                     "lo": float(np.percentile(v, 2.5)),
                     "hi": float(np.percentile(v, 97.5))}
        else:
            ci[k] = {"delta": float("nan"), "lo": float("nan"), "hi": float("nan")}
    return {"n_test": int(np.mean(n_te_all)), "pos_test": int(np.mean(pos_te_all)),
            "n_splits": int(max(1, n_splits)),
            "with": res["with"], "without": res["without"], "delta_ci": ci}


def _robustness_attacks(matrix, manifest, future_label, with_structural, without,
                        groups_col, n_boot, n_splits, gap_months) -> dict:
    """Travis's two adversarial checks, run on the size-matched structural set so
    a survivor is trustworthy:

      in_flight_dropped  drop forward positives banned within ``gap_months`` of
                         the cutoff (bans that were likely investigations already
                         in flight, i.e. the easy ones). Edge should survive on
                         the harder remainder.
      proximity_residualized  regress the within_2_hops proximity channel out of
                         the structural features, so any surviving edge cannot be
                         shell_score merely echoing 'near an already-excluded
                         provider'.
    """
    import numpy as np
    from .case_control import match_cohorts
    res = {"gap_months": gap_months}
    if future_label is None:
        return res
    fl = future_label.copy(); fl["npi"] = fl["npi"].astype(str)
    pos = set(fl.loc[pd.to_numeric(fl.get("is_prospective_positive", 1),
                                   errors="coerce").fillna(0) == 1, "npi"])
    drop = set(fl.loc[pd.to_numeric(fl.get("was_excluded_pre_cutoff", 0),
                                    errors="coerce").fillna(0) == 1, "npi"])

    # --- attack 1: drop in-flight bans within gap_months of the cutoff ---
    try:
        cutoff = pd.to_datetime(manifest.get("asof_cutoff") or "2023-12-01")
        d = fl.copy()
        d["_dt"] = pd.to_datetime(d.get("first_excl_date"), errors="coerce")
        soon = set(d.loc[d["_dt"].notna()
                         & (d["_dt"] <= cutoff + pd.DateOffset(months=gap_months))
                         & d["npi"].isin(pos), "npi"])
        m = matrix[~matrix["npi"].astype(str).isin(drop | soon)].copy()
        y = m["npi"].astype(str).isin(pos - soon).astype(int)
        if int(y.sum()) >= 20:
            mi = m.copy(); mi["_forward_positive"] = y.to_numpy()
            if "confirmed_clean" in mi.columns:
                mi = mi.drop(columns=["confirmed_clean"])
            matched = match_cohorts(mi, label_col="_forward_positive")
            if len(matched) and "cohort" in matched.columns:
                ym = (matched["cohort"] == "case").astype(int)
                gm = matched["match_id"].to_numpy() if "match_id" in matched.columns else None
                res["in_flight_dropped"] = _ab_once(matched, ym, with_structural,
                                                    without, gm, None, n_boot, n_splits)
                res["in_flight_dropped_n_pos"] = int(y.sum())
    except Exception as e:                                    # pragma: no cover
        res["in_flight_error"] = str(e)

    # --- attack 2: residualize shell/structural on the proximity channel ---
    try:
        prox = "within_2_hops_of_exclusion"
        if prox in matrix.columns:
            fl2 = matrix.copy()
            fl2["_forward_positive"] = fl2["npi"].astype(str).isin(pos).astype(int)
            fl2 = fl2[~fl2["npi"].astype(str).isin(drop)]
            if "confirmed_clean" in fl2.columns:
                fl2 = fl2.drop(columns=["confirmed_clean"])
            matched = match_cohorts(fl2, label_col="_forward_positive")
            if len(matched) and "cohort" in matched.columns:
                pvec = pd.to_numeric(matched[prox], errors="coerce").fillna(0).to_numpy()
                resid = matched.copy()
                for c in with_structural:
                    if c in resid.columns and c != prox:
                        x = pd.to_numeric(resid[c], errors="coerce").fillna(0).to_numpy(float)
                        denom = float((pvec * pvec).sum()) or 1.0
                        beta = float((x * pvec).sum()) / denom
                        resid[c] = x - beta * pvec        # remove the prox-aligned part
                struct_no_prox = [c for c in with_structural if c != prox]
                ym = (resid["cohort"] == "case").astype(int)
                gm = resid["match_id"].to_numpy() if "match_id" in resid.columns else None
                res["proximity_residualized"] = _ab_once(
                    resid, ym, struct_no_prox, without, gm, None, n_boot, n_splits)
    except Exception as e:                                    # pragma: no cover
        res["proximity_error"] = str(e)
    return res


def run_network_ab(matrix: pd.DataFrame, manifest: dict,
                   future_label: pd.DataFrame | None = None, n_boot: int = 200,
                   realistic_controls: bool = False, n_splits: int = 5,
                   robustness: bool = False, gap_months: int = 6) -> dict:
    label = manifest.get("label") or "provider_on_exclusion"
    without, with_net, net = feature_sets(matrix, manifest)
    label_adjacent = [c for c in net if c in LABEL_ADJACENT_NET]
    structural_net = [c for c in net if c not in LABEL_ADJACENT_NET]
    with_structural = list(dict.fromkeys(without + structural_net))
    out = {"label": label, "n_network_features": len(net),
           "network_features": net, "n_trainable": len(with_net),
           "label_adjacent_net": label_adjacent, "structural_net": structural_net,
           "is_forward": future_label is not None,
           "graph_substrate": manifest.get("graph_substrate")}
    if not net:
        out["error"] = "no network/graph features present — nothing to test."
        return out
    if label not in matrix.columns and future_label is None:
        out["error"] = f"label {label} not in matrix and no --future-label given."
        return out

    groups = matrix["group_id"].to_numpy() if "group_id" in matrix.columns else None

    # ----- FULL population -----
    if future_label is not None:
        fl = future_label.copy()
        fl["npi"] = fl["npi"].astype(str)
        pos = set(fl.loc[pd.to_numeric(fl.get("is_prospective_positive", 1),
                                       errors="coerce").fillna(0) == 1, "npi"])
        drop = set(fl.loc[pd.to_numeric(fl.get("was_excluded_pre_cutoff", 0),
                                        errors="coerce").fillna(0) == 1, "npi"])
        m = matrix[~matrix["npi"].astype(str).isin(drop)].copy()
        yfull = m["npi"].astype(str).isin(pos).astype(int)
        if int(yfull.sum()) == 0:
            out["error"] = ("forward label has ZERO positives among the matrix NPIs "
                            "— stale exclusion file or an as-of graph was used to "
                            "build the label. No verdict can be computed.")
            return out
        gfull = m["group_id"].to_numpy() if "group_id" in m.columns else None
        out["full_label"] = "forward (future bans)"
        out["full"] = _ab_once(m, yfull, with_net, without, gfull, None, n_boot, n_splits)
    else:
        out["full_label"] = label
        out["full"] = _ab_once(matrix, matrix[label], with_net, without, groups, None, n_boot, n_splits)

    # ----- MATCHED set (size-controlled) -----
    try:
        from .case_control import match_cohorts
        if future_label is not None:
            # FORWARD matched: cases = FUTURE-banned providers, controls = matched
            # not-yet-banned peers, features as-of. Matching on the in-time label
            # here would put each case's own exclusion node back in the graph and
            # let within_2_hops read off the answer — the exact leak this test
            # exists to remove. Realistic controls forced (the clean anchors were
            # selected with in-time criteria).
            match_input = m.copy()
            match_input["_forward_positive"] = yfull.to_numpy()
            if "confirmed_clean" in match_input.columns:
                match_input = match_input.drop(columns=["confirmed_clean"])
            out["control_kind"] = "realistic (ordinary matched peers)"
            out["matched_label"] = "forward (future bans)"
            match_label = "_forward_positive"
        else:
            match_input = matrix
            match_label = label
            out["matched_label"] = label
            if realistic_controls and "confirmed_clean" in matrix.columns:
                # drop the manufactured-clean anchors so match_cohorts falls back to
                # ordinary same-size/specialty providers as controls — a realistic
                # (harder) comparison with headroom, not known-bad vs manufactured-clean.
                match_input = matrix.drop(columns=["confirmed_clean"])
                out["control_kind"] = "realistic (ordinary matched peers)"
            else:
                out["control_kind"] = "confirmed_clean anchors"
        matched = match_cohorts(match_input, label_col=match_label)
        if len(matched) and "cohort" in matched.columns:
            ym = (matched["cohort"] == "case").astype(int)
            gm = matched["match_id"].to_numpy() if "match_id" in matched.columns else None
            out["matched"] = _ab_once(matched, ym, with_net, without, gm, None, n_boot, n_splits)
            out["matched_n"] = int(len(matched))
            # structural-only: drops the label-adjacent flags so an in-time win
            # can't come from within_2_hops reading off the label.
            if structural_net:
                out["matched_structural"] = _ab_once(
                    matched, ym, with_structural, without, gm, None, n_boot, n_splits)
        else:
            out["matched_error"] = "match_cohorts produced no matched set (need positives + confirmed_clean)."
    except Exception as e:  # pragma: no cover
        out["matched_error"] = f"matched A/B failed: {e}"

    # ----- robustness attacks (Travis) — structural set, forward only -----
    if robustness and future_label is not None and structural_net:
        out["robustness"] = _robustness_attacks(
            matrix, manifest, future_label, with_structural, without,
            groups, n_boot, n_splits, gap_months)

    out["verdict"] = _verdict(out)
    return out


def _verdict(out: dict) -> str:
    def judge(blk):
        if not blk:
            return None
        ci = blk.get("delta_ci", {}).get("roc_auc", {})
        w, wo = blk.get("with", {}).get("roc_auc"), blk.get("without", {}).get("roc_auc")
        if w == w and wo == wo and min(w or 0, wo or 0) >= CEILING_AUC:
            return "SATURATED"
        lo, delta = ci.get("lo"), ci.get("delta")
        if lo == lo and delta == delta and lo > 0 and delta >= MIN_KEEP_DELTA:
            return ("KEEP", delta)
        return "NONE"

    la = out.get("label_adjacent_net", [])
    in_time = not out.get("is_forward")
    leak_note = ""
    if in_time and la:
        leak_note = (f" NOTE: on this in-time matrix the full-network number is inflated "
                     f"by label-adjacent flags ({', '.join(la)}) — a provider is trivially "
                     f"near its own exclusion — so the verdict below is judged on the "
                     f"STRUCTURAL features only. The full-network figure only becomes "
                     f"trustworthy on the frozen (forward-label) matrix.")

    # in-time → judge structural-only; forward → judge full network
    blk = out.get("matched_structural") if (in_time and out.get("matched_structural")) \
        else out.get("matched", {})
    scope = "structural graph features" if (in_time and out.get("matched_structural")) \
        else "network family"
    verdict = judge(blk)

    if verdict == "SATURATED":
        return ("SATURATED / INCONCLUSIVE: both models score near-perfect on the matched "
                f"set (ROC-AUC >= {CEILING_AUC}); no headroom to measure the contribution. "
                "Controls too easy or the matrix leaks." + leak_note)
    if isinstance(verdict, tuple):
        return (f"KEEP (size-independent): the {scope} lifts discrimination against size/"
                f"taxonomy-matched controls by a real margin (delta {verdict[1]:+.3f}, CI "
                f"clear of zero). Use under the out-of-time split." + leak_note)
    full = out.get("full", {}).get("delta_ci", {}).get("lift10", {})
    if (full and full.get("lo", float("nan")) == full.get("lo") and full["lo"] > 0
            and out.get("matched")):
        return (f"SIZE / LEAK ARTIFACT: the {scope} do not beat size-matched controls once "
                "judged honestly. The full-population lift was size or label leakage, not "
                "structure. Drop or size-normalize." + leak_note)
    return (f"NO MEASURABLE SIGNAL: the {scope} do not move the matched metric beyond "
            "noise. Do not rely on it." + leak_note)


def _fmt(d):
    return (f"{d.get('delta', float('nan')):+.4f} "
            f"[{d.get('lo', float('nan')):+.4f}, {d.get('hi', float('nan')):+.4f}]")


def to_markdown(out: dict) -> str:
    L = ["# NETWORK A/B REPORT — is the graph family real signal or size?\n"]
    if out.get("error"):
        return "\n".join(L + [f"**{out['error']}**"])
    L.append(f"- label: `{out['label']}`  |  network features tested: "
             f"**{out['n_network_features']}**  |  trainable columns: {out['n_trainable']}")
    if out.get("label_adjacent_net"):
        L.append(f"- label-adjacent (leaky in-time): {', '.join(out['label_adjacent_net'])}")
    if out.get("structural_net"):
        L.append(f"- structural (trustworthy): {', '.join(out['structural_net'])}")
    if out.get("verdict"):
        L.append(f"- **VERDICT: {out['verdict']}**\n")
    for pop in ("full", "matched", "matched_structural"):
        blk = out.get(pop)
        if not blk:
            if out.get(f"{pop}_error"):
                L.append(f"## {pop.upper()}  \n_{out[f'{pop}_error']}_\n")
            continue
        if pop == "full":
            lbl = out.get("full_label", "")
        elif pop == "matched":
            lbl = (f"FULL network vs none, case vs matched control "
                   f"[label: {out.get('matched_label', '?')}; {out.get('control_kind', '?')}]")
        else:
            lbl = "STRUCTURAL network only vs none, case vs matched control (label-adjacent flags removed)"
        L.append(f"## {pop.upper()} — {lbl}")
        base = blk["pos_test"] / max(blk["n_test"], 1)
        L.append(f"n_test={blk['n_test']:,}  positives_test={blk['pos_test']:,}"
                 f"  (splits={blk.get('n_splits', 1)})"
                 + (f"  matched_rows={out.get('matched_n'):,}" if pop == "matched" else ""))
        if pop != "full":
            L.append(f"_matched base rate ~{base:.0%}: absolute PR-AUC here is NOT "
                     f"comparable to deployment prevalence — read the DELTA column._")
        L.append("| metric | with network | without | delta (with-without) [95% CI] |")
        L.append("|---|---|---|---|")
        for m, nm in (("lift10", "top-decile lift"), ("pr_auc", "PR-AUC"), ("roc_auc", "ROC-AUC")):
            w = blk["with"].get(m, float("nan"))
            wo = blk["without"].get(m, float("nan"))
            L.append(f"| {nm} | {w:.4f} | {wo:.4f} | {_fmt(blk['delta_ci'].get(m, {}))} |")
        L.append("")
    rob = out.get("robustness")
    if rob:
        L.append("")
        L.append("## ROBUSTNESS — the structural edge under two attacks")
        gm = rob.get("gap_months", 6)
        for key, title in (
            ("in_flight_dropped",
             f"Drop forward bans within {gm} months of the cutoff (investigations "
             "likely already in flight)"),
            ("proximity_residualized",
             "Residualize the within_2_hops proximity channel out of the "
             "structural features"),
        ):
            blk = rob.get(key)
            if not blk:
                continue
            npos = rob.get("in_flight_dropped_n_pos")
            note = f" (positives left: {npos})" if key == "in_flight_dropped" and npos else ""
            L.append(f"\n**{title}{note}**")
            L.append("| metric | with network | without | delta [95% CI] |")
            L.append("|---|---|---|---|")
            for m, nm in (("lift10", "top-decile lift"), ("pr_auc", "PR-AUC"),
                          ("roc_auc", "ROC-AUC")):
                w = blk["with"].get(m, float("nan"))
                wo = blk["without"].get(m, float("nan"))
                L.append(f"| {nm} | {w:.4f} | {wo:.4f} | {_fmt(blk['delta_ci'].get(m, {}))} |")
        L.append("\n_An edge that holds under both attacks is not the model reading "
                 "the answer off proximity or off easy in-flight bans._")
    sub = out.get("graph_substrate") or manifest_substrate(out)
    if sub is not None:
        frozen = sub.get("address_layer_frozen")
        L.append("")
        L.append(f"## SUBSTRATE — {'FROZEN' if frozen else 'NOT frozen'}")
        if frozen:
            L.append(f"- Co-location addresses frozen to the "
                     f"{sub.get('asof_nppes_edition')} NPPES edition. shell_score "
                     "carries no post-cutoff address leak. Clean.")
        else:
            L.append("- Co-location addresses use TODAY's NPPES, so shell_score "
                     "still carries a post-cutoff leak. Rebuild the graph with "
                     "--asof-nppes for a clean read (see run6).")
    L.append("")
    L.append("_Matched-set delta is decisive: a network advantage that survives size/"
             "taxonomy matching is real; one that only shows on the full population was size._")
    return "\n".join(L)


def manifest_substrate(out: dict):
    """Substrate info if the A/B was handed it (via manifest); else None."""
    return out.get("graph_substrate")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--future-label", default=None,
                    help="prospective_label CSV (npi,is_prospective_positive,"
                         "was_excluded_pre_cutoff) for a forward evaluation.")
    ap.add_argument("--out", default="NETWORK_AB_REPORT.md")
    ap.add_argument("--n-boot", type=int, default=200)
    ap.add_argument("--n-splits", type=int, default=5,
                    help="grouped train/test splits to average over (the delta CI "
                         "pools split-level + cluster-bootstrap variation)")
    ap.add_argument("--realistic-controls", action="store_true",
                    help="match cases to ORDINARY same-size/specialty providers instead "
                         "of the manufactured-clean anchors — a harder, headroom-having "
                         "test. Use this when the confirmed_clean run saturates at AUC 1.")
    ap.add_argument("--robustness", action="store_true",
                    help="run the two adversarial checks (Travis): drop in-flight "
                         "bans near the cutoff, and residualize the proximity "
                         "channel. Forward runs only.")
    ap.add_argument("--gap-months", type=int, default=6,
                    help="in-flight window for --robustness: drop forward positives "
                         "banned within this many months of the cutoff.")
    args = ap.parse_args()

    matrix = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text())
    fut = pd.read_csv(args.future_label, dtype=str) if args.future_label else None
    out = run_network_ab(matrix, manifest, future_label=fut, n_boot=args.n_boot,
                         realistic_controls=args.realistic_controls,
                         n_splits=args.n_splits, robustness=args.robustness,
                         gap_months=args.gap_months)
    report = to_markdown(out)
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
