"""
test_openpayments_validation.py — the Open Payments adapter + kickback
co-occurrence, and the temporal-holdout validation harness.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.ingest_cms.openpayments import (
    compute_openpayments_metrics, kickback_co_occurrence,
)
from src.model_a.validation import (
    temporal_holdout_precision_at_k, outcomes_from_case_db,
)
from src.enforcement import parse_press_release, build_case_db
from src.entity_graph.__main__ import run as run_graph
from tests.fixtures.synthetic import build_synthetic_inputs


def _op_frame() -> pd.DataFrame:
    # real Open Payments headers; the paid prescriber gets concentrated dollars
    # from one manufacturer tied to one product; plus a bad-NPI row
    return pd.DataFrame([
        {"Covered_Recipient_NPI": "1003000415",
         "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": "PHARMA ONE",
         "Total_Amount_of_Payment_USDollars": "45000",
         "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1": "ELIQUIS"},
        {"Covered_Recipient_NPI": "1003000415",
         "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": "PHARMA TWO",
         "Total_Amount_of_Payment_USDollars": "5000",
         "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1": "OTHERDRUG"},
        {"Covered_Recipient_NPI": "bad-npi",
         "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": "PHARMA ONE",
         "Total_Amount_of_Payment_USDollars": "100",
         "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1": "ELIQUIS"},
    ])


def _partd_frame() -> pd.DataFrame:
    return pd.DataFrame([
        # the paid prescriber's cost is dominated by the paid product
        {"Prscrbr_NPI": "1003000415", "Brnd_Name": "ELIQUIS",
         "Gnrc_Name": "APIXABAN", "Tot_Clms": "100", "Tot_Drug_Cst": "90000"},
        {"Prscrbr_NPI": "1003000415", "Brnd_Name": "METFORMIN",
         "Gnrc_Name": "METFORMIN", "Tot_Clms": "50", "Tot_Drug_Cst": "10000"},
        # an unpaid prescriber of the same drug → zero co-occurrence
        {"Prscrbr_NPI": "1003000407", "Brnd_Name": "ELIQUIS",
         "Gnrc_Name": "APIXABAN", "Tot_Clms": "80", "Tot_Drug_Cst": "70000"},
    ])


def test_openpayments_metrics_and_pays_edges():
    metrics, pays, quarantined = compute_openpayments_metrics(_op_frame())
    assert quarantined == 1
    m = metrics.set_index("npi")
    assert m.loc["1003000415", "op_total_dollars"] == 50_000.0
    assert m.loc["1003000415", "op_payment_concentration"] == 0.9   # 45k/50k
    assert m.loc["1003000415", "n_manufacturers"] == 2
    assert set(pays["edge_type"]) == {"pays"}
    assert "manufacturer:PHARMA ONE" in set(pays["src_id"])
    assert "provider:1003000415" in set(pays["dst_id"])


def test_kickback_co_occurrence():
    co = kickback_co_occurrence(_op_frame(), _partd_frame()).set_index("npi")
    # paid prescriber: 90k of 100k cost sits on the paid product
    assert co.loc["1003000415", "op_payment_utilization_corr"] == pytest.approx(0.9)
    # unpaid prescriber of the same drug: no payments → no kickback exposure
    assert co.loc["1003000407", "op_payment_utilization_corr"] == 0.0


def test_temporal_holdout_lift():
    # 100 orgs; orgs 0–9 are post-cut positives; the score ranks them on top,
    # the size baseline is anti-correlated with the truth
    scores = pd.DataFrame({
        "org_node_id": [f"org:{i}" for i in range(100)],
        "adjusted_prob": [1.0 - i / 100 for i in range(100)],
        "size_baseline": list(range(100)),
    })
    outcomes = pd.DataFrame({
        "org_node_id": [f"org:{i}" for i in range(10)],
        "outcome_date": ["2025-06-01"] * 10,
    })
    r = temporal_holdout_precision_at_k(scores, outcomes, cut_date="2024-12-31",
                                        k=10, baseline_col="size_baseline")
    assert r["base_rate"] == 0.1
    assert r["precision_at_k"] == 1.0
    assert r["lift"] == 10.0
    assert r["beats_baseline"] is True


def test_pre_cut_outcomes_are_excluded():
    scores = pd.DataFrame({"org_node_id": ["org:a", "org:b", "org:c"],
                           "adjusted_prob": [0.9, 0.5, 0.1]})
    outcomes = pd.DataFrame({
        "org_node_id": ["org:a", "org:b"],
        "outcome_date": ["2020-01-01", "2025-01-01"],   # a = already known pre-cut
    })
    r = temporal_holdout_precision_at_k(scores, outcomes, cut_date="2024-01-01", k=1)
    assert r["n_orgs"] == 2          # org:a removed from the holdout universe
    assert r["n_positives"] == 1     # only org:b counts


def test_outcomes_from_case_db_joins_the_graph(tmp_path):
    g = run_graph(build_synthetic_inputs(), tmp_path / "graph")
    release = ("Independent Clinic LLC agreed to pay $2 million to resolve "
               "allegations of billing for home health services not rendered.")
    db = build_case_db([parse_press_release(
        release, source_url="u1", announced_date="2025-03-01",
        defendant_name="Independent Clinic LLC")])
    outcomes = outcomes_from_case_db(db, g["nodes/org_nodes"])
    assert len(outcomes) == 1                       # matched via the name key
    assert outcomes.attrs["n_cases_matched"] == 1
    assert outcomes.iloc[0]["org_node_id"].startswith("org:")
