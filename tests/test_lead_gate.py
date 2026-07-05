"""
test_lead_gate.py — the scheme-aware recovery gate (docs/OUTPUT_METHODOLOGY.md).

The case that motivated the design: an orchestrator with tiny OWN billing but
huge INFLUENCED dollars must pass; a blanket own-billing threshold would have
deleted them.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.model_a.lead_gate import influenced_dollars, apply_recovery_gate


def test_influenced_dollars_combines_components():
    dme = pd.DataFrame({"npi": ["1000000001"], "total_allowed": [2_000_000.0]})
    partd = pd.DataFrame({"npi": ["1000000001", "1000000002"],
                          "total_cost": [4_000_000.0, 1_000_000.0]})
    kick = pd.DataFrame({"npi": ["1000000001", "1000000002"],
                         "op_payment_utilization_corr": [0.5, 0.0]})
    out = influenced_dollars(dme, partd, kick).set_index("npi")
    # 2M ordered DME + 0.5 × 4M induced drugs = 4M
    assert out.loc["1000000001", "influenced_dollars"] == 4_000_000.0
    assert out.loc["1000000002", "influenced_dollars"] == 0.0
    # skip-missing: nothing available → empty, never zero-filled
    assert influenced_dollars().empty


def test_orchestrator_passes_on_influenced_not_own_billing():
    leads = pd.DataFrame({
        "npi": ["1000000001", "1000000002"],
        "org_node_id": ["org:a", "org:b"],
        "net_paid": [40_000.0, 45_000.0],          # both tiny own billing
        "subscore_dme_ring": [0.9, None],           # orchestrator evidence
        "subscore_upcoding": [None, 0.9],           # own-billing-only evidence
    })
    infl = pd.DataFrame({"npi": ["1000000001"],
                         "influenced_dollars": [20_000_000.0]})
    out = apply_recovery_gate(leads, org_payments=None, influenced=infl,
                              threshold=5_000_000.0)
    a, b = out.set_index("npi").loc["1000000001"], out.set_index("npi").loc["1000000002"]
    # the $136M-nurse shape: passes via influenced dollars (20M × 0.40 = 8M ≥ 5M)
    assert bool(a["passes_gate"]) and a["gate_basis"] == "influenced_dollars:dme_ring"
    # same own billing, upcoding-only evidence: 45K own dollars can't carry a case
    assert not bool(b["passes_gate"])


def test_ring_aggregate_and_own_billing_bases():
    leads = pd.DataFrame({
        "npi": ["1000000003", "1000000004"],
        "org_node_id": ["org:ring", "org:big"],
        "net_paid": [8_000.0, 30_000_000.0],
        "subscore_ownership_integrity": [0.8, None],
        "subscore_upcoding": [None, 0.8],
    })
    org_pay = pd.DataFrame({"org_node_id": ["org:ring"],
                            "payments": [40_000_000.0]})
    out = apply_recovery_gate(leads, org_payments=org_pay,
                              threshold=5_000_000.0).set_index("npi")
    # tiny member of a $40M ring passes at the RING grain
    assert bool(out.loc["1000000003", "passes_gate"])
    assert out.loc["1000000003", "gate_basis"].startswith("ring_aggregate:")
    # big own-biller passes on own billing via the net_paid fallback
    assert bool(out.loc["1000000004", "passes_gate"])
    assert out.loc["1000000004", "gate_basis"].startswith("own_billing:")


def test_gate_never_reranks_and_no_evidence_fails_closed():
    leads = pd.DataFrame({
        "npi": [f"10000000{i:02d}" for i in range(5)],
        "org_node_id": ["org:x"] * 5,
        "net_paid": [9e9] * 5,                      # huge dollars but...
    })                                               # ...no scheme evidence
    out = apply_recovery_gate(leads)
    assert list(out["npi"]) == list(leads["npi"])   # order preserved
    assert not out["passes_gate"].any()             # fails closed
    assert (out["gate_basis"] == "").all()
    assert out["expected_recovery"].isna().all()
