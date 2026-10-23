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
never sees its own label; a training row is encoded leave-one-out so its own label
never reaches its feature). m = M_SMOOTH pulls thin classes toward the global rate
(an m-estimate / empirical-Bayes shrinkage), so a two-provider taxonomy with one
positive reads near the base rate, not 50%.

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


def encode_fold(keys: pd.DataFrame, y, tr: np.ndarray, te: np.ndarray,
                m: float = M_SMOOTH, w=None, min_n: int = MIN_CLASS_N) -> tuple[np.ndarray, np.ndarray]:
    """Out-of-fold target encoding for one fold.

    Training rows: leave-one-out (their own label is subtracted); test rows: the
    full training-fold statistics. Returns (Xtr_extra, Xte_extra), one column per
    key, in keys.columns order.

    v22: ``w`` (the scorer's sample weights = the per-case label budget) weights
    every positive, so forty-nine NPIs of one broadcast settlement count as that
    case's budget, not as forty-nine independent offenders; a class with fewer
    than ``min_n`` training ROWS takes the global rate on both sides of the fold
    (a thin class is where one case masquerades as a base rate)."""
    y = np.asarray(y, dtype=float)
    w = np.ones(len(y), dtype=float) if w is None else np.asarray(w, dtype=float)
    ytr, wtr = y[tr], w[tr]
    g = float((wtr * ytr).sum() / wtr.sum()) if len(ytr) and wtr.sum() > 0 else 0.0
    cols_tr, cols_te = [], []
    for k in keys.columns:
        kv = keys[k].to_numpy()
        ktr = pd.Series(kv[tr])
        st = (pd.DataFrame({"k": ktr.to_numpy(), "wy": wtr * ytr, "w": wtr, "one": 1.0})
              .groupby("k").agg(pos=("wy", "sum"), n=("w", "sum"), rows=("one", "sum")))
        thin = st["rows"] < min_n
        pos = ktr.map(st["pos"]).to_numpy(dtype=float)
        n = ktr.map(st["n"]).to_numpy(dtype=float)
        loo = (pos - wtr * ytr + m * g) / np.maximum(n - wtr + m, 1e-9)
        loo = np.where(ktr.map(thin).fillna(True).to_numpy(dtype=bool), g, loo)
        kte = pd.Series(kv[te])
        pos_te = kte.map(st["pos"]).fillna(0.0).to_numpy(dtype=float)
        n_te = kte.map(st["n"]).fillna(0.0).to_numpy(dtype=float)
        full = (pos_te + m * g) / (n_te + m)
        full = np.where(kte.map(thin).fillna(True).to_numpy(dtype=bool), g, full)
        cols_tr.append(loo)
        cols_te.append(full)
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
