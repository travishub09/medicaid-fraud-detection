"""test_label_lag.py — visibility of forward positives at the freeze, per tier."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.model_a.label_lag import lag_table, load_matrix_slim, to_markdown


def test_lag_table_separates_dormant_from_billing(tmp_path: Path):
    matrix = pd.DataFrame({
        "npi": ["1", "2", "3", "4"],
        "net_paid": [1e6, 2e5, 5e4, 9e5],
        "last_service_month": ["2023-11", "2020-06", "2023-12", "2021-01"],
        "first_service_month": ["2015-01"] * 4,
        "extra_feature": [0.1, 0.2, 0.3, 0.4],
    })
    mp = tmp_path / "provider_features_for_model.parquet"
    matrix.to_parquet(mp, index=False)
    m = load_matrix_slim(mp)
    assert "extra_feature" not in m.columns and "net_paid" in m.columns

    label = pd.DataFrame({
        "npi": ["1", "2", "3", "4", "5"],
        "is_prospective_positive": ["1", "1", "1", "1", "1"],
        "first_excl_date": ["2024-06-01", "2024-06-01", "2025-01-01", "2024-01-01", "2024-03-01"],
        "fca_tier": ["hard", "hard", "excluded", "hard", "hard"],
    })
    t = lag_table(label, m, "2023-12").set_index("tier")
    hard = t.loc["hard"]
    assert hard["positives"] == 4 and hard["in_universe"] == 3      # npi 5 not in universe
    # npi 1 billed in 2023-11 (recent); npi 2 stopped 2020-06 (dormant 42 mo); npi 4 2021-01 (35 mo)
    assert abs(hard["billing_in_last_6mo_share"] - 1 / 3) < 1e-9
    assert abs(hard["dormant_24mo_share"] - 2 / 3) < 1e-9
    assert hard["median_months_last_bill_to_freeze"] == 35
    exc = t.loc["excluded"]
    assert exc["billing_in_last_6mo_share"] == 1.0 and exc["dormant_24mo_share"] == 0.0
    md = to_markdown({"future_bans_after_2023-12__hard.csv": t.reset_index()}, "2023-12")
    assert "| hard | 4 | 3 (75%) | 33% | 67% | 35 |" in md
    assert "reading B" in md and "reading A" in md
