"""
test_class_priors_and_lab_scheme_2026_10.py — the two v19 lessons built for v21.

(1) CLASS PRIORS (model_a/class_priors.py): the class base rate (taxonomy / family /
    entity / state) target-encoded OUT-OF-FOLD — a test row never sees its own label,
    a training row is encoded leave-one-out, thin classes shrink to the base rate,
    and the policy threads from the manifest / CLI override through both fold loops.
(2) LABORATORY TESTING (ingest_cms/sector_schemes.py + scheme_subscores): the lab
    code families (definitive toxicology, genetic, respiratory panels, chemistry
    panels, venipuncture) -> lab_* metrics (NULL without lab dollars; mix shares of the
    lab dollars) -> subscore_lab_testing, registered everywhere a scheme must be.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.model_a import class_priors as cp


def _keys_and_labels(n=400, seed=1):
    rng = np.random.default_rng(seed)
    tax = rng.choice(["291U00000X", "207Q00000X", "363L00000X"], size=n, p=[0.1, 0.7, 0.2])
    ent = rng.choice(["1", "2"], size=n)
    # labs carry a 30% positive rate, everyone else 2%
    y = np.where(tax == "291U00000X", rng.random(n) < 0.30, rng.random(n) < 0.02).astype(int)
    m = pd.DataFrame({"npi": [f"{1000000000 + i}" for i in range(n)],
                      "primary_taxonomy": tax, "entity_type": ent, "practice_state": "TX"})
    return m, y


def test_parse_spec_and_feature_names():
    assert cp.parse_spec(None) == [] and cp.parse_spec("") == [] and cp.parse_spec("none") == []
    assert cp.parse_spec("taxonomy, entity") == ["taxonomy", "entity"]
    assert cp.parse_spec(["entity", "entity"]) == ["entity"]
    assert cp.feature_names(["taxonomy"]) == ["prior_taxonomy"]
    with pytest.raises(ValueError):
        cp.parse_spec("zipcode")


def test_prior_keys_derive_family_and_bucket_missing():
    m = pd.DataFrame({"primary_taxonomy": ["291U00000X", "207Q00000X", None],
                      "entity_type": ["2.0", "1", None]})
    k = cp.prior_keys(m, ["taxonomy", "family", "entity"])
    assert list(k["taxonomy"]) == ["291U00000X", "207Q00000X", cp.UNKNOWN]
    assert list(k["family"]) == ["other", "physician", cp.UNKNOWN]
    assert list(k["entity"]) == ["2", "1", cp.UNKNOWN]
    # a key the matrix lacks is one bucket, never a crash
    assert (cp.prior_keys(m, ["state"])["state"] == cp.UNKNOWN).all()


def test_encode_fold_is_leave_one_out_and_shrinks_thin_classes():
    m, y = _keys_and_labels()
    keys = cp.prior_keys(m, ["taxonomy"])
    idx = np.arange(len(m))
    tr, te = idx[: 300], idx[300:]
    etr, ete = cp.encode_fold(keys, y, tr, te, m=10.0, min_n=0)      # min_n=0: the v21 encoder (no size floor)
    assert etr.shape == (300, 1) and ete.shape == (100, 1)
    # the lab class reads far above the physician class on the test side
    lab_te = ete[(keys["taxonomy"].to_numpy()[te] == "291U00000X"), 0]
    doc_te = ete[(keys["taxonomy"].to_numpy()[te] == "207Q00000X"), 0]
    assert lab_te.mean() > 3 * doc_te.mean()
    # test rows of one class share ONE value (full-train statistics, no own label)
    assert np.allclose(lab_te, lab_te[0])
    # training rows are leave-one-out: within a class, a positive row reads LOWER
    # than a negative row (its own 1 was subtracted) — never its own label leaking in
    kt = keys["taxonomy"].to_numpy()[tr]
    pos = etr[(kt == "291U00000X") & (y[tr] == 1), 0]
    neg = etr[(kt == "291U00000X") & (y[tr] == 0), 0]
    assert pos.mean() < neg.mean()
    # a class with ONE training row and a positive label shrinks to the base rate
    keys2 = keys.copy()
    keys2.loc[tr[0], "taxonomy"] = "SOLO"
    y2 = y.copy(); y2[tr[0]] = 1
    etr2, _ = cp.encode_fold(keys2, y2, tr, te, m=50.0, min_n=0)
    base = y2[tr].mean()
    assert abs(etr2[0, 0] - base) < 0.02          # (1 - 1 + 50 g) / (0 + 50) = g


def test_rate_table_lists_every_class_with_lift():
    m, y = _keys_and_labels()
    rt = cp.rate_table(cp.prior_keys(m, ["taxonomy", "entity"]), y, min_n=0)     # min_n=0: the v21 table (no size floor)
    assert set(rt["key"]) == {"taxonomy", "entity"}
    lab = rt[(rt["key"] == "taxonomy") & (rt["value"] == "291U00000X")].iloc[0]
    assert lab["lift"] > 3 and lab["positives"] > 0
    assert rt.attrs["base_rate"] == pytest.approx(y.mean())


def test_policy_threads_from_manifest_and_cli_into_the_scorer(monkeypatch):
    """Both fold loops widen X by one column per prior key, computed per fold; the
    manifest carries it, the CLI override wins, '' switches it off."""
    import src.model_a.company_list as cl
    from src.model_a import accuracy_diagnostics as ad
    from src.model_a import network_ab as nab
    widths = []

    def fake_fit(Xtr, ytr, Xte, seed=0, sample_weight=None, **_):
        widths.append((Xtr.shape[1], Xte.shape[1]))
        return np.linspace(0, 1, len(Xte))

    monkeypatch.setattr(cl, "_fit_predict", fake_fit)
    monkeypatch.setattr(nab, "_fit_predict", fake_fit)      # the diagnostics import it per call
    m, y = _keys_and_labels(n=240)
    m["org_node_id"] = [f"org:{i % 30}" for i in range(len(m))]
    m["group_id"] = m["org_node_id"]
    m["org_display_name"] = m["org_node_id"]
    m["provider_on_exclusion"] = y
    m["net_paid"] = 1e5
    m["shell_score"] = np.random.default_rng(0).normal(size=len(m))
    man = {"raw_feature_cols": ["shell_score"], "peerpct_cols": [], "subscore_cols": [],
           "embedding_cols": [], "leakage_hard": [], "sources_used": {"entity_graph": ["shell_score"]},
           "scheme_coverage": {}, "training_policy": {"n_seeds": 1, "broadcast_rows": "weight",
                                                      "group_policy": "org", "class_priors": ["taxonomy", "entity"]}}
    cl.set_training_policy(None, None, None)
    try:
        assert cl.training_policy(man)["class_priors"] == ["taxonomy", "entity"]
        cl.oof_scores(m, man, n_splits=2)
        assert widths and all(w == (3, 3) for w in widths)          # 1 feature + 2 priors
        widths.clear()
        # the diagnostics' loop takes the same policy
        amb = np.zeros(len(m), dtype=bool)
        ad.oof_with_folds(m, man, ["shell_score"], y, amb, n_splits=2)
        assert widths and all(w == (3, 3) for w in widths)
        widths.clear()
        # CLI override: '' = no priors even though the manifest asks for them
        cl.set_training_policy(class_priors="")
        assert cl.training_policy(man)["class_priors"] == []
        cl.oof_scores(m, man, n_splits=2)
        assert all(w == (1, 1) for w in widths)
        widths.clear()
        cl.set_training_policy(class_priors="family")
        cl.oof_scores(m, man, n_splits=2)
        assert all(w == (2, 2) for w in widths)
    finally:
        cl.set_training_policy(None, None, None)
    # the manifest + registry agree on the spelling
    from src.model_a.run_registry import CHECK_ALIASES
    assert CHECK_ALIASES["priors"] == "class_priors"


def test_export_manifest_records_class_priors_policy():
    from src.model_a.provider_features_export import build_provider_matrix
    import inspect
    sig = inspect.signature(build_provider_matrix)
    assert "class_priors" in sig.parameters and sig.parameters["class_priors"].default is None


# --- (2) laboratory testing ---------------------------------------------------

def _lab_fact(tmp_path):
    df = pd.DataFrame({
        "billing_npi": ["1003000126"] * 5 + ["1003000134"] * 2 + ["1003000142"],
        "hcpcs_code": ["G0483", "81420", "80053", "36415", "99213",   # a lab: tox + genetic + panel + draw + one visit
                       "87804", "99213",                            # a clinic with one rapid flu test
                       "99213"],                                    # no lab dollars at all
        "service_month": ["2024-01"] * 8,
        "total_paid": [5000.0, 3000.0, 1000.0, 500.0, 500.0, 200.0, 1800.0, 900.0],
        "total_claim_lines": [50.0, 10.0, 40.0, 50.0, 5.0, 2.0, 18.0, 9.0],
        "total_patients": [10.0, 10.0, 10.0, 10.0, 5.0, 2.0, 18.0, 9.0],
    })
    p = tmp_path / "spending_fact.parquet"
    df.to_parquet(p, index=False)
    return p


def test_lab_metrics_size_and_mix(tmp_path):
    from src.ingest_cms.sector_schemes import LAB_METRICS, sector_metrics_from_parquet
    out = sector_metrics_from_parquet(_lab_fact(tmp_path)).set_index("npi")
    lab = out.loc["1003000126"]
    assert lab["lab_paid_share"] == pytest.approx(9500.0 / 10000.0)
    assert lab["lab_lines_per_patient"] == pytest.approx(150.0 / 40.0)
    assert lab["lab_definitive_drug_share"] == pytest.approx(5000.0 / 9500.0)
    assert lab["lab_genetic_share"] == pytest.approx(3000.0 / 9500.0)
    assert lab["lab_resp_panel_share"] == pytest.approx(0.0)        # observed zero: a real mix fact
    clinic = out.loc["1003000134"]
    assert clinic["lab_paid_share"] == pytest.approx(200.0 / 2000.0)
    assert clinic["lab_resp_panel_share"] == pytest.approx(1.0)
    # no lab dollars at all: absent from the table (NULL, never zero)
    assert "1003000142" not in out.index or pd.isna(out.loc["1003000142", "lab_paid_share"])
    assert set(LAB_METRICS) <= set(out.columns)


def test_lab_code_set_matches_the_predicate_families():
    from src.ingest_cms.sector_schemes import lab_code_set
    s = lab_code_set()
    assert {"G0480", "G0483", "80307", "81420", "81406", "81220", "87804", "U0003", "82247",
            "83036", "36416", "80081", "86480", "87536"} <= s
    assert "99213" not in s and "H0004" not in s


def test_lab_testing_registered_end_to_end():
    from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS, compute_subscores
    from src.model_a.scheme_code_families import family_for
    from src.model_a.sector_priors import SCHEME_EXPOSURE_BASIS, SCHEME_RECOVERY_MULTIPLIER
    from src.model_a.dossier import SCHEME_NARRATIVES
    from src.model_a.case_validation import SECTOR_TO_SUBSCHEME, DOJ_TO_SUBSCHEME
    from src.model_a.provider_features_export import ADAPTER_FEATURE_COLS
    assert "lab_testing" in DEFAULT_SCHEME_WEIGHTS
    assert family_for("lab_testing")[0] == "codes" and "G0483" in family_for("lab_testing")[1]
    assert SCHEME_EXPOSURE_BASIS["lab_testing"] == "own_billing"
    assert 0 < SCHEME_RECOVERY_MULTIPLIER["lab_testing"] <= 1
    assert "lab_testing" in SCHEME_NARRATIVES
    assert "lab_testing" in SECTOR_TO_SUBSCHEME["lab"] and "lab_testing" in DOJ_TO_SUBSCHEME["medical_necessity"]
    # the export peer-normalizes every weight-table input: the lab metrics are in
    for c in ("lab_paid_share", "lab_lines_per_patient", "lab_definitive_drug_share",
              "lab_genetic_share", "lab_resp_panel_share"):
        assert c in ADAPTER_FEATURE_COLS
    feats = pd.DataFrame({"lab_paid_share": [0.99, None], "lab_lines_per_patient": [0.97, None],
                          "lab_definitive_drug_share": [0.98, None], "lab_genetic_share": [0.5, None],
                          "lab_resp_panel_share": [0.1, None]})
    subs, cov = compute_subscores(feats)
    assert subs["subscore_lab_testing"].iloc[0] > 0.5
    assert pd.isna(subs["subscore_lab_testing"].iloc[1])          # NULL-aware
    assert cov["lab_testing"] == sorted(feats.columns)
