"""org_find: 'did we see this name?' + code concentration, on a synthetic fixture.

2026-10-12: counsel brought a name from outside the model (TMS billed under one
psychiatrist's NPI across many sites). The answer has to come from the operator's
own files in one command: NPPES hits, matrix rows, list ranks, and the code-share
table that finds others with the same shape."""
import subprocess
import sys

import pandas as pd
import pytest


@pytest.fixture
def fx(tmp_path):
    proc = tmp_path / "processed"
    proc.mkdir()
    run = tmp_path / "run"
    run.mkdir()
    dim = pd.DataFrame({
        "npi": ["1000000001", "1000000002", "1000000003", "1000000004"],
        "entity_type": ["2", "1", "1", "2"],
        "org_legal_name": ["LEARNING TO ACHIEVE WELLNESS LLC", "", "", "OTHER CLINIC INC"],
        "provider_name": ["LEARNING TO ACHIEVE WELLNESS LLC", "DOE, JANE", "ROE, RICK", "OTHER CLINIC INC"],
        "addr_city": ["DALLAS", "DALLAS", "AUSTIN", "MIAMI"],
        "addr_state": ["TX", "TX", "TX", "FL"],
        "taxonomy_code": ["261Q00000X", "2084P0800X", "2084P0800X", "261Q00000X"],
    })
    dim.to_parquet(proc / "provider_dim.parquet", index=False)
    rows = []
    # 1000000002: an individual psychiatrist NPI with $900 on TMS codes, one renderer (itself)
    for m in ("2023-01", "2023-02", "2023-03"):
        rows.append(("1000000002", "1000000002", "90868", f"{m}-01", 300.0, 30, 5))
        rows.append(("1000000002", "1000000002", "99213", f"{m}-01", 50.0, 5, 5))
    # 1000000001: the org NPI, TMS under three renderers
    for s in ("1000000005", "1000000006", "1000000007"):
        rows.append(("1000000001", s, "90867", "2023-02-01", 200.0, 10, 5))
    # 1000000003: no TMS at all
    rows.append(("1000000003", "1000000003", "99214", "2023-02-01", 400.0, 10, 8))
    # 1000000004: org with a small TMS share
    rows.append(("1000000004", "1000000008", "90869", "2023-02-01", 20.0, 1, 1))
    rows.append(("1000000004", "1000000008", "99213", "2023-02-01", 980.0, 90, 40))
    sp = pd.DataFrame(rows, columns=["billing_npi", "servicing_npi", "hcpcs_code",
                                     "service_month", "total_paid", "total_claim_lines",
                                     "total_patients"])
    sp.to_parquet(proc / "spending_fact.parquet", index=False)
    mat = pd.DataFrame({
        "npi": ["1000000001", "1000000002", "1000000003"],
        "org_node_id": ["org:a", "org:a", "org:b"],
        "org_legal_name": ["LEARNING TO ACHIEVE WELLNESS LLC", "", ""],
        "provider_name": ["LEARNING TO ACHIEVE WELLNESS LLC", "DOE, JANE", "ROE, RICK"],
        "practice_state": ["TX", "TX", "TX"],
        "net_paid": [600.0, 1050.0, 400.0],
        "provider_on_exclusion": [0, 0, 1],
        "subscore_capacity_mismatch": [0.1, 0.9, float("nan")],
        "subscore_pill_mill": [0.0, 0.2, 0.5],
    })
    mat.to_parquet(run / "provider_features_for_model.parquet", index=False)
    pd.DataFrame({"org_id": ["org:b", "org:a"], "org_name": ["ROE RICK", "LEARNING TO ACHIEVE WELLNESS LLC"],
                  "n_providers": [1, 2], "total_billed": [400.0, 1650.0],
                  "score_dollar_weighted": [0.3, 0.2], "score_max": [0.5, 0.9],
                  "top_schemes": ["pill_mill", "capacity_mismatch"]}).to_csv(
        run / "provider_priority_list.csv", index=False)
    pd.DataFrame({"org_id": ["org:a"], "org_name": ["LEARNING TO ACHIEVE WELLNESS LLC"],
                  "n_providers": [2], "total_billed": [1650.0]}).to_csv(
        run / "provider_priority_list_pursuit.csv", index=False)
    return tmp_path, proc, run


def test_name_lookup_hits_dim_matrix_and_lists(fx):
    from src.model_a.org_find import find_in_provider_dim, find_in_matrix, find_in_lists
    _, proc, run = fx
    d = find_in_provider_dim(proc / "provider_dim.parquet", "learning to achieve")
    assert list(d["npi"]) == ["1000000001"] and d.iloc[0]["state"] == "TX"
    m = find_in_matrix(run / "provider_features_for_model.parquet", ["1000000001", "1000000002"])
    assert set(m["npi"]) == {"1000000001", "1000000002"}
    r2 = m.set_index("npi").loc["1000000002"]
    assert r2["top_subscores"].startswith("capacity_mismatch=0.90") and int(r2["label"]) == 0
    assert "subscore_capacity_mismatch" not in m.columns
    lists = find_in_lists(run, ["org:a"])
    assert set(lists["list"]) == {"provider_priority_list.csv", "provider_priority_list_pursuit.csv"}
    main = lists[lists["list"] == "provider_priority_list.csv"].iloc[0]
    assert int(main["rank"]) == 2 and int(main["list_rows"]) == 2
    # pattern alone also lands on the list row (an org named on a list but not in NPPES)
    assert len(find_in_lists(run, [], pattern="achieve")) == 2


def test_code_concentration_shapes(fx):
    from src.model_a.org_find import code_concentration
    _, proc, _ = fx
    c = code_concentration(proc / "spending_fact.parquet", ["90867", "90868", "90869"],
                           provider_dim=proc / "provider_dim.parquet", top=10)
    c = c.set_index("npi")
    assert "1000000003" not in c.index                       # no TMS dollars
    assert c.loc["1000000002", "paid_codes"] == 900.0 and c.loc["1000000002", "paid_all"] == 1050.0
    assert abs(c.loc["1000000002", "code_share"] - 900 / 1050) < 1e-9
    assert c.loc["1000000002", "n_servicing_codes"] == 1
    assert c.loc["1000000002", "shape"] == "individual_npi_one_renderer"
    assert c.loc["1000000001", "n_servicing_codes"] == 3 and c.loc["1000000001", "shape"] == "org_npi"
    assert abs(c.loc["1000000004", "code_share"] - 0.02) < 1e-9
    assert c.index[0] == "1000000002"                        # ranked by dollars on the codes
    # restricted to a set
    r = code_concentration(proc / "spending_fact.parquet", ["90868"], npis=["1000000002", "1000000003"])
    assert list(r["npi"]) == ["1000000002"]


def test_cli_end_to_end(fx):
    tmp, proc, run = fx
    out = tmp / "find.md"
    cmd = [sys.executable, "-m", "src.model_a.org_find", "--name", "learning to achieve",
           "--run-dir", str(run), "--data-root", str(tmp), "--codes", "90867,90868,90869",
           "--top", "5", "--out", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    md = out.read_text(encoding="utf-8")
    assert "NPPES (provider_dim): 1 NPI(s) match" in md
    assert "Run matrix: 1 row(s)" in md                      # the org NPI; the psychiatrist is a different name
    assert "provider_priority_list_pursuit.csv" in md
    assert "pi_facts --run-dir <run> --deep --orgs \"org:a\"" in md
    assert "National top" in md and "individual_npi_one_renderer" in md
    assert "never accusations" in md
    # no --name and no --codes is an error
    bad = subprocess.run([sys.executable, "-m", "src.model_a.org_find", "--data-root", str(tmp)],
                         capture_output=True, text=True)
    assert bad.returncode != 0


def test_servicing_profile_says_whose_number(fx):
    """2026-10-13: a PI finds staff NPIs at a card's address; the registry says where
    they practise, the fact says whose number their work went out under."""
    from src.model_a.org_find import servicing_profile
    _, proc, _ = fx
    summ, under = servicing_profile(proc / "spending_fact.parquet",
                                    ["1000000005", "1000000002", "1000000008", "1999999999"],
                                    provider_dim=proc / "provider_dim.parquet")
    s = summ.set_index("npi")
    assert s.loc["1000000005", "shape"] == "renders_under_others_only"
    assert s.loc["1000000005", "paid_as_renderer_for_others"] == 200.0
    assert s.loc["1000000005", "n_billers_rendered_for"] == 1
    assert s.loc["1000000002", "shape"] == "bills_own_number_only" and s.loc["1000000002", "paid_as_biller"] == 1050.0
    assert s.loc["1000000008", "paid_as_renderer_for_others"] == 1000.0
    assert s.loc["1999999999", "shape"] == "absent_from_fact"
    u = under.set_index(["npi", "billing_npi"])
    assert u.loc[("1000000005", "1000000001"), "billing_name"].startswith("LEARNING TO ACHIEVE")
    assert u.loc[("1000000005", "1000000001"), "billing_entity_type"] == "2"


def test_cli_npi_mode(fx):
    tmp, proc, run = fx
    out = tmp / "npi.md"
    res = subprocess.run([sys.executable, "-m", "src.model_a.org_find", "--npi", "1000000005,1000000002",
                          "--data-root", str(tmp), "--out", str(out)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    md = out.read_text(encoding="utf-8")
    assert "Whose number went on their work (2 NPIs)" in md
    assert "renders_under_others_only" in md and "bills_own_number_only" in md
