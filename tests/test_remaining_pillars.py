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
