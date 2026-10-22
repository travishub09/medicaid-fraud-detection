"""
test_scheme_health_population_split_2026_10.py — the forward FCA AUC split by entity
type (docs/SCHEME_DEEP_DIVE_2026-10-22 finding 1): a scheme that separates the
organization cases and not the individual ones reads as 'wrong population', not noise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def test_fca_auc_is_reported_per_entity_type():
    from src.model_a.scheme_health import audit_scheme_health, to_markdown
    rng = np.random.default_rng(7)
    n = 600
    et = np.where(np.arange(n) % 2 == 0, "1", "2")
    y = (rng.random(n) < 0.15).astype(int)
    # the subscore separates ORGANIZATION cases only; on individuals it is noise
    sub = rng.random(n)
    sub = np.where((et == "2") & (y == 1), sub * 0.4 + 0.6, sub)
    m = pd.DataFrame({"npi": [f"{1000000000 + i}" for i in range(n)], "entity_type": et,
                      "subscore_toy": sub, "toy_in": rng.random(n), "provider_on_exclusion": 0})
    fwd = pd.DataFrame({"npi": m["npi"], "is_prospective_positive": y.astype(str),
                        "was_excluded_pre_cutoff": "0"})
    df = audit_scheme_health(m, {"label": "provider_on_exclusion"},
                             weights={"toy": {"toy_in": 1.0}}, forward=fwd)
    r = df.set_index("scheme").loc["toy"]
    assert r["fca_auc_org"] is not None and r["fca_auc_org"] > 0.8
    assert r["fca_auc_ind"] is not None and abs(r["fca_auc_ind"] - 0.5) < 0.12
    assert r["n_fca_pos_org"] + r["n_fca_pos_ind"] == r["n_fca_pos"]
    md = to_markdown(df, [])
    assert "FCA AUC ind (pos)" in md and "wrong" in md
    # without an entity_type column the split is simply absent
    df2 = audit_scheme_health(m.drop(columns=["entity_type"]), {"label": "provider_on_exclusion"},
                              weights={"toy": {"toy_in": 1.0}}, forward=fwd)
    assert df2.iloc[0]["fca_auc_org"] is None
