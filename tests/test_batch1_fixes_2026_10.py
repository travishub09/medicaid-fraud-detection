"""PLATFORM_AUDIT batch 1 (2026-10-21): the twelve fixes before any run, each pinned."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.entity_graph.resolve_entities import norm_org_name
from src.model_a.case_labels import build_case_labels
from src.model_a.fca_relevance import fca_tier
from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS
from src.model_a.temporal_sources import asof_filter, real_dates
from src.ingest_cms import hcris, nadac, order_referring, partb, facility, openpayments
from src.enforcement.research_evidence import load_state_exclusions
from tests.test_audit_fixes_2026_10 import RING_NPI, _graph


def test_h2_case_label_freezes_at_month_start(tmp_path):
    """A case announced INSIDE the cutoff month is not a training positive (month START,
    like every other freeze); announced the month before, it is."""
    _, org_nodes, npi_to_org = _graph(tmp_path)
    org_id = npi_to_org.set_index("npi").loc[RING_NPI, "org_node_id"]
    org_name = org_nodes.set_index("org_node_id").loc[org_id, "org_name"]
    def db(ann):
        return pd.DataFrame([{
            "case_id": "C1", "announced_date": ann, "defendant_name": org_name,
            "defendant_name_key": norm_org_name(org_name), "sector": "home_health",
            "scheme": "upcoding", "amount_usd": 5_000_000, "jurisdiction": "", "source_url": "",
            "summary": "submitted upcoded claims from 2017 through 2020; settled."}])
    assert len(build_case_labels(db("2021-12-15"), org_nodes, npi_to_org, asof_cutoff="2021-12")) == 0
    assert len(build_case_labels(db("2021-11-30"), org_nodes, npi_to_org, asof_cutoff="2021-12")) > 0


def test_h6_state_rows_tier_under_the_state_term_ruler():
    table = pd.DataFrame([{"npi": "1234567893", "name": "ACME CLINIC", "excl_type": "Medicaid fraud conviction",
                           "excl_date": "2023-01-05"},
                          {"npi": "1245319599", "name": "B CLINIC", "excl_type": "license revoked", "excl_date": "2023-02-01"}])
    ex, _ = load_state_exclusions(table, "ga")
    assert ex["excl_type"].iloc[0].startswith("state_term:GA:")
    assert fca_tier(ex["excl_type"].iloc[0])[0] == "hard"
    assert fca_tier(ex["excl_type"].iloc[1])[0] == "weak"
    ex2, _ = load_state_exclusions(table, "ga", source="mfcu")
    assert ex2["excl_type"].iloc[0].startswith("mfcu_ga:")


def test_cf10_charge_rule_is_anchored_and_outcomes_win():
    assert fca_tier("state_term:FL:convicted of medicaid fraud; indictment 2019")[0] == "hard"
    assert fca_tier("state_term:FL:discharged from medicaid program")[0] != "charged"
    assert fca_tier("state_term:FL:indicted for health care fraud")[0] == "charged"
    assert fca_tier("state_term:FL:credible allegation of fraud payment suspension")[0] == "weak"


def test_h4_order_referring_refuses_a_file_without_the_dme_column():
    raw = pd.DataFrame({"NPI": ["1234567893"], "PARTB": ["Y"], "HHA": ["Y"], "PMD": ["N"]})
    with pytest.raises(ValueError, match="DME"):
        order_referring.eligible_referrers(raw)
    ok, _ = order_referring.eligible_referrers(raw.assign(DME=["N"]))
    assert ok["dme"].tolist() == [0]


def test_silent_blanks_are_null_or_raise():
    # Part B without the allowed amount: allowed_per_bene is NaN, never 0
    raw = pd.DataFrame({"Rndrng_NPI": ["1234567893"] * 2, "HCPCS_Cd": ["99213", "99214"],
                        "Tot_Srvcs": ["10", "5"], "Tot_Benes": ["8", "4"]})
    m, _ = partb.compute_partb_metrics(raw)
    assert np.isnan(m["allowed_per_bene"].iloc[0])
    # HCRIS without an admin-cost column: the share is NaN for every facility
    h = pd.DataFrame({"PRVDR_NUM": ["010001"], "TOTAL_COSTS": ["1000"], "TOTAL_CHARGES": ["2000"], "STATE": ["AL"]})
    hm, _ = hcris.compute_hcris_metrics(h)
    assert "admin_cost_share" not in hm.columns or hm["admin_cost_share"].isna().all()
    # PBJ without LPN / CNA hours refuses
    p = pd.DataFrame({"PROVNUM": ["010001"], "STATE": ["AL"], "WorkDate": ["20211001"], "MDScensus": ["50"], "Hrs_RN": ["8"]})
    with pytest.raises(ValueError, match="hrs_lpn"):
        facility.compute_pbj_metrics(p)
    # Open Payments without product columns refuses instead of scoring 0 for everyone
    op = pd.DataFrame({"Covered_Recipient_NPI": ["1234567893"],
                       "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": ["ACME"],
                       "Total_Amount_of_Payment_USDollars": ["100"]})
    pdraw = pd.DataFrame({"Prscrbr_NPI": ["1234567893"], "Brnd_Name": ["X"], "Gnrc_Name": ["x"], "Tot_Clms": ["1"], "Tot_Drug_Cst": ["10"]})
    with pytest.raises(ValueError, match="product"):
        openpayments.kickback_co_occurrence(op, pdraw)


def test_h8_reserved_inputs_are_out_of_the_weights():
    flat = {k for w in DEFAULT_SCHEME_WEIGHTS.values() for k in w}
    assert not {"bene_per_day_p95", "dme_ordering_md_concentration", "local_volume_implausibility"} & flat


def test_d6_placeholder_dates_are_unknown():
    s = pd.Series(["2020-05-01", "0001-01-01", "1800-01-01", "2099-12-31", "9999-12-31", None])
    r = real_dates(s)
    assert r.notna().tolist() == [True, False, False, False, False, False]
    edges = pd.DataFrame({"src": list("abcd"), "association_date": ["2020-05-01", "2099-12-31", "2023-01-01", "1800-01-01"]})
    kept = asof_filter(edges, "2021-12-01", "association_date", keep_undated=True)
    assert sorted(kept["src"]) == ["a", "b", "d"]          # 2099 and 1800 = undated = kept; 2023 dropped
    strict = asof_filter(edges, "2021-12-01", "association_date")
    assert sorted(strict["src"]) == ["a"]


def test_nadac_rows_on_or_before_the_freeze():
    raw = pd.DataFrame({"NDC": ["1", "2", "3"], "NADAC Per Unit": ["1", "2", "3"],
                        "Effective_Date": ["2021-06-01", "2021-12-15", "2022-01-01"]})
    assert nadac.asof_rows(raw, "2021-12")["NDC"].tolist() == ["1", "2"]
    assert len(nadac.asof_rows(raw, None)) == 3
    assert len(nadac.asof_rows(raw.drop(columns=["Effective_Date"]), "2021-12")) == 3


def test_d3_docgraph_edition():
    from src.entity_graph.__main__ import docgraph_edition
    assert docgraph_edition(Path("DocGraph_Hop_Teaming_2022.csv")) == "2022-12"
    assert docgraph_edition(Path("physician_shared_patient_2015_180.csv")) == "2015-12"
    assert docgraph_edition(Path("docgraph.csv")) is None
    assert docgraph_edition(None) is None


def test_h12_no_data_files_tracked():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1]).stdout
    tracked = [l for l in out.splitlines() if l.endswith((".csv", ".parquet", ".xlsx"))]
    assert tracked == [], tracked
