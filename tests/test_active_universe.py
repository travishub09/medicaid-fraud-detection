"""test_active_universe.py — the ACTIVE ruler: restrict the evaluation universe
to providers billing within N months of the freeze."""

from __future__ import annotations

import pandas as pd
import pytest

from src.model_a.label_lag import active_universe


def test_active_universe_keeps_recent_billers_only():
    m = pd.DataFrame({
        "npi": ["1", "2", "3", "4", "5"],
        "last_service_month": ["2021-12", "2021-01", "2019-06", None, "2022-03"],
        "f": [1, 2, 3, 4, 5],
    })
    out, st = active_universe(m, "2021-12", months=12)
    # 1 (0 mo) and 2 (11 mo) kept; 3 dormant 30 mo; 4 undated; 5 is AFTER the
    # freeze (a leak into the frozen matrix) and is dropped too
    assert list(out["npi"]) == ["1", "2"]
    assert st["n_before"] == 5 and st["n_after"] == 2
    assert abs(st["share_kept"] - 0.4) < 1e-9
    out6, _ = active_universe(m, "2021-12", months=6)
    assert list(out6["npi"]) == ["1"]


def test_active_universe_refuses_matrix_without_last_service_month():
    with pytest.raises(ValueError):
        active_universe(pd.DataFrame({"npi": ["1"]}), "2021-12")
