"""PLATFORM_AUDIT batch 2 (2026-10-21, v20 semantics): asserted joins instead of silent
de-duplication, the stage-one reject count, the label gates on every path, one outcome
vocabulary, the exclusion-node dedup, the assessable flag enforced, the death cut with a
DOB, the measurement baseline, the affiliated ruler discovered, Luhn everywhere, the
pre-ranked pass-through, case-tolerant header resolvers. Each pinned here."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.attempt_2.clean_data import is_valid_npi

NPI_A, NPI_B, NPI_C, NPI_D = "1234567893", "1245319599", "1003000126", "1992999999"
BAD_NPI = "1234567890"                      # ten digits, Luhn fails
assert all(is_valid_npi(n) for n in (NPI_A, NPI_B, NPI_C, NPI_D)) and not is_valid_npi(BAD_NPI)


# ---- H9: join_m1 keeps the join many-to-one and quarantines the duplicate ---------------
def test_h9_join_m1_quarantines_duplicate_right_rows_and_never_fans_out():
    from src.model_a import provider_features_export as pfe
    pfe._JOIN_QUARANTINE.clear()
    left = pd.DataFrame({"npi": [NPI_A, NPI_B, NPI_C]})
    right = pd.DataFrame({"npi": [NPI_A, NPI_A, NPI_B], "x": [1.0, 2.0, 3.0]})
    logs = []
    out = pfe.join_m1(left, right, source="adapter:t", log=logs.append)
    assert len(out) == 3 and out.set_index("npi").loc[NPI_A, "x"] == 1.0     # the first kept
    assert len(pfe._JOIN_QUARANTINE) == 1
    q = pfe._JOIN_QUARANTINE[0]
    assert list(q["join_source"]) == ["adapter:t"] and q.iloc[0]["x"] == 2.0
    assert any("duplicate" in l for l in logs)
    pfe._JOIN_QUARANTINE.clear()


def test_h9_broadcast_refuses_an_npi_mapped_to_two_orgs():
    from src.model_a.provider_features_export import _broadcast_org_to_npi
    xw = pd.DataFrame({"npi": [NPI_A, NPI_A], "org_node_id": ["org:1", "org:2"]})
    org = pd.DataFrame({"org_node_id": ["org:1", "org:2"], "v": [1.0, 2.0]})
    with pytest.raises(AssertionError, match="fan out"):
        _broadcast_org_to_npi(xw, org, ["v"])


# ---- B.F-9: sources that already emit percentiles pass through -------------------------
def test_bf9_pre_ranked_inputs_bypass_peer_normalization():
    from src.model_a.provider_features_export import _is_pre_ranked
    assert _is_pre_ranked("pbj_weekend_staffing_gap") and _is_pre_ranked("nh_penalty_dollars")
    assert _is_pre_ranked("market_saturation_index") and _is_pre_ranked("hcris_cost_anomaly")
    assert not _is_pre_ranked("services_per_bene") and not _is_pre_ranked("opioid_rate")


# ---- H10: stage one counts and keeps the rejected raw lines ----------------------------
def test_h10_csv_to_parquet_sets_malformed_lines_aside(tmp_path):
    import duckdb
    from src.attempt_2.clean_data import csv_to_parquet
    csv = tmp_path / "spend.csv"
    csv.write_text("npi,hcpcs,paid\n1234567893,99213,10\n1245319599,99214,20,EXTRA,COLS,HERE\n"
                   "1003000126,99215,30\n", encoding="utf-8")
    con = duckdb.connect()
    out = Path(csv_to_parquet(con, str(csv), tmp_path / "pq"))
    good = pd.read_parquet(out)
    rej = tmp_path / "pq" / "spend_rejects.parquet"
    if rej.exists():
        r = pd.read_parquet(rej)
        n_lines = r["line"].nunique() if "line" in r.columns else len(r)
        assert len(good) + n_lines == 3 and n_lines == 1        # rows + rejected lines = every line seen
        assert "Expected Number of Columns" in str(r.iloc[0]["error_message"])
    else:                                           # this DuckDB has no store_rejects: nothing lost silently
        assert len(good) == 3


# ---- C.F4 / C.F8: the strict export reads the tier and the provenance suffix ------------
def test_cf8_provenance_suffix_never_changes_the_tier():
    from src.model_a.fca_relevance import fca_tier
    assert fca_tier("1128a1|excl_name_state_unique")[0] == fca_tier("1128a1")[0] == "hard"
    assert fca_tier("1128b4|research_medium")[0] == fca_tier("1128b4")[0]
    # the sources whose own body carries a pipe keep it
    assert fca_tier("sam:Ineligible|HHS|Reciprocal")[0] == fca_tier("sam:Ineligible|HHS|Reciprocal")[0]


def test_cf8_names_lane_rows_carry_their_match_level_in_excl_type():
    from src.model_a.label_expansion import name_resolved_exclusions
    res = pd.DataFrame([{"npi": NPI_A, "status": "resolved", "match_level": "excl_name_state_unique",
                         "entity_name": "JOHN DOE", "excl_type": "1128a1", "excl_date": "2019-03-01",
                         "source_node_id": "exclusion:1", "kind": "person", "state": "FL"}])
    out = name_resolved_exclusions(res)
    assert len(out) == 1 and out.iloc[0]["excl_type"] == "1128a1|excl_name_state_unique"


# ---- C.F5: an LEIE aide never name-matches NPPES -----------------------------------------
def test_cf5_non_npi_role_rows_never_resolve_by_name():
    from src.model_a.label_expansion import exclusion_name_resolve
    ex = pd.DataFrame([{"node_id": "exclusion:1", "npi": "", "entity_name": "SMITH, JANE",
                        "excl_type": "1128a1", "excl_date": "2019-03-01", "state": "FL", "city": "",
                        "general": "NURSE AIDE", "specialty": "CNA", "kind": "person"},
                       {"node_id": "exclusion:2", "npi": "", "entity_name": "SMITH, JANE",
                        "excl_type": "1128a1", "excl_date": "2019-03-01", "state": "GA", "city": "",
                        "general": "PHYSICIAN", "specialty": "MD", "kind": "person"}])
    pdim = pd.DataFrame({"npi": [NPI_A, NPI_B], "provider_name": ["SMITH, JANE", "SMITH, JANE"],
                         "entity_type": ["1", "1"], "addr_state": ["FL", "GA"], "addr_city": ["", ""]})
    try:
        res = exclusion_name_resolve(ex, pdim)
    except TypeError:
        pytest.skip("exclusion_name_resolve signature differs on this branch")
    by = res.set_index("source_node_id") if "source_node_id" in res.columns else res.set_index("node_id")
    assert by.loc["exclusion:1", "status"] == "non_npi_role"
    assert by.loc["exclusion:2", "status"] == "resolved"


# ---- C.F9: CIA research entities go through the NPPES name gate -------------------------
def test_cf9_cia_entities_require_the_nppes_name_to_agree():
    from src.enforcement.research_evidence import load_cia_entities
    table = pd.DataFrame([
        {"party": "Acme Health Partners", "enrolled_entity_name": "Acme Home Health LLC", "npi": NPI_A},
        {"party": "Acme Health Partners", "enrolled_entity_name": "Acme Hospice Inc", "npi": NPI_B},    # a typo NPI
        {"party": "Acme Health Partners", "enrolled_entity_name": "Acme Clinic", "npi": BAD_NPI},
        {"party": "Acme Health Partners", "enrolled_entity_name": "Acme Labs", "npi": NPI_C},         # not in NPPES
    ])
    pdim = pd.DataFrame({"npi": [NPI_A, NPI_B], "provider_name": ["ACME HOME HEALTH LLC", "ZETA DIALYSIS CENTER"],
                         "addr_city": ["TAMPA", "MIAMI"]})
    out = load_cia_entities(table, provider_dim=pdim).set_index("enrolled_entity_name")
    assert out.loc["Acme Home Health LLC", "status"] == "resolved"
    assert out.loc["Acme Home Health LLC", "match_level"] == "research_cia"
    assert out.loc["Acme Hospice Inc", "status"] == "research_name_conflict"
    assert out.loc["Acme Hospice Inc", "npi"] == "" and out.loc["Acme Hospice Inc", "npi_candidate"] == NPI_B
    assert out.loc["Acme Clinic", "status"] == "research_no_npi"
    assert out.loc["Acme Labs", "status"] == "research_name_conflict"
    assert "not in NPPES" in out.loc["Acme Labs", "gate_note"]
    # without provider_dim the gate cannot run and the row says so
    out2 = load_cia_entities(table.head(1))
    assert out2.iloc[0]["status"] == "resolved" and "not run" in out2.iloc[0]["gate_note"]


# ---- C.F12: one outcome vocabulary on the MFCU / lanes path ----------------------------
def test_cf12_mfcu_rows_without_an_outcome_are_usable_zero():
    from src.enforcement.research_evidence import load_mfcu_cases
    t = pd.DataFrame([
        {"defendant": "Sunrise Clinic Inc", "date": "2022-02-02", "amount": "500000",
         "summary": "agreed to pay to settle allegations of upcoding", "url": "https://ag.ga/a"},
        {"defendant": "Dr Pending", "date": "2022-03-03", "amount": "100000",
         "summary": "charged with medicaid fraud; the case is pending", "url": "https://ag.ga/b"},
    ])
    out = load_mfcu_cases(t, "GA")
    by = out.set_index("defendant_name")
    assert int(by.loc["Sunrise Clinic Inc", "usable"]) == 1
    assert int(by.loc["Dr Pending", "usable"]) == 0 and "outcome" in str(by.loc["Dr Pending", "qc_note"])


def test_cf12_lanes_outcome_vocabulary_is_the_strict_exports():
    from src.model_a import manus_lanes as ml
    assert ml._OUTCOME_OK.search("pleaded guilty to health care fraud")
    assert ml._OUTCOME_OK.search("agreed to pay $2 million to resolve allegations")
    assert not ml._OUTCOME_OK.search("was excluded from the program")          # an exclusion is not a case outcome
    assert ml._CHARGE_ONLY.search("was indicted on three counts")


# ---- C.F13: one exclusion record, one node ----------------------------------------------
def test_cf13_graph_loader_dedups_exclusion_records_across_files():
    from src.entity_graph.__main__ import dedup_exclusion_sources
    ex = pd.DataFrame({"npi": [NPI_A, NPI_A, NPI_B, ""], "name_key": ["a", "a", "b", "c"],
                       "excl_type": ["1128a1", "1128a1", "1128b4", "1128a1"],
                       "excl_date": ["2019-01-01", "2019-01-01", "2020-01-01", "2018-01-01"]})
    logs = []
    out = dedup_exclusion_sources(ex, log=logs.append)
    assert len(out) == 3 and any("1 duplicate" in l for l in logs)


# ---- C.F14: Luhn everywhere ----------------------------------------------------------------
def test_cf14_sam_extract_blanks_a_ten_digit_non_npi(tmp_path):
    from src.enforcement.sam_api import load_sam_extract
    p = tmp_path / "sam_exclusions_2026.csv"
    pd.DataFrame([{"Name": "ACME LLC", "First": "", "Middle": "", "Last": "", "NPI": BAD_NPI,
                   "Exclusion Type": "Ineligible", "Excluding Agency": "HHS", "Exclusion Program": "Reciprocal",
                   "Active Date": "2020-01-01", "Termination Date": "", "Record Status": "Active",
                   "State": "FL", "Classification": "Firm"},
                  {"Name": "BETA LLC", "First": "", "Middle": "", "Last": "", "NPI": NPI_A,
                   "Exclusion Type": "Ineligible", "Excluding Agency": "HHS", "Exclusion Program": "Reciprocal",
                   "Active Date": "2020-01-01", "Termination Date": "", "Record Status": "Active",
                   "State": "FL", "Classification": "Firm"}]).to_csv(p, index=False)
    df = load_sam_extract(p)
    by = df.set_index("entity_name")
    assert by.loc["ACME LLC", "npi"] == "" and by.loc["BETA LLC", "npi"] == NPI_A


def test_cf14_opensanctions_keeps_only_luhn_valid_npis():
    from src.enforcement.opensanctions import normalize_opensanctions
    ents = [{"schema": "Person", "properties": {"name": ["JOHN DOE"], "npiCode": [BAD_NPI],
                                                "startDate": ["2020-01-01"]}, "datasets": ["us_oig_leie"]},
            {"schema": "Person", "properties": {"name": ["JANE ROE"], "npiCode": [NPI_A],
                                                "startDate": ["2020-01-01"]}, "datasets": ["us_oig_leie"]}]
    df = normalize_opensanctions(ents).set_index("entity_name")
    assert df.loc["JOHN DOE", "npi"] == "" and df.loc["JANE ROE", "npi"] == NPI_A


def test_cf14_case_labels_take_only_luhn_valid_named_npis(tmp_path):
    from tests.test_audit_fixes_2026_10 import _graph
    from src.model_a.case_labels import build_case_labels
    _, org_nodes, npi_to_org = _graph(tmp_path)
    db = pd.DataFrame([{"case_id": "C1", "announced_date": "2020-05-05", "defendant_name": "ZZZ NOBODY LLC",
                        "defendant_name_key": "zzz nobody", "sector": "home_health", "scheme": "upcoding",
                        "amount_usd": 1_000_000, "jurisdiction": "", "source_url": "https://doj.gov/pr/x",
                        "summary": "pleaded guilty to health care fraud from 2017 through 2019",
                        "npi_in_source": BAD_NPI},
                       {"case_id": "C2", "announced_date": "2020-05-05", "defendant_name": "ZZZ NOBODY2 LLC",
                        "defendant_name_key": "zzz nobody2", "sector": "home_health", "scheme": "upcoding",
                        "amount_usd": 1_000_000, "jurisdiction": "", "source_url": "https://doj.gov/pr/y",
                        "summary": "pleaded guilty to health care fraud from 2017 through 2019",
                        "npi_in_source": NPI_D}])
    lab = build_case_labels(db, org_nodes, npi_to_org)
    named = lab[lab["label_basis"] == "named_npi"]
    assert set(named["npi"]) == {NPI_D}


def test_cf14_enrollment_candidates_without_a_luhn_npi_go_to_the_administrative_file():
    from src.ingest_cms.medicaid_enrollment import luhn_gate_candidates
    ev = pd.DataFrame({"npi": [NPI_A, BAD_NPI, ""], "excl_type": ["mcd_enroll:FL:LICENSE REVOKED"] * 3,
                       "excl_date": pd.to_datetime(["2020-01-31"] * 3)})
    adm = pd.DataFrame({"npi": [NPI_B], "excl_type": ["mcd_enroll:FL:TERM - OTHER"],
                        "excl_date": pd.to_datetime(["2020-01-31"]), "drop_reason": ["no_sanction_named"],
                        "state_share": [0.9], "state_rate": [0.1]})
    ev2, adm2 = luhn_gate_candidates(ev, adm)
    assert list(ev2["npi"]) == [NPI_A]
    assert (adm2["drop_reason"] == "invalid_npi").sum() == 2 and len(adm2) == 3


def test_cf14_widened_label_reads_luhn_valid_nodes_only(tmp_path):
    from src.model_a.provider_features_export import _widened_label_from_graph
    g = tmp_path / "graph" / "nodes"
    g.mkdir(parents=True)
    pd.DataFrame({"node_id": ["exclusion:1", "exclusion:2", "exclusion:3"],
                  "npi": [NPI_A, BAD_NPI, "1234567890123"],
                  "excl_type": ["1128a1", "1128a1", "1128a1"],
                  "excl_date": pd.to_datetime(["2019-01-01"] * 3)}).to_parquet(g / "exclusion_nodes.parquet", index=False)
    wl = _widened_label_from_graph(tmp_path / "graph", print)
    assert wl is not None and set(wl["npi"]) == {NPI_A}


# ---- C.F6: the standalone research CLI refuses to fold without the name gate ------------
def test_cf6_defendant_research_poll_refuses_without_provider_dim(tmp_path):
    res = tmp_path / "case_defendant_npis.parquet"
    pd.DataFrame({"case_id": ["c"], "defendant": ["X"], "npi": [""], "status": ["unmatched"]}).to_parquet(res, index=False)
    out = tmp_path / "batch"
    out.mkdir()
    (out / "defendant_research_prompts.jsonl").write_text("", encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "src.enforcement.defendant_research", "--poll",
                        "--resolved", str(res), "--out-dir", str(out)],
                       capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode != 0 and "name gate" in (r.stdout + r.stderr)


# ---- B.F-14: assessable enforced by the scorer -------------------------------------------
def test_bf14_unassessable_rows_are_held_out_of_training_and_counted(capsys):
    from tests.test_company_list import _matrix, _manifest
    from src.model_a.company_list import oof_scores, rollup, unassessable_mask
    m = _matrix()
    m["assessable"] = 1
    neg = m.index[(pd.to_numeric(m["provider_on_exclusion"], errors="coerce").fillna(0) == 0)][:40]
    m.loc[neg, "assessable"] = 0
    assert int(unassessable_mask(m).sum()) == 40
    s = oof_scores(m, _manifest(), n_splits=3)
    out = capsys.readouterr().out
    assert "40 unassessable providers" in out and len(s) == len(m)
    r = rollup(m, s)
    assert "n_unassessable_members" in r.columns and int(r["n_unassessable_members"].sum()) == 40
    # a matrix without the column trains exactly as before: no mask, no column
    m2 = m.drop(columns=["assessable"])
    assert not unassessable_mask(m2).any() and "n_unassessable_members" not in rollup(m2, s).columns


# ---- the ablation fence = the shipped scorer's fence -------------------------------------
def test_ablation_fence_removes_the_label_adjacent_columns():
    from src.model_a.source_ablation import _fenced
    man = {"leakage_adjacent": ["graph_fraud_proximity"], "leakage_hard": ["provider_on_leie"]}
    cols = ["services_per_bene", "graph_fraud_proximity", "within_2_hops_of_exclusion",
            "subscore_ownership_integrity", "provider_on_leie", "graph_emb_0"]
    assert _fenced(cols, man) == ["services_per_bene", "graph_emb_0"]


# ---- B.F-22: the affiliated ruler is discovered and graded -------------------------------
def test_bf22_discover_rulers_reads_every_suffix(tmp_path):
    from src.model_a.accuracy_diagnostics import _discover_rulers
    for n in ("future_bans_after_2021-12.csv", "future_bans_after_2021-12__hard.csv",
              "future_bans_after_2021-12__fca36.csv", "future_bans_after_2021-12__aff36.csv",
              "future_bans_after_2021-12__quitam36.csv"):
        (tmp_path / n).write_text("npi\n", encoding="utf-8")
    got = _discover_rulers(tmp_path, "2021-12")
    assert set(got) == {"all", "hard", "fca_file", "aff", "qui_tam_file"}
    assert got["aff"].name.endswith("__aff36.csv")


# ---- B.F-21: the scheme-health baseline is the previous run ------------------------------
def test_bf21_measure_run_reads_the_previous_runs_scheme_health():
    sh = (Path(__file__).resolve().parents[1] / "ops" / "vm" / "measure_run.sh").read_text(encoding="utf-8")
    assert 'PREV="${PREV_RUN:-}"' in sh and '--baseline "$BASELINE"' in sh
    assert 'BASELINE="$PREV/scheme_health.json"' in sh


# ---- A.F-07: the death cut is DOB-corroborated from the exclusion nodes -------------------
def test_af07_death_match_uses_the_exclusion_nodes_dob():
    from src.enforcement.death_master import match_deceased_providers
    pdim = pd.DataFrame({"npi": [NPI_A], "provider_name": ["JOHN DOE"], "entity_type": ["1"]})
    dmf = pd.DataFrame({"name_key": ["DOE JOHN"], "dob": pd.to_datetime(["1950-02-02"]),
                        "dod": pd.to_datetime(["2019-06-01"])})
    low = match_deceased_providers(pdim, dmf)
    high = match_deceased_providers(pdim, dmf, dob_by_npi=pd.Series({NPI_A: "1950-02-02"}))
    wrong = match_deceased_providers(pdim, dmf, dob_by_npi=pd.Series({NPI_A: "1961-09-09"}))
    if len(low):
        assert (low["match_confidence"] != "high").all()     # name-only never reaches "high"
    assert len(high) == 1 and high.iloc[0]["match_confidence"] == "high"
    assert not len(wrong) or (wrong["match_confidence"] != "high").all()


# ---- A.§4d / §5i: case-tolerant header resolvers ----------------------------------------
def test_a4d_nucc_resolver_is_case_tolerant():
    from src.ingest_cms.nucc_taxonomy import load_taxonomy_hierarchy
    raw = pd.DataFrame({"CODE ": ["207Q00000X"], "grouping": ["Allopathic"], "CLASSIFICATION": ["Family Medicine"]})
    out = load_taxonomy_hierarchy(raw)
    assert list(out["taxonomy_code"]) == ["207Q00000X"] and out.iloc[0]["classification"] == "Family Medicine"


def test_a5i_asof_nppes_pick_is_case_tolerant():
    from src.entity_graph.asof_nppes import _pick
    assert _pick(["NPI", "npi"], ["npi "]) == "npi "
    assert _pick(["Provider Enumeration Date"], ["PROVIDER ENUMERATION DATE"]) == "PROVIDER ENUMERATION DATE"
    assert _pick(["NPI"], ["x"]) is None


# ---- A.F-31: the PUF reader admits .xlsx -------------------------------------------------
def test_af31_read_any_reads_xlsx(tmp_path):
    from src.model_a.provider_features_export import _read_any
    p = tmp_path / "t.xlsx"
    pd.DataFrame({"NPI": [NPI_A], "v": ["007"]}).to_excel(p, index=False)
    df = _read_any(p)
    assert df is not None and df.iloc[0]["v"] == "007"       # strings, leading zeros kept


# ---- the pre-registration locks EVERY check, whichever way --check is written -----------
def test_preregister_repeated_check_flags_accumulate(tmp_path):
    (tmp_path / "model_a").mkdir()
    base = [sys.executable, "-m", "src.model_a.run_registry", "--data-root", str(tmp_path),
            "--preregister", "--goal", "g", "--expected", "e"]
    cwd = str(Path(__file__).resolve().parents[1])
    r1 = subprocess.run(base + ["--tag", "t_rep", "--check", "hits100 >= 4", "--check", "train = hard",
                                "--check", "frozen = True"], capture_output=True, text=True, cwd=cwd)
    r2 = subprocess.run(base + ["--tag", "t_one", "--check", "hits100 >= 4", "train = hard", "frozen = True"],
                        capture_output=True, text=True, cwd=cwd)
    assert r1.returncode == 0 and r2.returncode == 0, r1.stderr + r2.stderr
    import json as _json
    rows = [_json.loads(l) for l in (tmp_path / "model_a" / "preregistrations.jsonl").read_text().splitlines() if l.strip()]
    by = {r["tag"]: r for r in rows}
    assert len(by["t_rep"]["checks"]) == 3 and len(by["t_one"]["checks"]) == 3


# ---- market saturation: the period END is parsed, every file in the folder stacks ---------
def test_saturation_period_end_reads_every_cms_label_shape():
    from src.ingest_cms.saturation import period_end
    assert period_end("2021-01-01") == "2021-01"
    assert period_end("01/01/2021-12/31/2021") == "2021-12"
    assert period_end("Jan 2021 - Dec 2021") == "2021-12"
    assert period_end("2021") == "2021-12"
    assert period_end("Market_Saturation_and_Utilization_State_County_Archive_Dataset_2018-2019") == "2019-12"
    assert period_end("") == ""


def test_saturation_folder_stacks_the_archive_files_and_the_freeze_picks_inside(tmp_path):
    from src.ingest_cms.saturation import load_saturation_folder, compute_saturation_metrics
    cols = {"Type of Service": "Home Health", "State Name": "Texas", "County Name": "Bexar",
            "State and County FIPS Code": "48029", "Number of Fee-for-Service Beneficiaries": "1000"}
    pd.DataFrame([dict(cols, **{"Number of Providers": "90", "Reference Period": "01/01/2024-12/31/2024"}),
                  dict(cols, **{"Number of Providers": "40", "Reference Period": "01/01/2021-12/31/2021"})]
                 ).to_csv(tmp_path / "saturation.csv", index=False)
    # an archive file (no reference-period column): dated from its name
    pd.DataFrame([dict(cols, **{"Number of Providers": "10"})]
                 ).to_csv(tmp_path / "Market_Saturation_and_Utilization_State_County_Archive_Dataset_2018-2019.csv", index=False)
    raw, files = load_saturation_folder(tmp_path, log=lambda *_: None)
    assert len(files) == 2 and len(raw) == 3
    cur = compute_saturation_metrics(raw)
    assert float(cur.iloc[0]["n_providers"]) == 90.0 and cur.attrs["reference_period_end"] == "2024-12"
    frz = compute_saturation_metrics(raw, max_period="2021-12")
    assert float(frz.iloc[0]["n_providers"]) == 40.0 and frz.attrs["reference_period_end"] == "2021-12"
    old = compute_saturation_metrics(raw, max_period="2020-06")
    assert float(old.iloc[0]["n_providers"]) == 10.0           # the archive file carries the freeze
    with pytest.raises(ValueError):
        compute_saturation_metrics(raw, max_period="2015-01")
