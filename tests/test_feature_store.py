"""
test_feature_store.py — the point-in-time (bitemporal) feature store (Pillar 1).

The core guarantee: reconstruct a provider's features as they stood BEFORE a label
date, never with future data. Covers valid-time snapshotting (idempotent), the
as-of join (latest snapshot strictly before the date), the unresolved-event report
(no prior snapshot → flagged, not filled), and the out-of-time split.
"""

from __future__ import annotations

import pandas as pd

from src.model_a.feature_store import (snapshot_features, load_snapshot_index,
                                       asof_join, temporal_split, ASOF_COL)

NPI = "1003000126"


def _matrix(val):
    return pd.DataFrame({"npi": [NPI], "some_feature": [val]})


def test_snapshot_is_stamped_and_idempotent(tmp_path):
    snapshot_features(_matrix(1.0), "2018-01-01", tmp_path)
    snapshot_features(_matrix(2.0), "2018-01-01", tmp_path)   # same date overwrites
    idx = load_snapshot_index(tmp_path)
    assert len(idx) == 1 and idx[0][0] == "2018-01-01"
    snap = pd.read_parquet(idx[0][1])
    assert snap[ASOF_COL].iloc[0] == "2018-01-01"
    assert snap["some_feature"].iloc[0] == 2.0


def test_asof_join_picks_latest_before_date(tmp_path):
    snapshot_features(_matrix("A"), "2018-01-01", tmp_path)
    snapshot_features(_matrix("B"), "2021-01-01", tmp_path)
    events = pd.DataFrame({"npi": [NPI, NPI], "asof_date": ["2020-06-01", "2022-01-01"]})
    feats, unresolved = asof_join(events, tmp_path)
    assert unresolved.empty
    # 2020 event → the 2018 snapshot (A); 2022 event → the 2021 snapshot (B)
    got = feats.groupby(ASOF_COL)["some_feature"].first().to_dict()
    assert got == {"2018-01-01": "A", "2021-01-01": "B"}


def test_asof_join_reports_events_with_no_prior_snapshot(tmp_path):
    snapshot_features(_matrix("A"), "2021-01-01", tmp_path)
    events = pd.DataFrame({"npi": [NPI], "asof_date": ["2019-01-01"]})  # before any snapshot
    feats, unresolved = asof_join(events, tmp_path)
    assert feats.empty                       # cannot answer without future data
    assert len(unresolved) == 1              # flagged, never silently filled


def test_asof_join_accepts_bare_conduct_year(tmp_path):
    snapshot_features(_matrix("A"), "2016-01-01", tmp_path)
    snapshot_features(_matrix("B"), "2019-06-01", tmp_path)
    events = pd.DataFrame({"npi": [NPI], "asof_date": ["2018"]})   # conduct_start year → Jan 1
    feats, unresolved = asof_join(events, tmp_path)
    assert unresolved.empty
    assert feats[ASOF_COL].iloc[0] == "2016-01-01"   # 2018-01-01 → latest before is 2016


def test_temporal_split_by_conduct_year():
    labels = pd.DataFrame({"npi": list("abcd"),
                           "conduct_start": [2015, 2018, 2021, None]})
    train, test = temporal_split(labels, cutoff_year=2020)
    assert train.tolist() == [True, True, False, False]
    assert test.tolist() == [False, False, True, False]
