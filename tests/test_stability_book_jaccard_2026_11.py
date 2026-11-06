"""The gated top-50 Jaccard reads the SHIPPED book (2026-11-06).

`accuracy_diagnostics.stability` named its rank array `order`, overwriting the `order`
argument (the book's sort key, e.g. 'dollar'). Every repeat's book then raised on
`ndarray.startswith`, the exception was logged ("book top-50 ... not built"),
`jaccard_top_book` came back None and the registry's `top50_jaccard` check fell back to
the all-orgs measure (v28 and v29 night_run.log carry the error on every repeat)."""
from __future__ import annotations

import numpy as np
import pandas as pd

import src.model_a.accuracy_diagnostics as ad


def _matrix(n: int = 60) -> pd.DataFrame:
    return pd.DataFrame({"npi": [f"10000000{i:02d}" for i in range(n)],
                         "org_node_id": [f"org:{i % 20}" for i in range(n)],
                         "net_paid": np.linspace(1e6, 2e7, n)})


def test_book_jaccard_is_computed_with_the_shipped_order(monkeypatch):
    m = _matrix()
    seen_orders = []

    def fake_refit(matrix, manifest, repeat=0, cols=None):
        rng = np.random.default_rng(repeat)
        return rng.random(len(matrix)), None, None

    def fake_org_table(matrix, scores, min_damages=10_000_000.0, probs=None):
        g = pd.DataFrame({"org_id": matrix["org_node_id"].astype(str), "s": scores})
        return g.groupby("org_id", as_index=False)["s"].mean()

    def fake_book(orgs, matrix, order="pmax_basis50"):
        seen_orders.append(order)
        assert isinstance(order, str), "the book's sort key must arrive as a string"
        return orgs.sort_values("s", ascending=False)

    monkeypatch.setattr(ad, "refit_scores", fake_refit)
    monkeypatch.setattr(ad, "org_table_from_refit", fake_org_table)
    monkeypatch.setattr(ad, "operator_book_from_refit", fake_book)

    base = (fake_refit(m, {}, repeat=0)[0], np.zeros(len(m), dtype=int))
    st = ad.stability(m, {}, [], {}, repeats=2, k_org=5, base=base, order="dollar")

    assert seen_orders == ["dollar", "dollar", "dollar"]          # repeat 0 + 2 refits
    assert st["jaccard_top_book"] is not None                       # was None on every run
    assert 0.0 <= st["jaccard_top_book"] <= 1.0
    assert st["book_order"] == "dollar"                             # was the rank array


def test_kpi_source_reads_book_when_the_book_jaccard_exists(monkeypatch):
    """With the book measure present the gated KPI is the book's, not the all-orgs one."""
    m = _matrix()
    monkeypatch.setattr(ad, "refit_scores",
                        lambda matrix, manifest, repeat=0, cols=None:
                        (np.random.default_rng(repeat).random(len(matrix)), None, None))
    monkeypatch.setattr(ad, "org_table_from_refit",
                        lambda matrix, scores, min_damages=10_000_000.0, probs=None:
                        pd.DataFrame({"org_id": matrix["org_node_id"].astype(str), "s": scores})
                        .groupby("org_id", as_index=False)["s"].mean())
    monkeypatch.setattr(ad, "operator_book_from_refit",
                        lambda orgs, matrix, order="pmax_basis50": orgs.sort_values("s", ascending=False))
    st = ad.stability(m, {}, [], {}, repeats=1, k_org=5,
                      base=(np.random.default_rng(0).random(len(m)), None), order="dollar")
    source = "book" if st.get("jaccard_top_book") is not None else "all_orgs"
    assert source == "book"
