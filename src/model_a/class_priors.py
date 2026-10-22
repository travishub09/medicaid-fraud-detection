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
                m: float = M_SMOOTH) -> tuple[np.ndarray, np.ndarray]:
    """Out-of-fold target encoding for one fold.

    Training rows: leave-one-out (their own label is subtracted); test rows: the
    full training-fold statistics. Returns (Xtr_extra, Xte_extra), one column per
    key, in keys.columns order."""
    y = np.asarray(y, dtype=float)
    ytr = y[tr]
    g = float(ytr.mean()) if len(ytr) else 0.0
    cols_tr, cols_te = [], []
    for k in keys.columns:
        kv = keys[k].to_numpy()
        ktr = pd.Series(kv[tr])
        stats = pd.DataFrame({"k": ktr.to_numpy(), "y": ytr}).groupby("k")["y"].agg(["sum", "count"])
        pos = ktr.map(stats["sum"]).to_numpy(dtype=float)
        n = ktr.map(stats["count"]).to_numpy(dtype=float)
        loo = (pos - ytr + m * g) / np.maximum(n - 1 + m, 1e-9)
        kte = pd.Series(kv[te])
        pos_te = kte.map(stats["sum"]).fillna(0.0).to_numpy(dtype=float)
        n_te = kte.map(stats["count"]).fillna(0.0).to_numpy(dtype=float)
        full = (pos_te + m * g) / (n_te + m)
        cols_tr.append(loo)
        cols_te.append(full)
    if not cols_tr:
        return np.zeros((len(tr), 0)), np.zeros((len(te), 0))
    return np.column_stack(cols_tr), np.column_stack(cols_te)


def rate_table(keys: pd.DataFrame, y, m: float = M_SMOOTH) -> pd.DataFrame:
    """The whole-universe rate per class (the explainability record a reader can
    audit: n, positives, raw rate, shrunk rate, lift over the base rate)."""
    y = np.asarray(y, dtype=float)
    g = float(y.mean()) if len(y) else 0.0
    rows = []
    for k in keys.columns:
        st = pd.DataFrame({"k": keys[k].to_numpy(), "y": y}).groupby("k")["y"].agg(["sum", "count"])
        for val, r in st.iterrows():
            shrunk = (r["sum"] + m * g) / (r["count"] + m)
            rows.append({"key": k, "value": val, "n": int(r["count"]), "positives": int(r["sum"]),
                         "rate": float(r["sum"] / r["count"]) if r["count"] else float("nan"),
                         "shrunk_rate": float(shrunk),
                         "lift": float(shrunk / g) if g > 0 else float("nan")})
    out = pd.DataFrame(rows, columns=["key", "value", "n", "positives", "rate", "shrunk_rate", "lift"])
    out.attrs["base_rate"] = g
    return out.sort_values(["key", "shrunk_rate"], ascending=[True, False]).reset_index(drop=True)
