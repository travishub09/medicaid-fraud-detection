"""
test_plausibility.py — clinical-plausibility score, data-derived (expansion A5).

Builds a per-code, per-specialty prevalence matrix from the spending data, flags
codes implausibly rare for the billing provider's OWN taxonomy, dollar-weights
the share, and names the drivers. Thin taxonomies (too few providers to anchor a
prevalence) are never judged; the registry blends the percentile into
specialty_mismatch without regressing the v3-only path.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analytics.plausibility import (
    code_prevalence_matrix, org_clinical_plausibility, plausibility_percentiles,
    RARE_THRESHOLD,
)
from src.model_a.scheme_subscores import compute_subscores
from src.model_a.dossier import render_dossier
from src.model_a.__main__ import run as run_model_a
from src.entity_graph.__main__ import run as run_graph
from tests.fixtures.synthetic import build_synthetic_inputs, build_company_features


def _provider_dim(npi_taxonomy: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame({"npi": list(npi_taxonomy),
                         "taxonomy_code": list(npi_taxonomy.values())})


# ----------------------------------------------------------- prevalence ---

def test_prevalence_matrix_counts_and_assessable():
    # taxonomy HOSP has 6 providers: all bill Q5001, one also bills T1019.
    # taxonomy TINY has 2 providers (below MIN_TAXONOMY_PROVIDERS=5).
    rows = []
    hosp = [f"100300000{i}" for i in range(6)]
    for npi in hosp:
        rows.append({"billing_npi": npi, "hcpcs_code": "Q5001", "total_paid": 100.0})
    rows.append({"billing_npi": hosp[0], "hcpcs_code": "T1019", "total_paid": 100.0})
    rows.append({"billing_npi": "2003000001", "hcpcs_code": "80061", "total_paid": 100.0})
    rows.append({"billing_npi": "2003000002", "hcpcs_code": "80061", "total_paid": 100.0})
    spending = pd.DataFrame(rows)
    pdim = _provider_dim({**{n: "HOSP" for n in hosp},
                          "2003000001": "TINY", "2003000002": "TINY"})

    m = code_prevalence_matrix(spending, pdim).set_index(["taxonomy_code", "hcpcs"])
    assert m.loc[("HOSP", "Q5001"), "prevalence"] == 1.0          # all 6 bill it
    assert m.loc[("HOSP", "T1019"), "prevalence"] == pytest.approx(1 / 6)
    assert bool(m.loc[("HOSP", "Q5001"), "assessable"]) is True
    assert bool(m.loc[("TINY", "80061"), "assessable"]) is False  # only 2 providers


# ------------------------------------------------------- org scoring ---

def _injected_prevalence() -> pd.DataFrame:
    return pd.DataFrame([
        {"taxonomy_code": "251G00000X", "hcpcs": "T1019",
         "prevalence": 0.003, "assessable": True},      # implausible for hospice
        {"taxonomy_code": "251G00000X", "hcpcs": "Q5001",
         "prevalence": 0.95, "assessable": True},        # the bread-and-butter code
    ])


def test_implausible_share_is_dollar_weighted_with_named_driver():
    spending = pd.DataFrame([
        {"billing_npi": "1003000415", "hcpcs_code": "T1019", "total_paid": 2_000_000.0},
        {"billing_npi": "1003000415", "hcpcs_code": "Q5001", "total_paid": 1_000_000.0},
    ])
    pdim = _provider_dim({"1003000415": "251G00000X"})
    xw = pd.DataFrame({"npi": ["1003000415"], "org_node_id": ["org:hosp"]})

    out = org_clinical_plausibility(spending, pdim, xw,
                                    prevalence=_injected_prevalence()).iloc[0]
    # $2M of $3M assessable billing is on the implausible code → 2/3, NOT 1/2
    assert out["implausible_dollar_share"] == pytest.approx(2 / 3)
    assert out["implausible_payments"] == 2_000_000.0
    assert "T1019" in out["clinical_implausibility_driver"]
    assert "0.3%" in out["clinical_implausibility_driver"]        # prevalence shown
    assert "251G00000X" in out["clinical_implausibility_driver"]  # peer group named


def test_thin_taxonomy_never_judged_share_nan():
    spending = pd.DataFrame([
        {"billing_npi": "1003000407", "hcpcs_code": "ZZZ99", "total_paid": 500_000.0},
    ])
    pdim = _provider_dim({"1003000407": "RARE_TAX"})
    xw = pd.DataFrame({"npi": ["1003000407"], "org_node_id": ["org:thin"]})
    # the code has no prevalence row and its taxonomy is unassessable
    prev = pd.DataFrame([{"taxonomy_code": "RARE_TAX", "hcpcs": "ZZZ99",
                          "prevalence": 0.0, "assessable": False}])
    out = org_clinical_plausibility(spending, pdim, xw, prevalence=prev).iloc[0]
    assert out["assessable_payments"] == 0.0
    assert np.isnan(out["implausible_dollar_share"])              # nothing judgeable
    assert out["clinical_implausibility_driver"] == ""


def test_plausible_org_scores_zero_share():
    spending = pd.DataFrame([
        {"billing_npi": "1003000415", "hcpcs_code": "Q5001", "total_paid": 900_000.0},
    ])
    pdim = _provider_dim({"1003000415": "251G00000X"})
    xw = pd.DataFrame({"npi": ["1003000415"], "org_node_id": ["org:clean"]})
    out = org_clinical_plausibility(spending, pdim, xw,
                                    prevalence=_injected_prevalence()).iloc[0]
    assert out["implausible_dollar_share"] == 0.0
    assert out["clinical_implausibility_driver"] == ""


def test_default_threshold_uses_module_constant():
    # prevalence exactly at the boundary is NOT implausible (< is strict)
    spending = pd.DataFrame([
        {"billing_npi": "1003000415", "hcpcs_code": "BORDER", "total_paid": 100.0},
    ])
    pdim = _provider_dim({"1003000415": "T"})
    xw = pd.DataFrame({"npi": ["1003000415"], "org_node_id": ["org:b"]})
    prev = pd.DataFrame([{"taxonomy_code": "T", "hcpcs": "BORDER",
                          "prevalence": RARE_THRESHOLD, "assessable": True}])
    out = org_clinical_plausibility(spending, pdim, xw, prevalence=prev).iloc[0]
    assert out["implausible_payments"] == 0.0


def test_percentiles_rank_and_preserve_nan():
    op = pd.DataFrame({
        "org_node_id": ["a", "b", "c"],
        "implausible_dollar_share": [0.8, 0.1, np.nan],
        "clinical_implausibility_driver": ["$1 on X (0.1% of T peers)", "", ""],
    })
    pct = plausibility_percentiles(op).set_index("org_node_id")
    assert pct.loc["a", "clinical_implausibility"] > pct.loc["b", "clinical_implausibility"]
    assert np.isnan(pct.loc["c", "clinical_implausibility"])
    assert "X" in pct.loc["a", "clinical_implausibility_driver"]


# ----------------------------------------------------- registry wiring ---

def test_registry_blends_without_regressing_v3_only():
    # v3 path: only specialty_mismatch present → unchanged from weight-1 behavior
    only_v3 = pd.DataFrame({"specialty_mismatch": [0.9, 0.1]})
    subs_v3, cov_v3 = compute_subscores(only_v3)
    assert cov_v3["specialty_mismatch"] == ["specialty_mismatch"]

    # with the A5 feature attached, both blend
    both = pd.DataFrame({"specialty_mismatch": [0.9, 0.1],
                         "clinical_implausibility": [0.95, 0.0]})
    subs_both, cov_both = compute_subscores(both)
    assert cov_both["specialty_mismatch"] == ["clinical_implausibility",
                                              "specialty_mismatch"]
    # the high-implausibility org stays high; the clean one stays low
    assert subs_both.loc[0, "subscore_specialty_mismatch"] > 0.5
    assert subs_both.loc[1, "subscore_specialty_mismatch"] < 0.5


# ------------------------------------------------------- integration ---

def test_pipeline_renders_plausibility_driver(tmp_path):
    g = run_graph(build_synthetic_inputs(), tmp_path / "graph")
    org_nodes = g["nodes/org_nodes"]
    feats = build_company_features(org_nodes)
    # attach an A5 percentile + driver for the top org, as the --spending path would
    top = feats["org_node_id"].iloc[0]
    feats["clinical_implausibility"] = np.where(
        feats["org_node_id"] == top, 0.99, 0.0)
    feats["clinical_implausibility_driver"] = np.where(
        feats["org_node_id"] == top,
        "$2,000,000 on T1019 (billed by 0.3% of 251G00000X peers)", "")
    res = run_model_a(org_nodes, g["org_graph_features"], feats,
                      g["rings/shared_address_shells"],
                      g["rings/common_owner_clusters"],
                      tmp_path / "ma", top_k_dossiers=len(org_nodes))
    assert "clinical_implausibility" in res.columns
    hit = [p for p in (tmp_path / "ma" / "dossiers").glob("*.md")
           if "T1019" in p.read_text(encoding="utf-8")]
    assert hit, "the implausibility driver must appear on the flagged org's dossier"
    assert "Clinical-implausibility driver:" in hit[0].read_text(encoding="utf-8")
