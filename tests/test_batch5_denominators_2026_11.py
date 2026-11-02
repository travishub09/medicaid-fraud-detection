"""Batch 5b-D (2026-11-02, architecture audit items C4 / C5 / C6): the denominators.

  C4  the "patient-month" base was SUM(patients) over code x month rows (a patient once
      per code); every intensity now divides by the per-month MAX over the family's
      codes (a lower bound on distinct patient-months), the old formula keeps a
      _codemonth suffix, and lab breadth is its own column
  C5  billing_after_deactivation is the deactivated NPI's OWN share at NPI grain; the
      org-grain frame exists only for single-NPI orgs, so a sibling never lights up
  C6  the drug-spread references (rx_spread_* and drug_markup_anomaly) are medians
      WITHIN the provider's state (national fallback under 20 pairs, recorded), and
      per day of supply where the NDC fact carries it

Small DuckDB / pandas frames only; no data in git.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.analytics.drug_markup import drug_markup_from_parquet
from src.ingest_cms import medicaid_ndc as mndc
from src.ingest_cms.nppes_deactivation import (billing_after_deactivation,
                                                billing_after_deactivation_frames)
from src.ingest_cms.sector_schemes import (PATIENT_BASE_COLS, REBASED_INTENSITIES,
                                           sector_metrics_from_parquet)
from src.model_a.servicing_grain import SV_COLS, servicing_stats

COLS = ["billing_npi", "hcpcs_code", "service_month", "total_paid", "total_claim_lines", "total_patients"]


def _spend(tmp_path: Path, rows, name="spending_fact.parquet") -> Path:
    p = tmp_path / name
    pd.DataFrame(rows, columns=COLS).to_parquet(p, index=False)
    return p


# ----------------------------------------------------------------- C4: the base ---

def test_two_code_family_max_base_differs_from_code_month_sum(tmp_path):
    """T1019 and T1020 on the same 10 patients in January: the code-month sum reads 20,
    the per-month MAX reads 10 (at most 20, at least 10 distinct patients); February
    adds 10 on one code. Base 30 vs 20; the intensities follow the max base, the old
    formula keeps the _codemonth suffix, positive-only dollars on the new intensity."""
    rows = [
        ("1003000126", "T1019", "2024-01", 1000.0, 100.0, 10.0),
        ("1003000126", "T1020", "2024-01", 500.0, 50.0, 10.0),
        ("1003000126", "T1019", "2024-02", 1000.0, 100.0, 10.0),
        ("1003000126", "T1019", "2024-03", -200.0, 0.0, 0.0),       # a refund month
        ("1003000134", "99213", "2024-01", 900.0, 9.0, 9.0),        # no personal-care family
    ]
    out = sector_metrics_from_parquet(_spend(tmp_path, rows)).set_index("npi")
    pc = out.loc["1003000126"]
    assert pc["pc_patient_code_months"] == 30.0
    assert pc["pc_patient_months_max"] == 20.0
    assert pc["pc_lines_per_patient_month"] == pytest.approx(250.0 / 20.0)
    assert pc["pc_lines_per_patient_month_codemonth"] == pytest.approx(250.0 / 30.0)
    assert pc["pc_paid_per_patient_month"] == pytest.approx(2500.0 / 20.0)        # positive-only dollars
    assert pc["pc_paid_per_patient_month_codemonth"] == pytest.approx(2300.0 / 30.0)  # the old net formula
    # the max-based intensity is never below the code-month one (the base is never larger)
    assert pc["pc_lines_per_patient_month"] >= pc["pc_lines_per_patient_month_codemonth"]
    # NULL where the family is absent: no base, no intensity, no _codemonth
    plain = out.loc["1003000134"]
    for c in PATIENT_BASE_COLS:
        assert pd.isna(plain[c])
    for c in REBASED_INTENSITIES:
        assert pd.isna(plain[c]) and pd.isna(plain[f"{c}_codemonth"])
    assert set(PATIENT_BASE_COLS) <= set(out.columns)


def test_lab_breadth_is_distinct_codes_per_patient_month(tmp_path):
    """Two labs on the same 10 patients a month for two months: one runs three codes
    on everyone, the other one code. Lines per patient under the old base could not
    tell them apart (the base grew with every code); the breadth column reads 3 vs 1
    codes per patient-month and the intensity divides by the max base."""
    rows = []
    for m in ("2024-01", "2024-02"):
        for code in ("G0483", "80053", "83036"):
            rows.append(("1003000126", code, m, 1000.0, 10.0, 10.0))
        rows.append(("1003000134", "G0483", m, 1000.0, 10.0, 10.0))
    out = sector_metrics_from_parquet(_spend(tmp_path, rows)).set_index("npi")
    wide, narrow = out.loc["1003000126"], out.loc["1003000134"]
    assert wide["lab_patient_code_months"] == 60.0 and wide["lab_patient_months_max"] == 20.0
    assert wide["lab_distinct_codes_per_patient_month"] == pytest.approx(6.0 / 20.0)   # 3 codes a month
    assert narrow["lab_distinct_codes_per_patient_month"] == pytest.approx(2.0 / 20.0)  # 1 code a month
    # the old lines-per-patient read the SAME 1.0 for both (the base ate the breadth)
    assert wide["lab_lines_per_patient_codemonth"] == pytest.approx(1.0)
    assert narrow["lab_lines_per_patient_codemonth"] == pytest.approx(1.0)
    assert wide["lab_lines_per_patient"] == pytest.approx(60.0 / 20.0)
    assert narrow["lab_lines_per_patient"] == pytest.approx(20.0 / 20.0)


def test_servicing_patient_months_use_the_per_month_max(tmp_path):
    """A clinician rendering 99213 for 40 patients under TWO billers in one month:
    the code-month sum reads 80 patient-months, the max base 40; dollars per
    patient-month follow the max base on positive dollars, the old net formula keeps
    the _codemonth suffix."""
    doc, a, b = "1003000209", "1003000506", "1003000514"
    rows = []
    for m in ("2023-01", "2023-02"):
        rows.append(dict(billing_npi=a, servicing_npi=doc, hcpcs_code="99213", service_month=m,
                         total_patients=40.0, total_claim_lines=400.0, total_paid=20000.0))
        rows.append(dict(billing_npi=b, servicing_npi=doc, hcpcs_code="99213", service_month=m,
                         total_patients=40.0, total_claim_lines=400.0, total_paid=20000.0))
    rows.append(dict(billing_npi=b, servicing_npi=doc, hcpcs_code="99213", service_month="2023-03",
                     total_patients=0.0, total_claim_lines=0.0, total_paid=-500.0))       # a refund month
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    s = servicing_stats(p).set_index("npi")
    assert list(s.columns) == SV_COLS
    r = s.loc[doc]
    assert r["sv_patient_months"] == 160.0 and r["sv_patient_months_max"] == 80.0
    assert r["sv_paid_per_patient_month"] == pytest.approx(80000.0 / 80.0)
    assert r["sv_paid_per_patient_month_codemonth"] == pytest.approx(79500.0 / 160.0)


# ----------------------------------------------------- C5: the deactivated NPI ---

def test_deactivated_npi_inside_a_three_npi_org_never_lights_its_siblings():
    deact = pd.DataFrame({"npi": ["1003000415", "1003000308"],
                          "deactivation_date": pd.to_datetime(["2024-01-01", "2023-01-01"])})
    spending = pd.DataFrame([
        # the deactivated member of a 3-NPI org: 100k before, 300k after
        {"billing_npi": "1003000415", "service_month": "2023-06", "total_paid": 100_000.0},
        {"billing_npi": "1003000415", "service_month": "2024-03", "total_paid": 300_000.0},
        # its two siblings, live, billing in the same months
        {"billing_npi": "1003000407", "service_month": "2024-03", "total_paid": 500_000.0},
        {"billing_npi": "1003000100", "service_month": "2024-03", "total_paid": 700_000.0},
        # a solo org whose one NPI is deactivated: billed only before (an observed 0)
        {"billing_npi": "1003000308", "service_month": "2022-06", "total_paid": 50_000.0},
    ])
    xw = pd.DataFrame({"npi": ["1003000415", "1003000407", "1003000100", "1003000308"],
                       "org_node_id": ["org:system", "org:system", "org:system", "org:solo"]})
    npi_frame, org_frame = billing_after_deactivation_frames(spending, deact, xw)
    n = npi_frame.set_index("npi")
    # the NPI-grain frame names the deactivated NPIs and nobody else
    assert set(n.index) == {"1003000415", "1003000308"}
    assert n.loc["1003000415", "billing_after_deactivation"] == 0.75
    assert n.loc["1003000308", "billing_after_deactivation"] == 0.0             # deactivated, billed only before
    assert "org_node_id" not in npi_frame.columns                                 # nothing to broadcast on
    # the org-grain frame carries the solo org only: the 3-NPI system has NO row, so
    # the two live siblings can never inherit the retiree's share
    o = org_frame.set_index("org_node_id")
    assert list(o.index) == ["org:solo"] and o.loc["org:solo", "org_member_count"] == 1
    assert "org:system" not in o.index
    # the default call is the NPI grain; grain='org' needs the crosswalk
    assert billing_after_deactivation(spending, deact).equals(npi_frame)
    with pytest.raises(ValueError):
        billing_after_deactivation(spending, deact, grain="org")


def test_deactivation_share_is_positive_only_and_null_without_billing():
    deact = pd.DataFrame({"npi": ["1003000415", "1003000308"],
                          "deactivation_date": pd.to_datetime(["2024-01-01", "2024-01-01"])})
    spending = pd.DataFrame([
        {"billing_npi": "1003000415", "service_month": "2023-06", "total_paid": 100.0},
        {"billing_npi": "1003000415", "service_month": "2024-02", "total_paid": 300.0},
        {"billing_npi": "1003000415", "service_month": "2024-03", "total_paid": -350.0},   # a recoupment
        {"billing_npi": "1003000308", "service_month": "2024-03", "total_paid": -10.0},    # nothing positive
    ])
    n = billing_after_deactivation(spending, deact).set_index("npi")
    assert n.loc["1003000415", "billing_after_deactivation"] == pytest.approx(300.0 / 400.0)
    assert pd.isna(n.loc["1003000308", "billing_after_deactivation"])


# ------------------------------------------------- C6: the reference within state ---

def _ndc_fact(tmp_path: Path, rows) -> Path:
    d = tmp_path / "medicaid_ndc"
    d.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "medicaid_ndc.csv", index=False)
    fact = tmp_path / "ndc_fact.parquet"
    mndc.build_ndc_fact(d, fact)
    return fact


def _ndc_row(npi, state, paid, lines=20, days=None, ndc="00071015523"):
    r = dict(BILLING_PROVIDER_NPI_NUM=npi, PRESCRIBING_PROVIDER_NPI_NUM="", NDC=ndc, CLAIM_FROM_MONTH="2023-01",
             TOTAL_PATIENTS=10, TOTAL_CLAIM_LINES=lines, TOTAL_PAID=paid, PROVIDER_STATE=state)
    if days is not None:
        r["TOTAL_DAYS_SUPPLY"] = days
    return r


def test_spread_proxy_reference_is_the_state_median_with_a_national_fallback(tmp_path):
    """One NDC. State XX pays 100 a line (26 pharmacies), state YY 50 a line (30): the
    national median is 50, XX's is 100. A pharmacy in XX at 100 is AT its state's
    median (spread 0, ref 'state'); the same price in a state with one pharmacy falls
    back to the national median and reads 2x (ref 'national', fallback share 1)."""
    px, pz = "1003000126", "1003000134"
    rows = [_ndc_row(px, "XX", 2000.0), _ndc_row(pz, "ZZ", 2000.0)]
    rows += [_ndc_row(f"19{i:08d}", "XX", 2000.0) for i in range(25)]
    rows += [_ndc_row(f"18{i:08d}", "YY", 1000.0) for i in range(30)]
    f = mndc.pharmacy_spread_proxy(_ndc_fact(tmp_path, rows)).set_index("npi")
    assert f.loc[px, "rx_spread_proxy"] == 0 and f.loc[px, "rx_spread_ref"] == "state"
    assert f.loc[px, "rx_spread_fallback_share"] == 0
    assert f.loc[pz, "rx_spread_proxy"] == pytest.approx(1.0) and f.loc[pz, "rx_spread_ref"] == "national"
    assert f.loc[pz, "rx_spread_fallback_share"] == 1 and f.loc[pz, "rx_spread_top_share"] == 1
    assert f.loc["1800000000", "rx_spread_proxy"] == 0 and f.loc["1800000000", "rx_spread_ref"] == "state"
    assert f.attrs["rx_spread_unit"] == "line"
    assert f.attrs["rx_spread_fallback_share"] == pytest.approx(1.0 / 57.0)


def test_spread_proxy_prices_per_day_of_supply_when_the_fact_carries_it(tmp_path):
    """A 90-day fill costs three 30-day fills: per LINE the pharmacy reads 3x the
    others, per DAY of supply it is at the median. Pairs without days supply have no
    reference and count against coverage."""
    p90, p_nodays = "1003000126", "1003000134"
    rows = [_ndc_row(p90, "XX", 6000.0, lines=20, days=1800)]          # 300 a line, 3.33 a day
    rows += [_ndc_row(f"19{i:08d}", "XX", 2000.0, lines=20, days=600) for i in range(25)]   # 100 a line, 3.33 a day
    rows += [_ndc_row(p_nodays, "XX", 2000.0, lines=20)]               # no days supply on this pair
    f = mndc.pharmacy_spread_proxy(_ndc_fact(tmp_path, rows)).set_index("npi")
    assert f.attrs["rx_spread_unit"] == "day"
    assert f.loc[p90, "rx_spread_proxy"] == pytest.approx(0.0) and f.loc[p90, "rx_spread_ref"] == "state"
    assert pd.isna(f.loc[p_nodays, "rx_spread_proxy"]) and pd.isna(f.loc[p_nodays, "rx_spread_ref"])
    assert pd.isna(f.loc[p_nodays, "rx_spread_coverage"]) or f.loc[p_nodays, "rx_spread_coverage"] == 0
    # under half the rows with days supply -> the unit stays the line
    g = mndc.pharmacy_spread_proxy(_ndc_fact(tmp_path, rows), days_supply_min_share=1.01).set_index("npi")
    assert g.attrs["rx_spread_unit"] == "line" and g.loc[p90, "rx_spread_proxy"] == pytest.approx(2.0)


def _markup_rows(with_state=True):
    rows = []
    def add(npi, state, paid):
        r = {"billing_npi": npi, "hcpcs_code": "J1885", "service_month": "2023-01",
             "total_paid": paid, "total_claim_lines": 10}
        if with_state:
            r["provider_state"] = state
        rows.append(r)
    for i in range(21):
        add(f"tx{i:08d}", "TX", 1000.0)          # TX fee schedule: 100 a line
    for i in range(22):
        add(f"ca{i:08d}", "CA", 5000.0)          # CA fee schedule: 500 a line
    add("txoutlier", "TX", 5000.0)               # a TX biller at the CA price
    add("nvalone", "NV", 5000.0)                 # a state with one biller
    return rows


def test_drug_markup_reference_is_within_state(tmp_path):
    """Nationally the median line price is 500 (the larger state's schedule), so the
    TX biller at 500 read as the pack; within TX it is 5x its peers. The CA biller at
    500 is at its state median; the lone NV biller falls back to the national
    reference and the row says so."""
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame(_markup_rows()).to_parquet(p, index=False)
    out = drug_markup_from_parquet(str(p)).set_index("npi")
    assert out.loc["txoutlier", "drug_markup_anomaly"] > 0.5
    assert out.loc["txoutlier", "drug_markup_ref"] == "state" and out.loc["txoutlier", "drug_markup_fallback_share"] == 0
    assert out.loc["tx00000000", "drug_markup_anomaly"] == 0 and out.loc["ca00000000", "drug_markup_anomaly"] == 0
    assert out.loc["nvalone", "drug_markup_ref"] == "national" and out.loc["nvalone", "drug_markup_fallback_share"] == 1
    assert out.loc["nvalone", "drug_markup_anomaly"] == 0                     # 500 IS the national median
    assert out.attrs["drug_markup_fallback_share"] == pytest.approx(1.0 / 45.0)
    # a fact without provider_state takes the state from a (npi, state) frame; with
    # neither, every row is national and the TX outlier is invisible (the old reading)
    p2 = tmp_path / "no_state.parquet"
    pd.DataFrame(_markup_rows(with_state=False)).to_parquet(p2, index=False)
    states = pd.DataFrame({"npi": [r["billing_npi"] for r in _markup_rows()],
                           "state": [r["provider_state"] for r in _markup_rows()]})
    o2 = drug_markup_from_parquet(str(p2), state_by_npi=states).set_index("npi")
    assert o2.loc["txoutlier", "drug_markup_anomaly"] == pytest.approx(out.loc["txoutlier", "drug_markup_anomaly"])
    o3 = drug_markup_from_parquet(str(p2)).set_index("npi")
    assert o3.loc["txoutlier", "drug_markup_anomaly"] == 0 and (o3["drug_markup_ref"] == "national").all()
    assert o3.attrs["drug_markup_fallback_share"] == 1.0
