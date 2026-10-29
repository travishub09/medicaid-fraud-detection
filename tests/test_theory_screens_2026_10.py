"""theory_screens: the definitive-reflex screen on a planted fact.

Planted: ALPHA runs a definitive panel on every specimen at 3 per patient-month (the
candidate); BETA screens far more than it confirms (fails reflex); GAMMA is Quest under its
own name (national, set aside); DELTA is under the dollar floor; EPSILON carries a standing
verdict; ZETA bills under a hospital number; THETA has no screens at all but bills the
panel (candidate: 'no denominator' passes reflex)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

from src.model_a import theory_screens as ts


def _fact(tmp: Path) -> tuple[Path, Path]:
    rows = []

    def add(npi, code, month, paid, lines, pats):
        rows.append({"billing_npi": npi, "servicing_npi": npi, "hcpcs_code": code, "service_month": month,
                     "total_paid": float(paid), "total_claim_lines": float(lines), "total_patients": float(pats)})

    for m in ("2023-01", "2023-07", "2024-01", "2024-07"):
        add("1000000001", "G0483", m, 500_000, 750, 250)     # 3,000 lines / 1,000 pm = 3.0
        add("1000000001", "80307", m, 10_000, 250, 250)      # 1,000 screens -> reflex 3.0
        add("1000000002", "G0481", m, 400_000, 125, 125)     # 500 lines / 500 pm = 1.0
        add("1000000002", "80307", m, 40_000, 1_000, 500)    # 4,000 screens -> reflex 0.125
        add("1000000003", "G0483", m, 5_000_000, 5_000, 2_000)
        add("1000000003", "80307", m, 50_000, 1_000, 1_000)
        add("1000000004", "G0483", m, 50_000, 300, 100)      # $200k < floor
        add("1000000005", "G0483", m, 600_000, 900, 300)
        add("1000000006", "G0483", m, 600_000, 900, 300)
        add("1000000007", "G0482", m, 300_000, 600, 200)     # no screens at all
        add("1000000008", "G0483", m, 600_000, 900, 300)     # a county public-health lab
    fact = tmp / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(fact, index=False)
    dim = pd.DataFrame([
        {"npi": "1000000001", "org_legal_name": "ALPHA TOX LAB LLC", "provider_name": "", "entity_type": "2", "addr_state": "FL", "taxonomy_code": "291U00000X"},
        {"npi": "1000000002", "org_legal_name": "BETA CLINICAL LAB", "provider_name": "", "entity_type": "2", "addr_state": "NJ", "taxonomy_code": "291U00000X"},
        {"npi": "1000000003", "org_legal_name": "QUEST DIAGNOSTICS INCORPORATED", "provider_name": "", "entity_type": "2", "addr_state": "CA", "taxonomy_code": "291U00000X"},
        {"npi": "1000000004", "org_legal_name": "DELTA LAB", "provider_name": "", "entity_type": "2", "addr_state": "TX", "taxonomy_code": "291U00000X"},
        {"npi": "1000000005", "org_legal_name": "EPSILON LABORATORIES", "provider_name": "", "entity_type": "2", "addr_state": "OH", "taxonomy_code": "291U00000X"},
        {"npi": "1000000006", "org_legal_name": "ZETA GENERAL HOSPITAL", "provider_name": "", "entity_type": "2", "addr_state": "OH", "taxonomy_code": "282N00000X"},
        {"npi": "1000000007", "org_legal_name": "THETA REFERENCE LAB", "provider_name": "", "entity_type": "2", "addr_state": "KY", "taxonomy_code": "291U00000X"},
        {"npi": "1000000008", "org_legal_name": "COUNTY OF IOTA PUBLIC HEALTH LABORATORY", "provider_name": "", "entity_type": "2", "addr_state": "CA", "taxonomy_code": "291U00000X"},
    ])
    dimp = tmp / "provider_dim.parquet"
    dim.to_parquet(dimp, index=False)
    return fact, dimp


def test_definitive_reflex_screen_classifies_the_planted_labs(tmp_path):
    fact, dim = _fact(tmp_path)
    verdicts = pd.DataFrame([["org:name:EPSILON", "EPSILON LABORATORIES", "set_aside", "2026-10-29", "x", "r"]],
                            columns=ts.load_verdicts.__globals__["COLS"])
    df = ts.run_screen(fact, ts.SCREENS["definitive_reflex"], provider_dim=dim, verdicts=verdicts)
    st = dict(zip(df["npi"], df["status"]))
    assert st["1000000001"] == "candidate"
    assert st["1000000007"] == "candidate"            # panel with no screens at all passes reflex
    assert st["1000000002"] == "fails_reflex"
    assert st["1000000003"] == "national"
    assert st["1000000004"] == "below_floor"
    assert st["1000000005"] == "verdict:set_aside"
    assert st["1000000006"] == "hospital"
    assert st["1000000008"] == "government"           # STATE OF / COUNTY OF: not an operator
    assert ts.is_government_name("STATE OF ALABAMA DEPARTMENT OF PUBLIC HEALTH")
    assert not ts.is_government_name("ALPHA TOX LAB LLC")
    assert "87507" not in ts.SCREENS["uti_pcr_reflex"].numerator   # the GI-panel codes are out
    a = df[df["npi"] == "1000000001"].iloc[0]
    assert abs(a["reflex_ratio"] - 3.0) < 1e-9 and abs(a["intensity"] - 3.0) < 1e-9
    assert abs(a["flag_share"] - 1.0) < 1e-9          # every definitive line is the top-tier code
    assert a["peak_year"] in ("2023", "2024") and a["last_year"] == "2024"
    # candidates ranked on numerator dollars: ALPHA ($2.0M) before THETA ($1.2M)
    ranked = df[df["status"].eq("candidate")].sort_values("rank")["npi"].tolist()
    assert ranked == ["1000000001", "1000000007"]
    md = ts.to_markdown({"definitive_reflex": df})
    assert "ALPHA TOX LAB" in md and "QUEST DIAGNOSTICS" in md and "Innocent explanations" in md


def test_cli_writes_report_and_csv(tmp_path):
    fact, dim = _fact(tmp_path)
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-m", "src.model_a.theory_screens", "--spending", str(fact),
                        "--provider-dim", str(dim), "--out", str(out), "--screen", "definitive_reflex",
                        "--verdicts", str(tmp_path / "none.csv")],
                       capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode == 0, r.stderr
    assert (out / "THEORY_SCREENS.md").exists() and (out / "screen_definitive_reflex.csv").exists()
    assert "candidates" in r.stdout
    names = (out / "names_definitive_reflex.txt").read_text(encoding="utf-8").splitlines()
    assert names == ["EPSILON LABORATORIES", "ALPHA TOX LAB LLC", "THETA REFERENCE LAB"]   # candidates in rank order (no verdicts file here), for first_to_file


def _perdiem_fact(tmp: Path) -> tuple[Path, Path, Path]:
    """Two residential programs on H0019 (one per-diem line per resident-day) and two aides on
    T1019 (one line per 15-minute unit): OVERFULL bills 40 residents a day in a 30-bed building,
    STEADY bills 20 in a 30-bed building; AIDE_A bills 120 units a day (30 hours) across two
    agencies, AIDE_B 32 a day (8 hours)."""
    rows = []

    def add(bnpi, snpi, code, month, paid, lines, pats):
        rows.append({"billing_npi": bnpi, "servicing_npi": snpi, "hcpcs_code": code, "service_month": month,
                     "total_paid": float(paid), "total_claim_lines": float(lines), "total_patients": float(pats)})

    for m in ("2024-01", "2024-04", "2024-06"):          # 31, 30, 30 days
        days = {"2024-01": 31, "2024-04": 30, "2024-06": 30}[m]
        add("2000000001", "2000000001", "H0019", m, 400_000, 40 * days, 40)   # 40 residents a day
        add("2000000002", "2000000002", "H0019", m, 400_000, 20 * days, 20)   # 20 a day
        add("3000000001", "4000000001", "T1019", m, 40_000, 80 * days, 3)     # aide A under agency 1
        add("3000000002", "4000000001", "T1019", m, 20_000, 40 * days, 2)     # aide A under agency 2
        add("3000000001", "4000000002", "T1019", m, 16_000, 32 * days, 2)     # aide B
        add("2000000003", "2000000003", "H0019", m, 400_000, 35 * days, 35)   # no capacity on file
        add("3000000001", "4000000003", "T1019", m, 90_000, 100 * days, 9)    # a physician's number as the aide
        add("3000000001", "4000000004", "T1019", m, 90_000, 100 * days, 9)    # a number NPPES does not carry
        add("3000000001", "1999999984", "T1019", m, 90_000, 100 * days, 9)    # a filler number
        add("4000000005", "4000000005", "T1019", m, 300_000, 400 * days, 30)   # an agency under a person's number
    fact = tmp / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(fact, index=False)
    dim = pd.DataFrame([
        {"npi": "2000000001", "org_legal_name": "OVERFULL RESIDENTIAL LLC", "provider_name": "", "entity_type": "2", "addr_state": "OH", "taxonomy_code": "320800000X"},
        {"npi": "2000000002", "org_legal_name": "STEADY HOUSE INC", "provider_name": "", "entity_type": "2", "addr_state": "OH", "taxonomy_code": "320800000X"},
        {"npi": "3000000001", "org_legal_name": "AGENCY ONE LLC", "provider_name": "", "entity_type": "2", "addr_state": "TX", "taxonomy_code": "253Z00000X"},
        {"npi": "3000000002", "org_legal_name": "AGENCY TWO LLC", "provider_name": "", "entity_type": "2", "addr_state": "TX", "taxonomy_code": "253Z00000X"},
        {"npi": "4000000001", "org_legal_name": "", "provider_name": "AIDE, A", "entity_type": "1", "addr_state": "TX", "taxonomy_code": "373H00000X"},
        {"npi": "4000000002", "org_legal_name": "", "provider_name": "AIDE, B", "entity_type": "1", "addr_state": "TX", "taxonomy_code": "373H00000X"},
        {"npi": "2000000003", "org_legal_name": "NOROSTER HOUSE LLC", "provider_name": "", "entity_type": "2", "addr_state": "PA", "taxonomy_code": "320800000X"},
        {"npi": "4000000003", "org_legal_name": "", "provider_name": "DOCTOR, D", "entity_type": "1", "addr_state": "NY", "taxonomy_code": "207R00000X"},
        {"npi": "4000000005", "org_legal_name": "", "provider_name": "OWNER, O", "entity_type": "1", "addr_state": "MS", "taxonomy_code": "376J00000X"},
    ])
    dimp = tmp / "provider_dim.parquet"
    dim.to_parquet(dimp, index=False)
    cap = pd.DataFrame([{"npi": "2000000001", "licensed_capacity": 30.0, "n_sites": 1},
                        {"npi": "2000000002", "licensed_capacity": 30.0, "n_sites": 1}])
    capp = tmp / "licensed_capacity_by_npi.parquet"
    cap.to_parquet(capp, index=False)
    return fact, dimp, capp


def test_perdiem_occupancy_ranks_the_overfull_building_first(tmp_path):
    fact, dim, cap = _perdiem_fact(tmp_path)
    df = ts.run_screen(fact, ts.SCREENS["perdiem_occupancy"], provider_dim=dim, capacity=cap)
    by = df.set_index("npi")
    assert by.loc["2000000001", "status"] == "candidate"
    assert by.loc["2000000002", "status"] == "under_capacity"      # 20 a day in a 30-bed building
    assert by.loc["2000000003", "status"] == "needs_capacity"      # no roster for its state
    assert abs(float(by.loc["2000000001", "peak_lines_per_day"]) - 40.0) < 1e-9
    assert abs(float(by.loc["2000000001", "occupancy_ratio"]) - 40 / 30) < 1e-9
    assert abs(float(by.loc["2000000002", "occupancy_ratio"]) - 20 / 30) < 1e-9
    assert bool(by.loc["2000000001", "over_capacity"]) and not bool(by.loc["2000000002", "over_capacity"])
    assert int(by.loc["2000000001", "rank"]) == 1 and pd.isna(by.loc["2000000002", "rank"])
    assert "4000000001" not in by.index                       # T1019 is not a per-diem code
    md = ts.to_markdown({"perdiem_occupancy": df})
    assert "OVERFULL RESIDENTIAL" in md and "occupancy" in md and "Peak lines per calendar day" in md
    assert "### needs_capacity" in md and "NOROSTER HOUSE" in md and "### under_capacity" in md
    # with no capacity table at all, the census rule alone decides (the old behaviour)
    df0 = ts.run_screen(fact, ts.SCREENS["perdiem_occupancy"], provider_dim=dim)
    assert set(df0[df0["status"].eq("candidate")]["npi"]) == {"2000000001", "2000000002", "2000000003"}


def test_attendant_hours_screens_the_servicing_npi_across_agencies(tmp_path):
    fact, dim, cap = _perdiem_fact(tmp_path)
    df = ts.run_screen(fact, ts.SCREENS["attendant_hours"], provider_dim=dim, capacity=cap)
    by = df.set_index("npi")
    assert set(by.index) == {"4000000001", "4000000002", "4000000003", "4000000004", "1999999984", "4000000005"}
    assert by.loc["4000000005", "status"] == "self_billed"       # servicing = the biller's own number
    assert abs(float(by.loc["4000000001", "self_share"])) < 1e-9
    assert by.loc["4000000003", "status"] == "not_aide"          # a physician's number in the servicing field
    assert by.loc["4000000004", "status"] == "not_in_nppes"      # a number the NPPES file does not carry
    assert by.loc["1999999984", "status"] == "placeholder"
    a = by.loc["4000000001"]
    assert a["status"] == "candidate"
    assert abs(float(a["peak_lines_per_day"]) - 120.0) < 1e-9  # 80 + 40 units a day across two agencies
    assert int(a["n_billers"]) == 2
    assert abs(float(a["paid_num"]) - 180_000.0) < 1e-6
    b = by.loc["4000000002"]
    assert b["status"] == "below_floor"                       # $48k over the window, 32 units a day
    # an organization in the servicing field is outside the screen's provider type
    rows = pd.DataFrame([{"billing_npi": "3000000001", "servicing_npi": "3000000001", "hcpcs_code": "T1019",
                          "service_month": "2024-01", "total_paid": 500_000.0, "total_claim_lines": 5000.0,
                          "total_patients": 50.0}])
    f2 = tmp_path / "f2.parquet"
    rows.to_parquet(f2, index=False)
    df2 = ts.run_screen(f2, ts.SCREENS["attendant_hours"], provider_dim=dim)
    assert df2.set_index("npi").loc["3000000001", "status"] == "other_type"
    md = ts.to_markdown({"attendant_hours": df})
    assert "### not_aide" in md and "DOCTOR, D" in md and "### not_in_nppes" in md and "4000000004" in md
    # the agencies behind the top aides, for the court-record check
    import duckdb
    con = duckdb.connect()
    ts._codes_table(con, "num_codes", ts.SCREENS["attendant_hours"].numerator)
    ag = ts.agencies_for(con, fact, ts.SCREENS["attendant_hours"], ["4000000001"], dim)
    assert set(ag["biller_name"]) == {"AGENCY ONE LLC", "AGENCY TWO LLC"}
    assert abs(float(ag.set_index("biller_npi").loc["3000000001", "paid"]) - 120_000.0) < 1e-6
