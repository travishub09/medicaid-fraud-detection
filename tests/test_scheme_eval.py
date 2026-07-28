"""
test_scheme_eval.py — the scheme-stratified source eval on a synthetic matrix.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.scheme_eval import run_scheme_eval, to_markdown


def _matrix(n=600, seed=0):
    rng = np.random.default_rng(seed)
    kick = np.zeros(n, dtype=int)
    kick[:60] = 1                                # 60 kickback-case providers
    scheme = np.where(kick == 1, "kickback", "")
    scheme[60:70] = "unrelated_scheme"           # other-case rows (must drop)
    return pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "group_id": [f"g{i % 120}" for i in range(n)],
        "provider_on_exclusion": 0,
        "fraud_scheme": scheme,
        "shell_score": rng.normal(0, 1, n),                     # core noise
        "net_paid": rng.normal(0, 1, n),                        # core noise
        "op_payment_concentration": rng.normal(0, 1, n) + 2.0 * kick,  # signal
    })


def _manifest():
    return {
        "raw_feature_cols": ["shell_score", "net_paid",
                             "op_payment_concentration"],
        "peerpct_cols": [], "subscore_cols": [], "embedding_cols": [],
        "leakage_hard": [],
        "sources_used": {
            "entity_graph": ["shell_score"], "spending": ["net_paid"],
            "open_payments": ["op_payment_concentration"]},
        "scheme_coverage": {},
    }


def test_source_wins_on_its_own_scheme():
    res = run_scheme_eval(_matrix(), _manifest(), min_pos=20, n_splits=3,
                          n_boot=100)
    assert "open_payments" in res["results"]
    r = res["results"]["open_payments"]
    assert r["n_pos"] == 60
    # other-scheme case rows dropped from the eval universe
    assert r["n_eval"] == 600 - 10
    # the kickback-signal feature carries its own scheme: marginal positive
    assert r["source_marginal"]["lift10"]["delta"] > 0
    assert r["full_vs_core"]["lift10"]["delta"] > 0
    md = to_markdown(res)
    assert "kickback-type cases" in md and "EARNS ITS KEEP" in md


def test_thin_schemes_are_skipped_not_judged():
    m = _matrix()
    m.loc[m["fraud_scheme"] == "kickback", "fraud_scheme"] = ""
    m.loc[:5, "fraud_scheme"] = "kickback"       # only 6 positives
    res = run_scheme_eval(m, _manifest(), min_pos=30, n_boot=50)
    assert "open_payments" in res["skipped"]
    assert "6" in res["skipped"]["open_payments"]
    assert "harvest gap" in to_markdown(res)


def test_missing_fraud_scheme_column_is_a_clear_error():
    m = _matrix().drop(columns=["fraud_scheme"])
    res = run_scheme_eval(m, _manifest())
    assert "fraud_scheme" in res["error"]
