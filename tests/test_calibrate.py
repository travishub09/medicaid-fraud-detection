"""test_calibrate.py — enforcement calibration: case DB → priors + PU model → Model A."""

from __future__ import annotations

import pytest

from src.entity_graph.__main__ import run as run_graph
from src.model_a.__main__ import run as run_model_a
from src.model_a.calibrate import calibrate, build_org_feature_matrix
from src.enforcement.case_db import build_case_db
from tests.fixtures.synthetic import build_synthetic_inputs, build_company_features


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    g = run_graph(build_synthetic_inputs(), tmp_path_factory.mktemp("g"))
    org_nodes = g["nodes/org_nodes"]
    return (org_nodes, g["org_graph_features"], build_company_features(org_nodes),
            g["rings/shared_address_shells"], g["rings/common_owner_clusters"])


def _cases(org_nodes, n, sector="home_health"):
    names = org_nodes["org_name"].astype(str).tolist()[:n]
    return build_case_db([
        {"case_id": f"c{i}", "defendant_name": nme, "sector": sector,
         "amount_usd": 1_000_000.0 * (i + 1), "announced_date": "2024-01-01"}
        for i, nme in enumerate(names)])


def test_calibrate_derives_priors_and_trains_pu_when_enough_positives(world):
    org_nodes, gf, cf, _, _ = world
    case_db = _cases(org_nodes, 6)
    priors, model, report = calibrate(case_db, org_nodes, gf, cf)
    assert "default" in priors and priors.get("home_health", 1.0) >= 1.0
    assert report["n_positive_orgs"] >= 5 and model is not None
    p = model.predict_proba(build_org_feature_matrix(org_nodes, gf, cf))
    assert len(p) == len(org_nodes) and ((p >= 0) & (p <= 1)).all()


def test_calibrate_priors_only_when_too_few_positives(world):
    org_nodes, gf, cf, _, _ = world
    case_db = _cases(org_nodes, 1, sector="lab")
    priors, model, report = calibrate(case_db, org_nodes, gf, cf)
    assert model is None and "default" in priors and report["n_positive_orgs"] < 5


def test_model_a_applies_priors_and_pu_model(world):
    org_nodes, gf, cf, shells, owners = world
    priors, model, _ = calibrate(_cases(org_nodes, 6), org_nodes, gf, cf)
    out = run_model_a(org_nodes, gf, cf, shells, owners,
                      __import__('pathlib').Path(__import__('tempfile').mkdtemp()), top_k_dossiers=1,
                      priors=priors, pu_model=model)
    assert "adjusted_prob_heuristic" in out.columns          # PU override applied
    assert out["adjusted_prob"].between(0, 1).all()
