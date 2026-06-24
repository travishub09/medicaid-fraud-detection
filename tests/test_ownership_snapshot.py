"""
test_ownership_snapshot.py — the CHOW operational layer (archive → diff → feature).

ownership_churn.py (the diff math) is tested elsewhere; this covers the archiver
that makes churn observable: stamping dated snapshots, dormancy until two exist,
and the turnover feature emerging once owners enter/exit between snapshots.
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.ownership_snapshot import (
    archive_snapshot, load_snapshots, compute_ownership_turnover)


def _edges(pairs):
    return pd.DataFrame([{"src_id": o, "dst_id": f"owner:{k}", "edge_type": "owned_by"}
                         for o, k in pairs])


def test_dormant_until_two_snapshots(tmp_path):
    archive_snapshot(_edges([("org:a", "OWN1")]), tmp_path, date="2026-01")
    assert len(load_snapshots(tmp_path)) == 1
    assert compute_ownership_turnover(tmp_path).empty       # one snapshot → dormant


def test_archive_is_idempotent_per_date(tmp_path):
    archive_snapshot(_edges([("org:a", "OWN1")]), tmp_path, date="2026-01")
    archive_snapshot(_edges([("org:a", "OWN1"), ("org:a", "OWN2")]), tmp_path, date="2026-01")
    snaps = load_snapshots(tmp_path)
    assert len(snaps) == 1                                  # same date overwrites
    assert len(snaps[0][1]) == 2


def test_turnover_from_entry_and_exit(tmp_path):
    # month 1: org:a owned by OWN1; month 2: OWN1 exits, OWN2 enters (2 events)
    archive_snapshot(_edges([("org:a", "OWN1")]), tmp_path, date="2026-01")
    archive_snapshot(_edges([("org:a", "OWN2")]), tmp_path, date="2026-02")
    turn = compute_ownership_turnover(tmp_path).set_index("org_node_id")
    assert turn.loc["org:a", "n_owner_entries"] == 1
    assert turn.loc["org:a", "n_owner_exits"] == 1
    assert turn.loc["org:a", "ownership_turnover"] > 0      # bounded 0–1 registry feature


def test_turnover_wired_into_export(tmp_path):
    from src.model_a.provider_features_export import _run_org_grain_adapters
    snaps = tmp_path / "owner_snapshots"
    archive_snapshot(_edges([("org:a", "OWN1")]), snaps, date="2026-01")
    archive_snapshot(_edges([("org:a", "OWN2")]), snaps, date="2026-02")
    proc = tmp_path / "processed"; proc.mkdir()
    npi_to_org = pd.DataFrame({"npi": ["1003000126"], "org_node_id": ["org:a"]})
    frames = _run_org_grain_adapters(tmp_path / "pre", proc, npi_to_org, None, None,
                                     lambda *_: None, snapshots_dir=snaps)
    assert "ownership_churn" in frames
    assert "ownership_turnover" in frames["ownership_churn"].columns
