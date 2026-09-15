"""test_label_audit.py — the label audit: per-source identifier/date/tier/reach
table, matrix join checks, case-DB recall, CIA recall, and the findings list."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.enforcement.cia import cia_case_rows, normalize_cia
from src.model_a.label_audit import (audit_case_db, audit_cia, audit_exclusions,
                                     audit_matrix, findings, label_source, run,
                                     to_markdown)

# valid Luhn NPIs (from the synthetic fixture family)
NPI_A, NPI_B, NPI_C = "1003000209", "1003000415", "1003000126"
BAD_LUHN = "1003000210"


def _nodes():
    return pd.DataFrame({
        "npi": [NPI_A, NPI_B, "", BAD_LUHN, NPI_C, NPI_A, NPI_C],
        "entity_name": ["DOE, JOHN", "ROE, JANE", "BADCO HOLDINGS", "X", "LEE, KIM",
                        "SMITH, PAT", "LEE, KIM"],
        "name_key": ["doe john", "roe jane", "badco holdings", "x", "lee kim",
                     "smith pat", "lee kim"],
        "excl_type": ["1128a1", "1128b14", "1128b8", "1128a1",
                      "medicare_revocation: 424.535(a)(3) felonies",
                      "1128zz9 mystery statute", "sam: Debarment"],
        "excl_date": ["2022-03-01", "2020-01-01", "2021-06-01", "2019-01-01",
                      "2023-05-01", "2099-01-01", None],
        "currently_active": [1, 0, 1, 1, 1, 1, 1],
    })


def test_label_source_detection():
    assert label_source("1128a1") == "leie"
    assert label_source("medicare_revocation: felony") == "medicare_revocation"
    assert label_source("sam: Debarment") == "sam"
    assert label_source("opensanctions:us_ofac") == "opensanctions"
    assert label_source("state_board:TX:revoked") == "state_board"
    assert label_source("doj_case") == "doj_case"
    assert label_source("") == "(blank excl_type)"
    assert label_source("weird feed").startswith("other:")


def test_audit_exclusions_per_source_and_integrity():
    ex = audit_exclusions(_nodes(), cutoff="2021-12", universe={NPI_A, NPI_C},
                          billing_npis={NPI_A})
    ps = ex["per_source"].set_index("source")
    assert set(ps.index) == {"leie", "medicare_revocation", "sam"}
    leie = ps.loc["leie"]
    assert leie["rows"] == 5 and leie["name_only_rows"] == 1
    assert leie["luhn_fail_rows"] == 1 and leie["bad_npi_rows"] == 1
    assert leie["future_dated"] == 1 and leie["unmapped_rows"] == 1
    assert leie["hard"] == 2 and leie["excluded"] == 1     # 1128a1 x2 (one bad NPI), b14
    assert leie["in_universe"] == 1 and leie["billing_seen"] == 1
    assert leie["post_cutoff_rows"] == 2                    # 2022-03 + 2099
    assert ps.loc["sam", "dated_share"] == 0.0
    assert ex["unmapped"].iloc[0]["excl_type"].startswith("1128zz9")
    # NPI_A carries two names (doe john / smith pat) -> identity collision
    assert list(ex["collisions"]["npi"]) == [NPI_A]
    t = ex["totals"]
    assert t["rows"] == 7 and t["distinct_valid_npi"] == 3
    assert t["future_dated"] == 1 and t["undated_rows"] == 1 and t["hard_npi"] == 2


def _matrix():
    return pd.DataFrame({
        "npi": [NPI_A, NPI_B, NPI_C, "1003000308", "1003000407"],
        "net_paid": [1000.0, 0.0, 500.0, 200.0, 50.0],
        "last_service_month": ["2019-01", "2019-06", "2023-08", "2021-12", "2021-11"],
        "provider_on_exclusion": [1, 1, 1, 1, 0],
        "exclusion_label_sources": ["leie", "leie", "medicare_revocation;doj_case", "", ""],
        "label_basis": ["", "", "org_billing", "", ""],
        "case_ids": ["", "", "c1;c2", "", ""],
        "fraud_scheme": ["", "", "kickback", "", ""],
        "conduct_start": [None, None, 2019, None, None],
        "conduct_end": [None, None, 2021, None, None],
    })


def test_audit_matrix_join_checks_and_lag():
    mx = audit_matrix(_matrix(), _nodes(), cutoff=None)
    assert mx["positives"] == 4
    assert mx["by_source"] == {"leie": 2, "medicare_revocation": 1, "doj_case": 1}
    assert mx["no_source_tag"] == 1
    assert mx["doj_by_basis"] == {"org_billing": 1}
    assert mx["doj_conduct_start_missing_share"] == 0.0
    assert mx["positives_zero_billing"] == 1
    # 1003000308 is flagged but in no source and has no DOJ tag -> unexplained
    assert mx["unexplained_positives"] == 1 and mx["unexplained_sample"] == ["1003000308"]
    assert mx["missed_positives"] == 0
    # NPI_A: last bill 2019-01, first exclusion 2022-03 -> 38 months (> 24);
    # NPI_B: 2019-06 -> 2020-01 = 7; NPI_C: 2023-08 vs 2023-05 = -3 (billed after)
    assert mx["lag_n"] == 3 and mx["lag_over_24m_share"] == pytest.approx(1 / 3)
    assert mx["billed_after_exclusion"] == 1                # NPI_C billed after 2023-05


def test_audit_matrix_frozen_cutoff_only_counts_known_exclusions():
    mx = audit_matrix(_matrix(), _nodes(), cutoff="2021-12")
    # only NPI_B (2020-01) is known at the cutoff; NPI_A's 2022 exclusion is
    # not, so A becomes unexplained alongside 1003000308
    assert mx["excluded_in_matrix"] == 1
    assert mx["unexplained_positives"] == 2


def _case_db():
    return pd.DataFrame({
        "case_id": ["c1", "c2", "c3", "c3"],
        "defendant_name": ["Acme Health Services LLC", "Beta Hospice Inc",
                           "Gamma Labs", "Gamma Labs"],
        "defendant_name_key": ["acme health services", "beta hospice", "gamma labs",
                               "gamma labs"],
        "announced_date": ["2022-01-05", "2021-07-01", "2020-03-03", "2020-03-03"],
        "amount_usd": ["1000000", "5000000", "20000000", "20000000"],
        "scheme": ["kickback", "unknown", "", ""],
        "sector": ["home_health", "hospice", "lab", "lab"],
        "qui_tam": ["1", None, "1", "1"],
        "intervened": [None, "1", None, None],
        "source_url": ["https://www.justice.gov/x", "https://news.example.com/y",
                       "https://oig.hhs.gov/z", "https://oig.hhs.gov/z"],
        "npi_in_source": ["", NPI_C, "", ""],
        "usable": ["1", "1", "1", "0"],
    })


def test_audit_case_db_precision_and_recall():
    c = audit_case_db(_case_db(), _matrix())
    assert c["rows"] == 4 and c["usable"] == 3
    assert c["with_npi"] == 1
    assert c["amount_total_usd"] == 26_000_000.0
    assert c["dup_case_id"] == 0
    assert c["scheme_known_share"] == pytest.approx(1 / 3)
    assert c["primary_source_share"] == pytest.approx(2 / 3)
    assert c["resolved_cases"] == 2 and c["resolved_share"] == pytest.approx(2 / 3)
    assert c["unresolved_dollars"] == 20_000_000.0
    assert c["top_unresolved"][0]["case_id"] == "c3"


def test_cia_normalize_and_case_rows_and_recall():
    raw = pd.DataFrame({
        "Party": ["Acme Health Services, LLC", "Delta Nursing Homes", " "],
        "State": ["fl", "TX", ""],
        "Effective Date": ["2021-05-04", "2019-09-01", ""],
        "Status": ["Active", "Closed", ""],
        "Link": ["https://oig.hhs.gov/a", "https://oig.hhs.gov/d", ""],
    })
    cn = normalize_cia(raw)
    assert len(cn) == 2 and list(cn["cia_status"]) == ["active", "closed"]
    assert cn.iloc[0]["state"] == "FL"
    rows = cia_case_rows(cn)
    assert list(rows["intervened"]) == [1, 1] and rows.iloc[0]["case_id"].startswith("cia:")
    org_nodes = pd.DataFrame({"org_node_id": ["org:1"], "name_key": ["delta nursing homes"]})
    r = audit_cia(cn, _case_db(), org_nodes)
    assert r["parties"] == 2
    assert r["case_db_exact"] + r["case_db_fuzzy"] == 1        # Acme matches, Delta doesn't
    assert r["case_db_recall"] == 0.5
    assert r["case_db_unmatched"][0]["entity_name"] == "Delta Nursing Homes"
    assert r["org_resolvable_share"] == 0.5
    r2 = audit_cia(cn, _case_db(), None, year_min=2021)
    assert r2["parties_in_window"] == 1 and r2["case_db_recall"] == 1.0


def test_findings_flag_the_real_defects_with_fixes():
    ex = audit_exclusions(_nodes(), cutoff="2021-12", universe={NPI_A, NPI_C},
                          billing_npis={NPI_A})
    mx = audit_matrix(_matrix(), _nodes(), None)
    cdb = audit_case_db(_case_db(), _matrix())
    f = findings(ex, mx, cdb, None, "2021-12")
    text = " ".join(x["finding"] for x in f)
    assert "fail the Luhn check" in text
    assert "dated in the future" in text
    assert "tier table does not know" in text
    assert "more than one distinct name" in text
    assert "in NO exclusion source" in text
    assert all(x["fix"] for x in f)
    assert f[0]["severity"] == "high" or any(x["severity"] == "high" for x in f)


def test_run_end_to_end_writes_markdown_and_json(tmp_path: Path):
    _nodes().to_parquet(tmp_path / "exclusion_nodes.parquet", index=False)
    _matrix().to_parquet(tmp_path / "provider_features_for_model.parquet", index=False)
    _case_db().to_csv(tmp_path / "doj_cases.csv", index=False)
    pd.DataFrame({"Party": ["Acme Health Services LLC"], "Effective Date": ["2021-01-01"],
                  "Status": ["Active"]}).to_csv(tmp_path / "cia.csv", index=False)
    md, payload = run(tmp_path / "exclusion_nodes.parquet",
                      tmp_path / "provider_features_for_model.parquet",
                      tmp_path / "doj_cases.csv", tmp_path / "cia.csv", None, "2021-12")
    assert md.startswith("# LABEL AUDIT")
    for section in ("## A. Exclusion-style sources", "## B. The matrix label",
                    "## C. DOJ case DB", "## D. OIG CIA list recall",
                    "## Findings and fixes", "never accusations"):
        assert section in md
    assert "| leie |" in md and "1128zz9" in md
    assert payload["case_db"]["resolved_cases"] == 2
    assert payload["cia"]["case_db_recall"] == 1.0
    assert isinstance(payload["exclusions"]["per_source"], list)
    # a report on exclusions alone still renders
    md2 = to_markdown(audit_exclusions(_nodes()), None, None, None,
                      findings(audit_exclusions(_nodes()), None, None, None, None),
                      {"exclusion_nodes": "x.parquet"}, None)
    assert "## A. Exclusion-style sources" in md2 and "## B." not in md2
