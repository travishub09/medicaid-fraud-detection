"""
test_limitation_fixes_2.py — the second limitation-mitigation batch.

Calibration layer (isotonic/Platt + reliability), PU class-prior + contamination-
corrected lift, billing-implied specialty (taxonomy-gaming defense), the CMS
Preclusion List exclusion adapter, and the export's assessability/group_id roles.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ------------------------------------------------------------- calibration ---
def test_isotonic_calibration_is_monotone_and_improves_brier():
    from src.model_a.calibration import (fit_calibrator, calibration_metrics,
                                          reliability_table)
    rng = np.random.default_rng(0)
    # a miscalibrated but well-RANKED raw score: squashed toward the middle
    y = (rng.random(2000) < 0.2).astype(int)
    raw = np.clip(0.5 + 0.15 * (y - 0.5) * 4 + rng.normal(0, 0.1, len(y)), 0, 1)
    cal = fit_calibrator(raw, y, method="isotonic")
    p = cal.predict(raw)
    # monotone in the raw score
    order = np.argsort(raw)
    assert np.all(np.diff(p[order]) >= -1e-9)
    # calibration error drops after fitting
    before = calibration_metrics(raw, y)["ece"]
    after = calibration_metrics(p, y)["ece"]
    assert after <= before + 1e-9
    tbl = reliability_table(p, y, n_bins=5)
    assert {"mean_predicted", "frac_positive", "count"}.issubset(tbl.columns)


def test_platt_falls_back_when_holdout_single_class():
    from src.model_a.calibration import fit_calibrator
    cal = fit_calibrator(np.linspace(0, 1, 10), np.zeros(10, dtype=int), method="platt")
    assert cal.method == "isotonic"            # degenerate → isotonic fallback
    assert (cal.predict([0.5]) >= 0).all()


# --------------------------------------------------------------- PU prior ---
def test_class_prior_and_corrected_lift():
    from src.model_a.pu_prior import (estimate_label_frequency, estimate_class_prior,
                                       corrected_lift)
    rng = np.random.default_rng(1)
    n = 5000
    y = (rng.random(n) < 0.10).astype(int)          # true offender rate 10%
    c_true = 0.4                                     # only 40% of offenders are caught
    s = ((y == 1) & (rng.random(n) < c_true)).astype(int)
    g = np.where(y == 1, 0.4, 0.05) + rng.normal(0, 0.01, n)   # a decent g(x)
    c = estimate_label_frequency(g, s)
    assert 0.2 < c < 0.6                             # recovers ~c_true
    pi = estimate_class_prior(s, c)
    assert pi > s.mean()                             # prior exceeds the labeled rate
    # contamination correction rescales the caught count by c, so the corrected
    # precision is always >= the naive precision (uncaught offenders recovered).
    res = corrected_lift(y + rng.normal(0, 0.01, n), s, c, k_frac=0.1)
    assert res["corrected_precision_at_k"] >= res["naive_precision_at_k"] - 1e-9
    assert 0 <= res["corrected_precision_at_k"] <= 1


# ----------------------------------------------------- preclusion adapter ---
def test_preclusion_normalizes_to_exclusion_schema():
    from src.enforcement.preclusion import normalize_preclusion, EXCLUSION_COLS
    raw = pd.DataFrame({
        "NPI": ["1003000100", ""],
        "Last Name": ["SMITH", "DOE"],
        "First Name": ["JOHN", "JANE"],
        "Preclusion Date": ["2023-01-15", "2022-06-01"],
        "Preclusion Reason": ["Felony", "Conduct detrimental"],
        "Preclusion End Date": ["", "2099-01-01"],
    })
    out = normalize_preclusion(raw, as_of="2024-01-01")
    assert list(out.columns) == EXCLUSION_COLS
    assert (out["excl_type"].str.startswith("preclusion")).all()
    assert out["currently_active"].sum() == 2        # one no-end, one future-end
    assert out.loc[out["npi"] == "1003000100", "name_key"].iloc[0] != ""


def test_export_label_source_maps_preclusion():
    from src.model_a.provider_features_export import _label_source
    assert _label_source("preclusion: Felony") == "preclusion"
    assert _label_source("medicare_revocation: x") == "medicare_revocation"
    assert _label_source("LEIE 1128(a)(1)") == "leie"


# ------------------------------------------------ billing-implied specialty ---
def _emb_frame():
    from src.model_a.billing_lm import EMB_PREFIX
    rng = np.random.default_rng(2)
    rows, dim = [], 4
    # two specialties with separated embeddings; one "impostor" claims T_A but
    # bills like T_B (sits in the T_B cluster).
    for i in range(40):
        base = np.array([1.0, 0, 0, 0]) if i < 20 else np.array([0, 1.0, 0, 0])
        v = base + rng.normal(0, 0.05, dim)
        rows.append({"npi": f"{i:010d}", **{f"{EMB_PREFIX}{j}": v[j] for j in range(dim)},
                     "taxonomy_code": "T_A" if i < 20 else "T_B"})
    df = pd.DataFrame(rows)
    # impostor: claims T_A but embedding sits in the T_B cluster
    imp = {"npi": "9999999999", "taxonomy_code": "T_A"}
    iv = np.array([0, 1.0, 0, 0]) + rng.normal(0, 0.05, dim)
    imp.update({f"{EMB_PREFIX}{j}": iv[j] for j in range(dim)})
    df = pd.concat([df, pd.DataFrame([imp])], ignore_index=True)
    return df


def test_billing_implied_taxonomy_flags_impostor():
    from src.model_a.billing_specialty import implied_specialty
    df = _emb_frame()
    pdim = df[["npi", "taxonomy_code"]]
    out = implied_specialty(df, pdim, min_providers=5).set_index("npi")
    # the impostor claims T_A but its billing implies T_B → mismatch, positive margin
    assert out.loc["9999999999", "billing_implied_taxonomy"] == "T_B"
    assert out.loc["9999999999", "billing_taxonomy_mismatch"] == 1.0
    assert out.loc["9999999999", "billing_taxonomy_margin"] > 0
    # a typical T_A provider matches its claim
    assert out.loc["0000000000", "billing_taxonomy_mismatch"] == 0.0


# ----------------------------------------------- export assessability/group ---
def test_export_emits_group_id_and_assessable(tmp_path):
    from src.entity_graph.__main__ import run as run_graph
    from src.entity_graph.graph_embeddings import to_provider_grain
    from src.model_a.provider_features_export import build_provider_matrix
    from tests.fixtures.synthetic import build_synthetic_inputs, build_provider_leads
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    leads = build_provider_leads(inputs["provider_dim"])
    pe = to_provider_grain(out["node_embeddings"], out["npi_to_org"])
    matrix, manifest = build_provider_matrix(
        leads, out["npi_to_org"], org_graph_features=out["org_graph_features"],
        adapter_npi_frames={"graph_embeddings": pe}, min_peer=5)
    assert manifest["group_cols"] == ["group_id"]
    assert manifest["assessability"] == ["assessable"]
    assert "group_id" in matrix.columns and "assessable" in matrix.columns
    # neither is a trainable feature
    assert "group_id" not in manifest["raw_feature_cols"]
    assert "assessable" not in manifest["raw_feature_cols"]
    assert set(matrix["assessable"].unique()).issubset({0, 1})
