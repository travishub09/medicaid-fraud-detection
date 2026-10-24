"""
class_priors.py — the class base rate as a trainable feature (v21 A/B, 2026-10-22).

THE v19 LESSON (LABEL_ENRICHMENT): the forward FCA cases concentrate in clinical
laboratories (taxonomy 291U: 19% of positives vs 0.7% of the universe, lift 26),
in organizations (entity type 2, lift 1.5), and in large organizations, while the
matrix files `primary_taxonomy` / `entity_type` / `practice_state` as IDENTIFIER
columns and every billing metric is a within-taxonomy percentile — which removes the
class base rate by construction. A lab billing like other labs is unremarkable to the
scorer even though labs as a class carry 26x the case rate.

This module turns those identifiers into out-of-fold TARGET-ENCODED rates:

    prior_<key> = (positives in the class + m * global rate) / (n in the class + m)

computed INSIDE each training fold from that fold's training rows only (a test row
never sees its own label). m = M_SMOOTH pulls thin classes toward the global rate
(an m-estimate / empirical-Bayes shrinkage), so a two-provider taxonomy with one
positive reads near the base rate, not 50%.

THE v23 LESSON (2026-10-24, the leave-one-out leak): v21 .. v23 encoded a TRAINING
row leave-one-out (its own label subtracted). Inside a class that gives every positive
a value exactly one count below every negative, and gradient-boosted trees read that
two-valued signature as the label: training AUC 1.000, the model memorises the prior
column, and at test time (where every row carries the fold's full rate, a third value
that sits on the negatives' side of the split) it predicts "negative" for nearly
everyone. The shipped lists read a median raw score of 0.0000016 against a base rate
of 0.0028, the NPI grain emptied, the top 50 re-shuffled between fold draws, and the
no-priors arm beat the priors on a synthetic twin (OOF AUC 0.589 vs 0.664). The fix
is the textbook one: training rows are encoded NESTED, from an inner K-fold over the
training rows (``inner_folds``, default PRIOR_INNER_FOLDS), so no row's own label
reaches its feature AND positives and negatives in a class draw from the same value
set. Nested priors beat no priors on the same twin (0.698; top-1% lift 6.1 vs 5.0).
``inner_folds=0`` keeps the leave-one-out path as a gradable arm, never a default.

Keys (``--class-priors taxonomy,entity``):
    taxonomy   the NUCC code (primary_taxonomy)
    family     the coarse clinical family (defendant_resolver.taxonomy_family)
    entity     NPPES entity type (1 individual / 2 organization)
    state      practice_state — ENFORCEMENT DENSITY as much as fraud density
               (WA / NM / AK lifts in v19 are a few large cases); off by default,
               A/B it on its own before it ever ships

Explainability (hard rule 5): the rate table a run used is written beside the
diagnostics (class_prior_rates.csv) and the driver reads as "class base rate:
clinical laboratories 1.2% vs 0.3% overall" — a named, auditable number, never a
bare composite. A/B harness only until a pre-registration ships it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

M_SMOOTH = 50.0
# v22 (the v21 read-out): a class with fewer training rows than this takes the
# global rate. v21's taxonomy prior put a 199-NPI hematology class at a 24.6%
# training rate (49 positives, one broadcast settlement) and lift 70, and the
# NPI-grain top 1% filled with the high-prior classes (zero forward positives;
# NPI lift 0.0 against 8.7 without priors). Thin classes are where one case
# masquerades as a base rate.
MIN_CLASS_N = 300
# v24 (the v23 read-out): training rows are encoded from an inner K-fold over the
# training rows (0 = the leave-one-out encoder of v21 .. v23, the defect)
PRIOR_INNER_FOLDS = 5

PRIOR_KEYS: dict[str, str] = {
    "taxonomy": "primary_taxonomy",
    "family": "primary_taxonomy",        # derived through taxonomy_family
    "entity": "entity_type",
    "state": "practice_state",
}
UNKNOWN = "(unknown)"


def parse_spec(spec) -> list[str]:
    """'taxonomy,entity' -> ['taxonomy', 'entity']; None / '' / 'none' -> []."""
    if spec is None:
        return []
    if isinstance(spec, (list, tuple)):
        items = [str(s).strip().lower() for s in spec]
    else:
        items = [s.strip().lower() for s in str(spec).split(",")]
    items = [s for s in items if s and s != "none"]
    bad = [s for s in items if s not in PRIOR_KEYS]
    if bad:
        raise ValueError(f"unknown class-prior key(s) {bad}; choose from {sorted(PRIOR_KEYS)}")
    return list(dict.fromkeys(items))


def feature_names(which: list[str]) -> list[str]:
    return [f"prior_{k}" for k in which]


def prior_keys(matrix: pd.DataFrame, which: list[str]) -> pd.DataFrame:
    """One string column per key (missing values -> their own '(unknown)' class)."""
    out = pd.DataFrame(index=matrix.index)
    for k in which:
        col = PRIOR_KEYS[k]
        if col not in matrix.columns:
            out[k] = UNKNOWN
            continue
        s = matrix[col].astype("string").fillna("").str.strip().str.upper()
        if k == "family":
            from src.enforcement.defendant_resolver import taxonomy_family
            s = s.map(lambda c: taxonomy_family(c) or "")
        if k == "entity":
            s = s.str.replace(r"\.0$", "", regex=True)
        out[k] = s.replace({"": UNKNOWN, "NAN": UNKNOWN, "NONE": UNKNOWN, "<NA>": UNKNOWN}).astype(str)
    return out


def _class_stats(kv: np.ndarray, y: np.ndarray, w: np.ndarray) -> pd.DataFrame:
    """Per class: weighted positives, weighted n, and the raw row count."""
    return (pd.DataFrame({"k": kv, "wy": w * y, "w": w, "one": 1.0})
            .groupby("k").agg(pos=("wy", "sum"), n=("w", "sum"), rows=("one", "sum")))


def _rate_from(st: pd.DataFrame, g: float, kv: np.ndarray, m: float, min_n: int) -> np.ndarray:
    """The shrunk class rate for rows ``kv`` from statistics ``st`` the rows did not
    contribute to; an unseen or thin (< min_n rows) class reads the global rate."""
    ks = pd.Series(kv)
    pos = ks.map(st["pos"]).fillna(0.0).to_numpy(dtype=float)
    n = ks.map(st["n"]).fillna(0.0).to_numpy(dtype=float)
    thin = ks.map(st["rows"] < min_n).fillna(True).to_numpy(dtype=bool)
    return np.where(thin, g, (pos + m * g) / (n + m))


def _inner_splits(y: np.ndarray, k: int, seed: int = 0) -> list[tuple[np.ndarray, np.ndarray]]:
    """Deterministic inner folds over the training rows: stratified on the label when
    both classes allow it, plain shuffled folds otherwise."""
    from sklearn.model_selection import KFold, StratifiedKFold
    n_pos, n_neg = int((y > 0).sum()), int((y <= 0).sum())
    k = int(min(k, len(y)))
    if k < 2:
        return [(np.arange(len(y)), np.arange(len(y)))]
    if n_pos >= k and n_neg >= k:
        return list(StratifiedKFold(n_splits=k, shuffle=True, random_state=seed).split(np.zeros(len(y)), (y > 0).astype(int)))
    return list(KFold(n_splits=k, shuffle=True, random_state=seed).split(np.zeros(len(y))))


def encode_fold(keys: pd.DataFrame, y, tr: np.ndarray, te: np.ndarray,
                m: float = M_SMOOTH, w=None, min_n: int = MIN_CLASS_N,
                inner_folds: int = PRIOR_INNER_FOLDS) -> tuple[np.ndarray, np.ndarray]:
    """Out-of-fold target encoding for one fold.

    Test rows: the full training-fold statistics. Training rows: NESTED (v24) — an
    inner ``inner_folds``-fold over the training rows, each inner slice encoded from
    the other slices' statistics, so no row's own label reaches its feature and the
    positives and negatives of a class draw from the same set of values (no label
    signature a tree can read). ``inner_folds=0`` is the leave-one-out encoder of
    v21 .. v23 (positives one count below negatives inside every class: the leak the
    module docstring describes), kept only as a gradable arm. Returns
    (Xtr_extra, Xte_extra), one column per key, in keys.columns order.

    v22: ``w`` (the scorer's sample weights = the per-case label budget) weights
    every positive, so forty-nine NPIs of one broadcast settlement count as that
    case's budget, not as forty-nine independent offenders; a class with fewer
    than ``min_n`` training ROWS takes the global rate on both sides of the fold
    (a thin class is where one case masquerades as a base rate)."""
    y = np.asarray(y, dtype=float)
    w = np.ones(len(y), dtype=float) if w is None else np.asarray(w, dtype=float)
    ytr, wtr = y[tr], w[tr]
    g = float((wtr * ytr).sum() / wtr.sum()) if len(ytr) and wtr.sum() > 0 else 0.0
    inner = _inner_splits(ytr, int(inner_folds or 0)) if int(inner_folds or 0) >= 2 else None
    cols_tr, cols_te = [], []
    for k in keys.columns:
        kv = keys[k].to_numpy()
        kvtr = kv[tr]
        st = _class_stats(kvtr, ytr, wtr)
        if inner is None:
            # leave-one-out (v21 .. v23): the row's own label subtracted — the defect
            ktr = pd.Series(kvtr)
            pos = ktr.map(st["pos"]).to_numpy(dtype=float)
            n = ktr.map(st["n"]).to_numpy(dtype=float)
            enc_tr = (pos - wtr * ytr + m * g) / np.maximum(n - wtr + m, 1e-9)
            enc_tr = np.where(ktr.map(st["rows"] < min_n).fillna(True).to_numpy(dtype=bool), g, enc_tr)
        else:
            enc_tr = np.empty(len(tr), dtype=float)
            for itr, ite in inner:
                wi, yi = wtr[itr], ytr[itr]
                gi = float((wi * yi).sum() / wi.sum()) if len(yi) and wi.sum() > 0 else g
                enc_tr[ite] = _rate_from(_class_stats(kvtr[itr], yi, wi), gi, kvtr[ite], m, min_n)
        cols_tr.append(enc_tr)
        cols_te.append(_rate_from(st, g, kv[te], m, min_n))
    if not cols_tr:
        return np.zeros((len(tr), 0)), np.zeros((len(te), 0))
    return np.column_stack(cols_tr), np.column_stack(cols_te)


def rate_table(keys: pd.DataFrame, y, m: float = M_SMOOTH, w=None, min_n: int = MIN_CLASS_N) -> pd.DataFrame:
    """The whole-universe rate per class (the explainability record a reader can
    audit: n, positives, weighted positives, raw rate, the shrunk rate the encoder
    uses, lift over the base rate, and whether the class is under the size floor)."""
    y = np.asarray(y, dtype=float)
    w = np.ones(len(y), dtype=float) if w is None else np.asarray(w, dtype=float)
    g = float((w * y).sum() / w.sum()) if len(y) and w.sum() > 0 else 0.0
    rows = []
    for k in keys.columns:
        st = (pd.DataFrame({"k": keys[k].to_numpy(), "y": y, "wy": w * y, "w": w})
              .groupby("k").agg(sum=("y", "sum"), count=("y", "size"), wpos=("wy", "sum"), wn=("w", "sum")))
        for val, r in st.iterrows():
            thin = bool(r["count"] < min_n)
            shrunk = g if thin else (r["wpos"] + m * g) / (r["wn"] + m)
            rows.append({"key": k, "value": val, "n": int(r["count"]), "positives": int(r["sum"]),
                         "weighted_positives": float(r["wpos"]), "below_min_n": thin,
                         "rate": float(r["sum"] / r["count"]) if r["count"] else float("nan"),
                         "shrunk_rate": float(shrunk),
                         "lift": float(shrunk / g) if g > 0 else float("nan")})
    out = pd.DataFrame(rows, columns=["key", "value", "n", "positives", "weighted_positives", "below_min_n", "rate", "shrunk_rate", "lift"])
    out.attrs["base_rate"] = g
    return out.sort_values(["key", "shrunk_rate"], ascending=[True, False]).reset_index(drop=True)
