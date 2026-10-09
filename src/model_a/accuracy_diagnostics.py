"""
accuracy_diagnostics.py — does the SHIPPED list find future cases, at the depth
a human actually reads it, and how far ahead of enforcement?

Why this exists (the 2026-10-08 accuracy audit): every evaluation in the
repository scored the wrong object. network_ab / source_ablation / label_ablation
grade an NPI ranking over the whole universe at the top DECILE (60,000 rows on
a national matrix) and compare two feature sets; the registry gate reads those
deltas. Nobody measured the business outcome: of the ~50 organizations in a
round (~16 to a private investigator), how many had a member provider named in
an FCA case or a fraud-tier exclusion that became public AFTER the freeze, and
how many months ahead of it the list was. A model can hold 3x decile lift and
put zero future cases in its first 50 rows.

What it computes (one run folder, one or more forward RULERS):

  rulers      forward labels in prospective_label's schema (is_prospective_positive,
              was_excluded_pre_cutoff, first_excl_date, fca_tier). The default is
              the run's future_bans_after_<cutoff>.csv ("all") plus the __hard
              variant; --case-db adds the FCA ruler (DOJ / OIG cases with a
              resolved outcome announced after the cutoff, model_a.prospective_label.
              build_forward_case_label) — the headline, because it IS the target.
  universe    everything known at the freeze is dropped before any number is
              computed: pre-cutoff records under the ruler, in-matrix positives,
              DOJ cases announced before the cutoff; undated records are dropped;
              a horizon (--horizon-months) boxes the events to (cutoff, cutoff+h]
              so two freezes are graded with the same ruler length. "Ambiguous"
              negatives (positive under another ruler, not under this one) are
              reported as a sensitivity variant.
  scores      --score-source priority_list reads the shipped provider_priority_list
              .csv (no training: the exact order the funder saw); refit rebuilds
              the shipped scorer out-of-fold (company_list's column policy, mask-
              not-negate, GroupKFold) and rolls it up the same way, which also
              yields the NPI-grain tables, fold shares, repeats and calibration.

  (a) precision at review budgets (orgs 16 / 50 / 100 / 250; NPIs and top
      fractions) with Wilson + Clopper-Pearson intervals and THREE baselines:
      random from the same filtered pool (hypergeometric mean and tail p),
      the dollar-ranked list, and a size-matched random list. Tie-aware:
      a tied block at the boundary gets fractional credit and the tie count is
      reported (subscores and peer ranks tie-flood).
  (b) lead time: months from the freeze to the event for the hits; precision at
      each horizon; share of hits with lead >= --gap-months (events inside the
      gap are investigations already under way, worth little to a relator);
      visibility (positives still billing within --active-within months).
  (c) error taxonomy of the top 50 / 250 orgs: government, already known,
      institutional, intermediary-shaped, tiny dollars, data artifact, other,
      with the hit rate per cause.
  (d) segments (state, taxonomy family, size band, grain) with empirical-Bayes
      shrunk lift so a 3-positive segment does not shout.
  (e) label-noise sensitivity, eval side (with vs without ambiguous negatives).
  (f) calibration at the top: cross-fitted isotonic on rank-quantile bins
      (1-50, 51-250, ...), org P(any member) = 1 - prod(1 - p), expected true
      and false discoveries in the top 50, the FDR-bounded list size.
  (g) stability under --repeats R re-shuffled fold assignments: SD of
      precision@50, Jaccard of the top sets, fold shares in the top k.
  (h) settlement-dollar recall at org budgets (per case, deduplicated).
  Sanity: ties at k, NaN scores, the largest group's share, fold mixing.

Outputs ACCURACY_DIAGNOSTICS.md (plain first, then the tables), an
accuracy_diagnostics.json KPI block the registry reads (acc_* fields,
enforceable with --check), and accuracy_topk_rows.csv (the rows behind the
headline numbers, so a reviewer can look at every hit and miss by name).

Leads for counsel review, never accusations: a "hit" here means a later public
enforcement event touched a listed organization, not that the list was right
about why.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

RANDOM_STATE = 42
DEFAULT_ORG_BUDGETS = (16, 50, 100, 250)
DEFAULT_NPI_BUDGETS = (16, 50, 250, 1000)
DEFAULT_TOP_FRACS = (0.001, 0.01, 0.10)
DEFAULT_HORIZONS = (6, 12, 24, 36, 60)
RANK_BINS = ((1, 50), (51, 250), (251, 1000), (1001, 6000), (6001, None))
_DISCLAIMER = ("_Every number below describes how a ranked list of organizations compared "
               "with later PUBLIC enforcement records. A hit is a listed organization "
               "with a member provider later named in such a record; it is not a finding "
               "about the organization, and a miss is not an exoneration. Leads for "
               "counsel review, never accusations._")


# --------------------------------------------------------------------------- #
# small statistics
# --------------------------------------------------------------------------- #

def wilson(hits: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion (works at 0 and n hits)."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def clopper_pearson(hits: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact binomial interval."""
    if n <= 0:
        return (float("nan"), float("nan"))
    from scipy.stats import beta
    lo = 0.0 if hits == 0 else float(beta.ppf(alpha / 2, hits, n - hits + 1))
    hi = 1.0 if hits == n else float(beta.ppf(1 - alpha / 2, hits + 1, n - hits))
    return (lo, hi)


def hypergeom_baseline(k: int, pool_n: int, pool_pos: int, hits: float) -> dict:
    """Random draw of k from the pool: expected hits and P(X >= hits)."""
    if pool_n <= 0 or k <= 0:
        return {"expected": float("nan"), "p_value": float("nan")}
    from scipy.stats import hypergeom
    k = min(k, pool_n)
    exp = k * pool_pos / pool_n
    h = int(math.floor(hits + 1e-9))
    p = float(hypergeom.sf(h - 1, pool_n, pool_pos, k)) if h > 0 else 1.0
    return {"expected": float(exp), "p_value": p}


def tie_aware_hits(y: np.ndarray, scores: np.ndarray, k: int) -> tuple[float, int, int]:
    """Expected positives in the top k when the block tied at the boundary gets
    fractional credit. Returns (expected_hits, exact_hits_by_position, n_tied_at_boundary)."""
    n = len(scores)
    k = int(min(k, n))
    if k <= 0 or n == 0:
        return 0.0, 0, 0
    s = np.asarray(scores, dtype=float)
    s = np.where(np.isnan(s), -np.inf, s)
    order = np.argsort(-s, kind="stable")
    exact = int(np.asarray(y)[order[:k]].sum())
    kth = s[order[k - 1]]
    above = s > kth
    tied = s == kth
    n_above = int(above.sum())
    n_tied = int(tied.sum())
    if n_tied <= 1:
        return float(exact), exact, 0
    need = k - n_above                       # slots left for the tied block
    tied_pos = float(np.asarray(y)[tied].sum())
    expected = float(np.asarray(y)[above].sum()) + tied_pos * need / n_tied
    return expected, exact, n_tied


def eb_shrunk_rate(hits: int, n: int, global_rate: float, strength: float = 20.0) -> float:
    """Beta-binomial posterior mean toward the global rate (prior weight = strength rows)."""
    if n <= 0:
        return float("nan")
    return (hits + strength * global_rate) / (n + strength)


def _col(df: pd.DataFrame, name: str, default=0.0) -> pd.Series:
    """Numeric column as a Series, a constant default when absent."""
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").fillna(default)
    return pd.Series(default, index=df.index, dtype=float)


def _months_between(a: pd.Series, b: pd.Series) -> pd.Series:
    return (b.dt.year - a.dt.year) * 12 + (b.dt.month - a.dt.month)


def _cutoff_ts(cutoff: str) -> pd.Timestamp:
    s = str(cutoff).strip()
    return pd.Timestamp(s[:7] + "-01" if len(s) == 7 else s)


# --------------------------------------------------------------------------- #
# rulers and the evaluation universe
# --------------------------------------------------------------------------- #

def load_ruler(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, dtype=str)
    df = df.copy()
    df["npi"] = df["npi"].astype(str).str.strip()
    for c in ("is_prospective_positive", "was_excluded_pre_cutoff", "undated_exclusion"):
        df[c] = pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0).astype(int)
    df["event_date"] = pd.to_datetime(df.get("first_excl_date"), errors="coerce")
    if "fca_tier" not in df.columns:
        df["fca_tier"] = ""
    return df[["npi", "event_date", "is_prospective_positive", "was_excluded_pre_cutoff",
               "undated_exclusion", "fca_tier"]]


def eval_frame(matrix: pd.DataFrame, ruler: pd.DataFrame, cutoff: str,
               horizon_months: int | None = None,
               other_rulers: list[pd.DataFrame] | None = None) -> tuple[pd.DataFrame, dict]:
    """The matrix joined to one ruler, with the freeze-time knowledge removed.

    Returns (frame, stats). ``frame`` carries y (0/1 forward positive),
    event_date, lead_months, known (dropped from the universe), ambiguous
    (positive under another ruler only; kept, flagged for the sensitivity run)."""
    cut = _cutoff_ts(cutoff)
    m = matrix[["npi"]].copy()
    m["npi"] = m["npi"].astype(str)
    r = ruler.drop_duplicates("npi").set_index("npi")
    m["y"] = m["npi"].map(r["is_prospective_positive"]).fillna(0).astype(int)
    m["event_date"] = m["npi"].map(r["event_date"])
    m["known"] = m["npi"].map(r["was_excluded_pre_cutoff"]).fillna(0).astype(int).astype(bool)
    m["undated"] = m["npi"].map(r["undated_exclusion"]).fillna(0).astype(int).astype(bool)
    # in-matrix knowledge: a provider the training label already calls positive, or
    # a DOJ case the export resolved before the freeze, was known when the list was built
    if "provider_on_exclusion" in matrix.columns:
        m["known"] |= pd.to_numeric(matrix["provider_on_exclusion"], errors="coerce").fillna(0).astype(int).eq(1).to_numpy()
    if "case_ids" in matrix.columns:
        m["known"] |= matrix["case_ids"].fillna("").astype(str).str.len().gt(0).to_numpy()
    m.loc[m["known"], "y"] = 0
    m["lead_months"] = _months_between(pd.Series(cut, index=m.index), m["event_date"])
    n_pos_raw = int(m["y"].sum())
    beyond = pd.Series(False, index=m.index)
    if horizon_months:
        beyond = (m["y"] == 1) & m["event_date"].notna() & (m["lead_months"] > int(horizon_months))
        m.loc[beyond, "y"] = 0
    m["ambiguous"] = False
    for o in (other_rulers or []):
        oo = o.drop_duplicates("npi").set_index("npi")
        pos_other = m["npi"].map(oo["is_prospective_positive"]).fillna(0).astype(int).eq(1)
        m["ambiguous"] |= (pos_other & (m["y"] == 0) & ~m["known"]).to_numpy()
    keep = ~m["known"] & ~m["undated"] & ~beyond
    stats = {"n_matrix": int(len(m)), "dropped_known": int(m["known"].sum()),
             "dropped_undated": int((m["undated"] & ~m["known"]).sum()),
             "dropped_beyond_horizon": int(beyond.sum()),
             "positives_before_horizon": n_pos_raw, "positives": int(m.loc[keep, "y"].sum()),
             "n_eval": int(keep.sum()), "ambiguous_negatives": int(m.loc[keep, "ambiguous"].sum())}
    out = m[keep].reset_index(drop=True)
    return out, stats


# --------------------------------------------------------------------------- #
# scores: the shipped list, or a refit of the shipped scorer
# --------------------------------------------------------------------------- #

def _screened_columns(matrix: pd.DataFrame, manifest: dict, y_screen: np.ndarray) -> list[str]:
    from .label_ablation import _screened_features
    cols, leaked = _screened_features(matrix, manifest, y_screen)
    if leaked:
        print("[accuracy_diagnostics] auto-dropped label-leaking feature(s): "
              + ", ".join(f"{c} (AUC {a})" for c, a in leaked))
    return cols


def oof_with_folds(matrix: pd.DataFrame, manifest: dict, cols: list[str], y_train: np.ndarray,
                   ambiguous: np.ndarray, n_splits: int = 5, repeat: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """company_list.oof_scores' loop with the fold id returned and an optional
    re-shuffle of the group assignment (repeat > 0 hashes the groups with the
    repeat as salt before GroupKFold, which is otherwise deterministic)."""
    from sklearn.model_selection import GroupKFold, KFold
    from .network_ab import _fit_predict
    X = matrix[cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    groups = matrix["group_id"].astype(str).to_numpy() if "group_id" in matrix.columns else None
    scores = np.zeros(len(matrix))
    fold = np.full(len(matrix), -1)
    if groups is not None and pd.Series(groups).nunique() > n_splits:
        if repeat:
            rng = np.random.RandomState(RANDOM_STATE + repeat)
            uniq = pd.unique(groups)
            perm = dict(zip(uniq, rng.permutation(len(uniq))))
            g = np.array([perm[x] for x in groups])
        else:
            g = groups
        it = GroupKFold(n_splits=n_splits).split(X, y_train, g)
    else:
        it = KFold(n_splits=n_splits, shuffle=True, random_state=RANDOM_STATE + repeat).split(X)
    for k, (tr, te) in enumerate(it):
        tr = tr[~ambiguous[tr]]
        scores[te] = _fit_predict(X[tr], y_train[tr], X[te], seed=RANDOM_STATE + k + 100 * repeat)
        fold[te] = k
    return scores, fold


def refit_scores(matrix: pd.DataFrame, manifest: dict, repeat: int = 0,
                 cols: list[str] | None = None) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """The shipped scorer, out-of-fold: in-matrix label under the current policy
    (company_list.set_label_policy), mask-not-negate, the same column screen."""
    from .company_list import _training_label, _to_num
    label_col = manifest.get("label") or ("provider_on_exclusion" if "provider_on_exclusion" in matrix.columns
                                          else "provider_on_leie")
    y = np.asarray(_training_label(matrix, label_col)).astype(int)
    if cols is None:
        cols = _screened_columns(matrix, manifest, y)
    amb = ((_to_num(matrix[label_col]) > 0) & (y == 0)) if label_col in matrix.columns else np.zeros(len(matrix), bool)
    scores, fold = oof_with_folds(matrix, manifest, cols, y, np.asarray(amb, dtype=bool), repeat=repeat)
    return scores, fold, cols


def org_table_from_refit(matrix: pd.DataFrame, scores: np.ndarray, min_damages: float = 10_000_000.0,
                         include_government: bool = False) -> pd.DataFrame:
    """Reproduce pursuit_dossier.build_priority_list's order from a score vector:
    rollup(drop_excluded) -> case size -> government mask -> damages bar -> sort."""
    from .company_list import rollup
    from .case_size import org_case_size
    from .pursuit_dossier import government_mask
    orgs = rollup(matrix, scores, drop_excluded=True)
    cs = org_case_size(matrix)
    orgs = orgs.merge(cs, on="org_id", how="left") if len(cs) else orgs.assign(case_size_yr=0.0, case_size_capped=False)
    orgs["case_size_yr"] = pd.to_numeric(orgs["case_size_yr"], errors="coerce").fillna(0.0)
    org_col = "org_node_id" if "org_node_id" in matrix.columns else "group_id"
    members = matrix.groupby(matrix[org_col].astype(str))["npi"].apply(lambda s: ";".join(s.astype(str)))
    orgs["member_npis"] = orgs["org_id"].astype(str).map(members).fillna("")
    if not include_government:
        orgs = orgs[~government_mask(orgs["org_name"])]
    orgs = orgs[orgs["case_size_yr"] >= float(min_damages)] if min_damages else orgs
    orgs = orgs.sort_values(["score_dollar_weighted", "score_max"], ascending=False).reset_index(drop=True)
    orgs.insert(0, "priority_rank", orgs.index + 1)
    return orgs


def load_priority_list(run_dir: Path, name: str = "provider_priority_list.csv") -> pd.DataFrame | None:
    p = run_dir / name
    if not p.exists():
        return None
    df = pd.read_csv(p, dtype=str)
    for c in ("priority_rank", "n_providers", "total_billed", "case_size_yr", "score_dollar_weighted",
              "score_max", "n_excluded_members"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "pursuit_rank" in df.columns:                     # the pursuit book: its own order is the rank
        df["priority_rank"] = pd.to_numeric(df["pursuit_rank"], errors="coerce")
    if "priority_rank" not in df.columns:
        df.insert(0, "priority_rank", np.arange(1, len(df) + 1))
    df["member_npis"] = df.get("member_npis", pd.Series("", index=df.index)).fillna("").astype(str)
    return df.sort_values("priority_rank").reset_index(drop=True)


def org_outcomes(orgs: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    """Per listed org: hit (any member a forward positive in the eval universe),
    earliest event lead, members in universe. Orgs with NO member in the eval
    universe stay listed (a hit is impossible for them, which is the truth of the
    list, not a reason to drop the row). Vectorized: explode the rosters once."""
    out = orgs.copy()
    members = out["member_npis"].fillna("").astype(str).str.split(";")
    ex = pd.DataFrame({"_i": np.repeat(np.arange(len(out)), members.str.len().to_numpy()),
                       "npi": np.concatenate(members.to_list()) if len(out) else np.array([], dtype=str)})
    ex = ex[ex["npi"] != ""]
    uni = ev.set_index("npi")
    ex["in_uni"] = ex["npi"].isin(uni.index)
    ex["lead"] = ex["npi"].map(uni.loc[uni["y"] == 1, "lead_months"])
    g = ex.groupby("_i")
    out["hit"] = 0
    out["lead_months"] = np.nan
    out["members_in_universe"] = 0
    if len(ex):
        lead_min = g["lead"].min()
        has = g["lead"].count() > 0
        out.loc[has.index[has.to_numpy()], "hit"] = 1
        out.loc[lead_min.index, "lead_months"] = lead_min.to_numpy()
        n_uni = g["in_uni"].sum()
        out.loc[n_uni.index, "members_in_universe"] = n_uni.to_numpy().astype(int)
    return out


# --------------------------------------------------------------------------- #
# (a) precision at review budgets
# --------------------------------------------------------------------------- #

def budget_rows_org(orgs: pd.DataFrame, budgets, pool: pd.DataFrame | None = None) -> list[dict]:
    """``orgs`` sorted by priority_rank with ``hit``; ``pool`` = the filtered pool the
    list was drawn from (same frame by default) for the random baseline."""
    pool = orgs if pool is None else pool
    pool_n, pool_pos = int(len(pool)), int(pool["hit"].sum())
    rows = []
    score = pd.to_numeric(orgs.get("score_dollar_weighted"), errors="coerce").fillna(-1).to_numpy() \
        if "score_dollar_weighted" in orgs.columns else -orgs["priority_rank"].to_numpy(dtype=float)
    y = orgs["hit"].to_numpy()
    billed = _col(orgs, "total_billed", 0.0).to_numpy()
    size = _col(orgs, "n_providers", 1.0).to_numpy()
    # size-matched baseline: hit rate within (n_providers band x billed decile) cells of the pool
    band = pd.Series(np.digitize(size, [2, 5, 25, 100]))
    dec = pd.Series(pd.qcut(pd.Series(billed).rank(method="first"), 10, labels=False, duplicates="drop")
                    if len(orgs) >= 10 else np.zeros(len(orgs), dtype=int))
    cell = band.astype(str) + "|" + dec.astype(str)
    cell_rate = pd.Series(y).groupby(cell).mean()
    for k in budgets:
        k = int(k)
        if k > len(orgs):
            continue
        exp_hits, exact, n_tied = tie_aware_hits(y, score, k)
        lo, hi = wilson(exact, k)
        cp_lo, cp_hi = clopper_pearson(exact, k)
        base = hypergeom_baseline(k, pool_n, pool_pos, exact)
        dollar_hits = int(y[np.argsort(-billed, kind="stable")[:k]].sum())
        size_matched = float(cell.iloc[:k].map(cell_rate).fillna(y.mean() if len(y) else 0).sum())
        rows.append({"budget": k, "hits": exact, "hits_tie_aware": round(exp_hits, 2), "ties_at_boundary": n_tied,
                     "precision": exact / k, "wilson_lo": lo, "wilson_hi": hi, "cp_lo": cp_lo, "cp_hi": cp_hi,
                     "recall": (exact / pool_pos) if pool_pos else float("nan"),
                     "random_expected": base["expected"], "random_p": base["p_value"],
                     "dollar_ranked_hits": dollar_hits, "size_matched_expected": size_matched,
                     "lift_vs_random": (exact / base["expected"]) if base["expected"] and base["expected"] > 0 else float("nan")})
    return rows


def budget_rows_npi(ev: pd.DataFrame, scores: np.ndarray, budgets, top_fracs) -> list[dict]:
    y = ev["y"].to_numpy()
    n, pos = len(y), int(y.sum())
    rows = []
    ks = [int(b) for b in budgets] + [max(1, int(round(n * f))) for f in top_fracs]
    labels = [f"top {int(b)}" for b in budgets] + [f"top {f:.1%}" for f in top_fracs]
    for lab, k in zip(labels, ks):
        if k > n:
            continue
        exp_hits, exact, n_tied = tie_aware_hits(y, scores, k)
        lo, hi = wilson(exact, k)
        base = hypergeom_baseline(k, n, pos, exact)
        rows.append({"budget": lab, "k": k, "hits": exact, "hits_tie_aware": round(exp_hits, 2),
                     "ties_at_boundary": n_tied, "precision": exact / k, "wilson_lo": lo, "wilson_hi": hi,
                     "recall": exact / pos if pos else float("nan"), "random_expected": base["expected"],
                     "random_p": base["p_value"],
                     "lift_vs_random": (exact / base["expected"]) if base["expected"] else float("nan")})
    return rows


# --------------------------------------------------------------------------- #
# (b) lead time, (c) taxonomy, (d) segments, (f) calibration, (g) stability, (h) dollars
# --------------------------------------------------------------------------- #

def lead_time_rows(orgs_hit: pd.DataFrame, horizons, k: int = 50, gap_months: int = 6) -> dict:
    top = orgs_hit.head(k)
    leads = pd.to_numeric(top.loc[top["hit"] == 1, "lead_months"], errors="coerce").dropna()
    per_h = []
    for h in horizons:
        within = int((leads <= h).sum())
        per_h.append({"horizon_months": int(h), "hits_within": within, "precision_within": within / max(len(top), 1)})
    return {"k": int(len(top)), "n_hits": int(len(leads)),
            "median_lead_months": float(leads.median()) if len(leads) else float("nan"),
            "iqr_lead_months": ([float(leads.quantile(0.25)), float(leads.quantile(0.75))] if len(leads) else [float("nan")] * 2),
            "share_lead_ge_gap": float((leads >= gap_months).mean()) if len(leads) else float("nan"),
            "gap_months": int(gap_months), "per_horizon": per_h}


def visibility(ev: pd.DataFrame, matrix: pd.DataFrame, cutoff: str, active_within: int = 12) -> dict:
    if "last_service_month" not in matrix.columns:
        return {"available": False}
    cut = _cutoff_ts(cutoff)
    last = pd.to_datetime(matrix.set_index(matrix["npi"].astype(str))["last_service_month"].astype(str).str[:7],
                          errors="coerce")
    lm = ev["npi"].map(last)
    gap = _months_between(lm, pd.Series(cut, index=ev.index))
    pos = ev["y"] == 1
    active = lm.notna() & (gap >= 0) & (gap <= active_within)
    return {"available": True, "active_within_months": int(active_within),
            "positives": int(pos.sum()), "positives_active": int((pos & active).sum()),
            "positives_active_share": float((pos & active).sum() / max(int(pos.sum()), 1)),
            "universe_active_share": float(active.mean()) if len(ev) else float("nan")}


def error_taxonomy(orgs_hit: pd.DataFrame, matrix: pd.DataFrame, ks=(50, 250)) -> list[dict]:
    """One cause per org, first match wins (government -> known -> institutional ->
    intermediary -> tiny dollars -> data artifact -> other)."""
    from .pursuit_dossier import government_mask, INTERMEDIARY_SCALE, _INSTITUTIONAL_RE
    from .clean_anchors import INSTITUTIONAL_NAME
    tax = matrix.set_index(matrix["npi"].astype(str)).get("primary_taxonomy")
    assess = matrix.set_index(matrix["npi"].astype(str)).get("assessable")
    rows = []
    for k in ks:
        top = orgs_hit.head(int(k)).copy()
        if not len(top):
            continue
        names = top.get("org_name", pd.Series("", index=top.index)).fillna("").astype(str)
        gov = np.array(government_mask(names).to_numpy(), dtype=bool, copy=True)
        known = np.array((_col(top, "n_excluded_members", 0.0) > 0).to_numpy(), dtype=bool, copy=True)
        n_prov = _col(top, "n_providers", 1.0).to_numpy()
        billed = _col(top, "total_billed", 0.0).to_numpy()
        inst = np.array((names.str.contains(_INSTITUTIONAL_RE, regex=True, na=False)
                         | names.str.contains(INSTITUTIONAL_NAME, na=False)).to_numpy(), dtype=bool, copy=True) | (n_prov >= 25)
        inter = (n_prov == 1) & (billed >= INTERMEDIARY_SCALE)
        capped = top.get("case_size_capped", pd.Series(False, index=top.index)).astype(str).str.lower().isin(["true", "1"]).to_numpy()
        tiny = billed < 250_000                      # cap-pinned rows are their own cause (no damages basis)
        # a writable copy: Arrow-backed string columns hand back READ-ONLY numpy
        # views, and the per-org loop below writes into this mask
        artifact = np.array(names.str.startswith("UNNAMED").to_numpy(), dtype=bool, copy=True)
        if tax is not None and assess is not None:
            for i, s in enumerate(top["member_npis"].fillna("").astype(str)):
                npis = [x for x in s.split(";") if x]
                if npis:
                    a = pd.to_numeric(assess.reindex(npis), errors="coerce").fillna(1)
                    if (a == 0).all():
                        artifact[i] = True
        cause = np.full(len(top), "other", dtype=object)
        for mask, name in ((artifact, "data_artifact"), (capped, "capped_no_basis"), (tiny, "tiny_dollars"),
                           (inter, "intermediary_shaped"), (inst, "institutional"), (known, "already_known"),
                           (gov, "government")):
            cause[mask] = name                      # later assignments win: government first in precedence
        top["cause"] = cause
        for c, g in top.groupby("cause"):
            rows.append({"k": int(k), "cause": c, "n": int(len(g)), "hits": int(g["hit"].sum()),
                         "hit_rate": float(g["hit"].mean())})
    return rows


def segment_rows(ev: pd.DataFrame, matrix: pd.DataFrame, scores: np.ndarray, top_frac: float = 0.01) -> list[dict]:
    from src.enforcement.defendant_resolver import taxonomy_family
    m = matrix.set_index(matrix["npi"].astype(str))
    f = ev.copy()
    f["score"] = scores
    f["state"] = f["npi"].map(m.get("practice_state", pd.Series(dtype=str))).fillna("")
    f["family"] = f["npi"].map(m.get("primary_taxonomy", pd.Series(dtype=str))).fillna("").map(taxonomy_family).replace("", "unknown")
    paid = pd.to_numeric(f["npi"].map(m.get("net_paid", pd.Series(dtype=float))), errors="coerce").fillna(0)
    f["size_band"] = pd.cut(paid, [-np.inf, 1e5, 1e6, 1e7, np.inf], labels=["<100k", "100k-1M", "1M-10M", ">10M"]).astype(str)
    f["grain"] = f["npi"].map(m.get("row_grain", pd.Series(dtype=str))).fillna("billing")
    global_rate = float(f["y"].mean()) if len(f) else 0.0
    k_frac = max(1, int(round(len(f) * top_frac)))
    top_global = set(f.sort_values("score", ascending=False).head(k_frac)["npi"])
    rows = []
    for dim in ("state", "family", "size_band", "grain"):
        for val, g in f.groupby(dim):
            if len(g) < 200:
                continue
            k = max(1, int(round(len(g) * top_frac)))
            hits_in = int(g.sort_values("score", ascending=False).head(k)["y"].sum())
            rate = float(g["y"].mean())
            lift = (hits_in / k) / rate if rate > 0 else float("nan")
            shr = eb_shrunk_rate(hits_in, k, global_rate) / global_rate if global_rate > 0 else float("nan")
            rows.append({"dimension": dim, "segment": str(val), "n": int(len(g)), "positives": int(g["y"].sum()),
                         "top_k": k, "hits_top": hits_in, "precision_top": hits_in / k, "lift": lift,
                         "lift_shrunk": shr, "share_of_global_top": float(g["npi"].isin(top_global).mean()),
                         "flag": "shrunk lift < 1" if (shr == shr and shr < 1) else ""})
    return rows


def crossfit_calibration(ev: pd.DataFrame, scores: np.ndarray, fold: np.ndarray, orgs: pd.DataFrame | None,
                         k_org: int = 50) -> dict:
    """Isotonic fit on four folds' OOF scores vs the FORWARD label, applied to the fifth."""
    from .calibration import fit_calibrator
    from .fdr import expected_false_discoveries, fdr_threshold
    y = ev["y"].to_numpy()
    s = np.asarray(scores, dtype=float)
    p = np.full(len(s), np.nan)
    folds = [f for f in np.unique(fold) if f >= 0]
    if len(folds) >= 2 and y.sum() >= 5:
        for f in folds:
            te = fold == f
            tr = ~te
            if y[tr].sum() == 0:
                p[te] = float(y[tr].mean())
                continue
            cal = fit_calibrator(np.nan_to_num(s[tr], nan=0.0), y[tr])
            p[te] = cal.predict(np.nan_to_num(s[te], nan=0.0))
    else:
        p[:] = float(y.mean()) if len(y) else 0.0
    order = np.argsort(-np.nan_to_num(s, nan=-np.inf), kind="stable")
    rank = np.empty(len(s), dtype=int)
    rank[order] = np.arange(1, len(s) + 1)
    bins = []
    for lo, hi in RANK_BINS:
        sel = (rank >= lo) & ((rank <= hi) if hi else True)
        if sel.sum() == 0:
            continue
        bins.append({"rank_bin": f"{lo}-{hi or len(s)}", "n": int(sel.sum()), "mean_calibrated_p": float(np.nanmean(p[sel])),
                     "observed_rate": float(y[sel].mean()), "gap": float(np.nanmean(p[sel]) - y[sel].mean())})
    top_n = min(1000, len(s))
    top_sel = rank <= top_n
    ece_top = float(np.nansum([abs(b["gap"]) * b["n"] for b in bins if int(b["rank_bin"].split("-")[0]) <= top_n]) / max(top_sel.sum(), 1)) if bins else float("nan")
    out = {"rank_bins": bins, "ece_top1000": ece_top, "n_positives": int(y.sum())}
    if orgs is not None and len(orgs):
        pmap = dict(zip(ev["npi"], np.nan_to_num(p, nan=0.0)))
        p_any = []
        for s_ in orgs["member_npis"].fillna("").astype(str):
            ps = [pmap[n] for n in s_.split(";") if n in pmap]
            p_any.append(1.0 - float(np.prod([1 - x for x in ps])) if ps else 0.0)
        p_any = np.asarray(p_any)
        topk = p_any[: min(k_org, len(p_any))]          # the list's own order, not re-sorted
        out["org_top"] = {"k": int(len(topk)), "expected_true": float(topk.sum()),
                          "expected_false": float((1 - topk).sum()),
                          "observed_hits": int(orgs.head(k_org)["hit"].sum()) if "hit" in orgs.columns else None}
        out["fdr_bounded_list"] = fdr_threshold(p_any, target=0.2)
        out["expected_discoveries_sorted"] = expected_false_discoveries(p_any, k=k_org)
    return out


def _log(msg: str) -> None:
    import datetime as _dt
    print(f"[accuracy_diagnostics {_dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def dollar_recall(case_db: pd.DataFrame, cutoff: str, org_nodes: pd.DataFrame, npi_to_org: pd.DataFrame,
                  ev_universe: set, orgs: pd.DataFrame, budgets, defendant_npis=None,
                  horizon_months: int | None = None, case_labels: pd.DataFrame | None = None) -> dict:
    """Per CASE (deduplicated): settlement dollars whose provider sits in the top-k
    orgs, over the dollars resolvable to the eval universe at all. ``case_labels`` =
    a prebuilt build_case_labels frame (the resolver + fuzzy pass over the whole case
    DB takes minutes on a national graph; build it once, pass it everywhere)."""
    from .case_labels import build_case_labels
    lab = case_labels if case_labels is not None else \
        build_case_labels(case_db, org_nodes, npi_to_org, defendant_npis=defendant_npis)
    if lab is None or not len(lab):
        return {"available": False}
    cases = case_db.drop_duplicates("case_id").set_index(case_db.drop_duplicates("case_id")["case_id"].astype(str))
    ann = pd.to_datetime(cases.get("announced_date"), errors="coerce")
    amt = pd.to_numeric(cases.get("amount_usd"), errors="coerce").fillna(0.0)
    cut = _cutoff_ts(cutoff)
    end = cut + pd.DateOffset(months=int(horizon_months)) if horizon_months else None
    case_npis: dict[str, set] = {}
    for r in lab.itertuples():
        if str(r.npi) not in ev_universe:
            continue
        for cid in str(r.case_ids).split(";"):
            if cid in ann.index and pd.notna(ann[cid]) and ann[cid] > cut and (end is None or ann[cid] <= end):
                case_npis.setdefault(cid, set()).add(str(r.npi))
    eligible = {cid: float(amt.get(cid, 0.0)) for cid in case_npis}
    total = sum(eligible.values())
    all_post = {cid for cid in ann.index if pd.notna(ann[cid]) and ann[cid] > cut and (end is None or ann[cid] <= end)}
    unresolvable = float(sum(amt.get(c, 0.0) for c in all_post if c not in eligible))
    rows = []
    for k in budgets:
        k = int(k)
        top = orgs.head(k)
        top_npis = set()
        for s in top["member_npis"].fillna("").astype(str):
            top_npis.update(x for x in s.split(";") if x)
        caught = sum(eligible[cid] for cid, ns in case_npis.items() if ns & top_npis)
        n_cases = sum(1 for cid, ns in case_npis.items() if ns & top_npis)
        rows.append({"budget": k, "cases_touched": n_cases, "dollars_touched": caught,
                     "dollar_recall": (caught / total) if total else float("nan")})
    return {"available": True, "eligible_cases": len(eligible), "eligible_dollars": total,
            "post_cutoff_dollars_unresolvable_to_universe": unresolvable, "rows": rows}


def _fast_org_top(matrix: pd.DataFrame, scores: np.ndarray, k_org: int) -> set:
    """Top-k orgs by the dollar-weighted mean member score (pandas groupby sums; the
    dossier's per-org Python rollup is minutes per pass on 500k orgs and is not needed
    to measure how much the top set moves between fold assignments)."""
    org_col = "org_node_id" if "org_node_id" in matrix.columns else "group_id"
    paid = pd.to_numeric(matrix.get("net_paid", matrix.get("gross_paid", 0)), errors="coerce").fillna(0.0).clip(lower=0)
    sc = pd.Series(np.nan_to_num(scores, nan=0.0))
    df = pd.DataFrame({"org": matrix[org_col].astype(str).to_numpy(), "sp": (sc * paid).to_numpy(), "p": paid.to_numpy(),
                       "s": sc.to_numpy()})
    g = df.groupby("org").agg(sp=("sp", "sum"), p=("p", "sum"), s=("s", "mean"))
    g["w"] = np.where(g["p"] > 0, g["sp"] / g["p"].replace(0, np.nan), g["s"])
    return set(g["w"].sort_values(ascending=False).head(k_org).index)


def stability(matrix: pd.DataFrame, manifest: dict, cols: list[str], ev_by_ruler: dict, repeats: int,
              k_org: int = 50, min_damages: float = 10_000_000.0, top_frac: float = 0.01,
              base: tuple[np.ndarray, np.ndarray] | None = None) -> dict:
    """R re-shuffled fold assignments: SD of precision@k, Jaccard of the top sets.
    ``base`` = (scores, fold) of the main refit, reused as repeat 0."""
    if repeats <= 0:
        return {"repeats": 0}
    npi_sets, org_sets, prec = [], [], {r: [] for r in ev_by_ruler}
    org_freq: dict[str, int] = {}
    org_col = "org_node_id" if "org_node_id" in matrix.columns else "group_id"
    members = matrix.groupby(matrix[org_col].astype(str))["npi"].apply(lambda s_: ";".join(s_.astype(str)))
    for rep in range(repeats + 1):
        if rep == 0 and base is not None:
            scores = base[0]
        else:
            _log(f"stability: refit {rep} of {repeats}")
            scores, _, _ = refit_scores(matrix, manifest, repeat=rep, cols=cols)
        k = max(1, int(round(len(matrix) * top_frac)))
        order = np.argsort(-np.nan_to_num(scores, nan=-np.inf), kind="stable")
        npi_sets.append(set(matrix["npi"].astype(str).to_numpy()[order[:k]]))
        top = _fast_org_top(matrix, scores, k_org)
        org_sets.append(top)
        for o in top:
            org_freq[o] = org_freq.get(o, 0) + 1
        orgs = pd.DataFrame({"org_id": sorted(top)})
        orgs["member_npis"] = orgs["org_id"].map(members).fillna("")
        for name, ev in ev_by_ruler.items():
            oh = org_outcomes(orgs, ev)
            prec[name].append(float(oh["hit"].mean()) if len(oh) else float("nan"))

    def _jac(sets):
        vals = []
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                u = sets[i] | sets[j]
                vals.append(len(sets[i] & sets[j]) / len(u) if u else 1.0)
        return float(np.mean(vals)) if vals else float("nan")
    return {"repeats": int(repeats), "jaccard_top_npi": _jac(npi_sets), "jaccard_top_org": _jac(org_sets),
            "precision_sd": {r: float(np.nanstd(v)) for r, v in prec.items()},
            "precision_mean": {r: float(np.nanmean(v)) for r, v in prec.items()},
            "orgs_in_every_top": int(sum(1 for v in org_freq.values() if v == repeats + 1)),
            "orgs_ever_in_top": int(len(org_freq)),
            "note": "org sets here are the top-k by dollar-weighted mean score over ALL orgs (no "
                    "government / damages filter), a stability measure only"}


def tie_block(matrix: pd.DataFrame, scores: np.ndarray, ev: pd.DataFrame, max_rows: int = 2000) -> pd.DataFrame:
    """The providers sharing the MAXIMUM score (the v6 / v7 head: 219 and 285 rows at
    one value, no future case among them), with the columns that explain a tie: how
    many features they carry, their grain, size, state, taxonomy, org. Written to
    accuracy_tie_block.csv so the block can be read instead of guessed at."""
    s = np.nan_to_num(np.asarray(scores, dtype=float), nan=-np.inf)
    top = s.max() if len(s) else 0.0
    idx = np.where(s >= top - 1e-12)[0][:max_rows]
    cols = [c for c in ("npi", "org_node_id", "org_name", "practice_state", "primary_taxonomy", "entity_type",
                        "row_grain", "net_paid", "n_distinct_hcpcs", "n_active_months", "tenure_months",
                        "assessable", "exclusion_label_sources") if c in matrix.columns]
    tb = matrix.iloc[idx][cols].copy()
    tb.insert(0, "score", s[idx])
    num = matrix.select_dtypes(include=[np.number])
    tb["n_nonnull_numeric"] = num.iloc[idx].notna().sum(axis=1).to_numpy()
    uni = ev.set_index("npi")["y"]
    tb["forward_positive"] = tb["npi"].astype(str).map(uni).fillna(-1).astype(int)   # -1 = not in the eval universe
    return tb.reset_index(drop=True)


def sanity(ev: pd.DataFrame, scores: np.ndarray | None, fold: np.ndarray | None, matrix: pd.DataFrame, k: int) -> dict:
    out = {}
    if scores is not None:
        s = np.asarray(scores, dtype=float)
        out["nan_scores"] = int(np.isnan(s).sum())
        order = np.argsort(-np.nan_to_num(s, nan=-np.inf), kind="stable")
        kk = min(k, len(s))
        out["ties_at_k"] = int((s == s[order[kk - 1]]).sum()) if kk else 0
        out["distinct_scores"] = int(len(np.unique(np.round(s[~np.isnan(s)], 10))))
        if fold is not None and kk:
            fs = pd.Series(fold[order[:kk]]).value_counts(normalize=True)
            out["fold_shares_top_k"] = {int(i): round(float(v), 3) for i, v in fs.items()}
    if "group_id" in matrix.columns:
        vc = matrix["group_id"].astype(str).value_counts(normalize=True)
        out["largest_group_share"] = float(vc.iloc[0]) if len(vc) else 0.0
        out["largest_group"] = str(vc.index[0]) if len(vc) else ""
    return out


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #

def run_diagnostics(matrix: pd.DataFrame, manifest: dict, rulers: dict[str, pd.DataFrame], cutoff: str,
                    priority_list: pd.DataFrame | None = None, score_source: str = "refit",
                    budgets_org=DEFAULT_ORG_BUDGETS, budgets_npi=DEFAULT_NPI_BUDGETS, top_fracs=DEFAULT_TOP_FRACS,
                    horizons=DEFAULT_HORIZONS, horizon_months: int | None = None, gap_months: int = 6,
                    active_within: int = 12, min_damages: float = 10_000_000.0, repeats: int = 0,
                    case_db: pd.DataFrame | None = None, org_nodes: pd.DataFrame | None = None,
                    npi_to_org: pd.DataFrame | None = None, defendant_npis: pd.DataFrame | None = None,
                    headline: str | None = None) -> dict:
    res: dict = {"cutoff": cutoff, "horizon_months": horizon_months, "score_source": score_source,
                 "rulers": {}, "n_matrix": int(len(matrix))}
    names = list(rulers)
    headline = headline or ("fca" if "fca" in rulers else ("hard" if "hard" in rulers else names[0]))
    res["headline_ruler"] = headline
    _log(f"{len(matrix):,} matrix rows; rulers {names}; headline {headline}; score source {score_source}")
    # eval frames per ruler (ambiguous = positive under another ruler)
    ev_by: dict[str, pd.DataFrame] = {}
    for name in names:
        others = [rulers[o] for o in names if o != name]
        ev, st = eval_frame(matrix, rulers[name], cutoff, horizon_months=horizon_months, other_rulers=others)
        ev_by[name] = ev
        res["rulers"][name] = {"universe": st}
    # scores
    scores = fold = None
    cols: list[str] = []
    if score_source == "refit" or priority_list is None:
        _log("refit: out-of-fold scores (5 grouped folds, LightGBM)")
        scores, fold, cols = refit_scores(matrix, manifest)
        _log("refit: rolling up to organizations the dossier's way (minutes on a national matrix)")
        orgs = org_table_from_refit(matrix, scores, min_damages=min_damages)
        res["org_list_source"] = "refit (company_list policy, rolled up like the dossier)"
    else:
        orgs = priority_list
        res["org_list_source"] = "provider_priority_list.csv (the shipped order; no training)"
        if score_source == "priority_list+refit":
            _log("refit: out-of-fold scores for the NPI-grain tables (5 grouped folds, LightGBM)")
            scores, fold, cols = refit_scores(matrix, manifest)
    case_labels = None
    if case_db is not None and org_nodes is not None and npi_to_org is not None:
        from .case_labels import build_case_labels
        _log("case labels: resolving the case DB to NPIs once (shared by the dollar-recall tables)")
        case_labels = build_case_labels(case_db, org_nodes, npi_to_org, defendant_npis=defendant_npis)
    res["n_orgs_listed"] = int(len(orgs))
    top_rows = None
    for name, ev in ev_by.items():
        _log(f"ruler {name}: budgets, lead time, taxonomy, segments, calibration")
        block = res["rulers"][name]
        oh = org_outcomes(orgs, ev)
        block["org_budgets"] = budget_rows_org(oh, budgets_org)
        block["lead_time"] = lead_time_rows(oh, horizons, k=min(50, len(oh)) or 1, gap_months=gap_months)
        block["visibility"] = visibility(ev, matrix, cutoff, active_within)
        block["error_taxonomy"] = error_taxonomy(oh, matrix)
        # eval-side label noise: drop the ambiguous negatives
        if ev["ambiguous"].any():
            ev2 = ev[~ev["ambiguous"]].reset_index(drop=True)
            oh2 = org_outcomes(orgs, ev2)
            block["org_budgets_without_ambiguous"] = budget_rows_org(oh2, budgets_org)
        if scores is not None:
            s_ev = ev["npi"].map(dict(zip(matrix["npi"].astype(str), scores))).to_numpy(dtype=float)
            block["npi_budgets"] = budget_rows_npi(ev, s_ev, budgets_npi, top_fracs)
            block["segments"] = segment_rows(ev, matrix, s_ev)
            f_ev = ev["npi"].map(dict(zip(matrix["npi"].astype(str), fold))).fillna(-1).to_numpy(dtype=int)
            block["calibration"] = crossfit_calibration(ev, s_ev, f_ev, oh)
            block["sanity"] = sanity(ev, s_ev, f_ev, matrix, k=max(1, int(round(len(ev) * 0.01))))
        else:
            block["sanity"] = sanity(ev, None, None, matrix, k=50)
        if case_db is not None and org_nodes is not None and npi_to_org is not None:
            block["dollars"] = dollar_recall(case_db, cutoff, org_nodes, npi_to_org, set(ev["npi"]), oh, budgets_org,
                                             defendant_npis=defendant_npis, horizon_months=horizon_months,
                                             case_labels=case_labels)
        if name == headline:
            top_rows = oh.head(250).copy()
            top_rows.insert(0, "ruler", name)
            if scores is not None:
                res["_tie_block"] = tie_block(matrix, scores, ev)
    if repeats and cols:
        res["stability"] = stability(matrix, manifest, cols, ev_by, repeats, min_damages=min_damages,
                                     base=(scores, fold) if scores is not None else None)
    _log("done")
    tie = res.pop("_tie_block", None)
    res["kpis"] = kpi_block(res)
    res["_top_rows"] = top_rows
    res["_tie_block"] = tie
    return res


def kpi_block(res: dict) -> dict:
    """The acc_* fields the registry stores (flat, enforceable with --check)."""
    h = res.get("headline_ruler")
    out = {"acc_eval_policy": h, "acc_horizon_mo": res.get("horizon_months"),
           "acc_score_source": res.get("score_source"), "acc_n_orgs_listed": res.get("n_orgs_listed")}

    def _b(name, k):
        rows = res["rulers"].get(name, {}).get("org_budgets", [])
        return next((r for r in rows if r["budget"] == k), None)
    for name in res["rulers"]:
        for k in (16, 50):
            r = _b(name, k)
            if r:
                out[f"acc_org_hits{k}_{name}"] = r["hits"]
                out[f"acc_org_prec{k}_{name}"] = r["precision"]
                out[f"acc_org_prec{k}_{name}_lo"] = r["wilson_lo"]
                out[f"acc_base{k}_{name}"] = r["random_expected"] / k if k else None
                out[f"acc_org_p{k}_vs_random_{name}"] = r["random_p"]
                out[f"acc_org_lift{k}_vs_dollar_{name}"] = (r["hits"] / r["dollar_ranked_hits"]) if r["dollar_ranked_hits"] else None
        lt = res["rulers"][name].get("lead_time", {})
        out[f"acc_lead_median_mo_{name}"] = lt.get("median_lead_months")
        out[f"acc_hits_lead_ge_gap_share_{name}"] = lt.get("share_lead_ge_gap")
        nb = res["rulers"][name].get("npi_budgets", [])
        r1 = next((r for r in nb if r["budget"] == "top 1.0%"), None)
        if r1:
            out[f"acc_npi_prec1pct_{name}"] = r1["precision"]
            out[f"acc_npi_prec1pct_{name}_lo"] = r1["wilson_lo"]
            out[f"acc_npi_lift1pct_{name}"] = r1["lift_vs_random"]
        tx = res["rulers"][name].get("error_taxonomy", [])
        n50 = sum(r["n"] for r in tx if r["k"] == 50) or 1
        out[f"acc_inst_share50_{name}"] = sum(r["n"] for r in tx if r["k"] == 50 and r["cause"] == "institutional") / n50
        out[f"acc_known_share50_{name}"] = sum(r["n"] for r in tx if r["k"] == 50 and r["cause"] == "already_known") / n50
        cal = res["rulers"][name].get("calibration", {})
        out[f"acc_ece_top1000_{name}"] = cal.get("ece_top1000")
        dl = res["rulers"][name].get("dollars", {})
        if dl.get("available"):
            r50 = next((r for r in dl["rows"] if r["budget"] == 50), None)
            out[f"acc_dollar_recall50_{name}"] = r50["dollar_recall"] if r50 else None
    # headline aliases (what --check reads by default)
    for key in list(out):
        if h and key.endswith(f"_{h}"):
            out[key[: -len(h) - 1]] = out[key]
        if h and key.endswith(f"_{h}_lo"):
            out[key[: -len(h) - 4] + "_lo"] = out[key]
    st = res.get("stability", {})
    out["acc_top50_jaccard"] = st.get("jaccard_top_org")
    out["acc_prec50_sd"] = (st.get("precision_sd", {}) or {}).get(h)
    return out


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

def _f(v, fmt="{:.3f}"):
    try:
        if v is None or (isinstance(v, float) and v != v):
            return "n/a"
        return fmt.format(v)
    except Exception:
        return str(v)


def to_markdown(res: dict, run_tag: str = "") -> str:
    h = res["headline_ruler"]
    L = [f"# ACCURACY DIAGNOSTICS - {run_tag}".rstrip(" -"), "", _DISCLAIMER, "",
         "## 0. Verdict in plain words", ""]
    hb = res["rulers"].get(h, {})
    r50 = next((r for r in hb.get("org_budgets", []) if r["budget"] == 50), None)
    r16 = next((r for r in hb.get("org_budgets", []) if r["budget"] == 16), None)
    lt = hb.get("lead_time", {})
    if r50:
        L.append(f"Under the **{h}** ruler (the headline), the first 50 organizations on the list held "
                 f"**{r50['hits']}** that a later public enforcement record touched "
                 f"(precision {_f(r50['precision'], '{:.1%}')}, 95% interval {_f(r50['wilson_lo'], '{:.1%}')} to "
                 f"{_f(r50['wilson_hi'], '{:.1%}')}). Picking 50 at random from the same filtered pool would "
                 f"have found about {_f(r50['random_expected'], '{:.1f}')} (one-sided p {_f(r50['random_p'])}); "
                 f"ranking by dollars alone would have found {r50['dollar_ranked_hits']}.")
    if r16:
        L.append(f"The first 16 (one PI round) held {r16['hits']} (interval {_f(r16['wilson_lo'], '{:.0%}')} to "
                 f"{_f(r16['wilson_hi'], '{:.0%}')}): a single round cannot tell a 3x model from chance, "
                 f"which is why the deeper budgets are the gate.")
    if lt.get("n_hits"):
        L.append(f"Lead time for those hits: median {_f(lt['median_lead_months'], '{:.0f}')} months after the freeze; "
                 f"{_f(lt['share_lead_ge_gap'], '{:.0%}')} of them at least {lt['gap_months']} months out "
                 f"(the ones a relator could have reached first).")
    dl = hb.get("dollars", {})
    if dl.get("available"):
        d50 = next((r for r in dl["rows"] if r["budget"] == 50), None)
        if d50:
            L.append(f"Settlement dollars: the top 50 touched {d50['cases_touched']} of {dl['eligible_cases']} "
                     f"post-cutoff cases resolvable to the universe, {_f(d50['dollar_recall'], '{:.0%}')} of their "
                     f"dollars (${dl['eligible_dollars']:,.0f} eligible; ${dl['post_cutoff_dollars_unresolvable_to_universe']:,.0f} "
                     f"more announced after the cutoff could not be tied to any scored provider).")
    L += ["", "## 1. Run identity and rulers", "",
          f"- cutoff {res['cutoff']}; horizon {res.get('horizon_months') or 'none (every later event counts)'}; "
          f"scores from {res.get('org_list_source')}; {res.get('n_orgs_listed'):,} organizations listed",
          "", "| ruler | matrix rows | dropped known at freeze | dropped undated | beyond horizon | positives | eval rows | ambiguous negatives |",
          "|---|--:|--:|--:|--:|--:|--:|--:|"]
    for name, b in res["rulers"].items():
        u = b["universe"]
        L.append(f"| {name}{' (headline)' if name == h else ''} | {u['n_matrix']:,} | {u['dropped_known']:,} | {u['dropped_undated']:,} | "
                 f"{u['dropped_beyond_horizon']:,} | {u['positives']:,} | {u['n_eval']:,} | {u['ambiguous_negatives']:,} |")
    L += ["", "_Known at the freeze = a pre-cutoff record under the ruler, an in-matrix positive, or a DOJ case "
          "the export resolved before the cutoff. Ambiguous = positive under another ruler only; section 6 "
          "re-runs the budgets without them._"]
    L += ["", "## 2. Precision at review budgets (organizations, the shipped grain)", ""]
    for name, b in res["rulers"].items():
        L += [f"### {name}", "", "| budget | hits | tie-aware | ties at k | precision | Wilson 95% | exact 95% | recall | random expected | p vs random | dollar-ranked hits | size-matched expected |",
              "|--:|--:|--:|--:|--:|---|---|--:|--:|--:|--:|--:|"]
        for r in b.get("org_budgets", []):
            L.append(f"| {r['budget']} | {r['hits']} | {r['hits_tie_aware']} | {r['ties_at_boundary']} | {_f(r['precision'], '{:.1%}')} | "
                     f"{_f(r['wilson_lo'], '{:.1%}')} to {_f(r['wilson_hi'], '{:.1%}')} | {_f(r['cp_lo'], '{:.1%}')} to {_f(r['cp_hi'], '{:.1%}')} | "
                     f"{_f(r['recall'], '{:.1%}')} | {_f(r['random_expected'], '{:.1f}')} | {_f(r['random_p'])} | {r['dollar_ranked_hits']} | "
                     f"{_f(r['size_matched_expected'], '{:.1f}')} |")
        nb = b.get("npi_budgets")
        if nb:
            L += ["", f"NPI grain under {name} (refit scores):", "", "| budget | k | hits | ties at k | precision | Wilson 95% | recall | random expected | lift vs random |",
                  "|---|--:|--:|--:|--:|---|--:|--:|--:|"]
            for r in nb:
                L.append(f"| {r['budget']} | {r['k']:,} | {r['hits']} | {r['ties_at_boundary']} | {_f(r['precision'], '{:.2%}')} | "
                         f"{_f(r['wilson_lo'], '{:.2%}')} to {_f(r['wilson_hi'], '{:.2%}')} | {_f(r['recall'], '{:.1%}')} | "
                         f"{_f(r['random_expected'], '{:.1f}')} | {_f(r['lift_vs_random'], '{:.2f}')} |")
        L.append("")
    L += ["## 3. Lead time (how far ahead of the public record the top 50 were)", ""]
    for name, b in res["rulers"].items():
        lt = b.get("lead_time", {})
        vis = b.get("visibility", {})
        L.append(f"- **{name}**: {lt.get('n_hits', 0)} hits in the top {lt.get('k')}; median lead "
                 f"{_f(lt.get('median_lead_months'), '{:.0f}')} months (IQR {_f((lt.get('iqr_lead_months') or [None])[0], '{:.0f}')} to "
                 f"{_f((lt.get('iqr_lead_months') or [None, None])[1], '{:.0f}')}); share with lead >= {lt.get('gap_months')} months "
                 f"{_f(lt.get('share_lead_ge_gap'), '{:.0%}')}"
                 + (f"; positives still billing within {vis['active_within_months']} months of the freeze: "
                    f"{_f(vis['positives_active_share'], '{:.0%}')} (universe {_f(vis['universe_active_share'], '{:.0%}')})"
                    if vis.get("available") else ""))
        if lt.get("per_horizon"):
            L.append("  - " + "; ".join(f"within {r['horizon_months']} mo: {r['hits_within']} hits ({_f(r['precision_within'], '{:.1%}')})"
                                        for r in lt["per_horizon"]))
    L += ["", "## 4. What the top of the list is made of (error taxonomy)", "",
          "| ruler | k | cause | n | hits | hit rate |", "|---|--:|---|--:|--:|--:|"]
    for name, b in res["rulers"].items():
        for r in b.get("error_taxonomy", []):
            L.append(f"| {name} | {r['k']} | {r['cause']} | {r['n']} | {r['hits']} | {_f(r['hit_rate'], '{:.1%}')} |")
    L += ["", "_Causes in precedence order: government, already known (a member excluded before the freeze), "
          "institutional (hospital / system name or 25+ providers), intermediary-shaped (one NPI at fiscal-agent "
          "scale), tiny dollars (under $250k billed), capped / no basis (the case size sits at the 40%-of-billed "
          "ceiling: no scheme-specific damages basis), data artifact (unnamed or no assessable member), other._", ""]
    any_seg = any(b.get("segments") for b in res["rulers"].values())
    if any_seg:
        L += ["## 5. Segments (NPI grain, top 1% within the segment; lift shrunk toward the global rate)", "",
              "| ruler | dimension | segment | n | positives | hits in top | precision | lift | shrunk lift | flag |",
              "|---|---|---|--:|--:|--:|--:|--:|--:|---|"]
        for name, b in res["rulers"].items():
            for r in sorted(b.get("segments", []), key=lambda r: (r["dimension"], -r["n"]))[:60]:
                L.append(f"| {name} | {r['dimension']} | {r['segment']} | {r['n']:,} | {r['positives']} | {r['hits_top']} | "
                         f"{_f(r['precision_top'], '{:.2%}')} | {_f(r['lift'], '{:.2f}')} | {_f(r['lift_shrunk'], '{:.2f}')} | {r['flag']} |")
        L.append("")
    L += ["## 6. Label-noise sensitivity (eval side: without the ambiguous negatives)", ""]
    for name, b in res["rulers"].items():
        rows = b.get("org_budgets_without_ambiguous")
        if not rows:
            L.append(f"- {name}: no ambiguous negatives under this ruler")
            continue
        base = {r["budget"]: r for r in b.get("org_budgets", [])}
        L.append(f"- {name}: " + "; ".join(f"@{r['budget']} {base.get(r['budget'], {}).get('hits', '?')} -> {r['hits']} hits "
                                            f"({_f(r['precision'], '{:.1%}')})" for r in rows))
    any_cal = any(b.get("calibration") for b in res["rulers"].values())
    if any_cal:
        L += ["", "## 7. Calibration at the top (cross-fitted isotonic on the forward label)", "",
              "| ruler | rank bin | n | mean calibrated p | observed rate | gap |", "|---|---|--:|--:|--:|--:|"]
        for name, b in res["rulers"].items():
            c = b.get("calibration", {})
            for r in c.get("rank_bins", []):
                L.append(f"| {name} | {r['rank_bin']} | {r['n']:,} | {_f(r['mean_calibrated_p'], '{:.4f}')} | "
                         f"{_f(r['observed_rate'], '{:.4f}')} | {_f(r['gap'], '{:+.4f}')} |")
            ot = c.get("org_top")
            if ot:
                L.append(f"\n- {name}: top {ot['k']} orgs by the list's order, P(any member) summed: expected "
                         f"{_f(ot['expected_true'], '{:.1f}')} true / {_f(ot['expected_false'], '{:.1f}')} false discoveries; "
                         f"observed {ot['observed_hits']}; FDR-bounded (20%) list size {c.get('fdr_bounded_list', {}).get('k')}")
    st = res.get("stability", {})
    if st.get("repeats"):
        L += ["", "## 8. Stability under re-shuffled folds", "",
              f"- {st['repeats']} extra fold assignments: Jaccard of the top-1% NPI set {_f(st['jaccard_top_npi'])}, "
              f"of the top-50 org set {_f(st['jaccard_top_org'])}; {st['orgs_in_every_top']} orgs in every top 50, "
              f"{st['orgs_ever_in_top']} ever; precision@50 SD by ruler: "
              + ", ".join(f"{k} {_f(v)}" for k, v in st["precision_sd"].items())]
    any_d = any(b.get("dollars", {}).get("available") for b in res["rulers"].values())
    if any_d:
        L += ["", "## 9. Settlement-dollar recall (per case, deduplicated)", "",
              "| ruler | budget | cases touched | dollars touched | dollar recall |", "|---|--:|--:|--:|--:|"]
        for name, b in res["rulers"].items():
            d = b.get("dollars", {})
            if d.get("available"):
                for r in d["rows"]:
                    L.append(f"| {name} | {r['budget']} | {r['cases_touched']} | ${r['dollars_touched']:,.0f} | {_f(r['dollar_recall'], '{:.0%}')} |")
    L += ["", "## 10. Interval design and sanity", "",
          "- Wilson and exact (Clopper-Pearson) intervals treat each listed organization as one draw; the random "
          "baseline is hypergeometric from the same filtered pool; a cluster design effect is not applied at org "
          "grain because the organization IS the cluster. NPI-grain intervals are optimistic for ring members "
          "(dependence through shared owners); read them with the stability section.", ""]
    for name, b in res["rulers"].items():
        s = b.get("sanity", {})
        if s:
            L.append(f"- {name}: " + ", ".join(f"{k} {v}" for k, v in s.items()))
    L += ["", "## 11. KPI block (accuracy_diagnostics.json)", "", "```"]
    for k, v in res["kpis"].items():
        L.append(f"{k} = {_f(v, '{:.4f}') if isinstance(v, float) else v}")
    L += ["```", ""]
    return "\n".join(L)


def _discover_rulers(run_dir: Path, cutoff: str) -> dict[str, Path]:
    out = {}
    base = run_dir / f"future_bans_after_{cutoff}.csv"
    if base.exists():
        out["all"] = base
    for tag in ("hard", "hard_weak"):
        p = run_dir / f"future_bans_after_{cutoff}__{tag}.csv"
        if p.exists():
            out[tag] = p
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, help="run folder or @champion")
    ap.add_argument("--forward-label", nargs="*", default=None,
                    help="NAME=PATH rulers (default: discover future_bans_after_<cutoff>[__hard].csv)")
    ap.add_argument("--cutoff", default=None, help="YYYY-MM (default: manifest asof_cutoff)")
    ap.add_argument("--case-db", default=None, help="enforcement/doj_cases_v3.csv: adds the FCA ruler + dollar recall")
    ap.add_argument("--graph-dir", default=None, help="FULL graph root (org_nodes + npi_to_org) for --case-db")
    ap.add_argument("--defendant-npis", default=None)
    ap.add_argument("--score-source", default="priority_list+refit", choices=["priority_list", "refit", "priority_list+refit"],
                    help="priority_list = the shipped CSV order only; refit = rebuild the scorer OOF; "
                         "priority_list+refit = shipped org order plus refit NPI tables (default)")
    ap.add_argument("--label-tier", default="all", choices=["all", "hard", "hard+weak"])
    ap.add_argument("--exclusion-nodes", default=None, help="for --label-tier (the full graph's exclusion nodes)")
    ap.add_argument("--budgets-org", default="16,50,100,250")
    ap.add_argument("--budgets-npi", default="16,50,250,1000")
    ap.add_argument("--top-fracs", default="0.001,0.01,0.1")
    ap.add_argument("--horizons", default="6,12,24,36,60")
    ap.add_argument("--horizon-months", type=int, default=None, help="box every ruler to (cutoff, cutoff+h]")
    ap.add_argument("--gap-months", type=int, default=6)
    ap.add_argument("--active-within", type=int, default=12)
    ap.add_argument("--min-damages", type=float, default=10_000_000.0)
    ap.add_argument("--repeats", type=int, default=0, help="extra re-shuffled fold assignments for stability")
    ap.add_argument("--list-name", default="provider_priority_list.csv",
                    help="which shipped list to grade: provider_priority_list.csv (default) or "
                         "provider_priority_list_pursuit.csv (the operator book)")
    ap.add_argument("--out", default=None, help="default <run-dir>/ACCURACY_DIAGNOSTICS.md")
    a = ap.parse_args()

    from .run_registry import resolve_run_dir
    run_dir = Path(resolve_run_dir(a.run_dir))
    manifest = json.loads((run_dir / "feature_manifest.json").read_text(encoding="utf-8"))
    cutoff = a.cutoff or manifest.get("asof_cutoff") or (manifest.get("graph_substrate") or {}).get("asof")
    if not cutoff:
        ap.error("no cutoff: pass --cutoff (the manifest carries none; an in-time run has no forward ruler)")
    matrix = pd.read_parquet(run_dir / "provider_features_for_model.parquet")
    matrix["npi"] = matrix["npi"].astype(str)
    if a.label_tier != "all":
        from .company_list import set_label_policy
        set_label_policy(a.label_tier, a.exclusion_nodes, cutoff=cutoff)
    rulers: dict[str, pd.DataFrame] = {}
    if a.forward_label:
        for item in a.forward_label:
            name, _, path = item.partition("=")
            if not path:
                name, path = Path(item).stem, item
            rulers[name] = load_ruler(path)
    else:
        for name, p in _discover_rulers(run_dir, str(cutoff)[:7]).items():
            rulers[name] = load_ruler(p)
    case_db = org_nodes = npi_to_org = dn = None
    if a.case_db:
        if not a.graph_dir:
            ap.error("--case-db needs --graph-dir")
        case_db = pd.read_csv(a.case_db, dtype=str) if str(a.case_db).endswith(".csv") else pd.read_parquet(a.case_db)
        g = Path(a.graph_dir)
        org_nodes = pd.read_parquet(g / "nodes" / "org_nodes.parquet")
        npi_to_org = pd.read_parquet(g / "npi_to_org.parquet")
        dn = pd.read_parquet(a.defendant_npis) if a.defendant_npis else None
        from .prospective_label import build_forward_case_label
        fca = build_forward_case_label(case_db, cutoff, org_nodes, npi_to_org, defendant_npis=dn,
                                       horizon_months=a.horizon_months)
        fca["event_date"] = pd.to_datetime(fca["first_excl_date"], errors="coerce")
        rulers["fca"] = fca[["npi", "event_date", "is_prospective_positive", "was_excluded_pre_cutoff",
                             "undated_exclusion", "fca_tier"]]
        print(f"[accuracy_diagnostics] FCA ruler: {int(fca['is_prospective_positive'].sum()):,} forward positives")
    if not rulers:
        ap.error("no ruler: pass --forward-label NAME=PATH or --case-db, or run prospective_label first")
    pl = load_priority_list(run_dir, a.list_name) if a.score_source != "refit" else None
    if a.score_source != "refit" and pl is None:
        ap.error(f"{a.list_name} not found in {run_dir}; use --score-source refit or build the list first")
    res = run_diagnostics(matrix, manifest, rulers, cutoff, priority_list=pl, score_source=a.score_source,
                          budgets_org=[int(x) for x in a.budgets_org.split(",")],
                          budgets_npi=[int(x) for x in a.budgets_npi.split(",")],
                          top_fracs=[float(x) for x in a.top_fracs.split(",")],
                          horizons=[int(x) for x in a.horizons.split(",")], horizon_months=a.horizon_months,
                          gap_months=a.gap_months, active_within=a.active_within, min_damages=a.min_damages,
                          repeats=a.repeats, case_db=case_db, org_nodes=org_nodes, npi_to_org=npi_to_org,
                          defendant_npis=dn)
    out = Path(a.out) if a.out else run_dir / "ACCURACY_DIAGNOSTICS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    top_rows = res.pop("_top_rows", None)
    tie = res.pop("_tie_block", None)
    if tie is not None and len(tie):
        tie.to_csv(out.parent / "accuracy_tie_block.csv", index=False)
    res["list_name"] = a.list_name
    out.write_text(to_markdown(res, run_dir.name), encoding="utf-8")
    (out.parent / "accuracy_diagnostics.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    if top_rows is not None and len(top_rows):
        top_rows.to_csv(out.parent / "accuracy_topk_rows.csv", index=False)
    k = res["kpis"]
    print(f"[accuracy_diagnostics] {res['headline_ruler']} ruler: org hits@50 = {k.get('acc_org_hits50')} "
          f"(random {_f(k.get('acc_base50'), '{:.3f}')} per slot), median lead {_f(k.get('acc_lead_median_mo'), '{:.0f}')} mo "
          f"-> {out}")


if __name__ == "__main__":
    main()
