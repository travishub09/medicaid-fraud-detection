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
