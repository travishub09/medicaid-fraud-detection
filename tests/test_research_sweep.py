"""
test_research_sweep.py — orchestrate Manus tasks over the top-N leads.
"""

from __future__ import annotations

import pandas as pd

from src.feeds.manus_research import ManusTransport
from src.model_a.research_sweep import (select_leads, run_sweep,
                                        write_enrichment, load_enrichment)
from src.model_a.dossier_batch import build_batch


class _Reality(ManusTransport):
    """Returns a completed reality result immediately for any task."""
    def create(self, prompt, mode="agent", **opts):
        return {"task_id": "x", "status": "completed",
                "structured_output": {"reality_score": 8,
                                      "gaps": ["no website found"],
                                      "confidence": "high"}}
    def get(self, task_id):
        return {}


def _pack():
    return pd.DataFrame([
        {"npi": "1588799746", "provider_name": "VEAL", "addr_city": "ABQ",
         "practice_state": "NM", "net_paid": 31e6, "expected_net_paid": 5e6,
         "primary_taxonomy": "235Z00000X", "entity_type": "1"},
        {"npi": "1033128848", "provider_name": "THOMASON", "addr_city": "MEMPHIS",
         "practice_state": "TN", "net_paid": 25e6, "expected_net_paid": 1.8e6,
         "primary_taxonomy": "2084P0800X", "entity_type": "1"},
        {"npi": "1999999999", "provider_name": "SMALL", "addr_city": "X",
         "practice_state": "X", "net_paid": 1e5, "expected_net_paid": 9e4,
         "primary_taxonomy": "T", "entity_type": "1"},
    ])


def test_select_top_by_suspect_dollars():
    leads = select_leads(_pack(), top=2)
    assert list(leads["npi"]) == ["1588799746", "1033128848"]   # 26M, 23.2M
    assert "1999999999" not in set(leads["npi"])                # tiny, dropped


def test_sweep_runs_task_per_lead():
    res = run_sweep(_pack(), tasks=["reality"], top=2, transport=_Reality())
    assert set(res) == {"1588799746", "1033128848"}
    assert res["1588799746"]["reality"]["result"]["reality_score"] == 8


def test_enrichment_roundtrip_and_dossier_wire(tmp_path):
    res = run_sweep(_pack(), tasks=["reality"], top=2, transport=_Reality())
    write_enrichment(res, tmp_path)
    loaded = load_enrichment(tmp_path / "research_enrichment.json")
    assert loaded["1588799746"]["reality"]["result"]["reality_score"] == 8
    # the summary table carries the score
    summ = pd.read_parquet(tmp_path / "research_summary.parquet").set_index("npi")
    assert summ.loc["1588799746", "reality_score"] == 8
    # dossier_batch attaches it into the reality panel
    dz = tmp_path / "dossiers"
    build_batch(["1588799746"], _pack(), dz, enrichment=loaded)
    md = (dz / "DOSSIER_1588799746.md").read_text()
    assert "Reality score: 8/100" in md and "no website found" in md


def test_one_bad_task_does_not_sink_the_batch():
    class _Boom(ManusTransport):
        def create(self, prompt, mode="agent", **opts):
            raise RuntimeError("network down")
        def get(self, task_id):
            return {}
    res = run_sweep(_pack(), tasks=["reality"], top=1, transport=_Boom())
    npi = next(iter(res))
    assert res[npi]["reality"]["ok"] is False
    assert "network down" in res[npi]["reality"]["error"]


def test_unknown_task_raises():
    import pytest
    with pytest.raises(ValueError):
        run_sweep(_pack(), tasks=["not_a_task"], transport=_Reality())
