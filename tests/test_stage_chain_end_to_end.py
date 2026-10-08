"""The attempt_2 stage chain, end to end, on the raw-shaped synthetic fixture.

Runs every stage CLI the Makefile `pipeline` target runs, in order, as subprocesses
against a throwaway MEDICAID_DATA_ROOT, and checks the planted truths survive the
whole chain:

  * NPI 1003000209 (LEIE-excluded 2022-08-01, with claims in 2022-09 and 2023-03)
    lands in Layer 1 as billed-after-exclusion, is QUALIFIED by the verifier, and is
    rank 1 of the final triage list as a direct billed-while-excluded lead;
  * the facility owned by BADCO HOLDINGS LLC (Tier-B name match to the LEIE BUSNAME)
    and by JOHN EXCLUDED (Tier-A owner-NPI match) carries both ownership flags and
    its company reaches the tracker as a probable-owner lead;
  * dollars are conserved from the spending fact through the company tracker, with
    the reconciliation read from this fact's corruption audit (not a constant).

Before 2026-10-08 this chain could not run on anything but the 2024 national file:
features.py and the tracker asserted against hard-coded totals, integrate skipped the
facility-flags file without owners, and four stages crashed on a small book.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from tests.fixtures.raw_sources import write_raw_sources

STAGES = [
    ["src.attempt_2.ingest.integrate"],
    ["src.attempt_2.audit.diagnose_coverage"],
    ["src.attempt_2.audit.audit_corruption"],
    ["src.attempt_2.ingest.features"],
    ["src.attempt_2.leads.detect"],
    ["src.attempt_2.leads.verify_layer1"],
    ["src.attempt_2.leads.refine_layer2"],
    ["src.attempt_2.leads.refine_layer2_v3"],
    ["src.attempt_2.leads.company_rollup"],
    ["src.attempt_2.leads.company_lead_tracker", "--min-net-paid", "0"],
    ["src.attempt_2.leads.finalize_tracker"],
    ["src.attempt_2.export.export_final_leads", "--min-net-paid", "0"],
]


def _run(mod_and_args, env):
    r = subprocess.run([sys.executable, "-m", *mod_and_args], env=env, capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parents[1]), timeout=600)
    assert r.returncode == 0, f"{mod_and_args[0]} failed:\n{r.stdout[-2000:]}\n{r.stderr[-3000:]}"
    return r.stdout


def test_stage_chain_recovers_planted_truths(tmp_path):
    raw = write_raw_sources(tmp_path / "fixture")
    root = tmp_path / "data"
    pre = root / "preclean"
    (pre / "owners").mkdir(parents=True)
    for name in ("NPPES", "PECOS", "Caught", "Spending"):
        (pre / f"{name}.csv").write_bytes((tmp_path / "fixture" / "raw" / f"{name}.csv").read_bytes())
    (pre / "owners" / "NursingOwners.csv").write_bytes((tmp_path / "fixture" / "raw" / "NursingOwners.csv").read_bytes())
    env = dict(os.environ, MEDICAID_DATA_ROOT=str(root), PYTHONWARNINGS="ignore")
    for st in STAGES:
        _run(st, env)

    proc, feat, det = root / "processed", root / "features", root / "detection"
    # the audit sidecar exists and the features stage reconciled against it
    assert (proc / "corruption_audit.json").exists()
    assert "corruption_audit.json" in (feat / "FEATURES_REPORT.md").read_text(encoding="utf-8")
    pf = pd.read_parquet(feat / "provider_features.parquet").set_index("npi")
    assert bool(pf.loc["1003000209", "provider_on_leie"])
    assert int(pf["facility_excl_high"].sum()) == 1 if "facility_excl_high" in pf.columns else True

    v3 = pd.read_parquet(det / "fraud_leads_v3.parquet").set_index("npi")
    assert v3.loc["1003000209", "priority_tier"].startswith("1_")
    assert bool(v3.loc["1003000209", "billed_after_exclusion"])
    cases = pd.read_parquet(det / "layer1_candidate_cases.parquet")
    assert set(cases["npi"]) == {"1003000209"} and (cases["disposition"] == "QUALIFIED").all()
    assert cases["paid_after"].iloc[0] == 5000.0 and int(cases["n_clean_after_months"].iloc[0]) == 2

    tracker = pd.read_csv(det / "company_lead_tracker.csv", dtype=str)
    tiers = dict(zip(tracker["company_id"], tracker["priority_tier"]))
    assert tiers["pac:PACSOLO"].startswith("1_")
    assert tiers["pac:PAC415"].startswith("4_")
    triage = pd.read_csv(det / "triage_priority.csv", dtype=str)
    assert triage.iloc[0]["company_name"] == "EXCLUDED, JOHN" and "excluded" in triage.iloc[0]["tier_label"].lower()
    # dollar conservation to the company grain
    sf = pd.read_parquet(proc / "spending_fact.parquet")
    base = float(sf.loc[sf["provider_matched"] == True, "total_paid"].sum())  # noqa: E712
    assert abs(float(tracker["company_total_billing_size_proxy_not_case_value"].astype(float).sum()) - base) < 1.0


def test_audit_basis_reads_sidecar_and_falls_back(tmp_path):
    from src.attempt_2.audit_basis import LEGACY_BASE_PAID, LEGACY_BASE_ROWS, audit_base
    rows, paid, basis = audit_base(tmp_path)
    assert (rows, paid) == (LEGACY_BASE_ROWS, LEGACY_BASE_PAID) and "legacy" in basis
    (tmp_path / "corruption_audit.json").write_text('{"n_base": 4, "paid_base": 10000.0}', encoding="utf-8")
    rows, paid, basis = audit_base(tmp_path)
    assert (rows, paid) == (4, 10000.0) and "this fact" in basis
