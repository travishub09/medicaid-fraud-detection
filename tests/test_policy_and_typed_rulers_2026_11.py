"""test_policy_and_typed_rulers_2026_11.py — the state policy library, the policy lane, the
scheme-typed rulers in scheme_health, the screens' per-state policy bar, and the
scheme-alternatives harness (2026-11-01, the operator: "let's do all of this").
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a import manus_lanes as ml
from src.model_a import scheme_alternatives as sa
from src.model_a.scheme_health import (SCHEME_CASE_TYPE, audit_scheme_health, load_typed_rulers, scheme_case_type)
from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS
from src.policy import registry as pr

sys.path.insert(0, str(Path(__file__).parent))
from test_batch_one_schemes_2026_10 import _fact as _batch_fact            # noqa: E402
from test_theory_screens_2026_10 import _fact as _screen_fact              # noqa: E402


# --------------------------------------------------------------------------- the policy library
def test_policy_prompts_and_gates():
    ps = pr.policy_prompts(["NJ"], ["drug_testing", "perdiem_day_program"])
    assert len(ps) == 2 and "New Jersey" in ps[0]["prompt"] and ps[0]["topic"] == "drug_testing"
    assert "max_definitive_per_year" in ps[0]["prompt"]
    quote = "Definitive drug testing is covered only when a presumptive test result is inconsistent with the clinical picture, up to twelve dates of service per year."
    t = pd.DataFrame([
        {"state": "NJ", "program": "FFS", "topic": "drug_testing", "param": "max_definitive_per_year", "value": "12",
         "rule_text": quote, "citation": "Newsletter 30-12", "url": "https://www.njmmis.com/documents/newsletter.pdf",
         "effective_from": "2020-07-01", "effective_to": "", "confidence": "high", "note": ""},
        {"state": "NJ", "program": "FFS", "topic": "drug_testing", "param": "requires_presumptive_first", "value": "yes",
         "rule_text": quote, "citation": "N.J.A.C. 10:61-3.5", "url": "https://www.state.nj.us/humanservices/dmahs/info/resources/manuals/",
         "effective_from": "2020-07", "effective_to": "", "confidence": "medium", "note": ""},
        {"state": "NJ", "program": "FFS", "topic": "drug_testing", "param": "max_definitive_per_year", "value": "4",
         "rule_text": quote, "citation": "a blog", "url": "https://labconsultant.example.com/nj-rules",
         "effective_from": "", "effective_to": "", "confidence": "high", "note": ""},
        {"state": "NJ", "program": "FFS", "topic": "drug_testing", "param": "max_definitive_per_year", "value": "6",
         "rule_text": "see manual", "citation": "x", "url": "https://www.nj.gov/x", "effective_from": "", "effective_to": "", "confidence": "high", "note": ""},
        {"state": "NJ", "program": "FFS", "topic": "drug_testing", "param": "frequency_cap", "value": "6",
         "rule_text": quote, "citation": "x", "url": "https://www.nj.gov/x", "effective_from": "", "effective_to": "", "confidence": "high", "note": ""},
        {"state": "NY", "program": "FFS", "topic": "drug_testing", "param": "max_definitive_per_year", "value": "6",
         "rule_text": quote, "citation": "x", "url": "https://www.health.ny.gov/x", "effective_from": "", "effective_to": "", "confidence": "high", "note": ""},
        {"state": "NJ", "program": "FFS", "topic": "drug_testing", "param": "max_definitive_per_year", "value": "6",
         "rule_text": quote, "citation": "x", "url": "https://www.nj.gov/x", "effective_from": "not a date", "effective_to": "", "confidence": "high", "note": ""},
    ])
    ok, bad = pr.load_policy_rows(t, "NJ", "drug_testing", task_id="policy:x")
    assert len(ok) == 2 and set(ok["param"]) == {"max_definitive_per_year", "requires_presumptive_first"}
    assert ok.iloc[0]["value_num"] == 12.0 and ok.iloc[1]["value_num"] == 1.0 and ok.iloc[1]["effective_from"] == "2020-07-01"
    reasons = " | ".join(bad["drop_reason"])
    assert "not on a government" in reasons and "quotation is required" in reasons and "not one of the topic's keys" in reasons
    assert "not the state asked" in reasons and "does not parse" in reasons and len(bad) == 5


def test_policy_store_param_text_context_and_report(tmp_path):
    root = tmp_path / "d"
    quote = "Definitive drug testing is covered only on a discrepant presumptive result, up to twelve dates of service per year."
    rows = pd.DataFrame([
        {"state": "OR", "program": "FFS", "topic": "drug_testing", "param": "max_definitive_per_year", "value": "12", "value_num": 12.0,
         "rule_text": quote, "citation": "D23", "url": "https://www.oregon.gov/oha/x.pdf", "effective_from": "2019-01-01", "effective_to": "",
         "confidence": "high", "retrieved_at": "2026-11-01", "source_task": "t", "note": ""},
        {"state": "OR", "program": "FFS", "topic": "drug_testing", "param": "max_definitive_per_year", "value": "24", "value_num": 24.0,
         "rule_text": quote + " (older)", "citation": "D22", "url": "https://www.oregon.gov/oha/y.pdf", "effective_from": "2016-01-01", "effective_to": "2018-12-31",
         "confidence": "high", "retrieved_at": "2026-11-01", "source_task": "t", "note": ""},
    ])
    p = pr.policy_path(root)
    assert pr.append_rows(p, rows) == 2 and pr.append_rows(p, rows) == 0                     # idempotent
    pr._CACHE.clear()
    df = pr.load_policy(root)
    assert pr.policy_param(df, "OR", "drug_testing", "max_definitive_per_year") == 12.0      # the newest effective row
    assert pr.policy_param(df, "OR", "drug_testing", "max_definitive_per_year", asof="2017-06-01") == 24.0
    assert np.isnan(pr.policy_param(df, "WA", "drug_testing", "max_definitive_per_year"))
    ctx = pr.policy_context(df, "OR", ["drug_testing", "nemt"])
    assert "twelve dates" in ctx and "D23" in ctx and "no rule on file" in ctx
    rep = pr.report(df)
    assert "| OR |" in rep and "max_definitive_per_year" in rep
    r = subprocess.run([sys.executable, "-m", "src.policy.registry", "--data-root", str(root), "--report", "--state", "OR", "--topic", "drug_testing"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "STATE RULES ON FILE" in r.stdout and (root / "processed" / "policy" / "STATE_POLICY.md").exists()


class _PolicyService:
    def __init__(self):
        self.prompts, self.n = {}, 0

    def create(self, prompt):
        self.n += 1
        self.prompts[f"p{self.n}"] = prompt
        return {"task_id": f"p{self.n}"}

    def get(self, tid):
        p = self.prompts[tid]
        if "Medicaid billing policy" in p and "perdiem_day_program" in p:
            out = ("```csv\nstate,program,topic,param,value,rule_text,citation,url,effective_from,effective_to,confidence,note\n"
                   "NJ,FFS,perdiem_day_program,perdiem_min_hours,5,\"The daily rate requires a minimum of five hours of service excluding transportation, no more than five days a week.\",N.J.A.C. 10:164,https://www.nj.gov/humanservices/dmahs/x.pdf,2015-01-01,,high,\n"
                   "NJ,FFS,perdiem_day_program,second_shift_allowed,yes,\"A proposal must specify more than one shift and the hours of each; capacity may not be exceeded at any time.\",N.J.A.C. 8:43F-2.6,https://www.nj.gov/health/x,2010-01-01,,high,\n"
                   "NJ,FFS,perdiem_day_program,census_may_exceed_license,no,short,x,https://blog.example.com,,,low,\n```")
        else:
            out = "no table"
        return {"task_id": tid, "status": "completed", "output": out, "attachments": []}


def test_policy_lane_builds_dispatches_and_loads_through_the_gates(tmp_path):
    root = tmp_path / "data"
    (root / "processed").mkdir(parents=True)
    (root / "enforcement").mkdir()
    svc = _PolicyService()
    ml.run_cycle(root, ["policy"], None, build=True, dispatch=True, poll=True, load=True, max_new=None,
                 total_budget=None, wait_minutes=0, poll_every=1, transport=svc, fetch=lambda u: "",
                 sleep=lambda s: None, log=lambda *_: None, states=["NJ"])
    # one task per PARAMETERISED topic for the one state (audit_findings is a loader-only topic:
    # the state_audits lane fills it, the policy lane never asks for it)
    assert svc.n == len([t for t in pr.TOPICS.values() if t.params])
    assert svc.n == len(pr.TOPICS) - 1
    df = pd.read_parquet(root / "processed" / "policy" / "state_policy.parquet")
    assert len(df) == 2 and set(df["param"]) == {"perdiem_min_hours", "second_shift_allowed"}
    assert pr.policy_param(df, "NJ", "perdiem_day_program", "perdiem_min_hours") == 5.0
    q = pd.read_csv(root / "processed" / "manus_lanes" / "policy" / "policy_quarantine.csv", dtype=str)
    assert len(q) == 1 and "quotation" in q.iloc[0]["drop_reason"]
    assert (root / "processed" / "policy" / "STATE_POLICY.md").exists()
    rep = (root / "processed" / "manus_lanes" / "MANUS_LANES.md").read_text()
    assert "| policy |" in rep


# --------------------------------------------------------------------------- typed rulers
def test_every_scheme_has_a_case_type_and_typed_rulers_grade_their_own_scheme(tmp_path):
    missing = [s for s in DEFAULT_SCHEME_WEIGHTS if not scheme_case_type(s)]
    assert not missing, f"schemes without a case type: {missing}"
    assert set(SCHEME_CASE_TYPE.values()) <= {"kickback", "upcoding", "medical_necessity", "phantom_billing",
                                              "eligibility", "worthless_services", "pill_mill"}
    rng = np.random.default_rng(3)
    n = 500
    m = pd.DataFrame({"npi": [f"{1000000000 + i}" for i in range(n)],
                      "shell_score": rng.uniform(0, 1, n), "within_2_hops_of_exclusion": rng.integers(0, 2, n),
                      "related_party_density_norm": rng.uniform(0, 1, n),
                      "subscore_ownership_integrity": rng.uniform(0, 1, n),
                      "em_high_level_share__peerpct": rng.uniform(0, 1, n), "em_level_mean__peerpct": rng.uniform(0, 1, n),
                      "subscore_upcoding": rng.uniform(0, 1, n)})
    up = (rng.uniform(0, 1, n) < 0.02 + 0.6 * m["subscore_upcoding"].to_numpy()).astype(int)      # upcoding cases follow upcoding
    kb = (rng.uniform(0, 1, n) < 0.05).astype(int)                                                 # kickback cases are noise for it
    run = tmp_path / "run"
    run.mkdir()
    for key, y in (("upcoding", up), ("kickback", kb)):
        pd.DataFrame({"npi": m["npi"], "is_prospective_positive": y, "was_excluded_pre_cutoff": 0}).to_csv(
            run / f"future_bans_after_2023-12__typed_{key}33.csv", index=False)
    typed = load_typed_rulers(run, "2023-12")
    assert set(typed) == {"upcoding", "kickback"}
    fca = pd.DataFrame({"npi": m["npi"], "is_prospective_positive": kb, "was_excluded_pre_cutoff": 0})   # the whole ruler = the noise
    df = audit_scheme_health(m, forward=fca, typed_forward=typed).set_index("scheme")
    r = df.loc["upcoding"]
    assert r["case_type"] == "upcoding" and r["typed_auc"] >= 0.6 and r["ruler_used"] == "typed:upcoding"
    assert r["ruler_auc"] == r["typed_auc"] and r["fca_auc"] < r["typed_auc"]                  # own cases vs the whole ruler
    oi = df.loc["ownership_integrity"]
    assert oi["case_type"] == "kickback" and oi["ruler_used"] == "typed:kickback"


def test_accuracy_diagnostics_never_grades_a_typed_ruler(tmp_path):
    from src.model_a.accuracy_diagnostics import _discover_rulers
    run = tmp_path / "r"
    run.mkdir()
    for name in ("future_bans_after_2023-12__fca33.csv", "future_bans_after_2023-12__typed_upcoding33.csv",
                 "future_bans_after_2023-12__state33.csv"):
        (run / name).write_text("npi,is_prospective_positive\n", encoding="utf-8")
    keys = set(_discover_rulers(run, "2023-12"))
    assert "state" in keys and "fca_file" in keys and not any(k.startswith("typed") for k in keys)


# --------------------------------------------------------------------------- the screens read the library
def test_screens_carry_the_state_policy_bar(tmp_path):
    from src.model_a.theory_screens import SCREENS, run_screen, to_markdown
    fact, dim = _screen_fact(tmp_path)
    quote = "Definitive drug testing is covered up to twelve dates of service per calendar year on a documented order."
    pol = pd.DataFrame([{"state": "FL", "program": "FFS", "topic": "drug_testing", "param": "max_definitive_per_year", "value": "12",
                         "value_num": 12.0, "rule_text": quote, "citation": "Lab Handbook 2.3", "url": "https://ahca.myflorida.com/x.pdf",
                         "effective_from": "2019-01-01", "effective_to": "", "confidence": "high", "retrieved_at": "2026-11-01",
                         "source_task": "t", "note": ""}])
    df = run_screen(fact, SCREENS["definitive_reflex"], provider_dim=dim, policy_df=pol)
    alpha = df[df["npi"] == "1000000001"].iloc[0]                       # FL, 3.0 definitive per patient-month = 36 a year
    assert alpha["policy_bar"] == 12.0 and bool(alpha["over_policy"]) is True
    beta = df[df["npi"] == "1000000002"].iloc[0]                        # NJ: no rule on file
    assert pd.isna(beta["policy_bar"]) and pd.isna(beta["over_policy"])
    md = to_markdown({"definitive_reflex": df}, policy_df=pol)
    assert "FL rule on file" in md and "Lab Handbook 2.3" in md and "policy_bar" in md


# --------------------------------------------------------------------------- the alternatives harness
def test_scheme_alternatives_compute_and_grade(tmp_path):
    fact = _batch_fact(tmp_path)
    matrix = pd.DataFrame({"npi": ["1003000126", "1003000134", "1003000142", "1003000159", "1003000167"],
                           "practice_state": ["FL"] * 5, "primary_taxonomy": ["373H00000X", "251E00000X", "291U00000X", "103T00000X", "207Q00000X"],
                           "subscore_evv_personal_care": [0.9, 0.1, 0.1, 0.1, 0.1],
                           "subscore_upcoding": [0.2, 0.2, 0.2, 0.8, 0.3]})
    alt = sa.compute_alternatives(fact, matrix=matrix).set_index("npi")
    assert alt.loc["1003000126", "alt_evv_personal_care__pc_units_per_pm"] == 200.0          # 800 lines / 4 patient-months
    assert alt.loc["1003000159", "alt_upcoding__em_high_share_mcd"] == 1.0                   # 99214 only
    assert abs(alt.loc["1003000159", "alt_session_inflation__psy60_share_mcd"] - 0.75) < 1e-9  # 30 of 40
    assert alt.loc["1003000126", "alt_evv_personal_care__pc_peak_per_day"] > 12               # 400 lines / 31 days
    assert alt.loc["1003000142", "alt_lab_unbundling__component_per_draw"] > 1.0
    assert 0 < alt.loc["1003000126", "alt_single_service_mill__top_code_share"] <= 1.0
    assert "alt_evv_personal_care__peer_state_tax" in alt.columns
    rulers = {"fca": pd.DataFrame({"npi": matrix["npi"], "is_prospective_positive": [1, 0, 0, 1, 0], "was_excluded_pre_cutoff": 0}),
              "typed_phantom_billing": pd.DataFrame({"npi": matrix["npi"], "is_prospective_positive": [1, 0, 0, 0, 0], "was_excluded_pre_cutoff": 0})}
    g = sa.grade(matrix, alt.reset_index(), rulers)
    assert {"current", "pc_units_per_pm"} <= set(g[g["scheme"] == "evv_personal_care"]["formula"])
    md = sa.to_markdown(g, ["fca", "typed_phantom_billing"])
    assert "## evv_personal_care" in md and "pc_units_per_pm" in md and "The read" in md


def test_policy_gate_reads_a_spelled_out_state_and_refuses_ambiguous_numbers():
    """2026-11-01: the first cycle refused 1,482 rows that said CALIFORNIA instead of CA;
    and "1:4_recommended" must not become the number 1."""
    assert pr.norm_state("California") == "CA" and pr.norm_state("ca") == "CA" and pr.norm_state("N.Y.") == "NY"
    t = pd.DataFrame([{"state": "CALIFORNIA", "topic": "drug_testing", "param": "max_definitive_per_year",
                       "value": "12", "rule_text": "x" * 60, "citation": "c", "url": "https://www.dhcs.ca.gov/x",
                       "effective_from": "2024-01-01", "confidence": "high"},
                      {"state": "Texas", "topic": "drug_testing", "param": "max_definitive_per_year",
                       "value": "12", "rule_text": "x" * 60, "citation": "c", "url": "https://www.dhcs.ca.gov/x",
                       "effective_from": "2024-01-01", "confidence": "high"}])
    ok, bad = pr.load_policy_rows(t, "CA", "drug_testing")
    assert len(ok) == 1 and ok.iloc[0]["state"] == "CA" and ok.iloc[0]["value_num"] == 12.0
    assert len(bad) == 1 and "TX is not the state asked" in bad.iloc[0]["drop_reason"]
    assert pr._num("1:4_recommended") != pr._num("1:4_recommended")      # NaN
    assert pr._num("5_onsite_10_offsite") != pr._num("5_onsite_10_offsite")
    assert pr._num("12 per year") == 12.0 and pr._num("yes") == 1.0
