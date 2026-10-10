"""
test_case_labels.py — scheme-typed, time-boxed DOJ-outcome labels (Pillar 2).

The strongest positives we have (prosecuted fraud), carrying the scheme and the
conduct WINDOW — which is what makes scheme-stratified and out-of-time training
possible. Covers conduct-window extraction, case→NPI resolution, and the export
folding them into the widened label with metadata kept out of the feature set.
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.__main__ import run as run_graph
from src.entity_graph.resolve_entities import norm_org_name
from src.model_a.case_labels import extract_conduct_window, build_case_labels
from src.model_a.provider_features_export import build_provider_matrix
from tests.fixtures.synthetic import build_synthetic_inputs, build_provider_leads

RING_NPI = "1003000100"


def test_extract_conduct_window():
    assert extract_conduct_window("from 2015 through 2019", "2022-01-01") == (2015, 2019)
    assert extract_conduct_window("conduct in 2018", "2022-01-01") == (2018, 2018)
    # no years in text → conservative lookback ending at the announcement year
    assert extract_conduct_window("no dates here", "2023-06-01") == (2019, 2023)
    # years after the announcement are ignored (can't be conduct)
    assert extract_conduct_window("2016 and 2099", "2020-01-01") == (2016, 2016)


def _graph_and_case(tmp_path):
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    org_nodes, npi_to_org = out["nodes/org_nodes"], out["npi_to_org"]
    org_id = npi_to_org.set_index("npi").loc[RING_NPI, "org_node_id"]
    org_name = org_nodes.set_index("org_node_id").loc[org_id, "org_name"]
    case_db = pd.DataFrame([{
        "case_id": "C1", "announced_date": "2022-03-01", "defendant_name": org_name,
        "defendant_name_key": norm_org_name(org_name), "sector": "home_health",
        "scheme": "upcoding", "amount_usd": 5_000_000, "qui_tam": 1, "intervened": 1,
        "jurisdiction": "", "source_url": "",
        "summary": "submitted upcoded claims from 2017 through 2020."}])
    return inputs, out, org_nodes, npi_to_org, case_db


def test_case_resolves_to_npi_with_scheme_and_window(tmp_path):
    _, _, org_nodes, npi_to_org, case_db = _graph_and_case(tmp_path)
    labels = build_case_labels(case_db, org_nodes, npi_to_org)
    assert RING_NPI in set(labels["npi"])
    row = labels.set_index("npi").loc[RING_NPI]
    assert row["fraud_scheme"] == "upcoding"
    assert row["conduct_start"] == 2017 and row["conduct_end"] == 2020
    assert row["label_source"] == "doj_case"
    assert row["fraud_label"] == 1


def test_case_labels_fold_into_widened_label(tmp_path):
    inputs, _, org_nodes, npi_to_org, case_db = _graph_and_case(tmp_path)
    labels = build_case_labels(case_db, org_nodes, npi_to_org)
    leads = build_provider_leads(inputs["provider_dim"])
    matrix, manifest = build_provider_matrix(
        leads, npi_to_org, case_labels=labels, min_peer=2)
    m = matrix.set_index("npi")
    assert manifest["label"] == "provider_on_exclusion"
    assert int(m.loc[RING_NPI, "provider_on_exclusion"]) == 1
    assert "doj_case" in str(m.loc[RING_NPI, "exclusion_label_sources"])
    assert int(m.loc[RING_NPI, "conduct_start"]) == 2017          # window available for OOT split
    # scheme + window are label metadata, never trainable features
    for c in ["fraud_scheme", "conduct_start", "conduct_end"]:
        assert c in manifest["label_metadata"]
        assert c not in manifest["raw_feature_cols"]


def test_empty_case_db_is_safe(tmp_path):
    _, _, org_nodes, npi_to_org, _ = _graph_and_case(tmp_path)
    assert build_case_labels(pd.DataFrame(), org_nodes, npi_to_org).empty


def test_non_usable_rows_never_become_labels(tmp_path):
    _, _, org_nodes, npi_to_org, case_db = _graph_and_case(tmp_path)
    # v3 provenance: the row is a superseded duplicate
    case_db["usable"] = "0"
    labels = build_case_labels(case_db, org_nodes, npi_to_org)
    assert len(labels) == 0
    # and usable=1 passes through as before
    case_db["usable"] = "1"
    labels2 = build_case_labels(case_db, org_nodes, npi_to_org)
    assert RING_NPI in set(labels2["npi"])


def test_affiliation_broadcast_tiers_and_guard():
    import pandas as pd
    from src.model_a.case_labels import build_case_labels
    case_db = pd.DataFrame([
        {"case_id": "c1", "defendant": "Acme Home Health LLC",
         "defendant_name_key": "ACME HOME HEALTH",
         "amount_usd": "5000000", "scheme": "upcoding",
         "announced_date": "2024-06-01",
         "summary": "Billed from 2020 through 2023.",
         "npi_in_source": "1234567893"},
    ])
    org_nodes = pd.DataFrame([
        {"org_node_id": "org:1", "org_name": "Acme Home Health LLC"}])
    npi_to_org = pd.DataFrame([
        {"npi": "1999999992", "org_node_id": "org:1"}])       # org billing NPI
    # two affiliated clinicians at the settled org, plus a mega-org edge set
    aff = pd.DataFrame(
        [{"src_id": "provider:1888888881", "dst_id": "org:1",
          "edge_type": "reassigns_to", "basis": "reassignment"},
         {"src_id": "provider:1777777770", "dst_id": "org:1",
          "edge_type": "reassigns_to", "basis": "reassignment"}]
        + [{"src_id": f"provider:{1000000000 + i}", "dst_id": "org:big",
            "edge_type": "reassigns_to", "basis": "reassignment"}
           for i in range(300)])
    out = build_case_labels(case_db, org_nodes, npi_to_org,
                            fuzzy_threshold=None, affiliations=aff,
                            max_org_members=150)
    by = out.set_index("npi")
    assert by.loc["1234567893", "label_basis"] == "named_npi"
    assert by.loc["1999999992", "label_basis"] == "org_billing"
    assert by.loc["1888888881", "label_basis"] == "affiliated_individual"
    assert by.loc["1888888881", "fraud_scheme"] == "upcoding"
    # the 300-member org was not settled AND would be guarded anyway
    assert "1000000005" not in by.index
    # dollars counted once per case even though the org reaches it two ways
    assert by.loc["1999999992", "amount_usd"] == 5000000.0


def test_affiliation_guard_suppresses_mega_org():
    import pandas as pd
    from src.model_a.case_labels import build_case_labels
    case_db = pd.DataFrame([
        {"case_id": "c2", "defendant": "Giant Hospital System Inc",
         "defendant_name_key": "GIANT HOSPITAL SYSTEM",
         "amount_usd": "100000000", "scheme": "upcoding",
         "announced_date": "2024-01-01",
         "summary": "Conduct 2019 through 2022."}])
    org_nodes = pd.DataFrame([
        {"org_node_id": "org:big", "org_name": "Giant Hospital System Inc"}])
    npi_to_org = pd.DataFrame([
        {"npi": "1999999992", "org_node_id": "org:big"}])
    aff = pd.DataFrame(
        [{"src_id": f"provider:{1000000000 + i}", "dst_id": "org:big",
          "edge_type": "reassigns_to", "basis": "reassignment"}
         for i in range(200)])
    out = build_case_labels(case_db, org_nodes, npi_to_org,
                            fuzzy_threshold=None, affiliations=aff,
                            max_org_members=150)
    # billing NPI labeled; NONE of the 200 clinicians smeared
    bases = set(out["label_basis"])
    assert bases == {"org_billing"}
    assert len(out) == 1


def _fanout_case(n_orgs=4, members_per_org=5):
    """One settlement that resolves to n_orgs enrolled names (a chain), each with
    members_per_org billing NPIs: the per-ORG guard never fires, the CASE fans out
    to n_orgs * members_per_org NPIs."""
    import pandas as pd
    case_db = pd.DataFrame([
        {"case_id": "c3", "defendant": "Chain Care LLC",
         "defendant_name_key": "CHAIN CARE",
         "amount_usd": "25000000", "scheme": "upcoding",
         "announced_date": "2024-01-01",
         "summary": "Conduct 2019 through 2022.",
         "npi_in_source": "1234567893"}])
    org_nodes = pd.DataFrame([{"org_node_id": f"org:{i}", "org_name": "Chain Care LLC"}
                              for i in range(n_orgs)])
    npi_to_org = pd.DataFrame([{"npi": f"{1500000000 + i * 100 + j}", "org_node_id": f"org:{i}"}
                               for i in range(n_orgs) for j in range(members_per_org)])
    return case_db, org_nodes, npi_to_org


def test_per_case_budget_weights_broadcast_rows_never_drops_them():
    """2026-10-10 (label audit: the top 5 DOJ cases supplied 64% of v7's DOJ
    positives). A case that fans out past max_case_npis keeps every row but its
    broadcast rows carry label_weight = budget / fan-out; direct tiers stay 1.0."""
    from src.model_a.case_labels import build_case_labels, case_concentration
    case_db, org_nodes, npi_to_org = _fanout_case(n_orgs=4, members_per_org=5)
    out = build_case_labels(case_db, org_nodes, npi_to_org, fuzzy_threshold=None,
                            max_case_npis=10)
    by = out.set_index("npi")
    assert len(out) == 21                                        # 20 org_billing + the named NPI
    assert by.loc["1234567893", "label_basis"] == "named_npi"
    assert by.loc["1234567893", "label_weight"] == 1.0
    bc = by[by["label_basis"] == "org_billing"]
    assert (bc["case_fanout"] == 20).all()
    assert (bc["label_weight"] == 0.5).all()                     # 10 / 20
    # the case's training mass is capped at the budget (plus the direct row)
    conc = case_concentration(out, top=5)
    assert conc["case_id"].iat[0] == "c3" and int(conc["n_npis"].iat[0]) == 21
    assert abs(float(conc["weighted_mass"].iat[0]) - 11.0) < 1e-9
    # budget off -> every weight 1.0, nothing else changes
    off = build_case_labels(case_db, org_nodes, npi_to_org, fuzzy_threshold=None,
                            max_case_npis=None)
    assert len(off) == 21 and (off["label_weight"] == 1.0).all()
    # a fan-out under the budget is never diluted
    small = build_case_labels(case_db, org_nodes, npi_to_org, fuzzy_threshold=None,
                              max_case_npis=100)
    assert (small["label_weight"] == 1.0).all()


def test_label_weight_rides_the_matrix_as_metadata_not_feature(tmp_path):
    _, _, org_nodes, npi_to_org, case_db = _graph_and_case(tmp_path)
    inputs = build_synthetic_inputs()
    labels = build_case_labels(case_db, org_nodes, npi_to_org, max_case_npis=1)
    assert "label_weight" in labels.columns
    leads = build_provider_leads(inputs["provider_dim"])
    matrix, manifest = build_provider_matrix(leads, npi_to_org, case_labels=labels, min_peer=2)
    assert "label_weight" in matrix.columns
    assert "label_weight" in manifest["label_metadata"]
    assert "case_fanout" in manifest["label_metadata"]
    for c in ("label_weight", "case_fanout"):
        assert c not in manifest["raw_feature_cols"]
    # rows the case label never touched weigh exactly 1.0; the budget of 1 dilutes
    # every broadcast row of a case that reaches more than one NPI
    untouched = matrix[~matrix["npi"].isin(set(labels["npi"]))]
    assert (untouched["label_weight"] == 1.0).all()
    assert manifest["training_policy"]["sample_weight"].startswith("label_weight")
