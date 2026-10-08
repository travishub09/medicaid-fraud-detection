"""
test_prospective_label.py — the forward label for Travis's frozen-file network test.

Asserts the earliest-exclusion collapse, the three-way cutoff split, undated-row
handling (never a silent positive), and the NPI-required drop.
"""

from __future__ import annotations

import pandas as pd

from src.model_a.prospective_label import build_prospective_label


def _nodes():
    return pd.DataFrame({
        "npi": ["1111111111", "1111111111", "2222222222", "3333333333",
                "4444444444", "", "5555555555"],
        "excl_date": ["2019-05-01", "2025-02-01",  # re-listed; first ban is 2019 → pre
                      "2024-03-01",                  # first ban after cutoff → positive
                      "2023-12-01",                  # just before cutoff → pre
                      None,                          # undated → not a positive
                      "2024-06-01",                  # name-only, blank NPI → dropped
                      "2024-01-01"],                 # exactly on cutoff → positive
        "excl_type": ["1128a1"] * 7,
        "entity_name": ["A", "A", "B", "C", "D", "E", "F"],
    })


def test_earliest_exclusion_and_cutoff_split():
    out = build_prospective_label(_nodes(), cutoff="2024-01")
    by = out.set_index("npi")

    # re-listed provider keeps its 2019 first ban → pre-cutoff, not a forward positive
    assert by.loc["1111111111", "was_excluded_pre_cutoff"] == 1
    assert by.loc["1111111111", "is_prospective_positive"] == 0
    assert by.loc["1111111111", "first_excl_date"] == "2019-05-01"

    # first ban strictly after the freeze instant → positive
    assert by.loc["2222222222", "is_prospective_positive"] == 1
    # a ban dated EXACTLY at the freeze instant is already in the as-of graph
    # (features keep excl_date <= cutoff) → known, NOT a forward positive
    assert by.loc["5555555555", "is_prospective_positive"] == 0
    assert by.loc["5555555555", "was_excluded_pre_cutoff"] == 1

    # first ban just before cutoff → pre
    assert by.loc["3333333333", "was_excluded_pre_cutoff"] == 1

    # undated → flagged, never a positive
    assert by.loc["4444444444", "undated_exclusion"] == 1
    assert by.loc["4444444444", "is_prospective_positive"] == 0


def test_blank_npi_dropped():
    out = build_prospective_label(_nodes(), cutoff="2024-01")
    assert "" not in set(out["npi"])
    assert "none" not in {n.lower() for n in out["npi"]}


def test_empty_input():
    assert build_prospective_label(pd.DataFrame(), cutoff="2024-01").empty
    assert build_prospective_label(None, cutoff="2024-01").empty


def test_positive_count_matches():
    out = build_prospective_label(_nodes(), cutoff="2024-01")
    # only 2222222222 (2024-03) is strictly after the freeze; 5555555555 sits
    # exactly on it and counts as known
    assert int(out["is_prospective_positive"].sum()) == 1


# ---------------------------------------------------------------------------
# the FORWARD FCA RULER (2026-10-08): DOJ cases announced after the cutoff
# ---------------------------------------------------------------------------

def _graph_case_db(tmp_path):
    from src.entity_graph.__main__ import run as run_graph
    from src.entity_graph.resolve_entities import norm_org_name
    from tests.fixtures.synthetic import build_synthetic_inputs
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    org_nodes, npi_to_org = out["nodes/org_nodes"], out["npi_to_org"]
    n2o = npi_to_org.set_index("npi")["org_node_id"]
    names = org_nodes.set_index("org_node_id")["org_name"]
    ring, other = "1003000100", "1003000506"
    rows = []
    for cid, npi, ann, summ in [
        ("NEW", ring, "2022-09-15", "paid $2,000,000 to resolve False Claims Act allegations for conduct from 2019 through 2021."),
        ("OLD", other, "2021-06-01", "paid $500,000 to settle false claims from 2018 through 2020."),
    ]:
        name = names[n2o[npi]]
        rows.append({"case_id": cid, "announced_date": ann, "defendant_name": name,
                     "defendant_name_key": norm_org_name(name), "sector": "home_health",
                     "scheme": "upcoding", "amount_usd": 2_000_000 if cid == "NEW" else 500_000,
                     "qui_tam": 1, "intervened": 1, "jurisdiction": "", "source_url": "",
                     "case_type": "civil_fca", "summary": summ})
    return pd.DataFrame(rows), org_nodes, npi_to_org, ring, other


def test_forward_case_label_counts_post_cutoff_resolved_cases(tmp_path):
    from src.model_a.prospective_label import build_forward_case_label
    case_db, org_nodes, npi_to_org, ring, other = _graph_case_db(tmp_path)
    lab = build_forward_case_label(case_db, "2021-12", org_nodes, npi_to_org).set_index("npi")
    assert set(lab.columns) >= {"first_excl_date", "excl_type", "is_prospective_positive",
                                "was_excluded_pre_cutoff", "fca_tier", "label_policy"}
    assert lab.loc[ring, "is_prospective_positive"] == 1 and lab.loc[ring, "was_excluded_pre_cutoff"] == 0
    assert lab.loc[ring, "first_excl_date"] == "2022-09-15" and lab.loc[ring, "excl_type"].startswith("doj:")
    assert (lab["label_policy"] == "fca_case").all() and (lab["fca_tier"] == "fca").all()
    # the OLD case was public at the freeze: known, never a forward positive
    assert lab.loc[other, "was_excluded_pre_cutoff"] == 1 and lab.loc[other, "is_prospective_positive"] == 0
    # a 6-month horizon excludes the September 2022 announcement
    short = build_forward_case_label(case_db, "2021-12", org_nodes, npi_to_org, horizon_months=6).set_index("npi")
    assert short.loc[ring, "is_prospective_positive"] == 0
    # conduct that began after the cutoff year is not a forward positive either
    late = case_db.copy()
    late.loc[late["case_id"] == "NEW", "summary"] = "paid $2,000,000 for conduct from 2023 through 2024."
    late.loc[late["case_id"] == "NEW", "announced_date"] = "2025-01-10"
    l2 = build_forward_case_label(late, "2021-12", org_nodes, npi_to_org).set_index("npi")
    assert l2.loc[ring, "is_prospective_positive"] == 0
    assert build_forward_case_label(pd.DataFrame(), "2021-12", org_nodes, npi_to_org).empty


def test_merge_forward_labels_unions_rulers_and_respects_known_at_freeze():
    from src.model_a.prospective_label import merge_forward_labels
    cols = ["npi", "first_excl_date", "excl_type", "entity_name", "is_prospective_positive",
            "was_excluded_pre_cutoff", "undated_exclusion", "fca_tier", "fca_basis", "label_policy"]
    excl = pd.DataFrame([["A", "2022-03-01", "1128a1", "", 1, 0, 0, "hard", "b", "all"],
                         ["B", "2022-05-01", "1128b4", "", 1, 0, 0, "weak", "b", "all"],
                         ["C", "2020-01-01", "1128a1", "", 0, 1, 0, "hard", "b", "all"]], columns=cols)
    case = pd.DataFrame([["B", "2021-01-01", "doj:fca_civil", "", 0, 1, 0, "fca", "c", "fca_case"],
                         ["D", "2022-08-01", "doj:fca_civil", "", 1, 0, 0, "fca", "c", "fca_case"]], columns=cols)
    m = merge_forward_labels(excl, case).set_index("npi")
    assert list(m.columns) == cols[1:]
    assert m.loc["A", "is_prospective_positive"] == 1 and m.loc["A", "label_policy"] == "all"
    assert m.loc["D", "is_prospective_positive"] == 1 and m.loc["D", "label_policy"] == "fca_case"
    # B: an exclusion after the cutoff but a case public BEFORE it: known at the freeze
    assert m.loc["B", "was_excluded_pre_cutoff"] == 1 and m.loc["B", "is_prospective_positive"] == 0
    assert m.loc["B", "label_policy"] == "all+fca_case"
    assert m.loc["C", "is_prospective_positive"] == 0
    assert merge_forward_labels(excl, pd.DataFrame()).equals(excl)
