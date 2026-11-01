"""test_lanes_mfcu_employer_2026_11.py — the two new research lanes (2026-11-01).

mfcu: a state-year sweep answer becomes case rows (one per defendant, the outcome word in
the summary, the agent's NPI kept as npi_in_source, a charge-only row usable=0).
employer: a convicted individual with no billing row -> the business that billed for
them; the gates hold (Luhn, NPPES organization, name agreement, the person must be one
we sent, the window must contain the record date) and the two output files carry the
affiliated schema + an exclusion row the ruler tiers from the individual's statute.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.model_a import employer_lane as el
from src.model_a import manus_lanes as ml
from src.model_a.fca_relevance import fca_tier

P1, P2, ORG_A, ORG_B, IND_C = "1003000100", "1003000415", "1003000407", "1003000522", "1003000209"


def _luhn_ok():
    from src.attempt_2.clean_data import is_valid_npi
    return all(is_valid_npi(n) for n in (P1, P2, ORG_A, ORG_B, IND_C))


class FakeService:
    def __init__(self):
        self.prompts, self.n = {}, 0

    def create(self, prompt):
        self.n += 1
        tid = f"t{self.n}"
        self.prompts[tid] = prompt
        return {"task_id": tid}

    def get(self, tid):
        p = self.prompts[tid]
        if "did NOT bill Medicaid under their own provider number" in p:
            out = ("```csv\nperson_npi,person_name,employer_name,employer_npi,employer_city,employer_state,employment_start,employment_end,role,confidence,evidence_url,note\n"
                   f"{P1},DOE JANE,Acme Home Care LLC,{ORG_A},Tampa,FL,2018,2020,nurse,high,https://justice.gov/doe,named in the release\n"
                   f"{P2},ROE JOHN,Beta Clinic PC,{IND_C},Miami,FL,2019,2020,biller,high,https://justice.gov/roe,the NPI is a person\n"
                   f"{P2},ROE JOHN,Gamma Pharmacy Inc,{ORG_B},Miami,FL,2019,2020,owner,low,https://justice.gov/roe2,low confidence\n"
                   f"9999999999,NOBODY,Acme Home Care LLC,{ORG_A},Tampa,FL,,,x,high,https://x,not ours\n```")
        elif "Medicaid Fraud Control Unit" in p:
            out = ("```csv\nannounced_date,defendant_name,defendant_type,city,state,npi,provider_type_or_specialty,scheme,amount_usd,outcome,source_url,docket_or_case_number,conduct_start_year,conduct_end_year\n"
                   f"2022-03-03,Sunrise Home Care LLC,organization,Tampa,FL,{ORG_A},home health,billing for services not rendered,400000,civil settlement,https://ag.fl/sunrise,,2019,2021\n"
                   "2022-04-04,Pending Person,individual,Miami,FL,,aide,services not rendered,,arrested,https://ag.fl/pending,,,\n```")
        else:
            out = "nothing"
        return {"task_id": tid, "status": "completed", "output": out, "attachments": []}


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    for d in ("enforcement", "processed", "graph/nodes"):
        (root / d).mkdir(parents=True)
    pd.DataFrame([{"case_id": "X1", "announced_date": "2020-01-01", "defendant_name": "Old Case",
                   "defendant_name_key": "OLD CASE", "sector": "", "scheme": "", "amount_usd": "", "qui_tam": "",
                   "intervened": "", "jurisdiction": "", "source_url": "https://justice.gov/old", "case_type": "civil_fca",
                   "summary": "settled."}]).to_csv(root / "enforcement" / "doj_cases_v3.csv", index=False)
    pd.DataFrame([
        {"node_id": "exclusion:0", "npi": P1, "entity_name": "DOE, JANE", "excl_type": "1128a1", "excl_date": "2020-06-01", "state": "FL"},
        {"node_id": "exclusion:1", "npi": P2, "entity_name": "ROE, JOHN", "excl_type": "1128a1", "excl_date": "2021-02-01", "state": "FL"},
        {"node_id": "exclusion:2", "npi": ORG_A, "entity_name": "ACME HOME CARE LLC", "excl_type": "1128b7", "excl_date": "2021-02-01", "state": "FL"},
        {"node_id": "exclusion:3", "npi": IND_C, "entity_name": "SMITH, BILLING", "excl_type": "1128a1", "excl_date": "2021-02-01", "state": "FL"},
    ]).to_parquet(root / "graph" / "nodes" / "exclusion_nodes.parquet", index=False)
    pd.DataFrame([
        {"npi": P1, "entity_type": "1", "org_legal_name": "", "provider_name": "DOE, JANE", "addr_city": "TAMPA"},
        {"npi": P2, "entity_type": "1", "org_legal_name": "", "provider_name": "ROE, JOHN", "addr_city": "MIAMI"},
        {"npi": ORG_A, "entity_type": "2", "org_legal_name": "ACME HOME CARE LLC", "provider_name": "", "addr_city": "TAMPA"},
        {"npi": ORG_B, "entity_type": "2", "org_legal_name": "GAMMA PHARMACY INC", "provider_name": "", "addr_city": "MIAMI"},
        {"npi": IND_C, "entity_type": "1", "org_legal_name": "", "provider_name": "SMITH, BILLING", "addr_city": "MIAMI"},
    ]).to_parquet(root / "processed" / "provider_dim.parquet", index=False)
    # SMITH billed under their own number; DOE and ROE never appear on any claim
    pd.DataFrame([{"billing_npi": IND_C, "servicing_npi": IND_C, "service_month": "2020-01", "total_paid": 100.0},
                  {"billing_npi": ORG_A, "servicing_npi": None, "service_month": "2020-01", "total_paid": 900.0}]
                 ).to_parquet(root / "processed" / "spending_fact.parquet", index=False)
    return root


def test_employer_candidates_are_the_unbilled_individuals(tmp_path):
    assert _luhn_ok()
    root = _root(tmp_path)
    cand = el.employer_candidates(pd.read_parquet(root / "graph/nodes/exclusion_nodes.parquet"),
                                  root / "processed/spending_fact.parquet",
                                  provider_dim=pd.read_parquet(root / "processed/provider_dim.parquet"))
    assert set(cand["person_npi"]) == {P1, P2}          # the org and the self-biller are out
    prompts = el.employer_prompts(cand, per_prompt=10)
    assert len(prompts) == 1 and set(prompts[0]["person_npis"]) == {P1, P2}
    assert "did NOT bill Medicaid under their own provider number" in prompts[0]["prompt"]


def test_employer_lane_gates_and_outputs(tmp_path):
    root = _root(tmp_path)
    svc = FakeService()
    ml.run_cycle(root, ["employer"], None, build=True, dispatch=True, poll=True, load=True, max_new=None,
                 total_budget=None, wait_minutes=0, poll_every=1, transport=svc, fetch=lambda u: "",
                 sleep=lambda s: None, log=lambda *_: None)
    research = pd.read_parquet(root / "processed" / "affiliated_research.parquet")
    assert list(research["npi"]) == [ORG_A] and list(research["excluded_npi"]) == [P1]
    assert research.iloc[0]["basis"] == "affiliated_research" and bool(research.iloc[0]["kept"])
    excl = pd.read_parquet(root / "processed" / "exclusions_employer_resolved.parquet")
    assert excl.iloc[0]["npi"] == ORG_A and excl.iloc[0]["excl_type"] == "affiliated:1128a1|research_employer"
    assert fca_tier(excl.iloc[0]["excl_type"])[0] == "hard"          # the individual's statute sets the tier
    q = pd.read_csv(root / "processed" / "manus_lanes" / "employer" / "employer_quarantine.csv", dtype=str)
    reasons = " ".join(q["drop_reason"])
    assert "individual, not an organization" in reasons and "below medium" in reasons and "not one we sent" in reasons
    assert len(q) == 3


def test_mfcu_lane_loads_case_rows_with_npi_and_outcome_gate(tmp_path):
    root = _root(tmp_path)
    svc = FakeService()
    ml.run_cycle(root, ["mfcu"], None, build=True, dispatch=True, poll=True, load=True, max_new=1,
                 total_budget=1, wait_minutes=0, poll_every=1, transport=svc, fetch=lambda u: "",
                 sleep=lambda s: None, log=lambda *_: None, states=["FL"], years=[2022])
    cdb = pd.read_csv(root / "enforcement" / "doj_cases_v3.csv", dtype=str).fillna("")
    sun = cdb[cdb["defendant_name"] == "Sunrise Home Care LLC"]
    assert len(sun) == 1 and sun.iloc[0]["npi_in_source"] == ORG_A and sun.iloc[0]["jurisdiction"] == "FL MFCU"
    assert sun.iloc[0]["case_id"].startswith("https://ag.fl/sunrise#") and "settlement" in sun.iloc[0]["summary"].lower()
    assert sun.iloc[0]["usable"] in ("1", "1.0", "True")
    pend = cdb[cdb["defendant_name"] == "Pending Person"]
    assert len(pend) == 1 and pend.iloc[0]["usable"] in ("0", "0.0", "False")
    rep = (root / "processed" / "manus_lanes" / "MANUS_LANES.md").read_text()
    assert re.search(r"\| mfcu \| 1 \| 1 \| 1 \|", rep)


def test_affiliated_prefix_and_field_source_tiers():
    assert fca_tier("affiliated:1128b7")[0] == "hard"
    assert fca_tier("affiliated:1128b14")[0] == "excluded"           # a loan default stays excluded through the prefix
    assert fca_tier("field:over_capacity")[0] == "hard"
    assert fca_tier("field:cleared")[0] == "excluded"
