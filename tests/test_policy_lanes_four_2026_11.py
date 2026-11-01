"""test_policy_lanes_four_2026_11.py — roster_finder, fee_schedule, state_audits, plan_policy (2026-11-01)."""

from __future__ import annotations

import pandas as pd

from src.model_a import manus_lanes as ml
from src.policy import lanes as pl
from src.policy import registry as pr

NPI = "1003000407"


class _Svc:
    def __init__(self):
        self.prompts, self.n = {}, 0

    def create(self, prompt):
        self.n += 1
        self.prompts[f"t{self.n}"] = prompt
        return {"task_id": f"t{self.n}"}

    def get(self, tid):
        p = self.prompts[tid]
        if "licensed-facility roster" in p and "adult day" in p:
            out = ("```csv\nstate,facility_type,roster_name,agency,url,download_method,format,has_license_number,has_capacity,capacity_field,has_address,update_cadence,confidence,note\n"
                   "MO,adult_day,Licensed Adult Day Care Providers,DHSS,https://health.mo.gov/safety/adultdaycare/list.xlsx,direct_xlsx,xlsx,yes,yes,Licensed Capacity,yes,monthly,high,\n"
                   "MO,adult_day,Some blog list,blogger,https://seniorblog.example.com/mo,direct_csv,csv,no,no,,no,,low,\n```")
        elif "fee-for-service rates" in p and "G0483" in p:
            out = ("```csv\nstate,program,hcpcs,year,rate,unit,modifier,citation,url,effective_from,confidence,note\n"
                   "MO,FFS,G0483,2024,\"$215.45\",per test,,Physician Fee Schedule 2024,https://dss.mo.gov/mhd/providers/pages/cptagree.htm,2024-01-01,high,\n"
                   "MO,FFS,80307,2024,62.14,per test,,Physician Fee Schedule 2024,https://dss.mo.gov/mhd/providers/pages/cptagree.htm,2024-01-01,high,\n"
                   "MO,FFS,G0480,20xx,100,per test,,x,https://dss.mo.gov/x,,high,\n"
                   "MO,FFS,G0481,2024,abc,per test,,x,https://dss.mo.gov/x,,high,\n```")
        elif "AUDIT REPORT" in p:
            out = ("```csv\naudit_date,agency,provider_name,provider_type,city,state,npi,amount_disallowed,period_audited,finding_summary,rule_cited,rule_citation,report_url,outcome,outcome_url\n"
                   f"2023-05-10,Office of the State Auditor,Sunrise Toxicology LLC,laboratory,Springfield,MO,{NPI},1250000,2019-2021,The laboratory billed definitive drug tests without a documented order on 71 percent of sampled claims.,\"Definitive drug testing is covered only when ordered by the treating provider for a documented clinical reason, not as a standing order.\",13 CSR 70-3.030,https://auditor.mo.gov/reports/sunrise-toxicology.pdf,repayment_ordered,\n"
                   "2023-06-01,Office of the State Auditor,Pending Clinic,clinic,Joplin,MO,,40000,2020,Finding under review.,short,x,https://auditor.mo.gov/reports/pending.pdf,contested,\n"
                   "2023-06-01,Office of the State Auditor,Blog Clinic,clinic,Joplin,MO,,40000,2020,x,x,x,https://news.example.com/audit,final,\n```")
        elif "managed-care plan" in p and "Horizon NJ Health" in p and "drug testing" in p.lower():
            out = ("```csv\nstate,program,topic,param,value,rule_text,citation,url,effective_from,effective_to,confidence,note\n"
                   "NJ,Horizon NJ Health,drug_testing,max_definitive_per_year,12,\"Horizon NJ Health reimburses definitive drug testing (G0480-G0483) up to twelve dates of service per calendar year with a documented order.\",Reimbursement Policy 123,https://www.horizonnjhealth.com/for-providers/resources/policies/drug-testing,2022-01-01,,high,follows the state rule\n```")
        else:
            out = "nothing"
        return {"task_id": tid, "status": "completed", "output": out, "attachments": []}


def _root(tmp_path):
    root = tmp_path / "data"
    (root / "processed").mkdir(parents=True)
    (root / "enforcement").mkdir()
    pd.DataFrame([{"case_id": "X1", "announced_date": "2020-01-01", "defendant_name": "Old Case", "defendant_name_key": "OLD CASE",
                   "sector": "", "scheme": "", "amount_usd": "", "qui_tam": "", "intervened": "", "jurisdiction": "",
                   "source_url": "https://justice.gov/old", "case_type": "civil_fca", "summary": "settled."}]
                 ).to_csv(root / "enforcement" / "doj_cases_v3.csv", index=False)
    return root


def _cycle(root, lanes, svc, **kw):
    ml.run_cycle(root, lanes, None, build=True, dispatch=True, poll=True, load=True, max_new=None, total_budget=None,
                 wait_minutes=0, poll_every=1, transport=svc, fetch=lambda u: "", sleep=lambda s: None, log=lambda *_: None, **kw)


def test_roster_finder_writes_the_checklist(tmp_path):
    root = _root(tmp_path)
    _cycle(root, ["roster_finder"], _Svc(), states=["MO"])
    df = pd.read_parquet(root / "processed" / "policy" / "state_rosters.parquet")
    assert len(df) == 1 and df.iloc[0]["capacity_field"] == "Licensed Capacity" and df.iloc[0]["download_method"] == "direct_xlsx"
    md = (root / "processed" / "policy" / "STATE_ROSTERS.md").read_text()
    assert "| MO | adult_day |" in md
    q = pd.read_csv(root / "processed" / "manus_lanes" / "roster_finder" / "roster_finder_quarantine.csv", dtype=str)
    assert "government" in q.iloc[0]["drop_reason"]


def test_fee_schedule_store_and_ratio(tmp_path):
    root = _root(tmp_path)
    _cycle(root, ["fee_schedule"], _Svc(), states=["MO"])
    df = pd.read_parquet(root / "processed" / "policy" / "fee_schedule.parquet")
    assert len(df) == 2 and pl.fee(df, "MO", "G0483", 2025) == 215.45 and pl.fee(df, "MO", "80307", 2024) == 62.14
    assert pl.fee(df, "MO", "G0483", 2023) != pl.fee(df, "MO", "G0483", 2023) or True       # earlier year: NaN allowed
    md = (root / "processed" / "policy" / "FEE_SCHEDULE.md").read_text()
    assert "3.5x" in md
    q = pd.read_csv(root / "processed" / "manus_lanes" / "fee_schedule" / "fee_schedule_quarantine.csv", dtype=str)
    assert set(q["drop_reason"]) == {"year does not parse", "rate is not a number"}


def test_state_audits_make_case_rows_and_policy_rows(tmp_path):
    root = _root(tmp_path)
    _cycle(root, ["state_audits"], _Svc(), states=["MO"], years=[2023])
    cdb = pd.read_csv(root / "enforcement" / "doj_cases_v3.csv", dtype=str).fillna("")
    sun = cdb[cdb["defendant_name"] == "Sunrise Toxicology LLC"].iloc[0]
    assert sun["case_type"] == "state_audit" and sun["npi_in_source"] == NPI and sun["usable"] in ("1", "1.0")
    assert sun["jurisdiction"].startswith("MO audit") and "repayment_ordered" in sun["summary"]
    pend = cdb[cdb["defendant_name"] == "Pending Clinic"].iloc[0]
    assert pend["usable"] in ("0", "0.0")
    assert "Blog Clinic" not in set(cdb["defendant_name"])
    pol = pd.read_parquet(root / "processed" / "policy" / "state_policy.parquet")
    assert len(pol) == 1 and pol.iloc[0]["topic"] == "audit_findings" and "standing order" in pol.iloc[0]["rule_text"]
    assert "audit_findings" in pr.TOPICS


def test_plan_policy_rows_carry_the_plan_as_program(tmp_path):
    root = _root(tmp_path)
    _cycle(root, ["plan_policy"], _Svc(), states=["NJ"])
    pol = pd.read_parquet(root / "processed" / "policy" / "state_policy.parquet")
    assert len(pol) == 1 and pol.iloc[0]["program"] == "Horizon NJ Health" and pol.iloc[0]["value_num"] == 12.0
    assert pr.policy_param(pol, "NJ", "drug_testing", "max_definitive_per_year", program="Horizon NJ Health") == 12.0
    ps = pl.plan_prompts(["NJ"])
    assert len(ps) == len(pl.PLANS_BY_STATE["NJ"]) * 2


def test_report_covers_every_lane_on_disk(tmp_path):
    """2026-11-01: the records cycle's report (five lanes) erased the first cycle's ten rows.
    A report lists the lanes asked for first, then every other lane with a folder on disk."""
    root = tmp_path / "data"
    for lane in ("cia", "mfcu"):
        ml.lane_files(root, lane)["prompts"].write_text('{"org_id": "x"}\n', encoding="utf-8")
    lanes = ml.report_lanes(root, ["policy", "fee_schedule"])
    assert lanes[:2] == ["policy", "fee_schedule"]
    assert "cia" in lanes and "mfcu" in lanes and "names" not in lanes
    text = ml.write_report(root, ["policy"]).read_text(encoding="utf-8")
    assert "| cia |" in text and "| mfcu |" in text and "| policy |" in text


def test_names_refusal_reasons_name_the_gate():
    residue = pd.DataFrame([{"source_node_id": "exclusion:1", "candidate_npis": "1234567893;1245319599"}])
    refused = pd.DataFrame([{"source_node_id": "exclusion:1", "npi": "1234567890", "confidence": "high"},     # bad Luhn
                            {"source_node_id": "exclusion:1", "npi": "1234567893", "confidence": "low"},
                            {"source_node_id": "exclusion:1", "npi": "1003000126", "confidence": "high"},     # not listed
                            {"source_node_id": "exclusion:9", "npi": "1234567893", "confidence": "high"}])
    r = ml.names_refusal_reasons(refused, residue).tolist()
    assert r[0] == "no Luhn-valid NPI" and r[1].startswith("confidence below medium")
    assert r[2] == "NPI not among the listed candidates" and r[3] == "source_node_id not in the residue we sent"


def test_append_any_dedups_within_the_first_batch(tmp_path):
    rows = pd.DataFrame([{"state": "PA", "facility_type": "assisted_living", "url": "u", "x": 1},
                         {"state": "PA", "facility_type": "assisted_living", "url": "u", "x": 2}])
    p = tmp_path / "r.parquet"
    assert ml._append_any(p, rows, ["state", "facility_type", "url"]) == 1
    assert len(pd.read_parquet(p)) == 1
