"""
billing_specialty.py — billing-implied specialty vs. the self-reported taxonomy.

Every peer-relative signal trusts the provider's NPPES taxonomy to pick the right
comparison cohort. That's the one input a fraudster can set themselves: code as a
sleepy specialty, then bill like something else and dodge your peers. This module
learns what each taxonomy's billing actually LOOKS like and flags providers whose
billing resembles a DIFFERENT specialty than the one they claim.

Method (nearest-centroid in billing-embedding space — reuses the billing-LM's
``billing_emb_*`` so it adds no new heavy pass):

  taxonomy_centroids     each taxonomy's centroid = the mean billing embedding of
                         its providers (taxonomies with enough providers to be a
                         stable centroid).
  implied_specialty      per NPI: the nearest taxonomy centroid is the
                         BILLING-IMPLIED specialty; compare it to the CLAIMED one.
      billing_implied_taxonomy     argmin-distance taxonomy (explanation)
      billing_taxonomy_mismatch    1 if implied != claimed (a clean 0/1 feature)
      billing_taxonomy_fit         distance to the CLAIMED centroid (high = the
                                   claimed specialty fits the billing poorly)
      billing_taxonomy_margin      fit(claimed) - fit(nearest) >= 0 (how much
                                   better some OTHER specialty explains the billing)

Clean (self-supervised — no exclusion labels touched). Chunked over providers so
the provider x taxonomy distance never materializes whole. numpy/pandas only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_TAXONOMY_PROVIDERS = 25     # a centroid needs enough members to be stable
_CHUNK = 20000                   # providers per distance-matrix block


def _emb_cols(df: pd.DataFrame) -> list[str]:
    from .billing_lm import EMB_PREFIX
    return [c for c in df.columns if c.startswith(EMB_PREFIX)]


def taxonomy_centroids(provider_emb: pd.DataFrame, provider_dim: pd.DataFrame,
                       min_providers: int = MIN_TAXONOMY_PROVIDERS
                       ) -> tuple[list[str], np.ndarray]:
    """Mean billing embedding per taxonomy (only taxonomies with >= min_providers).
    Returns (taxonomy list, centroid matrix [n_tax x dim])."""
    cols = _emb_cols(provider_emb)
    tax = provider_dim[["npi", "taxonomy_code"]].copy()
    tax["npi"] = tax["npi"].astype(str)
    tax["taxonomy_code"] = tax["taxonomy_code"].fillna("").astype(str)
    df = provider_emb[["npi"] + cols].copy()
    df["npi"] = df["npi"].astype(str)
    df = df.merge(tax, on="npi", how="inner")
    df = df[df["taxonomy_code"] != ""]
    if not len(df) or not cols:
        return [], np.zeros((0, len(cols)))
    grp = df.groupby("taxonomy_code")
    sizes = grp.size()
    keep = sizes[sizes >= min_providers].index
    cent = grp[cols].mean().loc[keep]
    return list(cent.index), cent.to_numpy(dtype=float)


def implied_specialty(provider_emb: pd.DataFrame, provider_dim: pd.DataFrame,
                      min_providers: int = MIN_TAXONOMY_PROVIDERS) -> pd.DataFrame:
    """Per-NPI billing-implied specialty + mismatch/fit/margin vs. the claimed one."""
    cols = _emb_cols(provider_emb)
    out_cols = ["npi", "billing_implied_taxonomy", "billing_taxonomy_mismatch",
                "billing_taxonomy_fit", "billing_taxonomy_margin"]
    taxa, C = taxonomy_centroids(provider_emb, provider_dim, min_providers)
    if not len(taxa) or not cols:
        return pd.DataFrame(columns=out_cols)
    tax_idx = {t: i for i, t in enumerate(taxa)}

    pe = provider_emb[["npi"] + cols].copy()
    pe["npi"] = pe["npi"].astype(str)
    claimed = (provider_dim[["npi", "taxonomy_code"]].astype(str)
               .drop_duplicates("npi"))
    pe = pe.merge(claimed, on="npi", how="left")
    pe["taxonomy_code"] = pe["taxonomy_code"].fillna("")

    X = pe[cols].to_numpy(dtype=float)
    cnorm = (C ** 2).sum(1)                       # ||centroid||^2 for the distance expansion
    implied_i = np.empty(len(X), dtype=int)
    nearest_d = np.empty(len(X), dtype=float)
    for lo in range(0, len(X), _CHUNK):
        hi = min(lo + _CHUNK, len(X))
        blk = X[lo:hi]
        # squared euclidean distance block via ||x||^2 - 2 x.C^T + ||C||^2
        d2 = (blk ** 2).sum(1)[:, None] - 2 * blk @ C.T + cnorm[None, :]
        np.maximum(d2, 0.0, out=d2)
        implied_i[lo:hi] = d2.argmin(1)
        nearest_d[lo:hi] = np.sqrt(d2[np.arange(hi - lo), implied_i[lo:hi]])

    implied_tax = [taxa[i] for i in implied_i]
    claimed_i = pe["taxonomy_code"].map(tax_idx).to_numpy()       # NaN if claimed tax has no centroid
    fit = np.full(len(X), np.nan)
    has_claim = ~pd.isna(claimed_i)
    if has_claim.any():
        ci = claimed_i[has_claim].astype(int)
        xb = X[has_claim]
        d2c = np.maximum((xb ** 2).sum(1) - 2 * (xb * C[ci]).sum(1) + cnorm[ci], 0.0)
        fit[has_claim] = np.sqrt(d2c)
    margin = fit - nearest_d                                       # >= 0; NaN if claimed has no centroid
    mismatch = np.where(pd.isna(claimed_i), np.nan,
                        (implied_i != np.nan_to_num(claimed_i, nan=-1)).astype(float))

    return pd.DataFrame({
        "npi": pe["npi"].to_numpy(),
        "billing_implied_taxonomy": implied_tax,
        "billing_taxonomy_mismatch": mismatch,
        "billing_taxonomy_fit": fit,
        "billing_taxonomy_margin": margin,
    })[out_cols]
