"""label_verification: batches, prompt shape, verdict parsing, fold-back and the tally."""
from __future__ import annotations

import json

import pandas as pd

from src.model_a.label_verification import build_prompts, fold_results, parse_verdicts, report, write_prompts


def _sample(n=23):
    return pd.DataFrame([{"npi": str(1000000000 + i), "defendant": f"Person {i}" if i % 3 else "", "label_tier": "hcf_criminal" if i % 3 else "excl_statute",
                          "state": "FL", "announced_date": "2024-01-01", "source_url": f"https://www.justice.gov/usao-mdfl/pr/{i}",
                          "case_id": f"c{i}"} for i in range(n)])


def test_batches_and_prompt(tmp_path):
    ps = build_prompts(_sample(), batch_size=10, sample_name="s.csv")
    assert [p["batch"] for p in ps] == [1, 2, 3] and ps[2]["rows"] == [21, 22, 23]
    assert "row 1: npi=1000000000" in ps[0]["prompt"] and "npiregistry" in ps[0]["prompt"] and "Do not contact anyone" in ps[0]["prompt"]
    assert "(exclusion-list entry, no name)" in ps[0]["prompt"]
    assert len({p["org_id"] for p in ps}) == 3
    pj, pc = write_prompts(ps, tmp_path)
    assert len([l for l in pj.read_text().splitlines() if l.strip()]) == 3 and pc.exists()


def test_parse_fold_and_report():
    out = 'Here are the results:\n[{"row": 1, "npi": "1000000001", "verdict": "Confirmed", "evidence_url": "https://x", "note": "ok"},' \
          '{"row": 2, "npi": "1000000002", "verdict": "wrong npi", "evidence_url": "https://y", "note": "belongs to a dentist"},' \
          '{"row": 3, "npi": "1000000003", "verdict": "nonsense", "evidence_url": "", "note": ""}] thanks'
    v = parse_verdicts(out)
    assert [x["verdict"] for x in v] == ["confirmed", "wrong_npi", "cannot_verify"]
    assert parse_verdicts("no json here") == []
    sample = _sample(3)
    ps = build_prompts(sample, 10, "s.csv")
    rows = fold_results(sample, {ps[0]["org_id"]: {"status": "completed", "output": out}})
    assert rows["verdict"].tolist() == ["confirmed", "wrong_npi", "cannot_verify"]
    rep = report(rows)
    assert "confirmed: 1" in rep and "TIGHTEN" in rep and "row 2: wrong_npi" in rep
    pending = fold_results(sample, {})
    assert (pending["verdict"] == "pending").all() and "answered: 0" in report(pending)


def test_exclusion_row_wrong_person_against_the_blank_is_a_confirmed_identity():
    from src.model_a.label_verification import fold_results, build_prompts
    sample = pd.DataFrame([{"npi": "1871637223", "defendant": "", "label_tier": "excl_statute", "state": "NY", "announced_date": "2019-01-01",
                            "source_url": "https://oig.hhs.gov/exclusions/", "case_id": "leie:1871637223:1128a1"},
                           {"npi": "1437360799", "defendant": "Southern Respiratory LLC", "label_tier": "hcf_criminal", "state": "GA",
                            "announced_date": "2020-01-01", "source_url": "https://www.justice.gov/x", "case_id": "https://www.justice.gov/x"}])
    results = {"verify:abc": {"status": "completed", "output": (
        '[{"row": 1, "npi": "1871637223", "verdict": "wrong_person", "evidence_url": "u1", '
        '"note": "The official OIG LEIE record for NPI 1871637223 names SHAHEEN BEGUM and NPPES agrees; the defendant nan is not a party."},'
        ' {"row": 2, "npi": "1437360799", "verdict": "wrong_person", "evidence_url": "u2", '
        '"note": "The DOJ announcement names Detra Wiley Pate as the convicted party; the LLC is her company."}]')}}
    rows = fold_results(sample, results)
    assert rows.loc[0, "verdict"] == "confirmed" and rows.loc[0, "note"].startswith("[list identity")
    assert rows.loc[1, "verdict"] == "wrong_person"
    assert "exclusion-list row" in build_prompts(sample)[0]["prompt"]


def test_rescore_against_current_label_sets_dropped_rows_aside():
    from src.model_a.label_verification import rescore_against, report, build_prompts
    rows = pd.DataFrame([
        {"npi": "1", "case_id": "a", "defendant": "X", "label_tier": "hcf_criminal", "evidence_strength": "weak", "verdict": "wrong_npi", "evidence_url": "", "note": "", "row": 1},
        {"npi": "2", "case_id": "b", "defendant": "Y", "label_tier": "hcf_criminal", "evidence_strength": "weak", "verdict": "confirmed", "evidence_url": "", "note": "", "row": 2},
        {"npi": "3", "case_id": "c", "defendant": "Z", "label_tier": "fca_civil", "evidence_strength": "moderate", "verdict": "confirmed", "evidence_url": "", "note": "", "row": 3},
        {"npi": "4", "case_id": "state_term:MS:4", "defendant": "", "label_tier": "state_fraud", "evidence_strength": "exact_id", "verdict": "cannot_verify", "evidence_url": "", "note": "", "row": 4},
    ])
    current = pd.DataFrame({"npi": ["2", "3", "4"], "case_id": ["b", "c", "state_term:MS:4"]})   # row 1 was rejected since
    scored = rescore_against(rows, current)
    assert scored["in_current_label"].tolist() == [False, True, True, True]
    rep = report(scored)
    assert "dropped since the sample was drawn: 1 (of which flagged wrong: 1)" in rep
    assert "confirmed among still-present rows with a decision (cannot_verify excluded): 2/2 (100%)" in rep
    assert "STATE MEDICAID TERMINATION" in build_prompts(rows)[0]["prompt"]
