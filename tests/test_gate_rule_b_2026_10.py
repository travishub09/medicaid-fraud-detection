"""Gate rule b (operator 2026-10-30): beside a pre-registered bar, the network verdict,
the ablation lift CI and the lift-delta drop are advisory. v27 passed all seventeen
declared checks and was still stamped HOLD by two gates no run can pass."""
from src.model_a.run_registry import recommend, to_markdown


def _row(**kw):
    r = {"tag": "v27", "run_dir": "/x/frozen_2023-12_v27", "asof_cutoff": "2023-12",
         "label_policy": "hard", "expectation_fails": 0,
         "network_verdict": "NO MEASURABLE SIGNAL: the network family do not move the matched metric",
         "ablation_lift_lo": -0.14, "ablation_lift_delta": 0.29, "acc_org_hits100": 3,
         "decision": None}
    r.update(kw)
    return r


def test_advisory_gates_do_not_block_a_run_that_meets_its_declared_bar():
    prereg = [{"tag": "v27", "goal": "g", "expected": "p", "checks": ["hits100 >= 2"]}]
    rec = recommend([_row()], prereg)
    assert rec["decision"] == "PROMOTE" and rec["champion"] == "v27"
    assert any("network verdict" in a for a in rec["advisories"])
    assert any("ablation lift CI" in a for a in rec["advisories"])
    assert rec["reasons"] == []
    md = to_markdown([_row(decision="PROMOTE")], rec, prereg)
    assert "Advisory (gate rule b" in md and "network verdict" in md


def test_a_failed_declared_check_still_holds():
    prereg = [{"tag": "v27", "goal": "g", "expected": "p", "checks": ["hits100 >= 4"]}]
    rec = recommend([_row()], prereg)
    assert rec["decision"] == "HOLD"
    assert any("pre-registered check failed" in r for r in rec["reasons"])


def test_expectation_fails_still_block_under_rule_b():
    prereg = [{"tag": "v27", "goal": "g", "expected": "p", "checks": ["hits100 >= 2"]}]
    rec = recommend([_row(expectation_fails=2)], prereg)
    assert rec["decision"] == "HOLD" and any("expectation FAIL" in r for r in rec["reasons"])


def test_without_a_declared_bar_the_gates_still_decide():
    rec = recommend([_row()])
    assert rec["decision"] == "HOLD"
    assert any("network verdict" in r for r in rec["reasons"]) and rec["advisories"] == []
