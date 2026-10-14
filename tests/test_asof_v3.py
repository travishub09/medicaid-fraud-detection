"""
test_asof_v3.py — the five v3 concept scores recomputed point-in-time (2026-10-14).

Planted truths on a synthetic spending fact: a single-code biller reads top
concentration; a provider's scores do not change when its POST-cutoff billing
changes (the leakage the frozen runs used to drop the scores to avoid); a provider
with only post-cutoff rows is absent; the export's freeze step restores the five
columns instead of dropping them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.model_a.asof_v3 import V3_SCORE_COLS, asof_v3_scores
from src.model_a.provider_features_export import _apply_asof_freeze

CUT = "2021-12"


def _luhn(body9: str) -> str:
    digits = [int(d) for d in "80840" + body9]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d = d * 2
            if d > 9:
                d -= 9
        total += d
    return body9 + str((10 - total % 10) % 10)


def _fact(seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    rows, dim = [], []
    months = [f"{y}-{m:02d}" for y in (2019, 2020, 2021, 2022) for m in range(1, 13)]
    codes = [f"9921{k}" for k in range(1, 6)]
    for tax_i, tax in enumerate(("207Q00000X", "363L00000X")):
        for j in range(45):
            npi = _luhn(f"{tax_i + 1}{j:08d}")
            dim.append({"npi": npi, "primary_taxonomy": tax, "entity_type": "1"})
            for ym in months:
                for code in codes:
                    paid = float(rng.uniform(800, 1200))
                    rows.append({"billing_npi": npi, "hcpcs_code": code, "service_month": ym,
                                 "total_paid": paid, "total_claim_lines": 20.0, "total_patients": 10.0})
    # the single-code biller (planted concentration) and its twin that differs ONLY after the cutoff
    for npi, post_mult in (("CONC_A", 1.0), ("CONC_B", 50.0)):
        n = _luhn("9" + ("1" if npi == "CONC_A" else "2") + "0000001")
        dim.append({"npi": n, "primary_taxonomy": "207Q00000X", "entity_type": "1"})
        for ym in months:
            mult = post_mult if ym > CUT else 1.0
            rows.append({"billing_npi": n, "hcpcs_code": "99215", "service_month": ym,
                         "total_paid": 5000.0 * mult, "total_claim_lines": 60.0, "total_patients": 30.0})
    # a provider that exists only after the cutoff
    n_post = _luhn("930000001")
    dim.append({"npi": n_post, "primary_taxonomy": "207Q00000X", "entity_type": "1"})
    for ym in months:
        if ym > CUT:
            rows.append({"billing_npi": n_post, "hcpcs_code": "99213", "service_month": ym,
                         "total_paid": 1000.0, "total_claim_lines": 20.0, "total_patients": 10.0})
    return pd.DataFrame(rows), pd.DataFrame(dim)


@pytest.fixture(scope="module")
def scored(tmp_path_factory):
    fact, dim = _fact()
    p = tmp_path_factory.mktemp("asof") / "spending_fact.parquet"
    fact.to_parquet(p, index=False)
    out = asof_v3_scores(p, CUT, dim, log=lambda *_: None)
    return out, fact, dim


def test_shape_and_ranges(scored):
    out, fact, dim = scored
    pre = set(fact.loc[fact["service_month"] < CUT, "billing_npi"])
    assert set(out["npi"]) == pre
    assert out["npi"].is_unique
    for c in V3_SCORE_COLS:
        v = out[c].dropna()
        assert len(v) > 80 and v.between(0, 1).all(), c
    assert "n_concept_signals" in out.columns and "log_max_single_month" in out.columns


def test_only_post_cutoff_provider_is_absent(scored):
    out, fact, dim = scored
    post_only = set(fact["billing_npi"]) - set(fact.loc[fact["service_month"] < CUT, "billing_npi"])
    assert post_only and not out["npi"].isin(post_only).any()


def test_planted_concentration_and_post_cutoff_invariance(scored):
    out, fact, dim = scored
    s = out.set_index("npi")
    a = _luhn("910000001")
    b = _luhn("920000001")
    assert s.loc[a, "concentration"] >= 0.95
    # the twin differs only AFTER the cutoff: every as-of score is identical
    for c in V3_SCORE_COLS + ["log_max_single_month", "n_concept_signals"]:
        va, vb = s.loc[a, c], s.loc[b, c]
        assert (pd.isna(va) and pd.isna(vb)) or va == pytest.approx(vb), c


def test_freeze_step_restores_the_five_scores(tmp_path):
    fact, dim = _fact()
    p = tmp_path / "spending_fact.parquet"
    fact.to_parquet(p, index=False)
    leads = dim.copy()
    for c in V3_SCORE_COLS + ["anomaly_score", "temporal"]:
        leads[c] = 0.5                      # full-history values the freeze must not keep
    leads["gross_paid"] = 1.0
    msgs = []
    out = _apply_asof_freeze(leads, p, CUT, msgs.append)
    assert len(out) == len(leads)
    for c in V3_SCORE_COLS:
        assert c in out.columns and out[c].notna().sum() > 80, c
    assert "anomaly_score" not in out.columns
    a = _luhn("910000001")
    assert out.set_index("npi").loc[a, "concentration"] >= 0.95
    assert any("asof_v3" in m for m in msgs)
    # the opt-out keeps the v6..v17 behaviour (scores dropped)
    out2 = _apply_asof_freeze(leads, p, CUT, msgs.append, with_v3=False)
    assert not any(c in out2.columns for c in V3_SCORE_COLS)
