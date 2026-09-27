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
