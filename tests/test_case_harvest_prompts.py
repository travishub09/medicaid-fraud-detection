"""case_harvest_prompts: the five prompt sets share one CSV schema and dispatch through the Manus plumbing."""
from __future__ import annotations

import json

from src.enforcement.case_harvest_prompts import CSV_SCHEMA, build_prompts, save_results, write_prompts


def test_sets_years_and_schema(tmp_path):
    ps = build_prompts(["mfcu", "takedown", "yir", "hcfac", "oig_sar"], [2023, 2024])
    by = {}
    for p in ps:
        by[p["set"]] = by.get(p["set"], 0) + 1
    assert by == {"mfcu": 18, "takedown": 2, "yir": 2, "hcfac": 2, "oig_sar": 4}
    assert len({p["org_id"] for p in ps}) == len(ps)
    assert all("npiregistry" in p["prompt"] and "Do not contact anyone" in p["prompt"] for p in ps)
    assert all("conduct_end_year" in p["prompt"] for p in ps)            # one schema everywhere
    td = next(p for p in ps if p["set"] == "takedown")
    assert "2023" in td["prompt"] and "District of South Carolina" in td["prompt"]
    pj, pc = write_prompts(ps, tmp_path)
    lines = [json.loads(l) for l in pj.read_text(encoding="utf-8").splitlines()]
    assert {"org_id", "org_name", "prompt"} <= set(lines[0]) and pc.exists()
    n = save_results({ps[0]["org_id"]: {"status": "completed", "output": "csv text", "attachments": [{"name": "a.csv"}]},
                      ps[1]["org_id"]: {"status": "running"}}, tmp_path)
    assert n == 1 and (tmp_path / "results" / (ps[0]["org_id"].replace(":", "_") + ".md")).read_text() == "csv text"
    assert "announced_date" in CSV_SCHEMA
