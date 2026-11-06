"""The biller's servicing mix (model_a/servicing_grain.billing_servicing_mix, 2026-11-05):
three BILLING-provider shares of gross (positive) paid dollars, self-rendered rows left
out of every numerator, registered under their own export source / partition group
(`servicing_mix`) so a run grades them on their own, merged m:1 (never a new row)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a.feature_partition import EXTRA_SOURCE_GROUPS, partition_features
from src.model_a.servicing_grain import SRV_COLS, billing_servicing_mix

BILL = "1100000001"          # the biller under test (TX)
HUB = "2200000001"           # renders under BILL + 9 other billers = 10 total -> hub
NEAR = "2200000002"          # renders under BILL + 8 others = 9 total (+ bills for itself) -> not hub
UNK = "2200000003"           # no state in NPPES
DEAD = "2200000004"          # deactivated 2022-06-15, never reactivated -> inactive in 2023-01
REACT = "2200000005"         # deactivated 2022-01-01, reactivated 2022-06-01 -> active in 2023-01
LATE = "2200000006"          # deactivated 2023-01-15 (mid service month) -> active (month-start rule); FL
OLDREACT = "2200000007"      # reactivation 2021 BEFORE its 2022 deactivation (an older cycle) -> inactive
NOSTATE_BILL = "1100000002"  # a biller with no provider_dim row
REFUND_BILL = "1100000003"   # a biller with only refund rows
OTHERS = [f"13000000{i:02d}" for i in range(1, 10)]   # nine other billers


def _row(bn, sv, paid, month="2023-01"):
    return dict(billing_npi=bn, servicing_npi=sv, hcpcs_code="99213", service_month=month,
                total_patients=1.0, total_claim_lines=1.0, total_paid=float(paid))


def _fact(tmp_path: Path, extra=()) -> Path:
    rows = [
        _row(BILL, BILL, 100),       # self-rendered: denominator only
        _row(BILL, None, 100),       # no servicing NPI: denominator only
        _row(BILL, HUB, 200),
        _row(BILL, NEAR, 300),
        _row(BILL, NEAR, -500, "2023-02"),   # a refund: adds nothing to the gross
        _row(BILL, UNK, 50),
        _row(BILL, DEAD, 150),
        _row(BILL, REACT, 80),
        _row(BILL, LATE, 70),
        _row(BILL, OLDREACT, 60),
        _row(NEAR, NEAR, 40),        # NEAR billing for itself is not a biller it shows up under
        _row(NOSTATE_BILL, REACT, 25),
        _row(REFUND_BILL, UNK, -30),
    ]
    rows += [_row(o, HUB, 10) for o in OTHERS]            # HUB: BILL + 9 = 10 billers
    rows += [_row(o, NEAR, 10) for o in OTHERS[:8]]       # NEAR: BILL + 8 = 9 billers
    rows += list(extra)
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


def _dim() -> pd.DataFrame:
    T = pd.Timestamp
    rec = [
        (BILL, "TX", T("2020-01-01"), pd.NaT),   # the biller itself deactivated: its self rows never count
        (HUB, "NY", pd.NaT, pd.NaT),
        (NEAR, "TX", pd.NaT, pd.NaT),
        (UNK, None, pd.NaT, pd.NaT),
        (DEAD, "TX", T("2022-06-15"), pd.NaT),
        (REACT, "TX", T("2022-01-01"), T("2022-06-01")),
        (LATE, "FL", T("2023-01-15"), pd.NaT),
        (OLDREACT, "TX", T("2022-01-01"), T("2021-01-01")),
        (REFUND_BILL, "TX", pd.NaT, pd.NaT),
    ] + [(o, "TX", pd.NaT, pd.NaT) for o in OTHERS]
    return pd.DataFrame(rec, columns=["npi", "addr_state", "deactivation_date", "reactivation_date"])


GROSS = 100 + 100 + 200 + 300 + 50 + 150 + 80 + 70 + 60      # 1,110: refunds add nothing


def test_three_shares_on_the_planted_biller(tmp_path):
    m = billing_servicing_mix(_fact(tmp_path), _dim()).set_index("npi")
    assert list(m.columns) == SRV_COLS
    b = m.loc[BILL]
    assert abs(b["srv_hub_share"] - 200 / GROSS) < 1e-12              # HUB only (10 billers incl. BILL)
    assert abs(b["srv_out_of_state_share"] - (200 + 70) / GROSS) < 1e-12   # HUB NY + LATE FL
    assert abs(b["srv_inactive_share"] - (150 + 60) / GROSS) < 1e-12        # DEAD + OLDREACT


def test_hub_threshold_counts_this_biller_and_not_self_billing(tmp_path):
    sp = _fact(tmp_path)
    m = billing_servicing_mix(sp, _dim()).set_index("npi")
    # one more biller for NEAR makes it 10 total -> a hub
    (tmp_path / "x").mkdir()
    sp2 = _fact(tmp_path / "x", extra=[_row(OTHERS[8], NEAR, 10)])
    m2 = billing_servicing_mix(sp2, _dim()).set_index("npi")
    assert abs(m2.loc[BILL, "srv_hub_share"] - (200 + 300) / GROSS) < 1e-12
    # a hub threshold of 11 drops HUB (exactly 10 billers)
    m3 = billing_servicing_mix(sp, _dim(), hub_min_billers=11).set_index("npi")
    assert m3.loc[BILL, "srv_hub_share"] == 0.0
    assert m.loc[OTHERS[8], "srv_hub_share"] == 1.0                     # every dollar via HUB
    assert m.loc[OTHERS[0], "srv_hub_share"] == 0.5                     # half via HUB, half via NEAR


def test_unknown_states_and_missing_biller(tmp_path):
    m = billing_servicing_mix(_fact(tmp_path), _dim()).set_index("npi")
    assert np.isnan(m.loc[NOSTATE_BILL, "srv_out_of_state_share"])      # biller's own state unknown
    assert m.loc[NOSTATE_BILL, "srv_hub_share"] == 0.0                   # the hub share still computes
    assert m.loc[NOSTATE_BILL, "srv_inactive_share"] == 0.0             # REACT active -> 0, not NULL


def test_refund_only_biller_is_null_and_shares_in_unit_interval(tmp_path):
    m = billing_servicing_mix(_fact(tmp_path), _dim()).set_index("npi")
    assert m.loc[REFUND_BILL, SRV_COLS].isna().all()                    # no positive dollars
    vals = m[SRV_COLS].to_numpy(dtype=float)
    vals = vals[~np.isnan(vals)]
    assert ((vals >= 0) & (vals <= 1)).all()


def test_cutoff_and_parquet_dim_and_no_deactivation_columns(tmp_path):
    late = [_row(BILL, HUB, 1000, "2024-06")]
    sp = _fact(tmp_path, extra=late)
    dp = tmp_path / "provider_dim.parquet"
    _dim().to_parquet(dp, index=False)
    m = billing_servicing_mix(sp, dp, cutoff="2024-01").set_index("npi")   # parquet path works too
    assert abs(m.loc[BILL, "srv_hub_share"] - 200 / GROSS) < 1e-12           # the 2024 row is cut
    m_all = billing_servicing_mix(sp, dp).set_index("npi")
    assert abs(m_all.loc[BILL, "srv_hub_share"] - 1200 / (GROSS + 1000)) < 1e-12
    # a provider_dim without deactivation dates -> srv_inactive_share NULL, never a silent 0
    m2 = billing_servicing_mix(sp, _dim()[["npi", "addr_state"]], cutoff="2024-01")
    assert m2["srv_inactive_share"].isna().all()


def test_partition_puts_srv_columns_in_their_own_group():
    assert EXTRA_SOURCE_GROUPS["servicing_mix"] == {"servicing_mix"}
    man = {"raw_feature_cols": list(SRV_COLS) + ["sv_n_billing_orgs"],
           "sources_used": {"servicing_mix": list(SRV_COLS), "servicing": ["sv_n_billing_orgs"]}}
    part = partition_features(man)
    assert part["extra"]["servicing_mix"] == sorted(SRV_COLS)
    assert "sv_n_billing_orgs" in part["core"] and not set(SRV_COLS) & set(part["core"])


def test_export_main_path_registers_servicing_mix_without_adding_rows(tmp_path):
    """The real main() path on the synthetic inputs, frozen-run style
    (--no-servicing-rows): the three columns land under their own source, are trainable
    (not leakage-fenced), carry the current_state vintage, and the row count is the
    leads count exactly."""
    from src.entity_graph.__main__ import run as run_graph
    from tests.fixtures.synthetic import build_provider_leads, build_synthetic_inputs
    from tests.test_servicing_grain import _fact as sv_fact

    inputs = build_synthetic_inputs()
    run_graph(inputs, tmp_path / "graph")
    proc = tmp_path / "processed"
    proc.mkdir()
    pre = tmp_path / "preclean"
    pre.mkdir()
    inputs["provider_dim"].to_parquet(proc / "provider_dim.parquet", index=False)
    sv_fact(proc)
    leads = build_provider_leads(inputs["provider_dim"])
    leads.to_parquet(tmp_path / "leads.parquet", index=False)
    r = subprocess.run([sys.executable, "-m", "src.model_a.provider_features_export",
                        "--graph-dir", str(tmp_path / "graph"), "--leads", str(tmp_path / "leads.parquet"),
                        "--processed", str(proc), "--preclean", str(pre), "--out", str(tmp_path / "out"),
                        "--no-resume-cache", "--no-servicing-rows"],
                       capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]),
                       env=dict(os.environ, PYTHONWARNINGS="ignore", OMP_NUM_THREADS="1"), timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "[servicing_mix]" in r.stdout
    mx = pd.read_parquet(tmp_path / "out" / "provider_features_for_model.parquet")
    assert len(mx) == len(leads)                                          # the join adds no rows
    for c in SRV_COLS:
        assert c in mx.columns
    man = json.loads((tmp_path / "out" / "feature_manifest.json").read_text())
    assert set(man["sources_used"]["servicing_mix"]) <= set(SRV_COLS)
    live = set(man["sources_used"]["servicing_mix"])
    assert live, "no srv_* column survived the export"
    assert not live & set(man["leakage_adjacent"]) and not live & set(man["leakage_hard"])
    assert live <= set(man["raw_feature_cols"]) | set(man.get("dropped_constant", []))
    assert live & set(man["raw_feature_cols"]) <= set(man["feature_vintage"]["current_state"])


def test_export_skip_sources_servicing_mix(tmp_path):
    from src.entity_graph.__main__ import run as run_graph
    from tests.fixtures.synthetic import build_provider_leads, build_synthetic_inputs
    from tests.test_servicing_grain import _fact as sv_fact

    inputs = build_synthetic_inputs()
    run_graph(inputs, tmp_path / "graph")
    proc = tmp_path / "processed"
    proc.mkdir()
    pre = tmp_path / "preclean"
    pre.mkdir()
    inputs["provider_dim"].to_parquet(proc / "provider_dim.parquet", index=False)
    sv_fact(proc)
    build_provider_leads(inputs["provider_dim"]).to_parquet(tmp_path / "leads.parquet", index=False)
    r = subprocess.run([sys.executable, "-m", "src.model_a.provider_features_export",
                        "--graph-dir", str(tmp_path / "graph"), "--leads", str(tmp_path / "leads.parquet"),
                        "--processed", str(proc), "--preclean", str(pre), "--out", str(tmp_path / "out"),
                        "--no-resume-cache", "--no-servicing-rows", "--skip-sources", "servicing_mix"],
                       capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]),
                       env=dict(os.environ, PYTHONWARNINGS="ignore", OMP_NUM_THREADS="1"), timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "[servicing_mix] skipped: --skip-sources" in r.stdout
    mx = pd.read_parquet(tmp_path / "out" / "provider_features_for_model.parquet")
    assert not set(SRV_COLS) & set(mx.columns)
