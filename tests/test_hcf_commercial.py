"""
test_hcf_commercial.py — commercial-plan fraud as a TRAINING-only label source (2026-10-14).

A provider convicted of billing a private plan falsely is source ``hcf_commercial``:
loaded from a reviewed state sweep (research_evidence commercial-cases) on a conviction /
plea / judgment only, trains under --label-tier hard+commercial, never flips the hard
label, never a forward positive, never the strict PurpleLab label.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.enforcement.case_harvest_prompts import build_prompts
from src.enforcement.research_evidence import load_commercial_cases
from src.entity_graph.__main__ import run as run_graph
from src.entity_graph.resolve_entities import norm_org_name
from src.model_a import company_list as cl
from src.model_a.case_labels import COMMERCIAL_SOURCE, build_case_labels
from src.model_a.fca_label_export import outcome_tier
from src.model_a.fca_relevance import TIER_POLICIES, tier_mask
from src.model_a.prospective_label import build_forward_case_label
from src.model_a.provider_features_export import build_provider_matrix
from tests.fixtures.synthetic import build_synthetic_inputs, build_provider_leads

RING_NPI = "1003000100"


def test_loader_admits_outcomes_and_quarantines_charges():
    table = pd.DataFrame([
        {"defendant": "Sunrise Chiropractic PC", "date": "2021-05-05", "outcome": "pleaded guilty",
         "summary": "billed Aetna and Blue Cross for visits never rendered", "amount": "$120,000",
         "url": "https://ag.state.fl.us/x", "payer_type": "commercial", "npi": "1003000100"},
        {"defendant": "Dr. Pending", "date": "2021-06-06", "outcome": "charged",
         "summary": "arrested and charged with insurance fraud", "amount": "", "url": "https://ag/y"},
        {"defendant": "Judgment Labs LLC", "date": "2021-07-07", "outcome": "civil judgment",
         "summary": "a judgment was entered for fraudulent workers compensation billing", "amount": "",
         "url": "", "payer_type": "workers_comp"},
    ])
    cases, quarantine = load_commercial_cases(table, "fl")
    assert list(cases["defendant_name"]) == ["Sunrise Chiropractic PC", "Judgment Labs LLC"]
    assert (cases["case_type"] == "hcf_commercial").all()
    assert cases.iloc[0]["jurisdiction"].startswith("FL commercial-plan")
    assert cases.iloc[0]["npi_in_source"] == "1003000100" and cases.iloc[1]["npi_in_source"] == ""
    assert cases.iloc[1]["case_id"].startswith("commercial:fl:")
    assert len(quarantine) == 1 and "no resolved outcome" in quarantine.iloc[0]["drop_reason"]


def test_strict_label_and_policies_never_take_commercial_rows():
    tier, why = outcome_tier("hcf_commercial", "pleaded guilty to insurance fraud", 5000)
    assert tier == "" and "training source only" in why
    assert "hard+commercial" in TIER_POLICIES and "hard+charged+commercial" in TIER_POLICIES
    t = pd.Series(["hard", "charged", "weak", "commercial", "excluded"])
    assert tier_mask(t, "hard").tolist() == [True, False, False, False, False]
    assert tier_mask(t, "hard+commercial").tolist() == [True, False, False, True, False]
    assert tier_mask(t, "hard+charged+commercial").tolist() == [True, True, False, True, False]
    assert tier_mask(t, "hard+weak").tolist() == [True, True, True, False, False]
    with pytest.raises(ValueError):
        tier_mask(t, "commercial")          # a policy without the hard tier is not a policy


def test_prompt_set_is_one_per_state_year():
    ps = build_prompts(["commercial"], years=[2023, 2024], states=["FL", "GA"])
    assert len(ps) == 4 and {p["set"] for p in ps} == {"commercial"}
    assert "COMMERCIAL health plans" in ps[0]["prompt"] and "resolved outcome" in ps[0]["prompt"]
    assert ps[0]["state"] == "FL" and ps[0]["year"] == 2023


def _graph(tmp_path):
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    org_nodes, npi_to_org = out["nodes/org_nodes"], out["npi_to_org"]
    org_id = npi_to_org.set_index("npi").loc[RING_NPI, "org_node_id"]
    org_name = org_nodes.set_index("org_node_id").loc[org_id, "org_name"]
    return inputs, out, org_nodes, npi_to_org, org_name


def _case_db(org_name: str) -> pd.DataFrame:
    return pd.DataFrame([
        {"case_id": "COM1", "announced_date": "2022-03-01", "defendant_name": org_name,
         "defendant_name_key": norm_org_name(org_name), "sector": "home_health", "scheme": "billing_fraud",
         "amount_usd": 50000, "qui_tam": None, "intervened": None, "jurisdiction": "FL commercial-plan (commercial)",
         "source_url": "https://ag.fl/com1", "case_type": "hcf_commercial",
         "summary": "pleaded guilty. billed Blue Cross for home visits never made from 2019 through 2021."},
        {"case_id": "COM2", "announced_date": "2022-03-02", "defendant_name": "Charged Only Clinic",
         "defendant_name_key": norm_org_name("Charged Only Clinic"), "sector": "", "scheme": "",
         "amount_usd": None, "qui_tam": None, "intervened": None, "jurisdiction": "FL commercial-plan (commercial)",
         "source_url": "https://ag.fl/com2", "case_type": "hcf_commercial",
         "summary": "arrested and charged with insurance fraud"},
    ])


def test_commercial_rows_label_as_training_only_source(tmp_path):
    inputs, out, org_nodes, npi_to_org, org_name = _graph(tmp_path)
    labels = build_case_labels(_case_db(org_name), org_nodes, npi_to_org)
    row = labels.set_index("npi").loc[RING_NPI]
    assert row["label_source"] == COMMERCIAL_SOURCE and row["fca_strict_tier"] == "hcf_commercial"
    assert not labels["case_ids"].str.contains("COM2").any()                 # the charge never labels
    # the forward FCA ruler never takes it
    fwd = build_forward_case_label(_case_db(org_name), "2021-12", org_nodes, npi_to_org, horizon_months=36)
    assert not (fwd["is_prospective_positive"] == 1).any()
    # the export records the source without flipping the hard label
    leads = build_provider_leads(inputs["provider_dim"])
    matrix, manifest = build_provider_matrix(leads, npi_to_org, case_labels=labels, min_peer=2)
    m = matrix.set_index("npi")
    assert int(m.loc[RING_NPI, "provider_on_exclusion"]) == 0
    assert "hcf_commercial" in str(m.loc[RING_NPI, "exclusion_label_sources"])
    nodes = pd.DataFrame({"npi": ["1000000004"], "excl_type": ["1128a1"], "excl_date": ["2019-01-01"]})
    y_hard = cl.tier_label(matrix, nodes, keep="hard")
    y_com = cl.tier_label(matrix, nodes, keep="hard+commercial")
    y_chg = cl.tier_label(matrix, nodes, keep="hard+charged")
    i = list(matrix["npi"]).index(RING_NPI)
    assert y_hard[i] == 0 and y_chg[i] == 0 and y_com[i] == 1
    nodes_p = tmp_path / "exclusion_nodes.parquet"
    nodes.to_parquet(nodes_p, index=False)
    cl.set_label_policy("hard+commercial", exclusion_nodes=str(nodes_p))
    try:
        s = cl.training_label_summary(matrix)
        assert s["label_tier"] == "hard+commercial" and s["n_commercial_positives"] == 1
    finally:
        cl.set_label_policy("all")
