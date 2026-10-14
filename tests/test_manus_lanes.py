"""
test_manus_lanes.py — the unattended research cycle (2026-10-14).

A fake research service answers each lane's prompt with a table; the cycle builds,
dispatches, polls and LOADS through the same loaders a human review would call, and
the machine gates hold: an NPI outside our candidate list never lands, a charged-only
row never becomes a case, a dismissal clears the charge, and the report says what
moved.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from src.model_a import manus_lanes as ml
from src.model_a.manus_lanes import tables_from_answer

NPI_A, NPI_B, NPI_C = "1003000100", "1003000415", "1003000407"       # Luhn-valid (fixture NPIs)


class FakeService:
    """create() returns ids; get() answers from the prompt text."""

    def __init__(self):
        self.prompts: dict[str, str] = {}
        self.n = 0

    def create(self, prompt):
        self.n += 1
        tid = f"task{self.n}"
        self.prompts[tid] = prompt
        return {"task_id": tid}

    def get(self, tid):
        p = self.prompts[tid]
        if "excluded from federal health programs" in p:
            sid = re.search(r"\[([^\]]+)\]", p).group(1)
            out = ("Here is the CSV:\n```csv\nsource_node_id,entity_name,state,npi,confidence,employer_name,employer_npi,evidence_url\n"
                   f"{sid},JANE DOE,FL,{NPI_A},high,,,https://oig/x\n"
                   f"{sid},JANE DOE,FL,{NPI_C},high,,,https://oig/y\n```")
        elif "holds only the CHARGE" in p:
            cid = re.search(r"\[([^\]]+)\]", p).group(1)
            out = ("| case_id | defendant_name | outcome | outcome_date | amount_usd | jurisdiction | evidence_url | outcome_summary |\n"
                   "|---|---|---|---|---|---|---|---|\n"
                   f"| {cid} | Acme Home Health | pleaded_guilty | 2022-02-02 | 250000 | M.D. Fla. | https://justice.gov/ch1-plea | The owner pleaded guilty. |\n")
        elif "COMMERCIAL health plans" in p:
            out = ("```csv\nannounced_date,defendant_name,defendant_type,city,state,npi,provider_type_or_specialty,scheme,amount_usd,outcome,source_url,payer_type,outcome_date\n"
                   f"2021-05-05,Sunrise Chiropractic PC,organization,Tampa,FL,{NPI_B},chiropractic,billing for services not rendered,120000,pleaded guilty,https://ag.fl/x,commercial,2021-05-05\n"
                   "2021-06-06,Dr Pending,individual,Miami,FL,,dentist,upcoding,,charged,https://ag.fl/y,commercial,\n```")
        elif "Corporate Integrity Agreement" in p:
            out = ("CSV A\n```csv\nannounced_date,defendant_name,defendant_type,city,state,npi,provider_type_or_specialty,scheme,amount_usd,outcome,source_url,docket_or_case_number,conduct_start_year,conduct_end_year,qui_tam,relator_role,conduct_summary\n"
                   "2022-03-03,Big Lab Holdings,organization,Austin,TX,,laboratory,kickbacks,5000000,civil settlement,https://justice.gov/biglab,,2018,2021,yes,former employee,paid kickbacks for referrals\n"
                   "2022-04-04,Charged Clinic,organization,Dallas,TX,,clinic,upcoding,,charged,https://justice.gov/cc,,,,,,\n```\n"
                   "CSV B\n```csv\nparty,enrolled_entity_name,city,state,npi,evidence_url\n"
                   f"Big Lab Holdings,Big Lab of Austin LLC,Austin,TX,{NPI_B},https://npiregistry/x\n"
                   "Big Lab Holdings,Big Lab Unknown,Austin,TX,,https://npiregistry/y\n```")
        else:
            out = "no table"
        return {"task_id": tid, "status": "completed", "output": out, "attachments": []}


def _data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    (root / "enforcement").mkdir(parents=True)
    (root / "processed").mkdir()
    pd.DataFrame([
        {"case_id": "CH1", "announced_date": "2021-03-01", "defendant_name": "Acme Home Health",
         "defendant_name_key": "ACME HOME HEALTH", "sector": "home_health", "scheme": "upcoding", "amount_usd": "",
         "qui_tam": "", "intervened": "", "jurisdiction": "M.D. Fla.", "source_url": "https://justice.gov/ch1",
         "case_type": "criminal_hcf", "summary": "The owner was indicted on charges of health care fraud."},
    ]).to_csv(root / "enforcement" / "doj_cases_v3.csv", index=False)
    pd.DataFrame([{"source_node_id": "exclusion:7", "entity_name": "DOE, JANE", "state": "FL",
                   "excl_type": "1128a1", "excl_date": "2019-05-01", "candidate_npis": f"{NPI_A};{NPI_B}",
                   "n_candidates": 2, "status": "ambiguous"}]).to_csv(root / "processed" / "exclusion_name_residue.csv", index=False)
    return root


def test_tables_from_answer_reads_fenced_pipe_and_attachments():
    out = ("```csv\na,b,c\n1,2,3\n```\n\n| x | y |\n|---|---|\n| 1 | 2 |\n")
    atts = [{"filename": "answer.csv", "content_type": "text/csv", "url": "https://cdn/a.csv"}]
    tabs = tables_from_answer(out, atts, fetch=lambda url: "p,q\n7,8\n")
    assert [list(t.columns) for t in tabs] == [["p", "q"], ["a", "b", "c"], ["x", "y"]]
    assert tables_from_answer("prose only, no table") == []


def test_cycle_builds_dispatches_polls_and_loads_with_the_gates(tmp_path):
    root = _data_root(tmp_path)
    svc = FakeService()
    lanes = ["names", "outcome_refresh", "commercial"]
    res = ml.run_cycle(root, lanes, None, build=True, dispatch=True, poll=True, load=True, max_new=None,
                       total_budget=None, wait_minutes=0, poll_every=1, transport=svc, fetch=lambda u: "",
                       sleep=lambda s: None, log=lambda *_: None, states=["FL"], years=[2021])
    assert res["dispatched"] == 3                                  # 1 names + 1 outcome + 1 commercial prompt
    # names: the agent chose NPI_A (a candidate) and NPI_C (NOT a candidate): only NPI_A lands
    nr = pd.read_parquet(root / "processed" / "exclusions_name_resolved.parquet")
    assert list(nr["npi"]) == [NPI_A] and nr.iloc[0]["match_level"] == "research_high"
    # outcome refresh: the plea became its own case row (keyed by the evidence URL)
    cdb = pd.read_csv(root / "enforcement" / "doj_cases_v3.csv", dtype=str)
    assert "https://justice.gov/ch1-plea" in set(cdb["case_id"])
    # commercial: the conviction loaded as hcf_commercial, the charge quarantined
    assert (cdb["case_type"] == "hcf_commercial").sum() == 1
    q = pd.read_csv(root / "processed" / "manus_lanes" / "commercial" / "commercial_quarantine.csv", dtype=str)
    assert len(q) == 1 and "Dr Pending" in q.iloc[0]["defendant_name"]
    # the report
    rep = (root / "processed" / "manus_lanes" / "MANUS_LANES.md").read_text(encoding="utf-8")
    assert "| names | 1 | 1 | 1 | 0 | 1 |" in rep and "| commercial | 1 | 1 | 1 | 0 | 1 | 1 |" in rep
    st = {r["lane"]: r for r in json.loads((root / "processed" / "manus_lanes" / "manus_lanes.json").read_text())}
    assert st["outcome_refresh"]["loaded"] == 1
    # loading again is idempotent
    ml.load_lane("names", root, log=lambda *_: None)
    assert len(pd.read_parquet(root / "processed" / "exclusions_name_resolved.parquet")) == 1


def test_cia_lane_loads_settlement_rows_and_entities(tmp_path):
    root = _data_root(tmp_path)
    svc = FakeService()
    files = ml.lane_files(root, "cia")
    ml._write_jsonl([{"org_id": "harv:cia:1", "org_name": "CIA parties batch 1", "set": "cia",
                      "prompt": "Each party below signed an OIG Corporate Integrity Agreement ..."}], files["prompts"])
    ml.dispatch_lane("cia", root, transport=svc, log=lambda *_: None)
    ml.poll_lane("cia", root, transport=svc, log=lambda *_: None)
    t = ml.load_lane("cia", root, fetch=lambda u: "", log=lambda *_: None)
    cdb = pd.read_csv(root / "enforcement" / "doj_cases_v3.csv", dtype=str)
    row = cdb.set_index("case_id").loc["https://justice.gov/biglab"]
    assert row["case_type"] == "civil_fca" and row["qui_tam"] == "1" and "civil settlement" in row["summary"]
    assert "Charged Clinic" not in set(cdb["defendant_name"])          # charged-only rows never become cases
    ents = pd.read_parquet(root / "processed" / "cia_defendant_npis.parquet")
    assert list(ents["npi"]) == [NPI_B] and ents.iloc[0]["match_level"] == "research_cia"
    assert t["loaded"] == 2 and t["quarantined"] == 2                   # 1 charged row + 1 entity without an NPI


def test_budget_caps_dispatch_across_lanes(tmp_path):
    root = _data_root(tmp_path)
    svc = FakeService()
    res = ml.run_cycle(root, ["commercial"], None, build=True, dispatch=True, poll=False, load=False, max_new=None,
                       total_budget=3, wait_minutes=0, poll_every=1, transport=svc, sleep=lambda s: None,
                       log=lambda *_: None, states=["FL", "GA"], years=[2020, 2021, 2022])
    assert res["dispatched"] == 3 and ml.lane_status("commercial", root)["prompts"] == 6
