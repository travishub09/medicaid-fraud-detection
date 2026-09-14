"""test_peer_rank.py — within-peer percentile transform of model scores."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.source_ablation import peer_rank_scores


def test_peer_rank_removes_sector_offset():
    n = 400
    rng = np.random.default_rng(1)
    # two sectors: labs score globally high, agencies globally low, but each
    # sector has one standout at the top of ITS OWN distribution
    tax = np.array(["LAB"] * 200 + ["HHA"] * 200)
    p = np.concatenate([rng.uniform(0.6, 0.9, 200), rng.uniform(0.05, 0.3, 200)])
    p[199] = 0.95      # top lab
    p[399] = 0.35      # top agency — globally below every lab
    m = pd.DataFrame({"primary_taxonomy": tax, "net_paid": rng.uniform(1e4, 1e6, n)})
    r = peer_rank_scores(p, m, bins=1)                 # bins=1 -> taxonomy only
    assert r[199] == 1.0 and r[399] == 1.0            # both are #1 among peers
    # global ordering would put the agency in the bottom half; peer rank does not
    assert (p[399] < np.median(p)) and (r[399] > 0.99)
    assert np.isnan(r).sum() == 0


def test_peer_rank_falls_back_for_tiny_groups_and_keeps_nan():
    m = pd.DataFrame({"primary_taxonomy": ["A"] * 100 + ["RARE"] * 3,
                      "net_paid": np.arange(103, dtype=float)})
    p = np.linspace(0, 1, 103)
    p[5] = np.nan
    r = peer_rank_scores(p, m, bins=2, min_group=20)
    assert np.isnan(r[5])
    assert not np.isnan(r[100:]).any()                 # RARE fell back to the universe
    assert 0 < r[102] <= 1.0
