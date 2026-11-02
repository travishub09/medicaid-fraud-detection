"""Batch 5b-G (architecture audit experiment 7, 2026-11-02): employer reach for the
Medicaid-side labels (label_expansion.affiliated_from_exclusions) and docket outcomes
onto the ruler (docket_backfill.docket_outcome_rows)."""
from __future__ import annotations

import subprocess
import sys

import pandas as pd
import pytest

from src.attempt_2.clean_data import _luhn_check_digit, is_valid_npi


def _npi(base9: str) -> str:
    """A Luhn-valid NPI from nine digits (the resolver rows pass the Luhn gate)."""
    return base9 + str(_luhn_check_digit("80840" + base9))


IND_HARD = _npi("100000001")       # LEIE 1128a1 (name-resolved), 2023-03-15
IND_WEAK = _npi("100000006")       # GA state termination, license revoked (weak), 2023-06-01
IND_MFCU = _npi("100000008")       # TX MFCU settlement resolved to this person, announced 2023-09-01
IND_EMPLOYER = _npi("100000050")   # an employer-lane row (affiliated:), an organization: set aside
IND_UNDATED = _npi("100000060")    # a state row without a date: set aside
BILLER_A = _npi("100000002")
BILLER_B = _npi("100000007")
BILLER_C = _npi("100000010")
BILLER_D = _npi("100000011")
BILLER_CHAIN = _npi("100000099")
OTHER = _npi("100000003")


def _spend(tmp_path, extra_rows=()):
    rows = []
    for m in ("2022-01", "2022-06", "2023-01"):
        rows.append((BILLER_A, IND_HARD, "99213", f"{m}-01", 100000.0))      # 300k of A's 400k window
        rows.append((BILLER_A, BILLER_A, "99213", f"{m}-01", 33333.33))
        rows.append((BILLER_B, IND_WEAK, "T1019", f"{m}-01", 20000.0))       # 60k of B's 100k window
        rows.append((BILLER_B, BILLER_B, "T1019", f"{m}-01", 13333.33))
        rows.append((BILLER_C, IND_MFCU, "H2015", f"{m}-01", 16666.67))      # 50k of C's 200k window (share 0.25)
        rows.append((BILLER_C, BILLER_C, "H2015", f"{m}-01", 50000.0))
    rows.append((BILLER_A, IND_HARD, "99213", "2019-01-01", 999999.0))        # outside the 24-month window
    rows.append((IND_HARD, IND_HARD, "99213", "2022-03-01", 5000.0))          # the individual's own number: never a row
    rows += list(extra_rows)
    p = tmp_path / "spend.parquet"
    pd.DataFrame(rows, columns=["billing_npi", "servicing_npi", "hcpcs_code", "service_month", "total_paid"]) \
        .to_parquet(p, index=False)
    return p


def _processed(tmp_path):
    """processed/ with the four exclusion files the graph build merges, plus the
    quarantine file (never loaded) and a field file (never a ruler)."""
    pr = tmp_path / "processed"
    pr.mkdir()
    pd.DataFrame({"npi": [IND_WEAK, IND_UNDATED], "entity_name": ["ROE, RICK", "NOBODY, DATED"],
                  "excl_type": ["state_term:GA:LICENSE REVOKED [nurse]", "state_term:GA:fraud conviction"],
                  "excl_date": ["2023-06-01", None]}).to_parquet(pr / "exclusions_state_terminations_ga.parquet", index=False)
    pd.DataFrame({"npi": [IND_HARD], "entity_name": ["DOE, JANE"], "excl_type": ["1128a1|excl_name_state_unique"],
                  "excl_date": ["2023-03-15"]}).to_parquet(pr / "exclusions_name_resolved.parquet", index=False)
    pd.DataFrame({"npi": [IND_EMPLOYER], "entity_name": ["ACME HOME HEALTH LLC"],
                  "excl_type": ["affiliated:1128a1|research_employer"], "excl_date": ["2023-02-01"]}) \
        .to_parquet(pr / "exclusions_employer_resolved.parquet", index=False)
    pd.DataFrame({"npi": [IND_HARD], "entity_name": ["DOE, JANE"], "excl_type": ["1128a1"], "excl_date": ["2023-03-15"]}) \
        .to_parquet(pr / "exclusions_duplicates.parquet", index=False)
    pd.DataFrame({"npi": [BILLER_A], "entity_name": ["X"], "excl_type": ["field:over_capacity"], "excl_date": ["2026-01-01"]}) \
        .to_parquet(pr / "exclusions_field.parquet", index=False)
    pd.DataFrame(columns=["npi", "entity_name", "excl_type", "excl_date"]) \
        .to_parquet(pr / "exclusions_medicaid_enrollment.parquet", index=False)
    return pr


def _case_db():
    return pd.DataFrame([
        {"case_id": "https://ag.tx.gov/mfcu/x#SMITH", "announced_date": "2023-09-01", "defendant_name": "John Smith",
         "jurisdiction": "TX MFCU", "summary": "John Smith, a personal care attendant, agreed to pay $40,000 to settle "
                                               "Medicaid fraud allegations.", "case_type": "", "usable": "1"},
        {"case_id": "https://ag.tx.gov/mfcu/y#AUDIT", "announced_date": "2023-09-01", "defendant_name": "Audit Person",
         "jurisdiction": "TX audit (OIG)", "summary": "repayment ordered after fraud audit, settlement", "case_type": ""},
        {"case_id": "https://www.justice.gov/usao/pr/z", "announced_date": "2023-09-01", "defendant_name": "Fed Person",
         "jurisdiction": "S.D. Tex.", "summary": "pleaded guilty to health care fraud", "case_type": "criminal_hcf"},
        {"case_id": "https://ag.tx.gov/mfcu/q#QUAR", "announced_date": "2023-09-01", "defendant_name": "Quarantined",
         "jurisdiction": "TX MFCU", "summary": "agreed to pay to settle fraud", "case_type": "", "usable": "0"},
    ])


def _defendant_npis():
    return pd.DataFrame([
        {"case_id": "https://ag.tx.gov/mfcu/x#SMITH", "defendant": "John Smith", "kind": "person", "state": "TX",
         "npi": IND_MFCU, "status": "resolved", "match_level": "name_state"},
        {"case_id": "https://ag.tx.gov/mfcu/y#AUDIT", "defendant": "Audit Person", "kind": "person", "state": "TX",
         "npi": OTHER, "status": "resolved", "match_level": "name_state"},
        {"case_id": "https://www.justice.gov/usao/pr/z", "defendant": "Fed Person", "kind": "person", "state": "TX",
         "npi": OTHER, "status": "resolved", "match_level": "name_state"},
        {"case_id": "https://ag.tx.gov/mfcu/q#QUAR", "defendant": "Quarantined", "kind": "person", "state": "TX",
         "npi": OTHER, "status": "resolved", "match_level": "name_state"},
        {"case_id": "https://ag.tx.gov/mfcu/x#SMITH", "defendant": "Smith Clinic", "kind": "org", "state": "TX",
         "npi": BILLER_D, "status": "resolved", "match_level": "name_state"},
    ])


def test_discover_and_load_exclusion_files_skip_the_quarantine_and_field_files(tmp_path):
    from src.model_a.label_expansion import discover_exclusion_files, load_exclusion_sources
    pr = _processed(tmp_path)
    files = discover_exclusion_files(pr)
    stems = [f.stem for f in files]
    assert "exclusions_duplicates" not in stems and "exclusions_field" not in stems
    assert {"exclusions_state_terminations_ga", "exclusions_name_resolved", "exclusions_employer_resolved",
            "exclusions_medicaid_enrollment"} <= set(stems)
    ex = load_exclusion_sources(files)
    assert set(ex["source"]) == {"exclusions_state_terminations_ga", "exclusions_name_resolved", "exclusions_employer_resolved"}
    assert len(ex) == 4


def test_state_case_individuals_mfcu_rows_only_with_the_outcome_tier():
    from src.model_a.label_expansion import state_case_individuals
    from src.model_a.fca_relevance import fca_tier
    sc = state_case_individuals(_case_db(), _defendant_npis())
    # the MFCU person: in; the audit row, the federal row, the quarantined row and the org: out
    assert sc["npi"].tolist() == [IND_MFCU]
    r = sc.iloc[0]
    assert r["excl_date"] == "2023-09-01" and r["source"] == "case_db" and r["entity_name"] == "John Smith"
    assert r["excl_type"].startswith("mfcu_tx:settlement:")
    assert fca_tier(r["excl_type"])[0] == "hard"                    # a settlement reads hard through the research path
    assert fca_tier("affiliated:" + r["excl_type"])[0] == "hard"    # and so does its employer row
    # a charge with no outcome stays an allegation: weak, carried, never promoted
    cdb = _case_db(); cdb.loc[0, "summary"] = "John Smith was charged with Medicaid fraud."
    sc2 = state_case_individuals(cdb, _defendant_npis())
    assert sc2.iloc[0]["excl_type"].startswith("mfcu_tx:charged") and fca_tier(sc2.iloc[0]["excl_type"])[0] == "weak"


def test_affiliated_from_exclusions_source_tier_date_share_and_funnel(tmp_path):
    from src.model_a.label_expansion import (affiliated_from_exclusions, affiliated_funnel_line, discover_exclusion_files,
                                             load_exclusion_sources, state_case_individuals, AFF_STATE_COLS)
    spend = _spend(tmp_path)
    pr = _processed(tmp_path)
    ex = pd.concat([load_exclusion_sources(discover_exclusion_files(pr)),
                    state_case_individuals(_case_db(), _defendant_npis())], ignore_index=True, sort=False)
    aff, fun = affiliated_from_exclusions(ex, spend, window_months=24)
    assert list(aff.columns) == AFF_STATE_COLS
    a = aff.set_index("npi")
    # the hard LEIE individual -> biller A, dated by the exclusion, share 0.75, hard
    assert a.loc[BILLER_A, "excluded_npi"] == IND_HARD and a.loc[BILLER_A, "excl_date"] == "2023-03-15"
    assert abs(a.loc[BILLER_A, "share_of_biller"] - 0.75) < 0.01 and bool(a.loc[BILLER_A, "kept"])
    assert a.loc[BILLER_A, "source_tier"] == "hard" and a.loc[BILLER_A, "source"] == "exclusions_name_resolved"
    assert a.loc[BILLER_A, "excl_type"] == "1128a1|excl_name_state_unique"
    # the WEAK state termination -> biller B, kept with its tier carried (the ruler decides)
    assert a.loc[BILLER_B, "excluded_npi"] == IND_WEAK and a.loc[BILLER_B, "excl_date"] == "2023-06-01"
    assert abs(a.loc[BILLER_B, "share_of_biller"] - 0.60) < 0.01 and bool(a.loc[BILLER_B, "kept"])
    assert a.loc[BILLER_B, "source_tier"] == "weak" and a.loc[BILLER_B, "source"] == "exclusions_state_terminations_ga"
    # the MFCU settlement individual -> biller C, dated by the announcement, hard
    assert a.loc[BILLER_C, "excluded_npi"] == IND_MFCU and a.loc[BILLER_C, "excl_date"] == "2023-09-01"
    assert abs(a.loc[BILLER_C, "share_of_biller"] - 0.25) < 0.01 and bool(a.loc[BILLER_C, "kept"])
    assert a.loc[BILLER_C, "source_tier"] == "hard" and a.loc[BILLER_C, "source"] == "case_db"
    assert a.loc[BILLER_C, "excl_type"].startswith("mfcu_tx:settlement")
    # the individual's own number is never a biller row; the employer row and the undated row never enter
    assert IND_HARD not in a.index and set(aff["excluded_npi"]) == {IND_HARD, IND_WEAK, IND_MFCU}
    assert fun["individuals_in"] == 3 and fun["by_tier"] == {"hard": 2, "weak": 1}
    assert fun["by_source"] == {"exclusions_name_resolved": 1, "exclusions_state_terminations_ga": 1, "case_db": 1}
    assert fun["with_rendering_history"] == 3 and fun["kept"] == 3 and fun["kept_billers"] == 3
    assert fun["set_aside"] == {"undated": 1, "employer_row_not_an_individual": 1}
    line = affiliated_funnel_line(fun)
    assert line.startswith("[label_expansion] affiliated_state funnel: individuals_in=3 (hard=2, weak=1; by source: ")
    assert "| with_rendering_history=3 | kept=3 rows (billers=3) | dropped: none | set aside before the join: undated=1, " \
           "employer_row_not_an_individual=1" in line
    # tier='hard' at the join keeps the weak individual out entirely (and says so)
    aff_h, fun_h = affiliated_from_exclusions(ex, spend, window_months=24, tier="hard")
    assert set(aff_h["excluded_npi"]) == {IND_HARD, IND_MFCU} and fun_h["set_aside"]["tier_not_kept"] == 1
    # provider_dim sets an organization aside before the join
    dim = pd.DataFrame({"npi": [IND_MFCU], "entity_type": ["2"]})
    aff_d, fun_d = affiliated_from_exclusions(ex, spend, window_months=24, provider_dim=dim)
    assert IND_MFCU not in set(aff_d["excluded_npi"]) and fun_d["set_aside"]["not_an_individual"] == 1


def test_chain_guard_lists_a_biller_many_individuals_point_at(tmp_path):
    from src.model_a.label_expansion import affiliated_from_exclusions, affiliated_convictions
    inds = [_npi(f"10000002{i}") for i in range(3)]
    rows = []
    for ind in inds:
        for m in ("2022-01", "2022-06"):
            rows.append((BILLER_CHAIN, ind, "T1019", f"{m}-01", 30000.0))
    spend = _spend(tmp_path, rows)
    ex = pd.DataFrame({"npi": inds, "entity_name": ["A, A", "B, B", "C, C"],
                       "excl_type": ["state_term:GA:convicted of medicaid fraud"] * 3,
                       "excl_date": ["2023-01-01"] * 3, "source": ["exclusions_state_terminations_ga"] * 3})
    aff, fun = affiliated_from_exclusions(ex, spend, max_individuals_per_biller=2)
    chain = aff[aff["npi"] == BILLER_CHAIN]
    assert len(chain) == 3 and not chain["kept"].any() and set(chain["drop_reason"]) == {"chain_guard_biller"}
    assert fun["kept"] == 0 and fun["dropped"] == {"chain_guard_biller": 3}
    # the guard off: all three kept (the pre-2026-11-02 behaviour of the hard-tier step)
    aff2, _ = affiliated_from_exclusions(ex, spend, max_individuals_per_biller=None)
    assert aff2[aff2["npi"] == BILLER_CHAIN]["kept"].all()
    # the legacy function takes the same guard as an opt-in and keeps its schema
    nodes = ex.drop(columns=["source"]).assign(excl_type="1128a1")
    legacy = affiliated_convictions(nodes, spend, max_individuals_per_biller=2)
    assert "source_tier" not in legacy.columns and set(legacy["drop_reason"]) == {"chain_guard_biller"}
    assert affiliated_convictions(nodes, spend)["kept"].all()


def test_merge_affiliated_tier_policy_reads_source_tier_and_the_legacy_file():
    from src.model_a.label_expansion import merge_affiliated, AFF_COLS
    base = {"excl_type": "x", "fca_tier": "hard", "fca_basis": "b", "entity_name": "", "dollars_rendered": 1.0,
            "biller_dollars_window": 2.0, "share_of_biller": 0.5, "months_rendered": 2, "rank_for_individual": 1,
            "basis": "affiliated_conviction", "kept": True, "drop_reason": ""}
    legacy = pd.DataFrame([dict(base, npi=BILLER_A, excluded_npi=IND_HARD, excl_date="2023-03-15", excl_type="1128a1")])[AFF_COLS]
    state = pd.DataFrame([dict(base, npi=BILLER_B, excluded_npi=IND_WEAK, excl_date="2023-06-01",
                               excl_type="state_term:GA:LICENSE REVOKED", fca_tier="weak", source_tier="weak",
                               source="exclusions_state_terminations_ga"),
                          dict(base, npi=BILLER_C, excluded_npi=IND_MFCU, excl_date="2023-09-01",
                               excl_type="mfcu_tx:settlement: agreed to pay", source_tier="hard", source="case_db")])
    aff = pd.concat([legacy, state], ignore_index=True, sort=False)      # the runner's several-files concat
    fl = pd.DataFrame({"npi": [OTHER], "first_excl_date": [""], "excl_type": [""], "entity_name": [""],
                       "is_prospective_positive": [0], "was_excluded_pre_cutoff": [0], "undated_exclusion": [0],
                       "fca_tier": [""], "fca_basis": [""], "label_policy": ["hard"]})
    every = merge_affiliated(fl, aff, "2021-12", horizon_months=36)
    assert set(every[every["is_prospective_positive"] == 1]["npi"]) == {BILLER_A, BILLER_B, BILLER_C}
    hard = merge_affiliated(fl, aff, "2021-12", horizon_months=36, tier="hard")
    pos = hard.set_index("npi")
    assert set(hard[hard["is_prospective_positive"] == 1]["npi"]) == {BILLER_A, BILLER_C}     # the weak employer stays out
    assert BILLER_B not in pos.index
    assert pos.loc[BILLER_C, "excl_type"] == "affiliated:mfcu_tx:settlement: agreed to pay"
    both = merge_affiliated(fl, aff, "2021-12", horizon_months=36, tier="hard+weak")
    assert set(both[both["is_prospective_positive"] == 1]["npi"]) == {BILLER_A, BILLER_B, BILLER_C}


def test_affiliated_state_cli_writes_the_file_and_the_funnel_line(tmp_path):
    spend = _spend(tmp_path)
    pr = _processed(tmp_path)
    _case_db().to_csv(tmp_path / "cases.csv", index=False)
    _defendant_npis().to_parquet(tmp_path / "npis.parquet", index=False)
    out = pr / "affiliated_state.parquet"
    r = subprocess.run([sys.executable, "-m", "src.model_a.label_expansion", "affiliated-state", "--processed", str(pr),
                        "--spending", str(spend), "--case-db", str(tmp_path / "cases.csv"),
                        "--defendant-npis", str(tmp_path / "npis.parquet"), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "affiliated_state funnel: individuals_in=3 (hard=2, weak=1" in r.stdout
    aff = pd.read_parquet(out)
    assert set(aff["npi"]) == {BILLER_A, BILLER_B, BILLER_C} and aff["kept"].all()
    assert (pr / "affiliated_state.md").exists() and "source_tier" in (pr / "affiliated_state.md").read_text()


# ------------------------------------------------------------- docket outcomes

def _dockets():
    return pd.DataFrame([
        {"docket_id": "1", "case_name": "United States ex rel. Doe v. Acme Home Health LLC", "court": "flmd",
         "nature_of_suit": "376 Qui Tam", "date_filed": "2021-03-04", "date_terminated": "", "docket_url": "/docket/1/acme/"},
        {"docket_id": "2", "case_name": "United States v. Delta Labs Inc", "court": "gand",
         "nature_of_suit": "375 False Claims Act", "date_filed": "2019-07-01", "date_terminated": "2022-01-01", "docket_url": "/docket/2/delta/"},
        {"docket_id": "3", "case_name": "United States ex rel. Roe v. Gamma Clinic PA", "court": "txsd",
         "nature_of_suit": "376", "date_filed": "2020-01-01", "date_terminated": "2023-05-05", "docket_url": "/docket/3/gamma/"},
        {"docket_id": "4", "case_name": "United States ex rel. Poe v. Omega Pharmacy Inc", "court": "njd",
         "nature_of_suit": "376", "date_filed": "2020-01-01", "date_terminated": "", "docket_url": "/docket/4/omega/"},
    ])


def _entries():
    return pd.DataFrame([
        # docket 1: intervened for settlement, then a stipulation of dismissal pursuant to the settlement
        {"docket": "https://www.courtlistener.com/api/rest/v4/dockets/1/", "entry_number": "40", "date_filed": "2023-11-02",
         "description": "NOTICE of Election to Intervene for Purposes of Settlement by United States of America"},
        {"docket": "https://www.courtlistener.com/api/rest/v4/dockets/1/", "entry_number": "41", "date_filed": "2023-11-20",
         "description": "JOINT STIPULATION of Dismissal pursuant to the Settlement Agreement"},
        {"docket": "https://www.courtlistener.com/api/rest/v4/dockets/1/", "entry_number": "39", "date_filed": "2023-10-01",
         "description": "MOTION to approve settlement (pending)"},
        # docket 2: a default judgment with a stated amount
        {"docket": "2", "entry_number": "12", "date_filed": "2022-01-01",
         "description": "DEFAULT JUDGMENT against Delta Labs Inc in the amount of $1,200,000 in favor of the United States"},
        # docket 3: declined, plain dismissal: NOT an outcome
        {"docket": "3", "entry_number": "5", "date_filed": "2021-02-02", "description": "NOTICE of Election to Decline Intervention"},
        {"docket": "3", "entry_number": "9", "date_filed": "2023-05-05", "description": "ORDER of Dismissal without prejudice"},
        {"docket": "3", "entry_number": "8", "date_filed": "2023-04-01", "description": "MOTION to dismiss by Gamma Clinic PA"},
        # docket 4: summary judgment and a judgment for the defendant: NOT an outcome
        {"docket": "4", "entry_number": "30", "date_filed": "2024-01-01", "description": "ORDER granting motion for summary judgment"},
        {"docket": "4", "entry_number": "31", "date_filed": "2024-01-02", "description": "JUDGMENT in favor of defendant Omega Pharmacy Inc"},
    ])


def test_entry_outcome_vocabulary():
    from src.enforcement.docket_backfill import entry_outcome
    assert entry_outcome("NOTICE of settlement") == "settlement"
    assert entry_outcome("STIPULATION of dismissal pursuant to settlement") == "settlement"
    assert entry_outcome("CONSENT JUDGMENT entered") == "settlement"
    assert entry_outcome("ORDER approving the settlement agreement") == "settlement"
    assert entry_outcome("JUDGMENT entered in favor of plaintiff United States") == "judgment"
    assert entry_outcome("DEFAULT JUDGMENT against Acme") == "judgment"
    assert entry_outcome("Satisfaction of judgment filed") == "judgment"
    for plain in ("ORDER of dismissal", "STIPULATION of dismissal", "JUDGMENT of dismissal", "MOTION to approve settlement",
                  "MOTION for summary judgment", "ORDER granting motion for summary judgment", "JUDGMENT in favor of defendant",
                  "ORDER vacating settlement", "ORDER denying motion to enforce settlement", "BRIEF re settlement", ""):
        assert entry_outcome(plain) == "", plain


def test_docket_outcome_rows_settlement_yields_a_row_and_a_dismissal_does_not():
    from src.enforcement.docket_backfill import docket_outcome_rows, merge_into_case_db, dockets_to_cases, DOCKET_OUTCOME_CASE_TYPE
    from src.model_a.fca_label_export import _CIVIL_OUTCOME
    out = docket_outcome_rows(_dockets(), _entries())
    assert set(out["docket_id"]) == {"1", "2"}                       # 3 (plain dismissal) and 4 (loss) never
    acme = out[out["docket_id"] == "1"].iloc[0]
    assert acme["case_type"] == DOCKET_OUTCOME_CASE_TYPE and acme["defendant_name"] == "Acme Home Health LLC"
    assert acme["announced_date"] == "2023-11-02" and int(acme["entry_number"]) == 40       # the FIRST outcome entry dates it
    assert acme["outcome_kind"] == "settlement" and acme["qui_tam"] == 1 and acme["intervened"] == 1
    assert acme["jurisdiction"] == "M.D. Fla." and acme["case_id"] == "/docket/1/acme/entry-40/"
    assert _CIVIL_OUTCOME.search(acme["summary"]) and "docket 1" in acme["summary"]
    delta = out[out["docket_id"] == "2"].iloc[0]
    assert delta["outcome_kind"] == "judgment" and abs(float(delta["amount_usd"]) - 1_200_000) < 1
    assert pd.isna(delta["qui_tam"]) and pd.isna(delta["intervened"]) and delta["announced_date"] == "2022-01-01"
    # the outcome row sits BESIDE the allegation row (its own key), never replacing it
    allegations = dockets_to_cases(_dockets())
    merged = merge_into_case_db(out, _tmp_csv(allegations))
    assert len(merged) == 6 and (merged["case_type"] == DOCKET_OUTCOME_CASE_TYPE).sum() == 2
    assert (merged["case_type"].str.startswith("docket_qui_tam") | merged["case_type"].str.startswith("docket_fca")).sum() == 4
    # without a docket listing the entries must carry the caption
    bare = _entries().assign(case_name="United States ex rel. Doe v. Acme Home Health LLC", court="flmd")
    assert len(docket_outcome_rows(None, bare)) == 2
    assert len(docket_outcome_rows(_dockets(), _entries().iloc[0:0])) == 0


def _tmp_csv(df):
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "cases.csv"
    df.to_csv(p, index=False)
    return p


def test_docket_outcome_rows_are_read_as_outcomes_and_allegation_dockets_are_not():
    """The integration (2026-11-02): fca_label_export.outcome_tier reads docket_outcome on
    the civil path, case_labels files it as its own source (ranked under doj_case, above
    charged_case) and prospective_label admits it to the ruler; a docket_fca /
    docket_qui_tam row stays an allegation."""
    from src.enforcement.docket_backfill import docket_outcome_rows
    from src.model_a.fca_label_export import outcome_tier, _CIVIL_OUTCOME
    out = docket_outcome_rows(_dockets(), _entries())
    r = out.iloc[0]
    assert _CIVIL_OUTCOME.search(r["summary"])
    assert outcome_tier(r["case_type"], r["summary"], r["amount_usd"])[0] == "fca_civil"
    assert outcome_tier("docket_qui_tam", r["summary"], r["amount_usd"])[0] == ""
    assert outcome_tier("docket_outcome", "complaint filed; motion to dismiss pending", None)[0] == ""


def test_docket_outcomes_cli(tmp_path):
    _entries().to_csv(tmp_path / "entries.csv", index=False)
    _dockets().to_csv(tmp_path / "dockets.csv", index=False)
    out = tmp_path / "docket_outcomes.csv"
    r = subprocess.run([sys.executable, "-m", "src.enforcement.docket_backfill", "--outcomes-from", str(tmp_path / "entries.csv"),
                        "--dockets", str(tmp_path / "dockets.csv"), "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "docket outcomes: 2 dockets with an outcome entry of 4 dockets" in r.stdout
    df = pd.read_csv(out, dtype=str)
    assert len(df) == 2 and set(df["case_type"]) == {"docket_outcome"} and set(df["outcome_kind"]) == {"settlement", "judgment"}


def test_fixture_npis_are_luhn_valid():
    assert all(is_valid_npi(n) for n in (IND_HARD, IND_WEAK, IND_MFCU, BILLER_A, BILLER_B, BILLER_C, BILLER_CHAIN, OTHER))
