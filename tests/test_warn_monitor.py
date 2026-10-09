"""
test_warn_monitor.py — WARN normalization, org matching, and surge leads.

Planted (fixtures/synthetic.build_warn_notices): a layoff at "Owned One, LLC"
(BADCO ring, boosted by Model A) must surface as a surge lead; the unrelated
retailer must land in unmatched, not vanish.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.entity_graph.__main__ import run as run_graph
from src.model_a.__main__ import run as run_model_a
from src.sourcing.warn_monitor import normalize_warn, match_warn_to_orgs, surge_leads
from tests.fixtures.synthetic import (
    build_synthetic_inputs, build_company_features, build_warn_notices,
)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    graph_out = tmp_path_factory.mktemp("graph")
    outputs = run_graph(build_synthetic_inputs(), graph_out)
    org_nodes = outputs["nodes/org_nodes"]
    erv = run_model_a(org_nodes, outputs["org_graph_features"],
                      build_company_features(org_nodes),
                      outputs["rings/shared_address_shells"],
                      outputs["rings/common_owner_clusters"],
                      tmp_path_factory.mktemp("model_a"), top_k_dossiers=1)
    return org_nodes, erv, outputs


def test_normalize_handles_state_format():
    warn = normalize_warn(build_warn_notices())
    assert len(warn) == 3
    assert "employer_key" in warn.columns
    assert warn["layoff_date"].notna().all()
    assert (warn["employer_key"] == "OWNED ONE").any()    # suffix stripped


def test_matching_splits_known_and_unknown(world):
    org_nodes, _, _ = world
    matched, unmatched = match_warn_to_orgs(normalize_warn(build_warn_notices()), org_nodes)
    assert len(matched) == 2                               # Owned One + Subpart Health
    assert len(unmatched) == 1                             # the retailer — kept, not dropped
    assert unmatched.iloc[0]["employer_raw"].startswith("Totally Unrelated")


def test_surge_lead_fires_for_flagged_org(world):
    org_nodes, erv, outputs = world
    matched, _ = match_warn_to_orgs(normalize_warn(build_warn_notices()), org_nodes)
    # asof inside the 6–18 month window after the 2024-10-01 layoff
    leads = surge_leads(matched, erv, top_fraction=0.5,
                        asof=pd.Timestamp("2025-06-01"))
    badco_org = outputs["npi_to_org"].set_index("npi")["org_node_id"].loc["1003000100"]
    hit = leads[leads["org_node_id"] == badco_org]
    assert len(hit) == 1
    assert hit.iloc[0]["window_status"] == "active"
    assert hit.iloc[0]["erv_rank"] <= len(erv) * 0.5


def test_window_status_transitions(world):
    org_nodes, erv, _ = world
    matched, _ = match_warn_to_orgs(normalize_warn(build_warn_notices()), org_nodes)
    early = surge_leads(matched, erv, top_fraction=1.0, asof=pd.Timestamp("2024-11-01"))
    late = surge_leads(matched, erv, top_fraction=1.0, asof=pd.Timestamp("2027-01-01"))
    assert (early["window_status"] == "pending").all()
    assert (late["window_status"] == "expired").all()


def test_load_warn_folder_reads_xlsx_with_banner_and_state_from_filename(tmp_path):
    """2026-10-09: the operator's four state postings were three Excel files (California,
    Texas, Rhode Island) and one csv (North Carolina); the monitor read one csv only.
    A folder of warn_<ST>_<yyyymm>.* files loads as one frame; an xlsx with a title
    row above its header is probed; the state comes from the filename when the
    sheet carries none."""
    from src.sourcing.warn_monitor import load_warn, read_warn_file
    d = tmp_path / "warn"
    d.mkdir()
    # California-style xlsx: a banner row, then the header
    ca = pd.DataFrame([["WARN Report 2026", None, None, None],
                       ["Company", "Notice Date", "Effective Date", "No. Of Employees"],
                       ["Sunrise Home Care LLC", "2026-07-01", "2026-09-01", "45"],
                       ["Valley Clinic Inc", "2026-08-15", "2026-10-15", "12"]])
    ca.to_excel(d / "warn_CA_202610.xlsx", index=False, header=False)
    # Texas-style xlsx: TWC headers, no banner
    tx = pd.DataFrame({"JOB_SITE_NAME": ["Lone Star Rehab"], "NOTICE_DATE": ["2026-06-02"],
                       "LayOff_Date": ["2026-08-01"], "TOTAL_LAYOFF_NUMBER": ["80"]})
    tx.to_excel(d / "warn_TX_202610.xlsx", index=False)
    # North Carolina csv with its own state column
    nc = pd.DataFrame({"Company Name": ["Piedmont Hospice"], "State": ["NC"],
                       "Notice Date": ["2026-05-05"], "Effective Date": ["2026-07-05"],
                       "Number of Employees": ["30"]})
    nc.to_csv(d / "warn_NC_202610.csv", index=False)
    (d / "notes.txt").write_text("ignored")
    w = load_warn(d)
    assert len(w) == 4 and set(w["source_file"]) == {"warn_CA_202610.xlsx", "warn_TX_202610.xlsx", "warn_NC_202610.csv"}
    by = w.set_index("employer_raw")
    assert by.loc["Sunrise Home Care LLC", "state"] == "CA" and by.loc["Lone Star Rehab", "state"] == "TX"
    assert by.loc["Piedmont Hospice", "state"] == "NC"
    assert int(by.loc["Lone Star Rehab", "employees"]) == 80
    assert str(by.loc["Sunrise Home Care LLC", "layoff_date"])[:10] == "2026-09-01"
    # a single file still works, and a sheet with no employer column says so
    assert len(read_warn_file(d / "warn_NC_202610.csv")) == 1
    pd.DataFrame({"foo": [1]}).to_excel(d / "warn_ZZ_202610.xlsx", index=False)
    with pytest.raises(ValueError):
        read_warn_file(d / "warn_ZZ_202610.xlsx")


def test_read_warn_file_finds_the_data_sheet_in_a_multi_sheet_workbook(tmp_path):
    """California's WARN workbook opens with an introduction sheet; the postings sit on
    sheet 3 under a title row. The reader probes every sheet."""
    from src.sourcing.warn_monitor import read_warn_file
    f = tmp_path / "warn_CA_202610.xlsx"
    with pd.ExcelWriter(f) as xw:
        pd.DataFrame({"Intro": ["This workbook contains 3 sheets."]}).to_excel(xw, sheet_name="Introduction", index=False)
        pd.DataFrame({"Summary": ["totals"]}).to_excel(xw, sheet_name="WARN Report Summary", index=False)
        pd.DataFrame([["Detailed WARN Report", None, None, None],
                      ["Company", "Notice Date", "Effective Date", "No. Of Employees"],
                      ["Golden State Home Health", "2026-07-01", "2026-09-01", "60"]]).to_excel(
            xw, sheet_name="Detailed WARN Report", index=False, header=False)
    raw = read_warn_file(f)
    assert len(raw) == 1 and raw["STATE"].iloc[0] == "CA"
    assert "Company" in raw.columns and raw["Company"].iloc[0] == "Golden State Home Health"


def test_warn_summary_reports_each_file_and_the_live_window(tmp_path):
    from src.sourcing.warn_monitor import load_warn, warn_summary
    d = tmp_path / "warn"
    d.mkdir()
    pd.DataFrame({"Company": ["A Corp", "B LLC"], "Notice Date": ["2026-07-01", "2020-01-01"],
                  "Effective Date": ["2026-09-01", "2020-03-01"], "No. Of Employees": ["10", "20"]}).to_csv(
        d / "warn_RI_202610.csv", index=False)
    out = warn_summary(load_warn(d))
    assert "warn_RI_202610.csv | 2 | RI | 2020-01-01 to 2026-07-01" in out
    assert "still open or ahead" in out and ": 1 of 2" in out


def test_north_carolina_header_resolves_by_keyword(tmp_path):
    """NC 2026 export: employer under 'WARN Notice: WARN Notice Name', count under
    'Number affected at this location'; no exact candidate matched before 2026-10-09."""
    from src.sourcing.warn_monitor import load_warn
    d = tmp_path / "warn"
    d.mkdir()
    pd.DataFrame({"County": ["Wake"], "Warn Number": ["1"], "Date of Notice": ["2026-07-01"],
                  "Date Received by NC": ["2026-07-02"], "Effective Date": ["2026-09-01"],
                  "WARN Notice: WARN Notice Name": ["Piedmont Home Health LLC"],
                  "WARN notice type": ["Layoff"], "Type of layoff or closure": ["Permanent"],
                  "Number affected at this location": ["42"], "Address 1": ["1 Main"], "City": ["Raleigh"],
                  "STATE": ["NC"]}).to_csv(d / "warn_NC_202610.csv", index=False)
    w = load_warn(d)
    assert w["employer_raw"].iloc[0] == "Piedmont Home Health LLC" and w["state"].iloc[0] == "NC"
    assert int(w["employees"].iloc[0]) == 42 and str(w["notice_date"].iloc[0])[:10] == "2026-07-01"
    assert str(w["layoff_date"].iloc[0])[:10] == "2026-09-01"
