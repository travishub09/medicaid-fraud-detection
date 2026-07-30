"""rank_lookup: rank table shape + masking."""

import numpy as np
import pandas as pd

from src.model_a.rank_lookup import rank_table


def test_rank_table_percentiles_and_flags():
    n = 100
    m = pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "provider_name": [f"PROV {i}" for i in range(n)],
        "addr_city": "X",
        "net_paid": 1.0,
    })
    scores = np.arange(n, dtype=float)          # provider 99 is riskiest
    y = np.zeros(n, dtype=int)
    y[99] = 1
    mask = np.zeros(n, dtype=bool)
    mask[[0, 50, 99]] = True
    t = rank_table(m, scores, y, mask)
    assert list(t["npi"]) == ["1000000099", "1000000050", "1000000000"]
    top = t.set_index("npi")
    assert top.loc["1000000099", "in_top_decile"] == "YES"
    assert top.loc["1000000099", "future_ban"] == "YES"
    assert top.loc["1000000050", "in_top_decile"] == "no"
    assert abs(top.loc["1000000000", "rank_pct"] - 0.01) < 0.02
