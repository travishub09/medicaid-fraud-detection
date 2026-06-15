"""
lookalikes.py — enforcement lookalikes, exemplar-based (expansion plan A9).

The manifesto lists "public enforcement lookalikes" as corroboration: an org
whose feature profile closely matches organizations that actually settled is
more credible. This is exemplar-based and therefore inherently explainable — the
dossier names the settled cases it resembles. Per the manifesto's layering, a
lookalike is NEVER a score driver; it is X-layer corroboration context only, so
nothing here feeds the subscores or the ranking.

  resolve_settled_orgs   case DB defendant name-keys → which of OUR org nodes
                         settled (the exemplar set), via the shared norm_org_name.
  enforcement_lookalikes per scored org: Euclidean distance in scheme-subscore
                         space to the nearest settled orgs, and the names of the
                         k closest — "feature profile most similar to [3 settled
                         cases, named]". Settled orgs are excluded from their own
                         neighbor search.

Dormant until the DOJ backfill populates the case DB with settled defendants
that resolve to org nodes; until then the exemplar set is empty and every org
gets an empty lookalike string (absent, never fabricated).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name

MAX_NAMED = 3


def resolve_settled_orgs(org_nodes: pd.DataFrame,
                         case_db: pd.DataFrame) -> pd.DataFrame:
    """Which org nodes appear as settled defendants in the case DB.

    Matches on the shared name key (org_name + aliases vs defendant_name_key),
    keeping only rows with a real recovery (amount_usd > 0) or an intervention —
    the exemplar set is *successful* enforcement, not every filing.
    Returns org_node_id, org_name, matched_case_id, amount_usd.
    """
    if case_db is None or not len(case_db) or not len(org_nodes):
        return pd.DataFrame(columns=["org_node_id", "org_name",
                                     "matched_case_id", "amount_usd"])
    cdb = case_db.copy()
    cdb["amount_usd"] = pd.to_numeric(cdb.get("amount_usd"), errors="coerce")
    intervened = pd.to_numeric(cdb.get("intervened"), errors="coerce")
    settled = cdb[(cdb["amount_usd"].fillna(0) > 0) | (intervened == 1)].copy()
    key_to_case = {}
    for r in settled.itertuples():
        k = str(getattr(r, "defendant_name_key", "") or "")
        if k:
            key_to_case.setdefault(k, (getattr(r, "case_id", ""),
                                       getattr(r, "amount_usd", np.nan)))
    rows = []
    for o in org_nodes.itertuples():
        names = {str(getattr(o, "org_name", "") or "")}
        names.update(a.strip() for a in
                     str(getattr(o, "aliases", "") or "").split(";"))
        for n in names:
            hit = key_to_case.get(norm_org_name(n))
            if hit:
                rows.append({"org_node_id": str(o.org_node_id),
                             "org_name": str(getattr(o, "org_name", "")),
                             "matched_case_id": hit[0], "amount_usd": hit[1]})
                break
    return pd.DataFrame(rows, columns=["org_node_id", "org_name",
                                       "matched_case_id", "amount_usd"])


def enforcement_lookalikes(scored_orgs: pd.DataFrame,
                           settled_org_ids: list[str] | pd.Series,
                           feature_cols: list[str] | None = None,
                           k: int = MAX_NAMED) -> pd.DataFrame:
    """Per-org nearest-settled-neighbor corroboration in subscore space.

    ``scored_orgs`` carries org_node_id + the subscore_* columns. Returns
    org_node_id, lookalike_distance (to the nearest settled org; NaN if none),
    and enforcement_lookalikes (named string). An org never matches itself.
    """
    out_cols = ["org_node_id", "lookalike_distance", "enforcement_lookalikes"]
    df = scored_orgs.copy()
    df["org_node_id"] = df["org_node_id"].astype(str)
    settled = set(map(str, settled_org_ids)) if settled_org_ids is not None else set()
    settled &= set(df["org_node_id"])
    if feature_cols is None:
        feature_cols = [c for c in df.columns if c.startswith("subscore_")]
    if not settled or not feature_cols:
        return pd.DataFrame({"org_node_id": df["org_node_id"],
                             "lookalike_distance": np.nan,
                             "enforcement_lookalikes": ""})[out_cols]

    X = df[feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy()
    ids = df["org_node_id"].to_numpy()
    name_by_id = dict(zip(df["org_node_id"],
                          df.get("org_name", df["org_node_id"]).astype(str)))
    settled_mask = np.array([i in settled for i in ids])
    S = X[settled_mask]
    settled_ids = ids[settled_mask]

    rows = []
    for i, oid in enumerate(ids):
        dists = np.linalg.norm(S - X[i], axis=1)
        order = np.argsort(dists)
        named, used = [], 0
        for j in order:
            if settled_ids[j] == oid:        # never your own exemplar
                continue
            nm = name_by_id.get(settled_ids[j], settled_ids[j]) or settled_ids[j]
            named.append(f"{nm} (d={dists[j]:.2f})")
            used += 1
            if used >= k:
                break
        nearest = (min(d for j, d in enumerate(dists) if settled_ids[j] != oid)
                   if (settled_ids != oid).any() else np.nan)
        rows.append({"org_node_id": oid,
                     "lookalike_distance": round(float(nearest), 4)
                     if not np.isnan(nearest) else np.nan,
                     "enforcement_lookalikes":
                     ("most similar to " + "; ".join(named)) if named else ""})
    return pd.DataFrame(rows, columns=out_cols)
