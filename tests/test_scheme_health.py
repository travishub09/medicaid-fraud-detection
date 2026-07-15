"""
test_scheme_health.py — per-scheme calc integrity + the run-to-run regression
alarm (the silent-degradation feedback loop).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from src.model_a.scheme_health import (audit_scheme_health, compare_to_baseline,
                                       write_health)


def _matrix(n=200, seed=0, with_upcoding=True, upcoding_constant=False):
    rng = np.random.default_rng(seed)
    cols = {
        "npi": [f"{1000000000 + i}" for i in range(n)],
        # graph feature (raw-fed) present and varying -> ownership_integrity healthy-ish
        "shell_score": rng.uniform(0, 1, n),
        "within_2_hops_of_exclusion": rng.integers(0, 2, n),
        "related_party_density_norm": rng.uniform(0, 1, n),
        "subscore_ownership_integrity": rng.uniform(0, 1, n),
    }
    if with_upcoding:
        # adapter feature reaches the subscore as __peerpct
        cols["em_high_level_share__peerpct"] = rng.uniform(0, 1, n)
        cols["em_level_mean__peerpct"] = rng.uniform(0, 1, n)
        cols["subscore_upcoding"] = (np.full(n, 0.3) if upcoding_constant
                                     else rng.uniform(0, 1, n))
    return pd.DataFrame(cols)


def test_status_classification():
    df = audit_scheme_health(_matrix()).set_index("scheme")
    # upcoding: both inputs present as __peerpct, subscore varies -> HEALTHY
    assert df.loc["upcoding", "status"] == "HEALTHY"
    assert df.loc["upcoding", "weight_present"] == 1.0
    # a scheme whose inputs are entirely un-procured -> DORMANT
    assert df.loc["cost_report_fraud", "status"] == "DORMANT"


def test_broken_when_subscore_constant():
    df = audit_scheme_health(_matrix(upcoding_constant=True)).set_index("scheme")
    assert df.loc["upcoding", "status"] == "BROKEN"
    assert df.loc["upcoding", "subscore_constant"] is True


def test_degraded_when_partial_inputs():
    m = _matrix()
    m = m.drop(columns=["em_level_mean__peerpct"])   # drop one of upcoding's two inputs
    df = audit_scheme_health(m).set_index("scheme")
    assert df.loc["upcoding", "status"] == "DEGRADED"
    assert df.loc["upcoding", "weight_present"] < 1.0
    assert "em_level_mean" in df.loc["upcoding", "missing_inputs"]


def test_regression_alarm_fires_on_dropped_source():
    healthy = audit_scheme_health(_matrix())
    # next run: upcoding lost an input (source silently dropped)
    degraded = audit_scheme_health(_matrix().drop(columns=["em_high_level_share__peerpct"]))
    regs = compare_to_baseline(degraded, healthy)
    kinds = {(r["scheme"], r["kind"]) for r in regs}
    assert ("upcoding", "status_drop") in kinds or ("upcoding", "weight_loss") in kinds


def test_write_health_roundtrip_and_baseline(tmp_path):
    manifest = {"scheme_coverage": {}}
    # first run: no baseline -> no regressions, writes json
    s1 = write_health(_matrix(), manifest, tmp_path)
    assert (tmp_path / "SCHEME_HEALTH.md").exists()
    assert (tmp_path / "scheme_health.json").exists()
    assert s1["regressions"] == []
    # second run in the SAME dir with a dropped source -> regression detected
    s2 = write_health(_matrix().drop(columns=["em_high_level_share__peerpct",
                                              "em_level_mean__peerpct"]),
                      manifest, tmp_path)
    assert any(r["scheme"] == "upcoding" for r in s2["regressions"])
