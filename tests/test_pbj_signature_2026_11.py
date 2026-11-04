"""pbj_signature (2026-11-04): a typed schedule reads differently from a payroll."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.ingest_cms import pbj_signature as ps

HDR = ["PROVNUM", "PROVNAME", "STATE", "CY_Qtr", "WorkDate", "MDScensus",
       "Hrs_RNDON", "Hrs_RNadmin", "Hrs_RN", "Hrs_RN_emp", "Hrs_RN_ctr",
       "Hrs_LPN", "Hrs_LPN_emp", "Hrs_LPN_ctr", "Hrs_CNA", "Hrs_CNA_emp", "Hrs_CNA_ctr", "Hrs_NAtrn", "Hrs_MedAide"]


def _quarter(path: Path, q: str, start: str, homes: dict) -> None:
    """homes: ccn -> (state, census, kind) where kind in payroll / typed / small."""
    rng = np.random.default_rng(7)
    dates = pd.date_range(start, periods=92, freq="D")
    rows = []
    for ccn, (state, census, kind) in homes.items():
        for d in dates:
            c = census + (rng.integers(-3, 4) if kind != "typed" else rng.integers(-3, 4))
            if kind == "payroll":
                base = 3.6 * c
                hrs = base * (0.85 if d.weekday() >= 5 else 1.0) + rng.normal(0, 0.06 * base)
                rn, ctr = 0.5 * c + rng.normal(0, 1), 0.02 * hrs
                total = hrs + 0.0137                     # minutes, never whole hours
            elif kind == "typed":
                total, rn, ctr = 3.6 * census, 0.5 * census, 0.0       # same whole number every day, census ignored
            else:                                                       # small: census under the floor
                c = 12
                total, rn, ctr = 40.0 + rng.normal(0, 2), 8.0, 0.0
            rows.append([ccn, f"HOME {ccn}", state, q, d.strftime("%Y%m%d"), c,
                         0.0, 0.0, max(rn, 0), max(rn, 0) - 0.0, 0.0,
                         total * 0.3, total * 0.3 - ctr * 0.5, ctr * 0.5,
                         total * 0.7 - max(rn, 0), total * 0.7 - max(rn, 0) - ctr * 0.5, ctr * 0.5, 0.0, 0.0])
    pd.DataFrame(rows, columns=HDR).to_csv(path, index=False)


def _homes() -> dict:
    homes = {f"36{i:04d}": ("OH", 80 + i, "payroll") for i in range(30)}
    homes["365999"] = ("OH", 90, "typed")
    homes["365998"] = ("OH", 12, "small")
    homes["155555"] = ("IN", 90, "typed")       # another state: never in the Ohio ranking
    return homes


def test_typed_schedule_ranks_at_the_top_of_the_state_and_before_after_reads(tmp_path):
    d = tmp_path / "facility"
    d.mkdir()
    _quarter(d / "pbj_daily_staffing_2023Q4.csv", "2023Q4", "2023-10-01", _homes())
    _quarter(d / "pbj_daily_staffing_2024Q1.csv", "2024Q1", "2024-01-01", _homes())
    (d / "pbj_daily_nonnurse_2023Q4.csv").write_text("PROVNUM,WorkDate,Hrs_OT\n365999,20231001,3\n", encoding="utf-8")
    days = ps.facility_days(sorted(d.glob("*.csv")), "OH")
    assert set(days["ccn"]) == {c for c, v in _homes().items() if v[0] == "OH"}
    assert set(days["quarter"]) == {"2023Q4", "2024Q1"}
    qm = ps.quarter_metrics(days)
    typed = qm[(qm["ccn"] == "365999") & (qm["quarter"] == "2023Q4")].iloc[0]
    pay = qm[(qm["ccn"] == "360000") & (qm["quarter"] == "2023Q4")].iloc[0]
    assert typed["identical_day_share"] > 0.95 and typed["round_share"] > 0.95
    assert pay["identical_day_share"] < 0.1 and pay["round_share"] < 0.1
    assert typed["weekend_ratio"] > pay["weekend_ratio"] and abs(typed["census_corr"]) < 0.05 < pay["census_corr"]
    # the typed home is the state's most schedule-like on every signature metric
    for m in ("identical_day_share", "round_share", "weekend_ratio", "census_corr", "cv_hprd"):
        assert typed[f"{m}__state_pct"] >= 0.95, m
    assert ps.signature_score(qm.loc[[typed.name]]).iloc[0] >= 0.95
    small = qm[qm["ccn"] == "365998"].iloc[0]
    assert not small["assessable"] and np.isnan(small["identical_day_share__state_pct"])
    # agency share is a level fact: the payroll home has one, the typed home none
    assert pay["agency_share"] > 0 and typed["agency_share"] == 0
    ba = ps.before_after(qm, pd.DataFrame([{"ccn": "365999", "date": "2024-01-01", "label": "CHOW"}]))
    assert ba.iloc[0]["quarters_before"] == 1 and ba.iloc[0]["quarters_after"] == 1
    md = ps.to_markdown(qm, ["365999", "360000"], "OH", ["pbj_daily_staffing_2023Q4.csv"], ba, {"365999": "TYPED HOME"})
    assert "TYPED HOME" in md and "Innocent explanations" in md and "## Before and after" in md
    assert "155555" not in md


def test_cli_writes_the_report_and_skips_the_non_nurse_file(tmp_path):
    d = tmp_path / "facility"
    d.mkdir()
    _quarter(d / "pbj_daily_staffing_2023Q4.csv", "2023Q4", "2023-10-01", _homes())
    (d / "pbj_daily_nonnurse_2023Q4.csv").write_text("PROVNUM,WorkDate,Hrs_OT\n365999,20231001,3\n", encoding="utf-8")
    ccns = tmp_path / "ccns.csv"
    ccns.write_text("ccn,facility_name\n365999,TYPED HOME\n", encoding="utf-8")
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-m", "src.ingest_cms.pbj_signature", "--pbj-dir", str(d), "--state", "OH",
                        "--ccns-file", str(ccns), "--out", str(out)], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert r.returncode == 0, r.stderr[-2000:]
    assert (out / "PBJ_SIGNATURE.md").exists() and (out / "pbj_signature_state.csv").exists()
    t = pd.read_csv(out / "pbj_signature_targets.csv", dtype={"ccn": str})
    assert t["ccn"].tolist() == ["365999"] and t["signature"].iloc[0] >= 0.95
