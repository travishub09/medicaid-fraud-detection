"""
test_v22_fixes_2026_10.py — the v21 read-out's fixes (v22 semantics):
(1) class priors weighted by the per-case label budget and floored at MIN_CLASS_N
    rows (a 199-NPI class with 49 positives from one settlement read lift 70 and
    emptied the NPI-grain top 1%);
(2) the lab mix shares clipped to [0, 1] (refund months read -0.0002);
(3) hospital-licensed NPIs (27xxxx / 28xxxx) are institutional for the dossier
    pick and a flag column on the list, never a filter on the graded book.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.model_a import class_priors as cp


def _frame(n_lab=199, n_doc=2000, seed=3):
    rng = np.random.default_rng(seed)
    tax = np.array(["207RH0000X"] * n_lab + ["207Q00000X"] * n_doc)
    y = np.zeros(len(tax), dtype=int)
    y[:49] = 1                                   # one settlement broadcast to 49 hematology NPIs
    y[n_lab:n_lab + 40] = 1                      # forty independent physician positives
    w = np.ones(len(tax)); w[:49] = 20 / 49      # the per-case budget (20 NPIs' worth)
    m = pd.DataFrame({"npi": [f"{1000000000 + i}" for i in range(len(tax))],
                      "primary_taxonomy": tax, "entity_type": "2", "practice_state": "OH"})
    return m, y, w


def test_size_floor_sends_a_thin_class_to_the_global_rate():
    m, y, w = _frame()
    keys = cp.prior_keys(m, ["taxonomy"])
    idx = np.arange(len(m)); rng = np.random.default_rng(0); rng.shuffle(idx)
    tr, te = idx[:1800], idx[1800:]
    _, ete = cp.encode_fold(keys, y, tr, te, min_n=300)
    g = y[tr].mean()
    kt = keys["taxonomy"].to_numpy()[te]
    assert np.allclose(ete[kt == "207RH0000X", 0], g)             # 199 rows < 300: the global rate
    assert not np.allclose(ete[kt == "207Q00000X", 0], g)         # the big class keeps its own rate
    # without the floor the thin class reads far above the base rate (the v21 shape)
    _, ete0 = cp.encode_fold(keys, y, tr, te, min_n=0)
    assert ete0[kt == "207RH0000X", 0].mean() > 3 * g


def test_label_weight_counts_a_broadcast_case_as_its_budget():
    m, y, w = _frame(n_lab=600)                   # above the floor, so the weights decide
    keys = cp.prior_keys(m, ["taxonomy"])
    idx = np.arange(len(m)); rng = np.random.default_rng(1); rng.shuffle(idx)
    tr, te = idx[:2200], idx[2200:]
    _, un = cp.encode_fold(keys, y, tr, te, min_n=0)
    _, wt = cp.encode_fold(keys, y, tr, te, min_n=0, w=w)
    kt = keys["taxonomy"].to_numpy()[te]
    assert wt[kt == "207RH0000X", 0].mean() < un[kt == "207RH0000X", 0].mean()   # 49 rows weigh as 20
    assert wt[kt == "207Q00000X", 0].mean() == pytest.approx(un[kt == "207Q00000X", 0].mean(), rel=0.05)
    rt = cp.rate_table(keys, y, w=w, min_n=300)
    row = rt.set_index("value").loc["207RH0000X"]
    assert row["positives"] == 49 and row["weighted_positives"] == pytest.approx(20.0) and not row["below_min_n"]
    thin = cp.rate_table(keys, y, w=w, min_n=1000).set_index("value").loc["207RH0000X"]
    assert thin["below_min_n"] and thin["shrunk_rate"] == pytest.approx(rt.attrs.get("base_rate") or (w * y).sum() / w.sum(), rel=1e-6)


def test_fold_design_passes_the_scorer_weights(monkeypatch):
    from src.model_a import company_list as cl
    seen = {}
    def fake_encode(keys, y, tr, te, m=50.0, w=None, min_n=300):
        seen["w"] = w
        return np.zeros((len(tr), 1)), np.zeros((len(te), 1))
    monkeypatch.setattr(cp, "encode_fold", fake_encode)
    m, y, w = _frame(n_lab=10, n_doc=10)
    m["label_weight"] = w
    keys = cp.prior_keys(m, ["taxonomy"])
    X = np.zeros((len(m), 2)); idx = np.arange(len(m))
    cl.fold_design(X, m, y, {"class_priors": ["taxonomy"]}, idx[:12], idx[12:], keys)
    assert seen["w"] is not None and seen["w"][0] == pytest.approx(20 / 49)


def test_lab_shares_are_clipped_on_refund_months(tmp_path):
    from src.ingest_cms.sector_schemes import sector_metrics_from_parquet
    df = pd.DataFrame({
        "billing_npi": ["1003000126"] * 3,
        "hcpcs_code": ["G0483", "81420", "80053"],
        "service_month": ["2024-01", "2024-01", "2024-01"],
        "total_paid": [5000.0, -2.0, 1000.0],          # a genetic-test refund month nets below zero
        "total_claim_lines": [50.0, 1.0, 40.0],
        "total_patients": [10.0, 1.0, 10.0],
    })
    p = tmp_path / "spending_fact.parquet"; df.to_parquet(p, index=False)
    out = sector_metrics_from_parquet(p).set_index("npi").loc["1003000126"]
    assert out["lab_genetic_share"] == 0.0 and 0 <= out["lab_definitive_drug_share"] <= 1


def test_hospital_licensed_orgs_are_flagged_and_kept_out_of_the_pick():
    from src.model_a.pursuit_dossier import hospital_org_ids, _INSTITUTIONAL_RE
    import re
    m = pd.DataFrame({"org_node_id": ["org:pac:1", "org:pac:1", "org:pac:2", "org:pac:3"],
                      "primary_taxonomy": ["282N00000X", "207Q00000X", "291U00000X", "273Y00000X"]})
    assert hospital_org_ids(m) == {"org:pac:1", "org:pac:3"}
    assert hospital_org_ids(pd.DataFrame({"org_node_id": ["x"]})) == set()
    assert re.search(_INSTITUTIONAL_RE, "GROUP HEALTH PLAN, INC.")
    # the flag is a column, not an entity_segment value (the graded book filters on the segment)
    import inspect
    from src.model_a import pursuit_dossier as pdm
    src = inspect.getsource(pdm.build_priority_list) if hasattr(pdm, "build_priority_list") else inspect.getsource(pdm)
    assert 'out["hospital_licensed"]' in src
