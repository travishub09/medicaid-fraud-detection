"""
graph_velocity.py — temporal-graph velocity (Pillar 3, docs/platform/17).

A static graph position is one signal; how FAST it's changing is another. A
provider whose ownership/co-location neighborhood is churning — new owners, new
shared-address shells, a sudden jump in connectivity — has the fly-by-night
structural signature, even if any single snapshot looks ordinary. We recover this
the same way ownership-churn does: DIFF two point-in-time snapshots.

The feature store already archives the full per-NPI matrix (including the graph
embeddings and motifs) stamped with a valid-time. This diffs the two most recent
snapshots into per-NPI velocity:

  graph_emb_drift            L2 distance the node's embedding moved (how much its
                             graph *position* changed)
  graph_degree_delta         change in connectivity
  graph_kcore_delta          change in core-ness (entering/leaving a dense core)
  graph_fraud_proximity_delta change in distance to the exclusion field

Dormant until two snapshots accumulate (like ownership_turnover). Motif deltas are
clean; the fraud-proximity delta is leakage-adjacent.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a.feature_store import load_snapshot_index
from src.entity_graph.graph_embeddings import EMB_PREFIX, PROXIMITY_COL

VELOCITY_COLS = ["npi", "graph_emb_drift", "graph_degree_delta",
                 "graph_kcore_delta", "graph_fraud_proximity_delta"]


def velocity_from_snapshots(store_dir: str | Path) -> pd.DataFrame:
    """Diff the two most recent feature snapshots → per-NPI structural velocity.
    Empty until ≥2 snapshots exist."""
    snaps = load_snapshot_index(store_dir)
    if len(snaps) < 2:
        return pd.DataFrame(columns=VELOCITY_COLS)
    (_, p_old), (_, p_new) = snaps[-2], snaps[-1]
    old = pd.read_parquet(p_old)
    new = pd.read_parquet(p_new)
    return velocity_between(old, new)


def velocity_between(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Per-NPI deltas between an earlier and a later matrix snapshot."""
    if "npi" not in old.columns or "npi" not in new.columns:
        return pd.DataFrame(columns=VELOCITY_COLS)
    old = old.copy(); new = new.copy()
    old["npi"] = old["npi"].astype(str)
    new["npi"] = new["npi"].astype(str)
    emb_cols = sorted([c for c in new.columns if c.startswith(EMB_PREFIX)
                       and c in old.columns])
    keep_old = ["npi"] + emb_cols + [c for c in ["graph_degree", "graph_kcore", PROXIMITY_COL]
                                     if c in old.columns]
    m = new.merge(old[keep_old].drop_duplicates("npi"), on="npi", how="inner",
                  suffixes=("", "_old"))
    if not len(m):
        return pd.DataFrame(columns=VELOCITY_COLS)

    if emb_cols:
        cur = m[emb_cols].to_numpy(dtype=float)
        prev = m[[f"{c}_old" for c in emb_cols]].to_numpy(dtype=float)
        drift = np.sqrt(np.nansum((cur - prev) ** 2, axis=1))
    else:
        drift = np.zeros(len(m))

    def _delta(col):
        if col in m.columns and f"{col}_old" in m.columns:
            return (pd.to_numeric(m[col], errors="coerce")
                    - pd.to_numeric(m[f"{col}_old"], errors="coerce"))
        return pd.Series(np.nan, index=m.index)

    return pd.DataFrame({
        "npi": m["npi"].to_numpy(),
        "graph_emb_drift": drift,
        "graph_degree_delta": _delta("graph_degree").to_numpy(),
        "graph_kcore_delta": _delta("graph_kcore").to_numpy(),
        "graph_fraud_proximity_delta": _delta(PROXIMITY_COL).to_numpy(),
    })[VELOCITY_COLS]
