"""defendant_research: tiered batch selection, prompt file, and folding Manus answers back."""
from __future__ import annotations

import json

import pandas as pd

from src.enforcement.defendant_research import fold_results, parse_answer, select_batch, write_prompts


def _residue():
    rows = [
        # tier 1: ambiguous person / small org
        dict(case_id="c1", defendant="Miguel Gonzalez", kind="person", city="", state="FL", status="ambiguous", n_candidates=8, amount_usd="4200000"),
        dict(case_id="c2", defendant="Foot Care Store, Inc.", kind="org", city="", state="FL", status="ambiguous", n_candidates=2, amount_usd="5538338"),
        # tier 2: unmatched above the floor
        dict(case_id="c3", defendant="Philip Esformes", kind="person", city="", state="FL", status="unmatched", n_candidates=0, amount_usd="1300000000"),
        dict(case_id="c3", defendant="Philip Esformes", kind="person", city="", state="FL", status="unmatched", n_candidates=0, amount_usd="1300000000"),  # dup
        dict(case_id="c4", defendant="Thomley", kind="org", city="", state="MS", status="unmatched", n_candidates=0, amount_usd="189000000"),          # fragment
        dict(case_id="c5", defendant="Pharmacist of A to Z Pharmacy", kind="person", city="", state="FL", status="unmatched", n_candidates=0, amount_usd="157000000"),
        dict(case_id="c6", defendant="Tenet Healthcare Corporation", kind="org", city="", state="GA", status="unmatched", n_candidates=0, amount_usd="513000000"),
        dict(case_id="c7", defendant="Fort Lauderdale, Florida", kind="person", city="", state="FL", status="unmatched", n_candidates=0, amount_usd="97000000"),
        dict(case_id="c8", defendant="Small Clinic LLC", kind="org", city="", state="TX", status="unmatched", n_candidates=0, amount_usd="9000000"),   # not SE
        dict(case_id="c9", defendant="Tiny Lab LLC", kind="org", city="", state="GA", status="unmatched", n_candidates=0, amount_usd="500000"),      # below floor
        dict(case_id="c10", defendant="Walgreens", kind="org", city="", state="FL", status="chain_org", n_candidates=35, amount_usd="300000000"),
    ]
    for r in rows:
        r.update(announced_date="2025-01-01", scheme="", mention="primary_defendant", npi="", match_level="", basis="resolved_defendant")
    return pd.DataFrame(rows)


def test_select_batch_tiers_and_hygiene():
    b = select_batch(_residue(), (1, 2), 2_000_000.0, ["FL", "GA", "MS"])
    assert b["defendant"].tolist() == ["Foot Care Store, Inc.", "Miguel Gonzalez", "Philip Esformes"]
    assert b["tier"].tolist() == [1, 1, 2]
    assert b["org_id"].str.startswith("dfd:").all() and b["org_id"].is_unique
    only1 = select_batch(_residue(), (1,), 2_000_000.0, ["FL"])
    assert only1["tier"].eq(1).all() and len(only1) == 2
    capped = select_batch(_residue(), (1, 2), 2_000_000.0, ["FL", "GA", "MS"], cap=2)
    assert len(capped) == 2 and capped["tier"].eq(1).all()


def test_prompts_file_and_fold_back(tmp_path):
    res = _residue()
    b = select_batch(res, (1, 2), 2_000_000.0, ["FL"])
    pj, pc = write_prompts(b, tmp_path)
    lines = [json.loads(l) for l in pj.read_text(encoding="utf-8").splitlines()]
    assert {"org_id", "org_name", "prompt", "case_id", "defendant", "state", "tier"} <= set(lines[0])
    assert "npiregistry" in lines[0]["prompt"] and "Do not contact anyone" in lines[0]["prompt"]
    assert pc.exists()
    oid = {l["defendant"]: l["org_id"] for l in lines}
    results = {
        oid["Miguel Gonzalez"]: {"status": "completed", "task_id": "t1",
                                 "output": 'Found it. {"npi": "1234567893", "entity_type": "individual", "confidence": "high", "evidence": "docket"}'},
        oid["Foot Care Store, Inc."]: {"status": "completed", "task_id": "t2",
                                       "output": '{"npi": "1245319599", "confidence": "low"}'},
        oid["Philip Esformes"]: {"status": "completed", "task_id": "t3",
                                 "output": '{"npi": null, "confidence": "high", "evidence": "owner, not a clinician; no NPI"}'},
    }
    out, summ = fold_results(res, pj, results)
    assert summ == {"folded_resolved": 1, "low_confidence": 1, "no_npi": 1}
    g = out[out["defendant"] == "Miguel Gonzalez"].iloc[0]
    assert g["npi"] == "1234567893" and g["status"] == "resolved" and g["match_level"] == "research_high" and g["task_id"] == "t1"
    assert out[out["defendant"] == "Foot Care Store, Inc."]["status"].iloc[0] == "research_low"
    assert (out[out["defendant"] == "Philip Esformes"]["status"] == "research_no_npi").all()
    assert out[out["defendant"] == "Walgreens"]["status"].iloc[0] == "chain_org"     # untouched


def test_parse_answer_rejects_bad_npis():
    assert parse_answer('{"npi": "1234567890", "confidence": "high"}')["npi"] == ""      # fails Luhn
    assert parse_answer("The NPI is 1234567893 (confidence: high).") == {"npi": "1234567893", "confidence": "high", "entity_type": ""}
    assert parse_answer("Two NPIs 1234567893 and 1245319599 could match; confidence low")["npi"] == ""
