"""
temporal_sources.py — per-source valid-time / point-in-time reconstruction (Pillar 1, depth).

``feature_store.py`` snapshots the whole matrix at run time. The deeper fix is
SOURCE-LEVEL bitemporality: each source row carries the date its fact became true,
so features can be reconstructed as-of any label date even though sources change at
different cadences. The single highest-value instance is the exclusion graph: today
``within_2_hops_of_exclusion`` / the fraud-proximity field / the embeddings all use
EVERY exclusion — including ones recorded AFTER a provider's conduct window, which
leaks the future into the features. The fix is to build the graph from only the
exclusions (and owner relationships) known *before* the as-of date.

  SOURCE_VALID_TIME           the date column that times each source.
  asof_filter(frame, asof, …) rows whose valid-time ≤ asof (undated rows dropped by
                              default — an undated exclusion can't be proven to
                              predate the date, so it's excluded to avoid leakage).
  point_in_time_tables(...)   filter the entity-graph inputs (exclusions by
                              ``excl_date``, owner edges by ``association_date``) to
                              an as-of date → a leakage-correct point-in-time graph.

Wire via ``python -m src.entity_graph --asof YYYY-MM-DD``: the resulting embeddings,
proximity, rings, and the exclusion label are all as-of-correct by construction.
"""

from __future__ import annotations

import pandas as pd

SOURCE_VALID_TIME = {
    "exclusions": "excl_date",
    "owner_edges": "association_date",
    "doj_case": "announced_date",
    "deactivation": "deactivation_date",
}


def asof_filter(frame: pd.DataFrame, asof, valid_col: str,
                keep_undated: bool = False) -> pd.DataFrame:
    """Rows whose ``valid_col`` ≤ ``asof``. Undated rows are dropped by default
    (can't be proven to predate the date → excluded to avoid leakage)."""
    if frame is None or not len(frame) or valid_col not in frame.columns:
        return frame
    asof_ts = pd.Timestamp(asof)
    vt = pd.to_datetime(frame[valid_col], errors="coerce")
    keep = vt <= asof_ts
    if keep_undated:
        keep = keep | vt.isna()
    return frame[keep.fillna(False)].copy()


def point_in_time_tables(tables: dict[str, pd.DataFrame], asof,
                         asof_addresses: pd.DataFrame | None = None
                         ) -> dict[str, pd.DataFrame]:
    """Filter the entity-graph input tables to an as-of date: exclusions by
    excl_date, owner edges by association_date.

    ``asof_addresses`` (npi, addr_key, addr_state from a historical NPPES
    edition) freezes the CO-LOCATION substrate too. Without it, provider_dim
    passed through with today's addresses, which leaks post-cutoff address
    structure into shell_score (the one feature carrying the network edge, and
    the one that sits in only the with-network arm of the A/B). Build it with
    ``entity_graph.asof_nppes.build_asof_addresses`` and pass a Dec-cutoff NPPES
    edition. When it is None the old behavior stands, but the graph logs the
    address layer as un-frozen so the leak is visible, not silent.
    """
    out = dict(tables)
    if "exclusions" in out and out["exclusions"] is not None:
        out["exclusions"] = asof_filter(out["exclusions"], asof, "excl_date")
    if "owner_edges" in out and out["owner_edges"] is not None:
        out["owner_edges"] = asof_filter(out["owner_edges"], asof, "association_date",
                                         keep_undated=True)
    if asof_addresses is not None and out.get("provider_dim") is not None:
        from src.entity_graph.asof_nppes import apply_asof_addresses
        out["provider_dim"] = apply_asof_addresses(out["provider_dim"], asof_addresses)
        out["_addr_frozen"] = True
    return out
