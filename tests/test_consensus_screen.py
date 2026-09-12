"""
test_consensus_screen.py — degenerate-run screening, CSV recompute, and
NPPES name resolution for the cross-run consensus.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.model_a.consensus import (consensus_from_frames, recompute,
                                   resolve_unnamed, run_health, screen_runs)


def _dw(n=200, seed=0):
    rng = np.random.default_rng(seed)
    good = lambda: rng.uniform(0, 1, n)                      # noqa: E731 ~half hot
    dw = pd.DataFrame({"dw_v6": good(), "dw_v5": good(), "dw_v3": good(),
                       "dw_v4": good()})
    # the old thin build: almost everything exactly zero, a couple of hits
    dead = np.zeros(n); dead[:2] = 0.9
    dw["dw_old"] = dead
    return dw


def test_screen_runs_drops_degenerate_and_explicit():
    dw = _dw()
    kept, dropped = screen_runs(dw, hot=0.5, min_frac=0.34)
    assert "dw_old" in dropped and "degenerate" in dropped["dw_old"]
    assert set(kept) == {"dw_v6", "dw_v5", "dw_v3", "dw_v4"}
    kept2, dropped2 = screen_runs(dw, exclude=["v5"])
    assert dropped2["dw_v5"].startswith("excluded")
    assert "dw_v5" not in kept2
    # the screen never leaves fewer than two runs
    tiny = dw[["dw_v6", "dw_old"]]
    k3, d3 = screen_runs(tiny)
    assert len(k3) == 2 and d3 == {}
    h = run_health(dw).set_index("run")
    assert h.loc["old", "zero_share"] > 0.9


def test_consensus_median_ignores_dropped_run():
    cols = ["org_id", "org_name", "score_dollar_weighted", "score_max",
            "total_billed", "top_schemes", "n_providers"]
    orgs = [f"org:{i}" for i in range(40)]
    def frame(scores):
        return pd.DataFrame([(o, o.upper(), s, s, 1e7, "x", 1)
                             for o, s in zip(orgs, scores)], columns=cols)
    hot = [0.9] * 20 + [0.05] * 20
    frames = {"v3": frame(hot), "v4": frame(hot), "v5": frame(hot),
              "old": frame([0.0] * 40)}
    out, dropped = consensus_from_frames(frames, "v3")
    assert "dw_old" in dropped
    top = out[out["org_id"] == "org:0"].iloc[0]
    # with the dead run in the median, org:0 would sit at (0,0.9,0.9,0.9)->0.9
    # anyway; the agreement count is what the dead run must not dilute
    assert top["runs_present"] == 3 and top["runs_hot"] == 3
    assert abs(top["consensus_score"] - 0.9) < 1e-9
    assert "dw_old" in out.columns          # transparency column survives


def test_recompute_from_csv_and_resolve_names(tmp_path: Path):
    dw = _dw(n=30)
    cons = dw.copy()
    cons.insert(0, "consensus_rank", range(1, 31))
    cons.insert(1, "org_id", [f"org:npi:{1000000000 + i}" for i in range(30)])
    cons["org_name"] = ["UNNAMED ORGANIZATION (x)"] * 3 + ["ACME"] * 27
    cons["score_max"] = dw.max(axis=1)
    cons["case_size_yr"] = 2e7
    src = tmp_path / "consensus_priority_list.csv"
    cons.to_csv(src, index=False)

    def fake_lookup(npi):
        return {"npi": npi, "org_name": "", "first_name": "JANE",
                "last_name": "DOE", "city": "MIAMI", "state": "FL"}

    out = recompute(src, out_dir=tmp_path / "out", resolve_names=True,
                    lookup=fake_lookup)
    got = pd.read_csv(out, dtype=str)
    assert (got["runs_present"].astype(int) == 4).all()     # dw_old screened
    assert "DOE, JANE (NPI-1) [MIAMI, FL]" in set(got["org_name"])
    assert not got["org_name"].str.startswith("UNNAMED").any()
    md = (tmp_path / "out" / "CONSENSUS.md").read_text(encoding="utf-8")
    assert "Runs dropped from the median" in md and "old" in md
    # rank 1 is the highest kept-run median
    med = dw[["dw_v6", "dw_v5", "dw_v3", "dw_v4"]].median(axis=1)
    assert float(got["consensus_score"].iloc[0]) == pytest_approx(med.max())


def pytest_approx(v):
    import pytest
    return pytest.approx(float(v), rel=1e-6)


def test_absent_from_newest_is_flagged_not_dropped():
    from src.model_a.consensus import flag_absent_from_newest
    cons = pd.DataFrame({"org_id": ["org:a", "org:b"],
                         "org_name": ["CHILDREN'S HOSPITAL", "ALPHA"],
                         "dw_v6": [np.nan, 0.4], "dw_v5": [0.6, 0.5]})
    out = flag_absent_from_newest(cons, ["dw_v6", "dw_v5"])
    assert list(out["absent_in_newest"]) == ["v6", ""]
    assert out["org_name"].iloc[0].startswith("CHILDREN'S HOSPITAL [absent in v6")
    assert out["org_name"].iloc[1] == "ALPHA"
    # idempotent: a second pass does not double the suffix
    again = flag_absent_from_newest(out, ["dw_v6", "dw_v5"])
    assert again["org_name"].iloc[0] == out["org_name"].iloc[0]


def test_government_screen_catches_municipal_corporations():
    from src.model_a.pursuit_dossier import government_mask
    m = government_mask(pd.Series(["MAYOR AND CITY COUNCIL OF BALTIMORE",
                                   "TOWN OF BROOKLINE HEALTH",
                                   "MONTEFIORE MEDICAL CENTER",
                                   "MUNICIPAL HOSPITAL DISTRICT 1"]))
    assert list(m) == [True, True, False, True]


def test_resolve_unnamed_keeps_named_rows():
    cons = pd.DataFrame({"org_id": ["org:npi:1", "org:x"],
                         "org_name": ["", "NAMED"]})
    out = resolve_unnamed(cons, lookup=lambda n: {"org_name": "ACME LLC",
                                                  "city": "", "state": ""},
                          log=lambda *_: None)
    assert list(out["org_name"]) == ["ACME LLC", "NAMED"]
