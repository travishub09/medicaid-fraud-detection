"""The rendering-clinician grain (model_a/servicing_grain.py) and its wiring into the
export: a servicing NPI that renders under several billing organizations gets its own
stats, the phantom-rendering shape is measured, a rendering NPI with no billing row
becomes a matrix row with row_grain='servicing', and the export's real (non-fixture)
main path carries all of it through to subscore_rendering_identity."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.entity_graph.__main__ import run as run_graph
from src.model_a.provider_features_export import build_provider_matrix
from src.model_a.servicing_grain import SV_COLS, servicing_only_rows, servicing_stats
from tests.fixtures.synthetic import build_provider_leads, build_synthetic_inputs

ORG_A, ORG_B, ORG_C = "1003000506", "1003000514", "1003000415"   # billing orgs in the fixture
DOC = "1003000209"                                                # renders for all three, no billing row
SOLO = "1003000407"                                               # bills and renders for itself only


def _fact(tmp_path: Path) -> Path:
    rows = []
    for m in ["2023-01", "2023-02", "2023-03"]:
        for bn in (ORG_A, ORG_B, ORG_C):
            rows.append(dict(billing_npi=bn, servicing_npi=DOC, hcpcs_code="99213", service_month=m,
                             total_patients=40.0, total_claim_lines=400.0, total_paid=20000.0, provider_state="TX"))
        rows.append(dict(billing_npi=ORG_A, servicing_npi=ORG_A, hcpcs_code="99214", service_month=m,
                         total_patients=10.0, total_claim_lines=10.0, total_paid=1000.0, provider_state="TX"))
        rows.append(dict(billing_npi=SOLO, servicing_npi=SOLO, hcpcs_code="99213", service_month=m,
                         total_patients=5.0, total_claim_lines=6.0, total_paid=300.0, provider_state="TX"))
    rows.append(dict(billing_npi=ORG_A, servicing_npi=DOC, hcpcs_code="99213", service_month="2024-06",
                     total_patients=1.0, total_claim_lines=1.0, total_paid=50.0, provider_state="TX"))
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


def test_servicing_stats_measure_the_phantom_rendering_shape(tmp_path):
    sp = _fact(tmp_path)
    s = servicing_stats(sp).set_index("npi")
    assert list(s.columns) == SV_COLS
    assert s.loc[DOC, "sv_n_billing_orgs"] == 3 and s.loc[DOC, "sv_max_orgs_same_month"] == 3
    assert s.loc[DOC, "sv_peak_lines_month"] == 1200.0                 # three orgs x 400 lines in one month
    assert abs(s.loc[DOC, "sv_share_under_others"] - 1.0) < 1e-9        # never bills for itself
    assert abs(s.loc[DOC, "sv_top_billing_share"] - (60050.0 / 180050.0)) < 1e-9
    assert s.loc[DOC, "sv_active_months"] == 4 and s.loc[DOC, "sv_tenure_months"] == 18
    assert s.loc[SOLO, "sv_n_billing_orgs"] == 1 and s.loc[SOLO, "sv_share_under_others"] == 0.0
    # as-of cutoff drops the 2024 line
    s2 = servicing_stats(sp, cutoff="2024-01").set_index("npi")
    assert s2.loc[DOC, "sv_active_months"] == 3 and s2.loc[DOC, "sv_last_month"] == "2023-03"


def test_servicing_only_rows_carry_identifiers_stats_and_label(tmp_path):
    sp = _fact(tmp_path)
    s = servicing_stats(sp)
    pdim = build_synthetic_inputs()["provider_dim"]
    rows = servicing_only_rows(s, billing_npis={ORG_A, ORG_B, ORG_C, SOLO}, provider_dim=pdim,
                               positive_npis={DOC}).set_index("npi")
    assert list(rows.index) == [DOC]
    r = rows.loc[DOC]
    assert r["row_grain"] == "servicing" and r["provider_on_leie"] == 1
    assert r["net_paid"] == 180050.0 and r["n_active_months"] == 4 and r["n_distinct_hcpcs"] == 1
    assert r["primary_taxonomy"] and r["practice_state"]
    # an NPI absent from provider_dim never becomes a row
    s2 = pd.concat([s, pd.DataFrame([dict(zip(["npi"] + SV_COLS, ["1999999999"] + [1.0] * len(SV_COLS)))])])
    assert "1999999999" not in set(servicing_only_rows(s2, set(), pdim)["npi"])


def test_matrix_scores_rendering_identity_and_keeps_grain_as_identifier(tmp_path):
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    leads = build_provider_leads(inputs["provider_dim"])
    leads = leads[leads["npi"] != DOC].copy()                        # DOC has no billing row
    leads["row_grain"] = "billing"
    sp = _fact(tmp_path)
    sv = servicing_stats(sp)
    extra = servicing_only_rows(sv, set(leads["npi"]), inputs["provider_dim"], positive_npis={DOC})
    leads = pd.concat([leads, extra], ignore_index=True)
    matrix, manifest = build_provider_matrix(leads, out["npi_to_org"],
                                             adapter_npi_frames={"servicing": sv}, min_peer=2)
    m = matrix.set_index("npi")
    assert m.loc[DOC, "row_grain"] == "servicing" and (m["row_grain"] == "billing").sum() == 13
    assert "row_grain" in manifest["identifier_cols"]
    fam = set(manifest["raw_feature_cols"] + manifest["peerpct_cols"] + manifest["subscore_cols"])
    assert "row_grain" not in fam
    assert "subscore_rendering_identity" in matrix.columns
    assert m["subscore_rendering_identity"].idxmax() == DOC               # the planted phantom renderer
    assert "sv_max_orgs_same_month__peerpct" in matrix.columns
    assert "subscore_impossible_day" in matrix.columns                    # sv_peak_lines_month now feeds it
    assert "servicing" in manifest["sources_used"]


def test_export_main_path_adds_servicing_rows(tmp_path):
    """The real main() path (not --fixture): graph dir + leads + processed with a spending
    fact and provider_dim. The rendering-only NPI must appear in the written matrix."""
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    proc = tmp_path / "processed"
    proc.mkdir()
    pre = tmp_path / "preclean"
    pre.mkdir()
    inputs["provider_dim"].to_parquet(proc / "provider_dim.parquet", index=False)
    _fact(proc)
    leads = build_provider_leads(inputs["provider_dim"])
    leads = leads[leads["npi"] != DOC]
    leads.to_parquet(tmp_path / "leads.parquet", index=False)
    r = subprocess.run([sys.executable, "-m", "src.model_a.provider_features_export",
                        "--graph-dir", str(tmp_path / "graph"), "--leads", str(tmp_path / "leads.parquet"),
                        "--processed", str(proc), "--preclean", str(pre), "--out", str(tmp_path / "out"),
                        "--no-resume-cache"],
                       capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]),
                       env=dict(os.environ, PYTHONWARNINGS="ignore"), timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "[servicing]" in r.stdout and "1 servicing-only rows added" in r.stdout
    mx = pd.read_parquet(tmp_path / "out" / "provider_features_for_model.parquet").set_index("npi")
    assert DOC in mx.index and mx.loc[DOC, "row_grain"] == "servicing"
    man = json.loads((tmp_path / "out" / "feature_manifest.json").read_text())
    assert any(rec.get("source") == "servicing" for rec in man.get("sources_audit", [])) or "servicing" in man["sources_used"]


def test_shares_stay_in_unit_interval_with_refund_rows(tmp_path):
    """v8 (2026-10-10): refund / recoupment rows carry negative total_paid and pushed
    sv_top_billing_share and sv_share_under_others outside [0, 1] (EXPECTATIONS FAILs).
    Shares are built on positive dollars only and clipped; the paid totals stay net."""
    rows = []
    for m in ["2023-01", "2023-02"]:
        rows.append(dict(billing_npi=ORG_A, servicing_npi=DOC, hcpcs_code="99213", service_month=m,
                         total_patients=10.0, total_claim_lines=10.0, total_paid=1000.0, provider_state="TX"))
        rows.append(dict(billing_npi=ORG_B, servicing_npi=DOC, hcpcs_code="99213", service_month=m,
                         total_patients=10.0, total_claim_lines=10.0, total_paid=-900.0, provider_state="TX"))
        rows.append(dict(billing_npi=DOC, servicing_npi=DOC, hcpcs_code="99213", service_month=m,
                         total_patients=1.0, total_claim_lines=1.0, total_paid=100.0, provider_state="TX"))
    p = tmp_path / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    s = servicing_stats(p).set_index("npi").loc[DOC]
    assert s["sv_paid_total"] == 400.0                                     # net stays net
    assert s["sv_paid_under_others"] == 200.0
    assert 0.0 <= s["sv_top_billing_share"] <= 1.0 and 0.0 <= s["sv_share_under_others"] <= 1.0
    assert abs(s["sv_top_billing_share"] - 2000.0 / 2200.0) < 1e-9          # gross dollars: 2000 of 2200
    assert abs(s["sv_share_under_others"] - 2000.0 / 2200.0) < 1e-9


def test_export_no_servicing_rows_keeps_features_and_drops_rows(tmp_path):
    """--no-servicing-rows (v9): the sv_* features are computed for every rendering NPI
    but the universe is the billing-NPI universe (v8 added 843,551 servicing-only rows)."""
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    proc = tmp_path / "processed"
    proc.mkdir()
    pre = tmp_path / "preclean"
    pre.mkdir()
    inputs["provider_dim"].to_parquet(proc / "provider_dim.parquet", index=False)
    _fact(proc)
    leads = build_provider_leads(inputs["provider_dim"])
    leads = leads[leads["npi"] != DOC]
    leads.to_parquet(tmp_path / "leads.parquet", index=False)
    r = subprocess.run([sys.executable, "-m", "src.model_a.provider_features_export",
                        "--graph-dir", str(tmp_path / "graph"), "--leads", str(tmp_path / "leads.parquet"),
                        "--processed", str(proc), "--preclean", str(pre), "--out", str(tmp_path / "out"),
                        "--no-resume-cache", "--no-servicing-rows"],
                       capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]),
                       env=dict(os.environ, PYTHONWARNINGS="ignore"), timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "NO servicing-only rows added" in r.stdout
    assert "[doj_case] skipped: no --case-db" in r.stdout                  # the v8 runner bug, now loud
    mx = pd.read_parquet(tmp_path / "out" / "provider_features_for_model.parquet").set_index("npi")
    assert DOC not in mx.index and (mx["row_grain"] == "billing").all()
    assert "sv_n_billing_orgs" in mx.columns and mx.loc[ORG_A, "sv_n_billing_orgs"] == 1
    man = json.loads((tmp_path / "out" / "feature_manifest.json").read_text())
    assert "servicing" in man["sources_used"]


def test_constant_and_empty_trainable_columns_are_dropped_and_recorded(tmp_path):
    """v8 / v9 / v10 shipped constant graph motifs and all-NaN opioid shares as trainable
    columns (an EXPECTATIONS FAIL each run). The matrix drops them and the manifest
    records them so the ledger still names the input that came out empty."""
    inputs = build_synthetic_inputs()
    out = run_graph(inputs, tmp_path / "graph")
    leads = build_provider_leads(inputs["provider_dim"])
    npis = leads["npi"].astype(str).tolist()
    dead = pd.DataFrame({"npi": npis, "px_dead_share": [np.nan] * len(npis),
                         "px_flat_count": [1.0] * len(npis), "px_live_share": np.linspace(0, 1, len(npis))})
    matrix, manifest = build_provider_matrix(leads, out["npi_to_org"],
                                             adapter_npi_frames={"medicaid_ndc": dead}, min_peer=2)
    assert "px_dead_share" in matrix.columns and "px_flat_count" in matrix.columns   # kept for readers
    assert "px_live_share" in matrix.columns and "px_live_share" in manifest["raw_feature_cols"]
    assert "px_flat_count" not in manifest["raw_feature_cols"]                          # never trained on
    assert set(manifest["dropped_constant"]) >= {"px_dead_share", "px_flat_count"}
    assert "px_dead_share" not in manifest["raw_feature_cols"]
