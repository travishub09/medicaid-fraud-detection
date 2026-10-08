"""Regression tests for the 2026-10-08 model audit fixes (none of these had coverage):

  1. docket-only case labels no longer raise NameError inside build_provider_matrix
  2. the smoking-gun label widening uses the NPI's OWN post-event dollars, never the
     org-broadcast flag on a multi-member org (one deactivated member used to make
     every member a positive)
  3. clean_anchors' frozen-run fallback compares the residual PERCENTILE to 0.5
     (the old <= 0 could never be true, so frozen runs had no negatives)
  4. NUCC gives a no-taxonomy provider no cohort instead of a fake "unknown" cell
  5. the in-matrix tier label freezes at the cutoff month's FIRST day, like the
     as-of graph and the forward label (late-month bans were both train and test)
  6. frozen-run DOJ labels require the case to have been ANNOUNCED by the cutoff
  7. label provenance names SAM / state-termination / state-board / research sources
"""
from __future__ import annotations

import pandas as pd

from src.entity_graph.__main__ import run as run_graph
from src.entity_graph.resolve_entities import norm_org_name
from src.ingest_cms.nucc_taxonomy import canonical_peer_group, load_taxonomy_hierarchy
from src.model_a import company_list as cl
from src.model_a.case_labels import build_case_labels
from src.model_a.clean_anchors import manufacture_negatives
from src.model_a.provider_features_export import _label_source, build_provider_matrix
from tests.fixtures.synthetic import build_provider_leads, build_synthetic_inputs

RING_NPI = "1003000100"
PAIR_NPI = "1003000506"          # ACME HEALTH: a two-member synthetic org


def _graph(tmp_path):
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    return inputs, out["nodes/org_nodes"], out["npi_to_org"]


def test_docket_only_case_labels_do_not_crash_the_matrix(tmp_path):
    inputs, org_nodes, npi_to_org = _graph(tmp_path)
    org_id = npi_to_org.set_index("npi").loc[RING_NPI, "org_node_id"]
    org_name = org_nodes.set_index("org_node_id").loc[org_id, "org_name"]
    case_db = pd.DataFrame([{
        "case_id": "D1", "announced_date": "2022-03-01", "defendant_name": org_name,
        "defendant_name_key": norm_org_name(org_name), "sector": "home_health",
        "scheme": "upcoding", "amount_usd": 5_000_000, "case_type": "docket_qui_tam",
        "jurisdiction": "", "source_url": "", "summary": "complaint filed 2021."}])
    labels = build_case_labels(case_db, org_nodes, npi_to_org)
    assert (labels["label_source"] == "docket_case").all()
    leads = build_provider_leads(inputs["provider_dim"])
    matrix, _ = build_provider_matrix(leads, npi_to_org, case_labels=labels, min_peer=2)
    m = matrix.set_index("npi")
    assert "docket_case" in str(m.loc[RING_NPI, "exclusion_label_sources"])
    assert int(m.loc[RING_NPI, "provider_on_exclusion"]) == 0        # an allegation never flips the label


def test_label_widening_never_smears_an_org_flag_over_members(tmp_path):
    inputs, org_nodes, npi_to_org = _graph(tmp_path)
    org_id = npi_to_org.set_index("npi").loc[PAIR_NPI, "org_node_id"]
    members = npi_to_org[npi_to_org["org_node_id"] == org_id]["npi"].astype(str).tolist()
    assert len(members) > 1
    leads = build_provider_leads(inputs["provider_dim"])
    # a widened label on an unrelated NPI, so the label column exists as it does in
    # every real export (the widening block only runs when it does)
    wl = pd.DataFrame({"npi": ["1003000415"], "provider_on_exclusion": [1], "exclusion_label_sources": ["leie"]})
    # org-grain flag only (the adapter's broadcast): nobody may become a positive
    org_flag = pd.DataFrame({"org_node_id": [org_id], "billing_after_deactivation": [0.4]})
    matrix, _ = build_provider_matrix(leads, npi_to_org, widened_label=wl,
                                      org_grain_frames={"nppes_deactivation": org_flag}, min_peer=2)
    m = matrix.set_index("npi")
    assert int(m.loc[members, "provider_on_exclusion"].sum()) == 0
    # the NPI's own post-deactivation dollars: that NPI alone becomes a positive
    own = pd.DataFrame({"npi": [PAIR_NPI], "deact_months_after": [3], "deact_paid_after": [1200.0],
                        "deact_first_after": ["2023-01"], "deact_last_after": ["2023-03"]})
    matrix, _ = build_provider_matrix(leads, npi_to_org, widened_label=wl,
                                      adapter_npi_frames={"smoking_gun_timeline": own},
                                      org_grain_frames={"nppes_deactivation": org_flag}, min_peer=2)
    m = matrix.set_index("npi")
    assert int(m.loc[PAIR_NPI, "provider_on_exclusion"]) == 1
    assert "deactivation" in str(m.loc[PAIR_NPI, "exclusion_label_sources"])
    others = [n for n in members if n != PAIR_NPI]
    assert int(m.loc[others, "provider_on_exclusion"].sum()) == 0


def test_clean_anchor_fallback_reads_residual_percentiles():
    def row(**kw):
        base = dict(provider_on_exclusion=0, within_2_hops_of_exclusion=0, graph_fraud_proximity=0.1,
                    tenure_months=200, primary_taxonomy="207Q00000X", org_legal_name="X",
                    billing_residual=0.30, volume_residual=0.20, consistency_flags=0)
        base.update(kw)
        return base
    m = pd.DataFrame([row(npi="A"), row(npi="B", billing_residual=0.92), row(npi="C", volume_residual=0.51)])
    neg = manufacture_negatives(m).set_index("npi")
    assert neg.loc["A", "confirmed_clean"] == 1          # at/below expectation on the percentile scale
    assert neg.loc["B", "confirmed_clean"] == 0 and neg.loc["C", "confirmed_clean"] == 0


def test_nucc_gives_no_cohort_without_a_taxonomy():
    hier = load_taxonomy_hierarchy(pd.DataFrame({
        "Code": ["207Q00000X"], "Grouping": ["Allopathic & Osteopathic Physicians"],
        "Classification": ["Family Medicine"], "Specialization": [""]}))
    pdim = pd.DataFrame({"npi": ["1", "2"], "taxonomy_code": ["207Q00000X", ""]})
    pg = canonical_peer_group(pdim, hier).set_index("npi")
    assert pg.loc["1", "peer_group_key"] == "Family Medicine"
    assert pd.isna(pg.loc["2", "peer_group_key"])
    assert "unknown" not in set(pg["peer_group_key"].dropna())


def test_tier_label_freezes_at_the_cutoff_month_start():
    cl.set_label_policy("all")
    nodes = pd.DataFrame({"npi": ["1000000001", "1000000002", "1000000003"],
                          "excl_type": ["1128a1", "1128a1", "1128a1"],
                          "excl_date": ["2021-12-01", "2021-12-15", "2021-11-30"]})
    m = pd.DataFrame({"npi": ["1000000001", "1000000002", "1000000003"]})
    assert list(cl.tier_label(m, nodes, keep="hard", cutoff="2021-12")) == [1, 0, 1]
    cl.set_label_policy("all")


def test_frozen_case_labels_require_announcement_before_cutoff(tmp_path):
    _, org_nodes, npi_to_org = _graph(tmp_path)
    org_id = npi_to_org.set_index("npi").loc[RING_NPI, "org_node_id"]
    org_name = org_nodes.set_index("org_node_id").loc[org_id, "org_name"]
    case_db = pd.DataFrame([{
        "case_id": "C1", "announced_date": "2022-03-01", "defendant_name": org_name,
        "defendant_name_key": norm_org_name(org_name), "sector": "home_health",
        "scheme": "upcoding", "amount_usd": 5_000_000, "jurisdiction": "", "source_url": "",
        "summary": "submitted upcoded claims from 2017 through 2020."}])
    assert len(build_case_labels(case_db, org_nodes, npi_to_org, asof_cutoff="2021-12")) == 0
    assert len(build_case_labels(case_db, org_nodes, npi_to_org, asof_cutoff="2022-12")) > 0


def test_label_source_names_every_merged_source():
    assert _label_source("sam:Ineligible (Proceedings Completed)") == "sam"
    assert _label_source("state_term:GA:fraud") == "state_termination"
    assert _label_source("state_board:FL:revoked") == "state_board"
    assert _label_source("mfcu_ga:conviction") == "research_mfcu"
    assert _label_source("mcd_enroll:FL:terminated") == "medicaid_enrollment"
    assert _label_source("1128a1") == "leie"
    assert _label_source("medicare_revocation:(a)(3)") == "medicare_revocation"
