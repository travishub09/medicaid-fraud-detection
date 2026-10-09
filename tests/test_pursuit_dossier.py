"""
test_pursuit_dossier.py — the standing end-of-run artifact: top-10 selection,
dual-level writing, guardrails baked in, research prompts always written, and
dispatch/poll through an injectable fake transport (never live).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.model_a.pursuit_dossier import (build, dispatch_research,
                                         poll_research, render, top_targets,
                                         research_prompt, write_prompts,
                                         _member_lines, _plain_story)


def _matrix(n=400, seed=0):
    """Synthetic run matrix: orgs of 4, one hot org with real signal."""
    rng = np.random.default_rng(seed)
    orgs = [f"org:{i // 4:03d}" for i in range(n)]
    hot = np.array([o == "org:000" for o in orgs])
    y = (hot & (rng.random(n) < 0.9)).astype(int)
    m = pd.DataFrame({
        "npi": [f"{1000000000 + i}" for i in range(n)],
        "org_node_id": orgs,
        "group_id": orgs,
        "org_legal_name": [f"COMPANY {o[-3:]}" for o in orgs],
        "provider_name": [f"COMPANY {o[-3:]}" for o in orgs],
        "addr_city": ["PROVIDENCE"] * n,
        "practice_state": ["RI"] * n,
        "provider_on_exclusion": y,
        "net_paid": rng.uniform(2e5, 9e5, n) + hot * 2e6,
        "shell_score": rng.normal(0, 1, n) + hot * 2.0,
        "facility_code_share__peerpct": rng.random(n),
        "subscore_facility_code_billing": rng.random(n) * (0.3 + 0.7 * hot),
        "subscore_upcoding": rng.random(n) * 0.2,
    })
    manifest = {
        "raw_feature_cols": ["net_paid", "shell_score"],
        "peerpct_cols": ["facility_code_share__peerpct"],
        "subscore_cols": ["subscore_facility_code_billing", "subscore_upcoding"],
        "embedding_cols": [], "leakage_hard": [], "leakage_adjacent": [],
        "sources_used": {"spending": ["net_paid", "shell_score"]},
        "scheme_coverage": {},
    }
    return m, manifest


class _FakeTransport:
    def __init__(self):
        self.created: list[str] = []

    def create(self, prompt, **_):
        self.created.append(prompt)
        return {"task_id": f"task_{len(self.created)}"}

    def get(self, task_id):
        return {"status": "completed",
                "output": f"research summary for {task_id}",
                "attachments": []}


def test_top_targets_selects_and_filters():
    m, man = _matrix()
    t = top_targets(m, man, top=5, min_billed=1.0)
    assert len(t) == 5
    assert (t["total_billed"] > 0).all()
    # the min-billed pursuit floor drops sub-scale orgs
    t2 = top_targets(m, man, top=5, min_billed=1e12)
    assert len(t2) == 0


def test_plain_story_is_plain_and_non_accusatory():
    m, man = _matrix()
    t = top_targets(m, man, top=1, min_billed=1.0)
    row = t.iloc[0]
    members = [{"npi": "1", "city": "PROVIDENCE", "state": "RI",
                "top_driver": "facility_code_share", "driver_pctile": 0.99}]
    s = _plain_story(row, members)
    assert "does not mean anyone did anything wrong" in s
    assert "$" in s and "RI" in s
    for banned in ("fraud", "criminal", "guilty"):
        assert banned not in s.lower()


def test_full_build_writes_dossier_and_prompts(tmp_path):
    m, man = _matrix()
    (tmp_path / "provider_features_for_model.parquet").write_bytes(b"")
    m.to_parquet(tmp_path / "provider_features_for_model.parquet")
    (tmp_path / "feature_manifest.json").write_text(json.dumps(man))
    out = build(tmp_path, top=3, min_billed=1.0)
    text = out.read_text(encoding="utf-8")
    assert "PURSUIT DOSSIER" in text and "top 3 cases" in text
    assert "The short story" in text and "The technical picture" in text
    assert "investigative hypotheses" in text.lower() or "review" in text.lower()
    assert "pending dispatch" in text          # research never silently skipped
    prompts = (tmp_path / "pursuit_research_prompts.jsonl").read_text().splitlines()
    assert len(prompts) == 3
    item = json.loads(prompts[0])
    assert "PUBLIC" in item["prompt"] and "no accusation" in item["prompt"]
    assert (tmp_path / "pursuit_targets.csv").exists()


def test_dispatch_and_poll_roundtrip(tmp_path):
    m, man = _matrix()
    t = top_targets(m, man, top=2, min_billed=1.0)
    member_map = {str(r["org_id"]): [] for _, r in t.iterrows()}
    prompts = write_prompts(t, member_map, tmp_path)
    reg = tmp_path / "reg.json"
    ft = _FakeTransport()
    info = dispatch_research(prompts, reg, transport=ft)
    assert info["dispatched"] == 2
    # a re-dispatch re-attaches instead of paying twice
    info2 = dispatch_research(prompts, reg, transport=ft)
    assert info2["dispatched"] == 0
    res = poll_research(reg, tmp_path / "res.json", transport=ft)
    assert len(res) == 2
    assert all(v["status"] == "completed" for v in res.values())
    md = render(t, member_map, "test_run", research=res)
    assert "Supplemental research (public records)" in md


def test_render_shows_disclaimer_and_alternatives():
    m, man = _matrix()
    t = top_targets(m, man, top=2, min_billed=1.0)
    md = render(t, {str(r["org_id"]): [] for _, r in t.iterrows()}, "r")
    assert "Alternative explanations are mandatory" in md
    assert md.count("**The short story.**") == 2


def test_priority_list_signal_sorted_filters_only(tmp_path):
    m, man = _matrix()
    # case-size inputs: raw excess (0.6 x paid) exceeds the 40% ceiling, so
    # every winner is cap-pinned — the flag must ride into the CSV
    m["expected_net_paid"] = m["net_paid"] * 0.4
    m["entity_type"] = "2"
    # one government org (filtered, never sorted out)
    govmask = m["org_node_id"] == "org:001"
    m.loc[govmask, ["org_legal_name", "provider_name"]] = \
        "STATE OF TESTLAND DEPARTMENT OF HEALTH"
    # one solo individual enrollment: personal name must never ship
    solo = pd.DataFrame([{
        "npi": "1999999999", "org_node_id": "org:solo", "group_id": "org:solo",
        "org_legal_name": "DOE, JANE", "provider_name": "DOE, JANE",
        "addr_city": "WARWICK", "practice_state": "RI",
        "provider_on_exclusion": 0, "net_paid": 5e6,
        "expected_net_paid": 1e6, "shell_score": 0.0,
        "facility_code_share__peerpct": 0.5,
        "subscore_facility_code_billing": 0.2, "subscore_upcoding": 0.1,
        "entity_type": "1"}])
    m = pd.concat([m, solo], ignore_index=True)
    m.to_parquet(tmp_path / "provider_features_for_model.parquet")
    (tmp_path / "feature_manifest.json").write_text(json.dumps(man))

    from src.model_a.pursuit_dossier import build_priority_list
    csv_path = build_priority_list(tmp_path, min_damages=1_000_000.0)
    out = pd.read_csv(csv_path)
    assert len(out) > 0
    # FILTERS: no government entities, nothing under the case-size bar
    assert not out["org_name"].str.contains("STATE OF TESTLAND").any()
    assert (out["case_size_yr"] >= 1_000_000.0).all()
    # SORT: fraud signal, descending — dollars never drive priority
    dw = out["score_dollar_weighted"].to_numpy()
    assert (dw[:-1] >= dw[1:] - 1e-12).all()
    # default: enrollment names ship as-is (operator decision 2026-08-04),
    # and every identifier rides along — the full member-NPI roster
    solo_rows = out[out["org_id"] == "org:solo"]
    assert len(solo_rows) == 1
    assert solo_rows.iloc[0]["org_name"] == "DOE, JANE"
    assert solo_rows.iloc[0]["member_npis"] == "1999999999"
    assert "member_npis" in out.columns and out["member_npis"].str.len().gt(0).all()
    # cap-pinned winners carry the honesty flag
    assert bool(solo_rows.iloc[0]["case_size_capped"])
    # --mask-individuals still available for copies that leave the operator
    csv2 = build_priority_list(tmp_path, min_damages=1_000_000.0,
                               mask_individuals=True)
    out2 = pd.read_csv(csv2)
    assert not out2["org_name"].str.contains("DOE").any()
    assert out2[out2["org_id"] == "org:solo"].iloc[0]["org_name"].startswith(
        "Individual provider (NPI 1999999999")
    # the companion frame ships the legal frame + filter accounting
    md = (tmp_path / "PRIORITY_LIST.md").read_text(encoding="utf-8")
    assert "investigative hypothesis" in md
    assert "never the sort" in md


def test_recovery_mode_sorts_by_signal_not_case_size(tmp_path):
    # the universal rule: signal sorts, case size only gates
    m, man = _matrix()
    m["expected_net_paid"] = m["net_paid"] * 0.4     # excess estimator fires
    from src.model_a.pursuit_dossier import top_targets_by_damages
    t = top_targets_by_damages(m, man, top=10, min_damages=1.0)
    if len(t) > 1:
        dw = t["score_dollar_weighted"].to_numpy()
        assert (dw[:-1] >= dw[1:] - 1e-12).all()
    # and the full build in recovery mode renders in the same order
    m.to_parquet(tmp_path / "provider_features_for_model.parquet")
    (tmp_path / "feature_manifest.json").write_text(json.dumps(man))
    build(tmp_path, top=5, min_damages=1.0, min_hot=0.0)
    targets = pd.read_csv(tmp_path / "pursuit_targets.csv")
    if len(targets) > 1:
        dw = targets["score_dollar_weighted"].to_numpy()
        assert (dw[:-1] >= dw[1:] - 1e-12).all()


def test_priority_list_entity_segment(tmp_path):
    m, man = _matrix()
    m["expected_net_paid"] = m["net_paid"] * 0.4
    m["entity_type"] = "2"
    solo = pd.DataFrame([{
        "npi": "1999999999", "org_node_id": "org:solo", "group_id": "org:solo",
        "org_legal_name": "DOE, JANE", "provider_name": "DOE, JANE",
        "addr_city": "WARWICK", "practice_state": "RI",
        "provider_on_exclusion": 0, "net_paid": 5e6,
        "expected_net_paid": 1e6, "shell_score": 0.0,
        "facility_code_share__peerpct": 0.5,
        "subscore_facility_code_billing": 0.2, "subscore_upcoding": 0.1,
        "entity_type": "1"}])
    m = pd.concat([m, solo], ignore_index=True)
    hosp = m["org_node_id"] == "org:002"
    m.loc[hosp, ["org_legal_name", "provider_name"]] = "TESTVILLE GENERAL HOSPITAL"
    m.to_parquet(tmp_path / "provider_features_for_model.parquet")
    (tmp_path / "feature_manifest.json").write_text(json.dumps(man))
    from src.model_a.pursuit_dossier import build_priority_list
    out = pd.read_csv(build_priority_list(tmp_path, min_damages=1_000_000.0))
    assert set(out["entity_segment"]) <= {"institutional", "operator", "individual"}
    assert out.loc[out["org_id"] == "org:solo", "entity_segment"].iloc[0] == "individual"
    assert out.loc[out["org_name"] == "TESTVILLE GENERAL HOSPITAL",
                   "entity_segment"].iloc[0] == "institutional"
    assert (out.loc[out["org_name"].str.startswith("COMPANY"),
                    "entity_segment"] == "operator").all()


def test_dispatch_stops_cleanly_on_server_error(tmp_path):
    """A 5xx from task.create (seen live) retries with backoff, then stops with the
    registry intact instead of a traceback; a re-run resumes from the registry."""
    import json
    from src.model_a.pursuit_dossier import dispatch_research

    class _Resp:
        status_code = 500

    class _Err(Exception):
        response = _Resp()

    calls = {"n": 0}

    class _T:
        def create(self, prompt):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"task_id": "t1"}
            raise _Err("500 Server Error")

    pj = tmp_path / "p.jsonl"
    pj.write_text('\n'.join(json.dumps({"org_id": f"o{i}", "org_name": f"n{i}", "prompt": "x"}) for i in range(3)) + "\n")
    reg = tmp_path / "reg.json"
    out = dispatch_research(pj, reg, transport=_T(), sleep=lambda s: None)
    assert out["dispatched"] == 1 and out.get("rate_limited") and "500" in out["note"]
    assert json.loads(reg.read_text())["o0"]["task_id"] == "t1"
    # resume: o0 is skipped, o1 is retried
    class _OK:
        def create(self, prompt):
            return {"task_id": "t2"}
    out2 = dispatch_research(pj, reg, transport=_OK(), sleep=lambda s: None)
    assert out2["dispatched"] == 2 and out2["total"] == 3


def test_dispatch_never_counts_or_stores_a_create_without_an_id(tmp_path):
    import json
    from src.model_a.pursuit_dossier import dispatch_research, _task_id_from
    assert _task_id_from({"data": {"task_id": "d1"}}) == "d1" and _task_id_from({"code": 429, "msg": "busy"}) == ""

    class _T:
        def __init__(self):
            self.n = 0

        def create(self, prompt):
            self.n += 1
            return {"task_id": "t1"} if self.n == 1 else {"code": 429, "msg": "concurrency limit"}

    pj = tmp_path / "p.jsonl"
    pj.write_text("\n".join(json.dumps({"org_id": f"o{i}", "org_name": f"n{i}", "prompt": "x"}) for i in range(6)) + "\n")
    reg = tmp_path / "reg.json"
    out = dispatch_research(pj, reg, transport=_T(), sleep=lambda s: None)
    assert out["dispatched"] == 1 and out.get("rate_limited") and "no task id" in out["note"]
    r = json.loads(reg.read_text())
    assert list(r) == ["o0"] and r["o0"]["task_id"] == "t1"          # nothing stored without an id
    # a re-run skips o0 and tries o1 again
    class _OK:
        def create(self, prompt):
            return {"task_id": "t2"}
    out2 = dispatch_research(pj, reg, transport=_OK(), sleep=lambda s: None)
    assert out2["dispatched"] == 5 and out2["total"] == 6


def test_dispatch_refuses_a_second_concurrent_run(tmp_path):
    import json
    from src.model_a.pursuit_dossier import dispatch_research

    class _T:
        def create(self, prompt):
            return {"task_id": "t"}

    pj = tmp_path / "p.jsonl"
    pj.write_text(json.dumps({"org_id": "o0", "org_name": "n", "prompt": "x"}) + "\n")
    reg = tmp_path / "reg.json"
    lock = reg.with_suffix(".lock")
    lock.write_text("123")
    out = dispatch_research(pj, reg, transport=_T(), sleep=lambda s: None)
    assert out["dispatched"] == 0 and out.get("refused") and "another dispatch" in out["note"]
    lock.unlink()
    out2 = dispatch_research(pj, reg, transport=_T(), sleep=lambda s: None)
    assert out2["dispatched"] == 1 and not lock.exists()          # lock released after a normal run


def test_pursuit_book_is_operator_only_dollar_weighted_sorted(tmp_path):
    """The companion pursuit list (2026-10-09): operator book only, sorted on the
    dollar-weighted score with score_max as the tiebreak (the v7 order sweep:
    P(any) and basis-first both lost hits; the operator FILTER is the win).
    score_p_any / score_p_max ride along as columns, never as the sort."""
    m, man = _matrix()
    m["expected_net_paid"] = m["net_paid"] * 0.4
    m.to_parquet(tmp_path / "provider_features_for_model.parquet")
    (tmp_path / "feature_manifest.json").write_text(json.dumps(man))
    from src.model_a.pursuit_dossier import build_priority_list
    build_priority_list(tmp_path, min_damages=1.0)
    main = pd.read_csv(tmp_path / "provider_priority_list.csv")
    assert {"score_p_any", "score_p_max"} <= set(main.columns)
    pur = pd.read_csv(tmp_path / "provider_priority_list_pursuit.csv")
    assert len(pur) > 0 and (pur["entity_segment"] == "operator").all()
    assert pur["pursuit_rank"].tolist() == list(range(1, len(pur) + 1))
    assert {"score_p_any", "score_p_max"} <= set(pur.columns)
    pdw = pur["score_dollar_weighted"].to_numpy()
    assert (pdw[:-1] >= pdw[1:] - 1e-12).all()                  # dollar-weighted order, top to bottom
    ties = np.isclose(pdw[:-1], pdw[1:])
    if ties.any():                                               # score_max breaks ties
        smax = pur["score_max"].to_numpy()
        assert (smax[:-1][ties] >= smax[1:][ties] - 1e-12).all()
    # the operator book in main-list order is exactly the pursuit order
    main_ops = main[main["entity_segment"] == "operator"]
    assert pur["org_id"].tolist() == main_ops["org_id"].tolist() or \
        set(pur["org_id"]) == set(main_ops["org_id"])
    # the main list is untouched: still signal-sorted on the dollar-weighted score
    dw = main["score_dollar_weighted"].to_numpy()
    assert (dw[:-1] >= dw[1:] - 1e-12).all()
    assert set(pur["org_id"]) <= set(main["org_id"])
