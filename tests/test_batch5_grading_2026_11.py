"""Batch 5b-B (2026-11-02, docs/ARCHITECTURE_AUDIT_2026-11.md D3e / D5e / D6e, experiment 2):
the CASE as the unit of the grade (clusters hit against a Monte Carlo baseline over
clusters), tie-aware hits at a tied budget boundary (and ties a mixed order hides), and
the case ruler resolved on the AS-OF graph instead of the current-day one."""
from __future__ import annotations

from math import comb

import numpy as np
import pandas as pd
import pytest

from src.model_a.accuracy_diagnostics import (boundary_ties_hidden, budget_rows_org, build_case_rulers,
                                              case_clusters, cluster_rows_org, kpi_block, list_score,
                                              load_graph_tables, org_outcomes, post_cutoff_case_npis,
                                              ruler_graph_tables, run_diagnostics, to_markdown)

CUTOFF = "2021-12"


def _ev(npis, positives):
    """A minimal eval frame: every NPI in the universe, y = 1 on ``positives``."""
    return pd.DataFrame({"npi": [str(n) for n in npis], "y": [1 if n in positives else 0 for n in npis],
                         "lead_months": [12 if n in positives else np.nan for n in npis]})


# --------------------------------------------------------------------------- #
# D3e: clusters hit, and a baseline over CLUSTERS
# --------------------------------------------------------------------------- #

def _toy_pool():
    """40 organizations, one member each: orgs 0..4 are five labs named in ONE release
    (cluster C), orgs 5..9 are five singleton events, 10..39 are clean. The list ranks the
    cluster first, then the singletons, then the rest."""
    n = 40
    npis = [f"{1000000000 + i}" for i in range(n)]
    orgs = pd.DataFrame({"priority_rank": range(1, n + 1), "org_id": [f"org:{i}" for i in range(n)],
                         "member_npis": npis, "org_name": [f"ORG {i}" for i in range(n)],
                         "score_dollar_weighted": np.linspace(1.0, 0.1, n)})
    orgs.attrs["sort_key"] = "score_dollar_weighted"
    positives = set(npis[:10])
    ev = _ev(npis, positives)
    clusters = {npis[i]: {"C"} for i in range(5)}                  # the singletons carry no case id
    return orgs, ev, clusters, npis


def test_cluster_grade_counts_one_release_once_and_draws_the_baseline_over_clusters():
    orgs, ev, clusters, npis = _toy_pool()
    oh = org_outcomes(orgs, ev)
    rows = {r["budget"]: r for r in cluster_rows_org(oh, (5, 10), ev, clusters, n_draws=2000, seed=7)}
    # the first five orgs are five org HITS but ONE case cluster
    assert rows[5]["hits"] == 5 and rows[5]["clusters_hit"] == 1
    assert rows[10]["hits"] == 10 and rows[10]["clusters_hit"] == 6
    assert rows[5]["clusters_in_pool"] == 6 and rows[5]["singleton_clusters_in_pool"] == 5
    assert rows[5]["n_draws"] == 2000
    # the baseline over clusters, analytically: P(C hit in 5 draws of 40) + 5 x P(a given singleton drawn)
    p_c = 1 - comb(35, 5) / comb(40, 5)
    exp5 = p_c + 5 * (5 / 40)
    assert abs(rows[5]["random_expected"] - exp5) < 0.08, (rows[5]["random_expected"], exp5)
    # ... which is BELOW the org-grain hypergeometric expectation (10 positives / 40 x 5 = 1.25)
    assert rows[5]["random_expected"] < 1.25
    # the full pool of events at 10 is far beyond chance; a single cluster at 5 is not
    assert rows[10]["random_p"] < 0.01
    assert rows[5]["random_p"] > rows[10]["random_p"]
    assert 0 < rows[5]["random_p"] <= 1 and 0 < rows[10]["random_p"] <= 1
    # seeded: the same draws twice
    again = {r["budget"]: r for r in cluster_rows_org(oh, (5, 10), ev, clusters, n_draws=2000, seed=7)}
    assert again[5]["random_expected"] == rows[5]["random_expected"] and again[10]["random_p"] == rows[10]["random_p"]
    # a budget beyond the list is skipped; an empty list yields no rows
    assert [r["budget"] for r in cluster_rows_org(oh, (5, 400), ev, clusters, n_draws=10)] == [5]
    assert cluster_rows_org(oh.iloc[0:0], (5,), ev, clusters, n_draws=10) == []


def test_cluster_grade_reads_the_lists_own_order():
    """A list whose rank disagrees with its score column is read by the sort key, like
    budget_rows_org; without a key, by the rank."""
    orgs, ev, clusters, npis = _toy_pool()
    rev = orgs.iloc[::-1].reset_index(drop=True)
    rev["priority_rank"] = range(1, len(rev) + 1)
    rev.attrs["sort_key"] = None                                   # rank order = the clean orgs first
    oh = org_outcomes(rev, ev)
    r = cluster_rows_org(oh, (30,), ev, clusters, n_draws=50)[0]
    assert r["hits"] == 0 and r["clusters_hit"] == 0 and r["random_p"] == 1.0
    score, key = list_score(rev)
    assert key is None and (np.diff(score) < 0).all()


def test_case_clusters_follow_the_announcement_window():
    """npi -> case ids from a build_case_labels frame: announced after the cutoff and within
    the horizon only; one NPI in two cases carries both; dollar_recall reads the same map."""
    lab = pd.DataFrame({"npi": ["1", "2", "3", "4"], "case_ids": ["A;B", "A", "OLD", "FAR"]})
    case_db = pd.DataFrame({"case_id": ["A", "B", "OLD", "FAR"],
                            "announced_date": ["2022-06-01", "2023-01-15", "2021-03-01", "2026-01-01"],
                            "amount_usd": [1e6, 2e6, 3e6, 4e6]})
    cl = case_clusters(lab, case_db, CUTOFF, horizon_months=36)
    assert cl == {"1": {"A", "B"}, "2": {"A"}}
    assert case_clusters(lab, case_db, CUTOFF) == {"1": {"A", "B"}, "2": {"A"}, "4": {"FAR"}}
    assert case_clusters(lab.iloc[0:0], case_db, CUTOFF) == {}
    case_npis, ann, amt = post_cutoff_case_npis(lab, case_db, CUTOFF, horizon_months=36, ev_universe={"1"})
    assert case_npis == {"A": {"1"}, "B": {"1"}} and float(amt["B"]) == 2e6


# --------------------------------------------------------------------------- #
# D5e: tie-aware hits at the boundary, and the ties a mixed order hides
# --------------------------------------------------------------------------- #

def _fake_res(rows):
    """The smallest result dict kpi_block and to_markdown accept."""
    uni = {"n_matrix": 12, "dropped_known": 0, "dropped_undated": 0, "dropped_beyond_horizon": 0,
           "positives_before_horizon": 4, "positives": 4, "n_eval": 12, "ambiguous_negatives": 0}
    res = {"headline_ruler": "fca", "cutoff": CUTOFF, "n_orgs_listed": 12,
           "rulers": {"fca": {"org_budgets": list(rows.values()), "universe": uni}}}
    res["kpis"] = kpi_block(res)
    return res


def _tied_book():
    """Rank 1 at 0.9 (a hit); ranks 2..7 share 0.5 and carry three hits; the rest at 0.1."""
    n = 12
    npis = [f"{1000000000 + i}" for i in range(n)]
    scores = [0.9] + [0.5] * 6 + [0.1] * 5
    orgs = pd.DataFrame({"priority_rank": range(1, n + 1), "org_id": [f"org:{i}" for i in range(n)],
                         "member_npis": npis, "org_name": [f"ORG {i}" for i in range(n)],
                         "score_dollar_weighted": scores, "score_p_max": scores})
    orgs.attrs["sort_key"] = "score_dollar_weighted"
    ev = _ev(npis, {npis[0], npis[1], npis[3], npis[5]})
    return orgs, ev


def test_tie_aware_hits_at_a_tied_boundary_reach_the_kpi_block():
    orgs, ev = _tied_book()
    oh = org_outcomes(orgs, ev)
    rows = {r["budget"]: r for r in budget_rows_org(oh, (3, 8))}
    # budget 3: one slot above the block, two slots into a six-way tie carrying three hits
    assert rows[3]["ties_at_boundary"] == 6 and abs(rows[3]["hits_tie_aware"] - (1 + 3 * 2 / 6)) < 1e-9
    assert rows[3]["hits"] in (1, 2, 3)                              # the positional count is one draw
    assert rows[3]["ties_hidden_n"] == 0 and rows[3]["ties_hidden_col"] is None   # the order explains itself
    # budget 8: the boundary sits inside the 0.1 block (five rows), no hits there
    assert rows[8]["ties_at_boundary"] == 5 and rows[8]["hits_tie_aware"] == 4.0
    res = _fake_res(rows)
    k = res["kpis"]
    assert k["acc_org_hits3_tie_aware_fca"] == rows[3]["hits_tie_aware"]
    assert k["acc_org_hits3_tie_aware"] == rows[3]["hits_tie_aware"]            # the headline alias
    assert "acc_org_ties_hidden3_fca" not in k
    md = to_markdown(res, "t")
    assert "Ties at the boundary" in md and "tie-aware" in md


def test_hidden_ties_are_read_from_the_underlying_score_when_the_order_is_rank_only():
    """A basis50 book is a mixed order no column explains: the diagnostics score it by
    rank (ties_at_boundary 0) while score_p_max ties straddle the budget."""
    orgs, ev = _tied_book()
    orgs.attrs["sort_key"] = None
    oh = org_outcomes(orgs, ev)
    rows = {r["budget"]: r for r in budget_rows_org(oh, (3, 7, 8))}
    assert rows[3]["ties_at_boundary"] == 0 and rows[3]["hits"] == 2        # by position: ranks 1 and 2
    assert rows[3]["ties_hidden_col"] == "score_p_max" and rows[3]["ties_hidden_n"] == 6
    assert rows[7]["ties_hidden_n"] == 0                                      # the block ends exactly at 7
    assert rows[8]["ties_hidden_col"] == "score_p_max" and rows[8]["ties_hidden_n"] == 5
    assert boundary_ties_hidden(orgs, 12) == (None, 0) and boundary_ties_hidden(orgs, 0) == (None, 0)
    res = _fake_res(rows)
    k = res["kpis"]
    assert k["acc_org_ties_hidden3_fca"] == 6 and k["acc_org_ties_hidden3"] == 6
    assert "acc_org_hits3_tie_aware_fca" not in k                             # no visible tie to count
    md = to_markdown(res, "t")
    assert "INVISIBLE to the order" in md and "plateau read" in md


# --------------------------------------------------------------------------- #
# D6e: the case ruler on the AS-OF graph
# --------------------------------------------------------------------------- #

def _graph_and_case(tmp_path):
    from src.entity_graph.__main__ import run as run_graph
    from src.entity_graph.resolve_entities import norm_org_name
    from tests.fixtures.synthetic import build_synthetic_inputs
    out = run_graph(build_synthetic_inputs(), tmp_path / "graph_build")
    org_nodes, npi_to_org = out["nodes/org_nodes"], out["npi_to_org"]
    n2o = npi_to_org.set_index("npi")["org_node_id"]
    names = org_nodes.set_index("org_node_id")["org_name"]
    ring = "1003000100"
    name = names[n2o[ring]]
    case_db = pd.DataFrame([{"case_id": "NEW", "announced_date": "2022-09-15", "defendant_name": name,
                             "defendant_name_key": norm_org_name(name), "sector": "home_health",
                             "scheme": "upcoding", "amount_usd": 2_000_000, "qui_tam": 1, "intervened": 1,
                             "jurisdiction": "", "source_url": "", "case_type": "civil_fca",
                             "summary": "paid $2,000,000 to resolve False Claims Act allegations for conduct "
                                        "from 2019 through 2021."}])
    return org_nodes, npi_to_org, case_db, ring


def _write_graph(root, org_nodes, npi_to_org):
    (root / "nodes").mkdir(parents=True)
    org_nodes.to_parquet(root / "nodes" / "org_nodes.parquet", index=False)
    npi_to_org.to_parquet(root / "npi_to_org.parquet", index=False)


def test_asof_graph_option_resolves_the_ruler_on_the_asof_npi_to_org(tmp_path):
    org_nodes, npi_to_org, case_db, ring = _graph_and_case(tmp_path)
    settled_org = npi_to_org.set_index("npi")["org_node_id"][ring]
    # the AS-OF roster: the ring NPI had not joined the settled org at the freeze, another had
    other = npi_to_org.loc[npi_to_org["org_node_id"] != settled_org, "npi"].iloc[0]
    asof = npi_to_org.copy()
    asof.loc[asof["npi"] == ring, "org_node_id"] = npi_to_org.set_index("npi")["org_node_id"][other]
    asof.loc[asof["npi"] == other, "org_node_id"] = settled_org
    current_dir, asof_dir = tmp_path / "graph", tmp_path / "graph_asof_2021-12_full"
    _write_graph(current_dir, org_nodes, npi_to_org)
    _write_graph(asof_dir, org_nodes, asof)
    # the default: the current-day graph, unchanged behaviour
    on, n2o, which, src = ruler_graph_tables(current_dir, None)
    assert which == "current" and src == current_dir and len(n2o) == len(npi_to_org)
    cur = build_case_rulers(case_db, CUTOFF, on, n2o)["fca"].set_index("npi")
    assert cur.loc[ring, "is_prospective_positive"] == 1 and other not in cur.index
    # the option: the as-of roster decides who the settlement reaches
    on2, n2o2, which2, src2 = ruler_graph_tables(current_dir, asof_dir)
    assert which2 == "asof" and src2 == asof_dir
    assert n2o2.set_index("npi")["org_node_id"][other] == settled_org
    built = build_case_rulers(case_db, CUTOFF, on2, n2o2, qui_tam=True)
    assert set(built) == {"fca", "qui_tam"}
    asof_r = built["fca"].set_index("npi")
    assert asof_r.loc[other, "is_prospective_positive"] == 1 and ring not in asof_r.index
    assert list(built["fca"].columns) == ["npi", "event_date", "is_prospective_positive",
                                          "was_excluded_pre_cutoff", "undated_exclusion", "fca_tier"]
    a, b = load_graph_tables(asof_dir)
    assert len(a) == len(org_nodes) and len(b) == len(asof)


def test_run_diagnostics_records_the_ruler_graph_and_the_cluster_kpis():
    """End to end on a shipped list (no training): the result json and the KPI block carry
    ruler_graph and the acc_cluster_* fields; the markdown prints the cluster table."""
    rng = np.random.default_rng(0)
    n, n_orgs = 400, 120
    org = rng.integers(0, n_orgs, n)
    npis = [f"{1000000000 + i}" for i in range(n)]
    m = pd.DataFrame({"npi": npis, "org_node_id": [f"org:{o}" for o in org], "group_id": [f"org:{o}" for o in org],
                      "org_name": [f"ORG {o} LLC" for o in org], "provider_on_exclusion": 0,
                      "exclusion_label_sources": "", "net_paid": rng.lognormal(12, 1, n),
                      "gross_paid": rng.lognormal(12, 1, n), "last_service_month": "2021-11",
                      "practice_state": "NY", "primary_taxonomy": "207Q00000X", "assessable": 1, "row_grain": "billing"})
    manifest = {"label": "provider_on_exclusion", "asof_cutoff": CUTOFF}
    # four orgs named in ONE release, listed first; two singleton events further down
    members = m.groupby("org_node_id")["npi"].apply(list)
    listed = [f"org:{i}" for i in range(n_orgs) if f"org:{i}" in members.index]
    case_orgs, single_orgs = listed[:4], listed[20:22]
    pos = {members[o][0] for o in case_orgs} | {members[o][0] for o in single_orgs}
    clusters = {members[o][0]: {"REL1"} for o in case_orgs}
    ruler = pd.DataFrame({"npi": sorted(pos), "event_date": pd.Timestamp("2022-06-01"), "is_prospective_positive": 1,
                          "was_excluded_pre_cutoff": 0, "undated_exclusion": 0, "fca_tier": "fca"})
    pl = pd.DataFrame({"priority_rank": range(1, len(listed) + 1), "org_id": listed,
                       "org_name": [f"ORG {o}" for o in listed],
                       "member_npis": [";".join(members[o]) for o in listed],
                       "n_providers": [len(members[o]) for o in listed],
                       "total_billed": [float(m.loc[m["org_node_id"] == o, "net_paid"].sum()) for o in listed],
                       "score_dollar_weighted": np.linspace(1, 0, len(listed))})
    pl.attrs["sort_key"] = "score_dollar_weighted"
    res = run_diagnostics(m, manifest, {"fca": ruler}, CUTOFF, priority_list=pl, score_source="priority_list",
                          budgets_org=(16, 50), ruler_graph="asof", cluster_ids=clusters, n_cluster_draws=300)
    assert res["ruler_graph"] == "asof"
    cb = {r["budget"]: r for r in res["rulers"]["fca"]["cluster_budgets"]}
    assert cb[16]["hits"] == 4 and cb[16]["clusters_hit"] == 1 and cb[16]["clusters_in_pool"] == 3
    assert cb[50]["hits"] == 6 and cb[50]["clusters_hit"] == 3
    k = res["kpis"]
    assert k["acc_ruler_graph"] == "asof"
    assert k["acc_cluster_hits16"] == 1 and k["acc_cluster_hits50"] == 3
    assert 0 < k["acc_cluster_base50"] < 3 and 0 < k["acc_cluster_p50"] <= 1
    assert k["acc_org_hits50"] == 6 and k["acc_cluster_hits50_fca"] == 3
    md = to_markdown(res, "t")
    assert "The CASE as the unit under fca" in md and "resolved on the **asof** graph" in md
    assert "—" not in md and "–" not in md
    # without a case ruler nothing is recorded (the default behaviour)
    res0 = run_diagnostics(m, manifest, {"fca": ruler}, CUTOFF, priority_list=pl, score_source="priority_list",
                           budgets_org=(16,))
    assert res0["ruler_graph"] is None and "acc_ruler_graph" not in res0["kpis"]
    assert "cluster_budgets" not in res0["rulers"]["fca"] and "acc_cluster_hits16" not in res0["kpis"]


def test_cli_accepts_the_asof_graph_flag():
    import subprocess, sys
    from pathlib import Path
    r = subprocess.run([sys.executable, "-m", "src.model_a.accuracy_diagnostics", "--help"], capture_output=True,
                       text=True, cwd=str(Path(__file__).resolve().parents[1]), timeout=300)
    assert r.returncode == 0 and "--asof-graph-dir" in r.stdout and "--cluster-draws" in r.stdout
