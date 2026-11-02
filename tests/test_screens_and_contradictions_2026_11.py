"""The three per-se builds of 2026-11-02 on planted frames.

A. theory_screens `dme_national_share`: a supplier at 60 percent of the nation's lines on one
   DME code is a candidate, one at 1 percent fails the share rule; the out-of-state order share
   comes from a planted supplier x referrer table and is NULL (and the report says so) without it.
B. contradictions `successor_after_exclusion`: a number that starts billing the month after a
   colleague's exclusion and carries the book is found; a long-standing colleague is not.
C. contradictions `enrolled_after_owner_exclusion`: an owner excluded in 2019 who joined in 2021
   is found; one who joined in 2017 is not. Both checks are no-ops without their inputs and the
   markdown carries the innocent explanations.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.model_a import contradictions as cx
from src.model_a import theory_screens as ts

BIG, SMALL, OTHER = "1000000001", "1000000002", "10000001"   # OTHER + 2 digits = the rest of the nation


# ---------------------------------------------------------------- A. the national-share screen
def _dme_fact(tmp: Path) -> tuple[Path, Path, Path]:
    rows = []

    def add(npi, code, month, paid, lines, pats):
        rows.append({"billing_npi": npi, "servicing_npi": npi, "hcpcs_code": code, "service_month": month,
                     "total_paid": float(paid), "total_claim_lines": float(lines), "total_patients": float(pats)})

    for m in ("2023-01", "2023-07", "2024-01", "2024-07"):
        add(BIG, "L0650", m, 400_000, 6_000, 3_000)        # 24,000 lines of the nation's 40,000 = 60 percent
        add(BIG, "E0601", m, 50_000, 100, 80)              # under $250k on this code: never the top code
        add(SMALL, "L0650", m, 300_000, 100, 90)           # 400 lines of 40,000 = 1 percent, above the floor
        for i in range(10, 48):                            # 38 other suppliers fill the remaining 39 percent
            add(f"{OTHER}{i}", "L0650", m, 10_000, 100, 60)   # 38 x 400 = 15,200
    # 24,000 + 400 + 15,200 = 39,600 lines; BIG = 60.6 percent, SMALL = 1.0 percent
    fact = tmp / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(fact, index=False)
    dim = [{"npi": BIG, "org_legal_name": "ALPHA BRACE SUPPLY LLC", "provider_name": "", "entity_type": "2",
            "addr_state": "FL", "taxonomy_code": "332B00000X", "addr_line1": "10 MAIN ST"},
           {"npi": SMALL, "org_legal_name": "BETA ORTHOTICS INC", "provider_name": "", "entity_type": "2",
            "addr_state": "TX", "taxonomy_code": "332B00000X", "addr_line1": "20 OAK AVE"}]
    for i in range(10, 48):
        dim.append({"npi": f"{OTHER}{i}", "org_legal_name": f"SUPPLIER {i} LLC", "provider_name": "", "entity_type": "2",
                    "addr_state": "OH", "taxonomy_code": "332B00000X", "addr_line1": f"{i} ELM ST"})
    # the ordering physicians: two in Georgia, one in Florida
    dim += [{"npi": "1900000001", "org_legal_name": "", "provider_name": "DOC ONE", "entity_type": "1",
             "addr_state": "GA", "taxonomy_code": "207Q00000X", "addr_line1": "1 PEACH ST"},
            {"npi": "1900000002", "org_legal_name": "", "provider_name": "DOC TWO", "entity_type": "1",
             "addr_state": "GA", "taxonomy_code": "207Q00000X", "addr_line1": "2 PEACH ST"},
            {"npi": "1900000003", "org_legal_name": "", "provider_name": "DOC THREE", "entity_type": "1",
             "addr_state": "FL", "taxonomy_code": "207Q00000X", "addr_line1": "3 PALM ST"}]
    dimp = tmp / "provider_dim.parquet"
    pd.DataFrame(dim).to_parquet(dimp, index=False)
    ref = pd.DataFrame([{"supplier_npi": BIG, "referrer_npi": "1900000001", "dollars": 600_000.0},
                        {"supplier_npi": BIG, "referrer_npi": "1900000002", "dollars": 200_000.0},
                        {"supplier_npi": BIG, "referrer_npi": "1900000003", "dollars": 200_000.0}])
    refp = tmp / "dme_referrers.csv"
    ref.to_csv(refp, index=False)
    return fact, dimp, refp


def test_national_share_screen_finds_the_dominant_supplier(tmp_path):
    fact, dim, refp = _dme_fact(tmp_path)
    s = ts.SCREENS["dme_national_share"]
    assert s.national_share_rule and s.numerator_like == ts.SCREENS["dme_mailbox"].numerator_like
    df = ts.run_screen(fact, s, provider_dim=dim, referrers=refp)
    st = dict(zip(df["npi"], df["status"]))
    assert st[BIG] == "candidate"
    assert st[SMALL] == "fails_national_share"
    big = df[df["npi"] == BIG].iloc[0]
    assert big["top_share_code"] == "L0650"                       # E0601 is under the $250k code floor
    assert abs(float(big["national_code_share"]) - 24_000 / 39_600) < 1e-9
    assert abs(float(big["top_share_lines"]) - 24_000 / 39_600) < 1e-9
    assert abs(float(big["top_share_dollars"]) - 1_600_000 / (1_600_000 + 1_200_000 + 38 * 40_000)) < 1e-9
    assert int(big["top_code_nat_suppliers"]) == 40
    small = df[df["npi"] == SMALL].iloc[0]
    assert abs(float(small["national_code_share"]) - 400 / 39_600) < 1e-9
    # the out-of-state order share: $800k of $1.0M ordered from Georgia against a Florida supplier
    assert abs(float(big["out_of_state_order_share"]) - 0.8) < 1e-9
    assert int(big["n_referrers"]) == 3 and int(big["n_referrer_states"]) == 2
    assert df[df["status"].eq("candidate")]["rank"].tolist() == [1]
    md = ts.to_markdown({"dme_national_share": df})
    assert "computed from the supplier x referrer table" in md
    assert "share of national lines" in md and "ALPHA BRACE SUPPLY" in md
    assert "manufacturer's own supplier number" in md             # the innocent explanations print


def test_national_share_screen_without_a_referrer_table_says_so(tmp_path):
    fact, dim, _ = _dme_fact(tmp_path)
    df = ts.run_screen(fact, ts.SCREENS["dme_national_share"], provider_dim=dim)
    big = df[df["npi"] == BIG].iloc[0]
    assert big["status"] == "candidate"
    assert pd.isna(big["out_of_state_order_share"]) and pd.isna(big["n_referrers"])
    md = ts.to_markdown({"dme_national_share": df})
    assert "NOT computed" in md and "--dme-referrers" in md


# ---------------------------------------------------------------- B. successor after exclusion
EXCL, SUCC, COLLEAGUE, LONER = "1003000209", "1003000415", "1003000126", "1003000308"


def _succ_fact(tmp: Path) -> Path:
    rows = []
    months = [f"{y}-{m:02d}" for y in (2020, 2021, 2022) for m in range(1, 13)]
    for ym in months:
        if ym < "2021-07":                                   # excluded 2021-07-15: $10k a month before
            rows.append({"billing_npi": EXCL, "service_month": ym, "total_paid": 10_000.0, "hcpcs_code": "99213"})
            rows.append({"billing_npi": EXCL, "service_month": ym, "total_paid": 2_000.0, "hcpcs_code": "99214"})
        if ym >= "2021-08":                                  # the successor starts the month after at $8k
            rows.append({"billing_npi": SUCC, "service_month": ym, "total_paid": 8_000.0, "hcpcs_code": "99213"})
            rows.append({"billing_npi": SUCC, "service_month": ym, "total_paid": 1_000.0, "hcpcs_code": "99214"})
        rows.append({"billing_npi": COLLEAGUE, "service_month": ym, "total_paid": 9_000.0, "hcpcs_code": "99213"})
        rows.append({"billing_npi": LONER, "service_month": ym, "total_paid": 50_000.0, "hcpcs_code": "99213"})
    p = tmp / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


def _succ_events() -> pd.DataFrame:
    excl = pd.DataFrame({"npi": [EXCL], "excl_date": ["2021-07-15"], "excl_type": ["1128a1"], "entity_name": ["DOE, JOHN"]})
    return cx._events(excl, None)


def test_successor_after_exclusion_finds_the_new_number_and_ignores_the_colleague(tmp_path):
    fact = _succ_fact(tmp_path)
    n2o = pd.DataFrame({"npi": [EXCL, SUCC, COLLEAGUE, LONER], "org_node_id": ["org:A", "org:A", "org:A", "org:B"]})
    res = cx.successor_after_exclusion(fact, _succ_events(), n2o)
    assert res["successor_npi"].tolist() == [SUCC]              # the colleague billed $9k a month all along
    r = res.iloc[0]
    assert r["excluded_npi"] == EXCL and r["org_node_id"] == "org:A" and r["successor_first_month"] == "2021-08"
    assert abs(r["excluded_pre6_avg"] - 12_000.0) < 1e-6         # 2021-01..2021-06
    assert abs(r["successor_post6_avg"] - 9_000.0 * 5 / 6) < 1e-6  # 2021-08..2021-12 (five of the six months)
    assert abs(r["successor_pre6_avg"]) < 1e-9
    assert abs(r["ratio"] - (9_000.0 * 5 / 6) / 12_000.0) < 1e-6
    assert r["shared_top_codes"] == 2 and r["shared_codes"] == "99213,99214"
    # the parquet path works as the npi_to_org input too, and lands in the report with its own section
    n2o_p = tmp_path / "npi_to_org.parquet"
    n2o.to_parquet(n2o_p, index=False)
    res2 = cx.successor_after_exclusion(fact, _succ_events(), n2o_p)
    assert res2["successor_npi"].tolist() == [SUCC]
    post = cx.post_event_billing(fact, _succ_events())
    t = cx.assemble(post, pd.DataFrame(columns=["npi"]), pd.DataFrame(columns=["npi"]), None, min_post_paid=1.0,
                    successors=res)
    row = t[t["contradiction"] == "successor_after_exclusion"].iloc[0]
    assert row["tier"] == "A" and row["npi"] == SUCC and abs(row["post_event_paid"] - 45_000.0) < 1e-6
    assert "successor of " + EXCL in row["detail"] and "shared top codes 2 of 5" in row["detail"]
    md = cx.to_markdown(t, {"successor_after_exclusion_status": "computed"})
    assert "## Successor billing after an exclusion" in md
    assert "legitimately hired replacement clinician" in md
    assert SUCC in md


def test_successor_check_is_a_no_op_without_npi_to_org(tmp_path, capsys):
    fact = _succ_fact(tmp_path)
    res = cx.successor_after_exclusion(fact, _succ_events(), tmp_path / "missing_npi_to_org.parquet")
    assert res.empty and list(res.columns) == cx.SUCCESSOR_COLS
    assert "skipped (no npi_to_org" in capsys.readouterr().out
    res2 = cx.successor_after_exclusion(fact, pd.DataFrame(columns=["npi", "event", "event_date"]), tmp_path / "x.parquet")
    assert res2.empty


# ---------------------------------------------------------------- C. enrolled after the owner's exclusion
def _owner_graph(tmp: Path):
    rows = []
    for ym in [f"{y}-{m:02d}" for y in (2020, 2021, 2022) for m in range(1, 13)]:
        rows.append({"billing_npi": "1000000011", "service_month": ym, "total_paid": 20_000.0, "hcpcs_code": "T2016"})
        rows.append({"billing_npi": "1000000012", "service_month": ym, "total_paid": 5_000.0, "hcpcs_code": "T2016"})
        rows.append({"billing_npi": "1000000021", "service_month": ym, "total_paid": 30_000.0, "hcpcs_code": "T2016"})
    fact = tmp / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(fact, index=False)
    n2o = pd.DataFrame({"npi": ["1000000011", "1000000012", "1000000021"],
                        "org_node_id": ["org:LATE", "org:LATE", "org:EARLY"]})
    owned_by = pd.DataFrame({"src_id": ["org:LATE", "org:EARLY"], "dst_id": ["owner:1777777777", "owner:SMITH|JANE"],
                             "edge_type": "owned_by", "pct_ownership": ["100", "50"],
                             "owner_role": ["OWNER", "MANAGING EMPLOYEE"],
                             "association_date": ["2021-05-01", "2017-01-01"], "is_private_equity": [0, 0]})
    excluded_in = pd.DataFrame({"src_id": ["owner:1777777777", "owner:SMITH|JANE", "provider:1000000099"],
                                "dst_id": ["exclusion:0", "exclusion:1", "exclusion:2"],
                                "edge_type": "excluded_in", "match_tier": ["exact", "probable", "exact"],
                                "excl_date": ["2019-03-01", "2019-03-01", "2018-01-01"]})
    excl_nodes = pd.DataFrame({"node_id": ["exclusion:0", "exclusion:1", "exclusion:2"],
                               "npi": ["1777777777", "", "1000000099"], "excl_type": ["1128a1", "1128b4", "1128a1"],
                               "excl_date": ["2019-03-01", "2019-03-01", "2018-01-01"],
                               "reinstate_date": [None, None, None]})
    owner_nodes = pd.DataFrame({"node_id": ["owner:1777777777", "owner:SMITH|JANE"], "owner_type": ["I", "I"]})
    org_nodes = pd.DataFrame({"org_node_id": ["org:LATE", "org:EARLY"], "org_name": ["LATE HOME CARE LLC", "EARLY HOME CARE LLC"]})
    return fact, n2o, owned_by, excluded_in, excl_nodes, owner_nodes, org_nodes


def test_enrolled_after_owner_exclusion_finds_the_late_joiner_only(tmp_path):
    fact, n2o, owned_by, excluded_in, excl_nodes, owner_nodes, org_nodes = _owner_graph(tmp_path)
    res = cx.enrolled_after_owner_exclusion(fact, owned_by, excluded_in, excl_nodes, owner_nodes, n2o, org_nodes)
    assert res["org_node_id"].tolist() == ["org:LATE"]           # EARLY's owner joined in 2017, excluded in 2019
    r = res.iloc[0]
    assert r["owner_node_id"] == "owner:1777777777" and r["match_basis"].startswith("exact")
    assert r["owner_role"] == "OWNER" and r["owner_type"] == "I" and r["excl_type"] == "1128a1"
    assert r["org_first_month"] == "2020-01" and r["top_npi"] == "1000000011" and r["org_name"] == "LATE HOME CARE LLC"
    assert abs(r["paid_since_association"] - 25_000.0 * 20) < 1e-6   # 2021-05..2022-12 = 20 months
    assert abs(r["org_total_paid"] - 25_000.0 * 36) < 1e-6
    assert r["basis_date"] == "association_date"
    t = cx.assemble(pd.DataFrame(columns=["npi", "event", "event_date", "end_date", "detail", "name", "post_paid",
                                          "post_months", "first_post_month", "last_month", "total_paid"]),
                    pd.DataFrame(columns=["npi"]), pd.DataFrame(columns=["npi"]), None, min_post_paid=1.0, owner_excl=res)
    row = t.iloc[0]
    assert row["contradiction"] == "enrolled_after_owner_exclusion" and row["tier"] == "A"
    assert row["npi"] == "1000000011" and row["name"] == "LATE HOME CARE LLC" and row["event_date"] == "2019-03-01"
    assert "owner owner:1777777777" in row["detail"] and "association 2021-05-01" in row["detail"]
    md = cx.to_markdown(t, {"enrolled_after_owner_exclusion_status": "computed"})
    assert "## Organizations whose PECOS owner was excluded before joining" in md
    assert "name collision on the owner match" in md and "LATE HOME CARE" in md
    assert "SMITH" not in md                                      # the ignored owner never reaches the page


def test_enrolled_after_owner_exclusion_uses_first_billed_month_when_the_date_is_missing(tmp_path):
    fact, n2o, owned_by, excluded_in, excl_nodes, owner_nodes, org_nodes = _owner_graph(tmp_path)
    ob = owned_by.copy()
    ob["association_date"] = [None, None]                        # no dates: EARLY first billed 2020-01 > excluded 2019-03
    res = cx.enrolled_after_owner_exclusion(fact, ob, excluded_in, excl_nodes, owner_nodes, n2o, org_nodes)
    assert sorted(res["org_node_id"]) == ["org:EARLY", "org:LATE"]
    assert set(res["basis_date"]) == {"org_first_billed_month"}
    e = res[res["org_node_id"] == "org:EARLY"].iloc[0]
    assert e["match_basis"].startswith("probable") and abs(e["paid_since_association"] - 30_000.0 * 36) < 1e-6
    assert res["paid_since_association"].is_monotonic_decreasing


def test_owner_exclusion_check_is_a_no_op_without_inputs(tmp_path, capsys):
    fact, n2o, owned_by, excluded_in, excl_nodes, owner_nodes, org_nodes = _owner_graph(tmp_path)
    assert cx.enrolled_after_owner_exclusion(fact, None, excluded_in, excl_nodes, owner_nodes, n2o).empty
    assert "no owned_by edges" in capsys.readouterr().out
    assert cx.enrolled_after_owner_exclusion(fact, owned_by, None, excl_nodes, owner_nodes, n2o).empty
    assert "no excluded_in edges" in capsys.readouterr().out
    assert cx.enrolled_after_owner_exclusion(fact, owned_by, excluded_in, excl_nodes, owner_nodes,
                                             tmp_path / "missing.parquet").empty
    assert "no npi_to_org" in capsys.readouterr().out
    # the report on an empty table still prints both sections and says why each check did not run
    md = cx.to_markdown(cx.assemble(pd.DataFrame(columns=["npi", "event", "event_date", "end_date", "detail", "name",
                                                          "post_paid", "post_months", "first_post_month", "last_month",
                                                          "total_paid"]),
                                    pd.DataFrame(columns=["npi"]), pd.DataFrame(columns=["npi"]), None),
                        {"successor_after_exclusion_status": "no npi_to_org (x)",
                         "enrolled_after_owner_exclusion_status": "graph files missing"})
    assert "This check did not run: no npi_to_org" in md and "This check did not run: graph files missing" in md
    assert "legitimately hired replacement clinician" in md and "name collision on the owner match" in md
