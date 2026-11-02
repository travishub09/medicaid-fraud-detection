"""test_batch5_policy_features_2026_11.py — policy-aware features per billing NPI
(src/policy/features.py, batch 5b-F, 2026-11-02): the state's own parameter as the
ruler, NULL where the library has no rule, 0 within it, the excess above it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.policy import features as pf
from src.policy import registry as pr

QUOTE = ("Definitive drug testing is covered only when a presumptive test result is inconsistent with the "
         "clinical picture, up to twelve dates of service per year.")


def _row(state, topic, param, value, program="FFS", effective_from="2020-01-01", confidence="high"):
    return {"state": state, "program": program, "topic": topic, "param": param, "value": str(value),
            "value_num": pr._num(str(value)), "rule_text": QUOTE, "citation": "manual", "url": f"https://www.{state.lower()}.gov/x",
            "effective_from": effective_from, "effective_to": "", "confidence": confidence, "retrieved_at": "2026-11-01",
            "source_task": "t", "note": ""}


def _policy() -> pd.DataFrame:
    return pd.DataFrame([
        _row("NJ", "drug_testing", "max_definitive_per_year", 12),
        _row("NJ", "drug_testing", "requires_presumptive_first", "yes"),
        _row("OR", "drug_testing", "max_definitive_per_year", 12),
        _row("NJ", "perdiem_day_program", "second_shift_allowed", "yes"),
        _row("NJ", "perdiem_day_program", "max_days_per_week", 5),
        _row("NY", "perdiem_day_program", "census_may_exceed_license", "no"),
        _row("TX", "personal_care", "max_hours_per_day_per_beneficiary", 8),
        _row("TX", "personal_care", "unit_minutes", 15),
        _row("CA", "personal_care", "max_hours_per_day_per_beneficiary", 8),   # no unit_minutes row: the 15-minute default
        _row("NJ", "psychotherapy", "psy_60_requires_justification", "yes"),
        _row("NJ", "psychotherapy", "interactive_complexity_payable", "no"),
        # a rule effective AFTER a 2023-12 freeze: out under asof
        _row("FL", "drug_testing", "max_definitive_per_year", 12, effective_from="2025-01-01"),
    ], columns=pr.POLICY_COLS)


def _frame() -> pd.DataFrame:
    return pd.DataFrame([
        # NJ lab at 2.0 definitive per patient-month = 24 a year against 12 -> excess 1.0; reflex 3.0 -> 2.0
        {"npi": "1000000004", "practice_state": "NJ", "lab_def_lines_per_patient_month": 2.0, "lab_def_to_pres_ratio": 3.0},
        # NJ lab within the rule (0.5 x 12 = 6 <= 12) -> 0; reflex 0.25 -> 0
        {"npi": "1000000012", "practice_state": "NJ", "lab_def_lines_per_patient_month": 0.5, "lab_def_to_pres_ratio": 0.25},
        # GA has no rule in the library -> NULL
        {"npi": "1000000020", "practice_state": "GA", "lab_def_lines_per_patient_month": 5.0, "lab_def_to_pres_ratio": 9.0},
        # NJ provider with no lab observation -> NULL (absent, never 0)
        {"npi": "1000000038", "practice_state": "NJ", "lab_def_lines_per_patient_month": np.nan, "lab_def_to_pres_ratio": np.nan,
         "perdiem_lines_per_patient_month": 26.07, "occupancy_ratio": 2.6, "psy_60min_share": 0.9, "psy_interactive_share": 0.4},
        # NY day programme: the licence may not be exceeded -> occupancy 1.5 reads 0.5
        {"npi": "1000000046", "practice_state": "NY", "occupancy_ratio": 1.5},
        # TX personal care: 4 units/day x 15 min = 1 h/day: within 8 -> 0; CA: 40 units a day = 10 h -> 0.25
        {"npi": "1000000053", "practice_state": "TX", "pc_lines_per_patient_month": 4 * pf.DAYS_PER_MONTH},
        {"npi": "1000000061", "practice_state": "CA", "pc_lines_per_patient_month": 40 * pf.DAYS_PER_MONTH},
        # FL lab: the rule is effective 2025-01 -> NULL under asof 2023-12, scored current-day
        {"npi": "1000000079", "practice_state": "FL", "lab_def_lines_per_patient_month": 2.0},
        # a blank state -> NULL everywhere, rule_present 0
        {"npi": "1000000087", "practice_state": "", "lab_def_lines_per_patient_month": 9.0},
    ])


def test_excess_against_the_states_own_parameter():
    f = pf.policy_features(_policy(), _frame())
    assert len(f) == 9 and f["npi"].is_unique
    r = f.set_index("npi")
    c = "pol_drug_testing_max_definitive_per_year_excess"
    assert r.loc["1000000004", c] == pytest.approx(1.0)            # 24 a year / 12 - 1
    assert r.loc["1000000012", c] == 0.0                           # within the rule
    assert np.isnan(r.loc["1000000020", c])                        # GA: no rule in the library
    assert np.isnan(r.loc["1000000038", c])                        # no observation: absent, never 0
    assert np.isnan(r.loc["1000000087", c])                        # no state
    # the yes/no rule: NJ requires a presumptive first -> definitive over presumptive minus one
    rc = "pol_drug_testing_requires_presumptive_first_excess"
    assert r.loc["1000000004", rc] == pytest.approx(2.0) and r.loc["1000000012", rc] == 0.0
    assert np.isnan(r.loc["1000000020", rc])
    # rule_present per topic: 1 where the state has any numeric parameter
    p = "pol_drug_testing_rule_present"
    assert r.loc["1000000004", p] == 1 and r.loc["1000000020", p] == 0 and r.loc["1000000087", p] == 0
    assert r.loc["1000000046", "pol_perdiem_day_program_rule_present"] == 1
    assert r.loc["1000000046", "pol_drug_testing_rule_present"] == 0
    # every column numeric, none a label
    for col in f.columns:
        if col != "npi":
            assert pd.api.types.is_numeric_dtype(f[col]), col
            assert col.startswith("pol_")


def test_perdiem_personal_care_and_psychotherapy_kinds():
    r = pf.policy_features(_policy(), _frame()).set_index("npi")
    # NJ allows a second shift: occupancy 2.6 / 2 - 1 = 0.3 (over the building on any schedule)
    assert r.loc["1000000038", "pol_perdiem_day_program_second_shift_allowed_excess"] == pytest.approx(0.3)
    # NJ: 26.07 per-diem lines per patient-month / 4.345 = 6 days a week against 5 -> 0.2
    assert r.loc["1000000038", "pol_perdiem_day_program_max_days_per_week_excess"] == pytest.approx(0.2, abs=1e-3)
    # NY bars exceeding the licence: 1.5 -> 0.5; NJ has no census rule -> NULL on that column
    assert r.loc["1000000046", "pol_perdiem_day_program_census_may_exceed_license_excess"] == pytest.approx(0.5)
    assert np.isnan(r.loc["1000000038", "pol_perdiem_day_program_census_may_exceed_license_excess"])
    # personal care: TX 1 h/day within 8 -> 0; CA 10 h/day on the 15-minute default -> 0.25
    c = "pol_personal_care_max_hours_per_day_per_beneficiary_excess"
    assert r.loc["1000000053", c] == 0.0 and r.loc["1000000061", c] == pytest.approx(0.25)
    # psychotherapy: a justification rule reads the 60-minute share; a not-payable rule reads the add-on share
    assert r.loc["1000000038", "pol_psychotherapy_psy_60_requires_justification_excess"] == pytest.approx(0.9)
    assert r.loc["1000000038", "pol_psychotherapy_interactive_complexity_payable_excess"] == pytest.approx(0.4)
    # the parameter's unit_minutes row itself has no observed quantity: no excess column for it
    assert "pol_personal_care_unit_minutes_excess" not in r.columns


def test_asof_keeps_a_post_freeze_rule_out():
    cur = pf.policy_features(_policy(), _frame()).set_index("npi")
    frozen = pf.policy_features(_policy(), _frame(), asof="2023-12").set_index("npi")
    c = "pol_drug_testing_max_definitive_per_year_excess"
    assert cur.loc["1000000079", c] == pytest.approx(1.0)
    assert np.isnan(frozen.loc["1000000079", c]) and frozen.loc["1000000079", "pol_drug_testing_rule_present"] == 0
    assert frozen.loc["1000000004", c] == pytest.approx(1.0)       # the 2020 rule stays


def test_new_parameter_flows_in_from_topics_without_code(monkeypatch):
    """A parameter the lane learns tomorrow: registered on the topic, observed as obs_<param>."""
    topics = dict(pr.TOPICS)
    t = topics["nemt"]
    topics["nemt"] = pr.Topic(t.key, t.title, t.question, t.params + ("max_trips_per_week",), t.consumers)
    monkeypatch.setattr(pr, "TOPICS", topics)
    pol = pd.DataFrame([_row("NJ", "nemt", "max_trips_per_week", 6)], columns=pr.POLICY_COLS)
    frame = pd.DataFrame([{"npi": "1000000004", "practice_state": "NJ", "obs_max_trips_per_week": 9.0},
                          {"npi": "1000000012", "practice_state": "NJ", "obs_max_trips_per_week": 3.0}])
    f = pf.policy_features(pol, frame).set_index("npi")
    assert f.loc["1000000004", "pol_nemt_max_trips_per_week_excess"] == pytest.approx(0.5)
    assert f.loc["1000000012", "pol_nemt_max_trips_per_week_excess"] == 0.0
    assert "pol_nemt_max_trips_per_week_excess" in pf.feature_columns(frame)
    # every OBSERVED_RULES key is a parameter some topic declares (the registry owns the keys)
    declared = {p for t_ in pr.TOPICS.values() for p in t_.params}
    assert set(pf.OBSERVED_RULES) <= declared
    assert {r.kind for r in pf.OBSERVED_RULES.values()} <= set(pf.KINDS)


def test_empty_library_and_duplicate_npis():
    f = pf.policy_features(pd.DataFrame(columns=pr.POLICY_COLS), _frame())
    assert len(f) == 9 and (f.filter(like="_rule_present") == 0).all().all()
    assert f.filter(like="_excess").isna().all().all()
    with pytest.raises(AssertionError):
        pf.policy_features(_policy(), pd.concat([_frame(), _frame().head(1)]))
    with pytest.raises(ValueError):
        pf.policy_features(_policy(), _frame().drop(columns=["practice_state"]))


def test_observed_from_parquet_and_cli(tmp_path):
    rows = []

    def add(npi, code, month, paid, lines, pats):
        rows.append({"billing_npi": npi, "servicing_npi": npi, "hcpcs_code": code, "service_month": month,
                     "total_paid": float(paid), "total_claim_lines": float(lines), "total_patients": float(pats)})
    for m in ("2023-01", "2023-07", "2024-01"):
        add("1000000004", "G0483", m, 500_000, 750, 250)     # 3 definitive per patient-month
        add("1000000004", "80307", m, 10_000, 250, 250)      # reflex 3.0
        add("1000000012", "G0481", m, 400_000, 125, 125)     # 1.0 per patient-month, screens 4x
        add("1000000012", "80307", m, 40_000, 500, 500)
        add("1000000038", "S5102", m, 300_000, 2_600, 100)   # 26 per-diem days per patient-month
        add("1000000038", "90837", m, 50_000, 90, 30)
        add("1000000038", "90834", m, 5_000, 10, 10)
        add("1000000053", "T1019", m, 100_000, 4 * 30.4 * 50, 50)
    fact = tmp_path / "spending_fact.parquet"
    pd.DataFrame(rows).to_parquet(fact, index=False)
    obs = pf.observed_from_parquet(fact)
    o = obs.set_index("npi")
    assert o.loc["1000000004", "lab_def_lines_per_patient_month"] == pytest.approx(3.0)
    assert o.loc["1000000004", "lab_def_to_pres_ratio"] == pytest.approx(3.0)
    assert o.loc["1000000012", "lab_def_to_pres_ratio"] == pytest.approx(0.25)
    assert o.loc["1000000038", "perdiem_lines_per_patient_month"] == pytest.approx(26.0)
    assert o.loc["1000000038", "psy_60min_share"] == pytest.approx(0.9)
    assert np.isnan(o.loc["1000000038", "lab_def_lines_per_patient_month"])
    assert o.loc["1000000053", "pc_lines_per_patient_month"] == pytest.approx(4 * 30.4)
    # the as-of filter drops 2024 (the same per-month numbers here, so the frame is identical)
    asof = pf.observed_from_parquet(fact, asof_cutoff="2023-12")
    assert len(asof) == len(obs)
    # the CLI: library + fact + provider_dim -> parquet + report
    root = tmp_path / "d"
    (root / "processed" / "policy").mkdir(parents=True)
    _policy().to_parquet(pr.policy_path(root), index=False)
    pd.DataFrame([{"npi": n, "addr_state": s} for n, s in
                  (("1000000004", "NJ"), ("1000000012", "NJ"), ("1000000038", "NJ"), ("1000000053", "TX"))]) \
        .to_parquet(root / "processed" / "provider_dim.parquet", index=False)
    out = tmp_path / "pol" / "policy_features.parquet"
    r = subprocess.run([sys.executable, "-m", "src.policy.features", "--data-root", str(root), "--fact", str(fact),
                        "--out", str(out), "--asof", "2023-12"], capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode == 0, r.stderr
    f = pd.read_parquet(out).set_index("npi")
    assert f.loc["1000000004", "pol_drug_testing_max_definitive_per_year_excess"] == pytest.approx(2.0)   # 36 / 12 - 1
    assert f.loc["1000000012", "pol_drug_testing_max_definitive_per_year_excess"] == 0.0
    assert f.loc["1000000038", "pol_perdiem_day_program_max_days_per_week_excess"] == pytest.approx(26.0 / pf.WEEKS_PER_MONTH / 5 - 1, abs=1e-3)
    assert f.loc["1000000053", "pol_personal_care_max_hours_per_day_per_beneficiary_excess"] == 0.0
    md = (out.parent / "POLICY_FEATURES.md").read_text()
    assert "max_definitive_per_year" in md and "no observed quantity" in md and "scored" in md
    assert (out.parent / "policy_features.json").exists()
