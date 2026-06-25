"""
test_remaining_pillars.py — temporal-graph velocity, external grounding, billing LM.

The last three roadmap items (docs/platform/17):
  * graph_velocity — structural change rate between two feature snapshots;
  * address_grounding — mailbox/PO-box detection + address reuse (the offline
    "bills $8M from a UPS Store" signal);
  * billing_lm — self-supervised code embeddings + per-provider surprisal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.model_a.feature_store import snapshot_features
from src.entity_graph.graph_velocity import velocity_from_snapshots, velocity_between
from src.entity_graph.graph_embeddings import EMB_PREFIX, PROXIMITY_COL
from src.model_a.address_grounding import address_flags
from src.model_a.billing_lm import (build_code_embeddings, provider_embeddings,
                                     billing_surprisal, EMB_PREFIX as BILL_EMB)


# ----------------------------------------------------- temporal-graph velocity ---
def _snap(val, deg, prox):
    return pd.DataFrame({"npi": ["A", "B"],
                         f"{EMB_PREFIX}0": [val, 0.0], f"{EMB_PREFIX}1": [0.0, 0.0],
                         "graph_degree": [deg, 1], PROXIMITY_COL: [prox, 0.1]})


def test_velocity_measures_structural_change(tmp_path):
    snapshot_features(_snap(0.0, 2, 0.1), "2025-01-01", tmp_path)
    snapshot_features(_snap(3.0, 5, 0.8), "2025-02-01", tmp_path)
    vel = velocity_from_snapshots(tmp_path).set_index("npi")
    assert vel.loc["A", "graph_emb_drift"] > 0          # A's embedding moved
    assert vel.loc["A", "graph_degree_delta"] == 3      # 2 → 5
    assert abs(vel.loc["A", "graph_fraud_proximity_delta"] - 0.7) < 1e-9
    assert vel.loc["B", "graph_emb_drift"] == 0         # B unchanged


def test_velocity_dormant_until_two_snapshots(tmp_path):
    snapshot_features(_snap(0.0, 2, 0.1), "2025-01-01", tmp_path)
    assert velocity_from_snapshots(tmp_path).empty


# ----------------------------------------------------------- address grounding ---
def test_address_flags_mailbox_and_reuse():
    pdim = pd.DataFrame({
        "npi": [f"n{i}" for i in range(7)],
        "addr_key": (["100 MAIN ST PMB 42 AUSTIN TX"]          # PMB → mailbox
                     + ["1 UPS STORE DALLAS TX"]               # brand → mailbox
                     + ["999 SHELL ROW HOUSTON TX"] * 5),      # 5 providers, one address
    })
    out = address_flags(pdim).set_index("npi")
    assert out.loc["n0", "addr_is_mailbox"] == 1
    assert out.loc["n1", "addr_is_mailbox"] == 1
    assert out.loc["n2", "addr_provider_count"] == 5
    assert out.loc["n2", "addr_shared"] == 1               # ≥5 at one address
    assert out.loc["n0", "addr_shared"] == 0


# --------------------------------------------------------------- billing LM ---
def _claims():
    # two "specialties": codes {A,B,C} billed together; one provider bills the odd code Z
    rows = []
    for i in range(8):
        for code in ["A", "B", "C"]:
            rows.append({"npi": f"reg{i}", "hcpcs": code, "weight": 100.0,
                         "taxonomy_code": "T"})
    for code in ["A", "Z"]:                               # odd one out
        rows.append({"npi": "weird", "hcpcs": code, "weight": 100.0, "taxonomy_code": "T"})
    return pd.DataFrame(rows)


def test_code_embeddings_and_provider_embedding_deterministic():
    df = _claims()
    codes, vecs = build_code_embeddings(df, dim=8)
    codes2, vecs2 = build_code_embeddings(df, dim=8)
    assert vecs.shape == (len(codes), 8)
    assert np.allclose(vecs, vecs2)                       # sign-canonicalized → deterministic
    emb = provider_embeddings(df, codes, vecs)
    assert emb["npi"].is_unique
    assert emb.filter(like=BILL_EMB).shape[1] == 8


def test_surprisal_higher_for_odd_code_mix():
    df = _claims()
    sur = billing_surprisal(df, df[["npi", "taxonomy_code"]].drop_duplicates()).set_index("npi")
    odd = sur.loc["weird", "billing_surprisal"]
    typical = sur.loc["reg0", "billing_surprisal"]
    assert odd > typical                                  # billing the rare code Z is "surprising"


def _spending_parquet(tmp_path):
    """A synthetic spending fact (billing_npi, hcpcs_code, total_paid, service_month)
    mirroring _claims() so the DuckDB path can be compared to the pandas path."""
    df = _claims().rename(columns={"hcpcs": "hcpcs_code", "weight": "total_paid"})
    df["billing_npi"] = df["npi"]
    df["service_month"] = "2024-01"
    df = df[["billing_npi", "hcpcs_code", "total_paid", "service_month"]]
    p = tmp_path / "spending.parquet"
    df.to_parquet(p)
    return p


def test_billing_lm_duckdb_matches_pandas(tmp_path):
    from src.model_a.billing_lm import (build_code_embeddings_duckdb,
                                         provider_embeddings_duckdb,
                                         billing_surprisal_duckdb)
    p = _spending_parquet(tmp_path)
    df = _claims()
    pdim = df[["npi", "taxonomy_code"]].drop_duplicates()

    # surprisal: DuckDB SQL cross-entropy must equal the pandas implementation
    sur_pd = billing_surprisal(df.groupby(["npi", "hcpcs"], as_index=False)["weight"].sum()
                               .merge(pdim, on="npi"), pdim).set_index("npi")
    sur_db = billing_surprisal_duckdb(str(p), pdim).set_index("npi")
    common = sur_pd.index.intersection(sur_db.index)
    assert len(common) > 0
    assert np.allclose(sur_pd.loc[common, "billing_surprisal"].to_numpy(),
                       sur_db.loc[common, "billing_surprisal"].to_numpy(), atol=1e-9)

    # embeddings: DuckDB co-occurrence + provider embedding cover every NPI, no NaNs
    codes, vecs = build_code_embeddings_duckdb(str(p), dim=8)
    assert len(codes) and vecs.shape == (len(codes), 8)
    emb = provider_embeddings_duckdb(str(p), codes, vecs)
    assert emb["npi"].is_unique
    assert emb.filter(like=BILL_EMB).shape[1] == 8
    assert not emb.filter(like=BILL_EMB).isna().any().any()


def test_plausibility_duckdb_matches_pandas(tmp_path):
    from src.analytics.plausibility import (org_clinical_plausibility,
                                            org_clinical_plausibility_duckdb)
    # build a fact where one taxonomy is large enough to assess and one code is rare
    rows = []
    for i in range(10):
        for code in ["A", "B"]:
            rows.append({"billing_npi": f"100000000{i}", "hcpcs_code": code,
                         "total_paid": 100.0, "service_month": "2024-01"})
    rows.append({"billing_npi": "1000000000", "hcpcs_code": "RARE",
                 "total_paid": 500.0, "service_month": "2024-01"})
    fact = pd.DataFrame(rows)
    pdim = pd.DataFrame({"npi": [f"100000000{i}" for i in range(10)],
                         "taxonomy_code": ["T"] * 10})
    xw = pd.DataFrame({"npi": [f"100000000{i}" for i in range(10)],
                       "org_node_id": [f"org:{i}" for i in range(10)]})
    p = tmp_path / "fact.parquet"
    fact.to_parquet(p)

    agg = fact.groupby(["billing_npi", "hcpcs_code"], as_index=False)["total_paid"].sum()
    pd_out = (org_clinical_plausibility(agg, pdim, xw, min_taxonomy_providers=5)
              .set_index("org_node_id")["implausible_dollar_share"])
    db_out = (org_clinical_plausibility_duckdb(str(p), pdim, xw, min_taxonomy_providers=5)
              .set_index("org_node_id")["implausible_dollar_share"])
    common = pd_out.index.intersection(db_out.index)
    assert len(common) == len(pd_out)
    assert np.allclose(pd_out.loc[common].fillna(-1).to_numpy(),
                       db_out.loc[common].fillna(-1).to_numpy(), atol=1e-9)
