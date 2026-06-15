"""
ownership_churn.py — change-of-ownership (CHOW) detection (expansion plan A7).

The manifesto names "short-lived entities, ownership churn, changes-of-ownership"
as concealment signals. We ingest the CMS All-Owners files but only ever see the
latest snapshot, so churn is invisible. The fix is operational, not clever: keep
each month's owner file (a runbook line, not code), then DIFF consecutive
snapshots of the ``owned_by`` edges to recover owner entry/exit events per org.

  ownership_change_events   list of dated owner-edge snapshots → one row per
                            (org, owner, event_type ∈ {entry, exit}, date).
                            Owners present in a later snapshot but not the earlier
                            one ENTERED; the reverse EXITED. The first snapshot
                            establishes the baseline (no events).
  ownership_turnover_features  events → per-org ownership_turnover (entries +
                            exits), counts, and last_change_date; plus a 0–1
                            ``ownership_turnover`` for the registry (bounded the
                            same way related_party_density is — churn saturates).

Dormant until two+ monthly snapshots accumulate. The event rows also feed the
exit-after-event timeline (A8) as CHOW catalysts.
"""

from __future__ import annotations

import pandas as pd

TURNOVER_SATURATION = 6        # entries+exits at/above this → ownership_turnover 1.0


def _owner_set(edges: pd.DataFrame) -> pd.DataFrame:
    """Normalize a snapshot to distinct (org_node_id, owner_key) pairs.

    Accepts either the ``owned_by`` edge shape (src_id / dst_id ``owner:<key>``)
    or an explicit org_node_id / owner_key frame.
    """
    e = edges.copy()
    if "org_node_id" not in e.columns:
        e["org_node_id"] = e["src_id"].astype(str)
    if "owner_key" not in e.columns:
        e["owner_key"] = (e["dst_id"].astype(str)
                          .str.replace(r"^owner:", "", regex=True))
    return e[["org_node_id", "owner_key"]].astype(str).drop_duplicates()


def ownership_change_events(snapshots: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    """Diff consecutive dated owner snapshots into entry/exit events.

    ``snapshots`` is a list of ``(date_str, owned_by_edges)`` in any order; they
    are sorted by date. Returns columns org_node_id, owner_key, event_type
    ('entry'|'exit'), event_date (the later snapshot's date).
    """
    cols = ["org_node_id", "owner_key", "event_type", "event_date"]
    if not snapshots or len(snapshots) < 2:
        return pd.DataFrame(columns=cols)

    ordered = sorted(snapshots, key=lambda kv: str(kv[0]))
    rows = []
    prev_date, prev = ordered[0]
    prev_pairs = set(map(tuple, _owner_set(prev).itertuples(index=False)))
    for date, snap in ordered[1:]:
        pairs = set(map(tuple, _owner_set(snap).itertuples(index=False)))
        for org, owner in pairs - prev_pairs:
            rows.append({"org_node_id": org, "owner_key": owner,
                         "event_type": "entry", "event_date": str(date)})
        for org, owner in prev_pairs - pairs:
            rows.append({"org_node_id": org, "owner_key": owner,
                         "event_type": "exit", "event_date": str(date)})
        prev_pairs = pairs
    return pd.DataFrame(rows, columns=cols).sort_values(
        ["org_node_id", "event_date"]).reset_index(drop=True)


def ownership_turnover_features(events: pd.DataFrame,
                                saturation: int = TURNOVER_SATURATION
                                ) -> pd.DataFrame:
    """Per-org churn features. Returns org_node_id, n_owner_entries,
    n_owner_exits, ownership_turnover_count, last_change_date, and the bounded
    0–1 ``ownership_turnover`` the ownership_integrity scheme consumes."""
    cols = ["org_node_id", "n_owner_entries", "n_owner_exits",
            "ownership_turnover_count", "last_change_date", "ownership_turnover"]
    if events is None or not len(events):
        return pd.DataFrame(columns=cols)
    g = events.groupby("org_node_id")
    out = pd.DataFrame({
        "n_owner_entries": g["event_type"].apply(lambda s: int((s == "entry").sum())),
        "n_owner_exits": g["event_type"].apply(lambda s: int((s == "exit").sum())),
        "last_change_date": g["event_date"].max(),
    })
    out["ownership_turnover_count"] = out["n_owner_entries"] + out["n_owner_exits"]
    out["ownership_turnover"] = (out["ownership_turnover_count"]
                                 / float(saturation)).clip(upper=1.0)
    return out.reset_index()[cols]
