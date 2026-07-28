"""run_history curation + economics ledger + dashboard v2 rendering."""

import json

from src.model_a import economics as eco
from src.model_a import run_history as rh
from src.model_a.run_registry import recommend, to_html, to_markdown


def test_history_entries_are_marked_retrospective():
    assert len(rh.HISTORY) >= 5
    for e in rh.HISTORY:
        assert e["retrospective"] is True
        assert e.get("evidence"), f"{e['tag']} missing evidence"
        assert e.get("goal") and e.get("why")


def test_history_write_is_idempotent(tmp_path):
    p1 = rh.write_history(tmp_path)
    first = p1.read_text(encoding="utf-8")
    p2 = rh.write_history(tmp_path)
    assert p1 == p2 and p2.read_text(encoding="utf-8") == first
    rows = [json.loads(ln) for ln in first.splitlines() if ln.strip()]
    assert len(rows) == len(rh.HISTORY)


def test_history_markdown_has_context_and_results():
    md = rh.to_markdown(rh.HISTORY)
    assert "What we were trying to change" in md
    assert "reconstructed" in md.lower()
    assert "2.0x" in md or "2.0" in md          # the origin backtest


def test_registry_markdown_includes_history_section():
    rows = [{"tag": "v2", "registered_at": "2026-07-28T00:00:00",
             "ablation_lift_delta": 0.745, "ablation_lift_lo": 0.413,
             "ablation_lift_hi": 1.107, "expectation_fails": 0,
             "network_verdict": "KEEP", "decision": "PROMOTE",
             "run_dir": "/x"}]
    md = to_markdown(rows, recommend(rows), [], [], rh.HISTORY)
    assert "Release history before the registry" in md
    assert "never drive a PROMOTE/HOLD" in md


def test_html_dashboard_toggles_history_and_revert():
    rows = [{"tag": "v2", "registered_at": "2026-07-28T00:00:00",
             "ablation_lift_delta": 0.745, "ablation_lift_lo": 0.413,
             "ablation_lift_hi": 1.107, "expectation_fails": 0,
             "forward_positives": 1021, "catches_full_only": 205,
             "catches_core_only": 131, "network_verdict": "KEEP",
             "decision": "PROMOTE", "git_commit": "02b1d1d",
             "run_dir": "/x"}]
    html = to_html(rows, recommend(rows), [], [], rh.HISTORY,
                   economics_md="# ECONOMICS", dispositions_md=None)
    # the two view toggles
    assert "plain</button>" in html and "technical</button>" in html
    assert "rec-short" in html and "rec-full" in html
    # history rendered apart, with badge and dashed trend point
    assert "reconstructed" in html
    assert "stroke-dasharray" in html
    # revert instructions from the recorded commit
    assert "git checkout 02b1d1d" in html
    # plain sentence exists and the economics section landed
    assert "too big to be" in html
    assert "ECONOMICS" in html


def test_html_glossary_hover_terms():
    rows = [{"tag": "v2_withcases", "registered_at": "2026-07-28T00:00:00",
             "ablation_lift_delta": 0.745, "ablation_lift_lo": 0.413,
             "ablation_lift_hi": 1.107, "expectation_fails": 0,
             "forward_positives": 1021, "network_verdict": "KEEP",
             "decision": "PROMOTE", "run_dir": "/x"}]
    html = to_html(rows, recommend(rows), [], [], rh.HISTORY)
    # hover spans exist and carry definitions
    assert 'class="dfn"' in html and "cursor:help" in html
    assert "cannot peek at the future" in html          # frozen definition
    assert "court-case file our research agents" in html  # withcases def
    # the version tag itself is hoverable
    assert "dfn" in html.split("v2_withcases")[0].rsplit("<", 2)[-1] or \
        '<span class="dfn"' in html
    # terms card renders every glossary entry for non-hover readers
    assert "Terms used on this page" in html
    from src.model_a.run_registry import GLOSSARY
    for g in GLOSSARY:
        assert g["def"][:40] in html
    # markdown report carries the same terms
    md = to_markdown(rows, recommend(rows), [], [], rh.HISTORY)
    assert "## Terms" in md and "grades their own homework" in md


def test_gloss_wraps_free_text_terms():
    from src.model_a.run_registry import _gloss
    out = _gloss("the lift delta beat the core model under the KEEP rule")
    assert out.count('class="dfn"') == 3
    assert "title=" in out
    # unknown words untouched
    assert _gloss("nothing special here") == "nothing special here"


def test_economics_log_and_unit_costs(tmp_path):
    p = tmp_path / "economics.jsonl"
    eco.log_entry(p, "manus_enrichment", cost_credits=900,
                  gained={"identifiers": 212}, note="round 1")
    eco.log_entry(p, "manus_enrichment", cost_credits=300,
                  gained={"identifiers": 88})
    eco.log_entry(p, "state_exclusions", cost_hours=2,
                  gained={"labels": 90000})
    by = eco.summarize(eco.load_log(p))
    m = by["manus_enrichment"]
    assert m["cost_credits"] == 1200 and m["gained"]["identifiers"] == 300
    assert m["unit_costs"]["identifiers"]["credits_per"] == 4.0
    md = eco.to_markdown(by)
    assert "manus_enrichment" in md and "state_exclusions" in md


def test_economics_gained_parser():
    assert eco.parse_gained("identifiers=212, labels=3121") == {
        "identifiers": 212.0, "labels": 3121.0}
    assert eco.parse_gained("") == {}
    try:
        eco.parse_gained("bad")
        raise AssertionError("should have raised")
    except ValueError:
        pass
