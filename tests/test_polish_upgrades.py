"""
test_polish_upgrades.py — the two polish upgrades.

USPS-CMRA exact-match + address-cluster-degree (distinct orgs per address) in the
external-grounding layer, and interpolated Kneser-Ney smoothing (configurable order)
for the code-adoption sequence LM.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ----------------------------------------------------- address grounding ++ ---
def test_cmra_exact_match_and_cluster_degree():
    from src.model_a.address_grounding import address_flags, load_cmra_reference
    # six NPIs: five distinct orgs at ONE address (shell cluster), one elsewhere
    pdim = pd.DataFrame({
        "npi": [f"{i:010d}" for i in range(6)],
        "addr_key": ["100 MAIN ST AUSTIN TX 78701"] * 5 + ["9 OAK RD AUSTIN TX 78702"],
        "org_node_id": [f"org:{i}" for i in range(5)] + ["org:5"],
    })
    cmra = load_cmra_reference(pd.DataFrame(
        {"line1": ["100 Main St"], "city": ["Austin"], "state": ["TX"], "zip": ["78701"]}))
    out = address_flags(pdim, cmra_addresses=cmra).set_index("npi")
    # the shared address matches the USPS CMRA registry exactly
    assert out.loc["0000000000", "addr_is_cmra"] == 1
    assert out.loc["0000000005", "addr_is_cmra"] == 0
    # five distinct orgs at one suite → cluster degree fires; the lone office doesn't
    assert out.loc["0000000000", "addr_distinct_orgs"] == 5
    assert out.loc["0000000000", "addr_cluster_degree"] == 1
    assert out.loc["0000000005", "addr_cluster_degree"] == 0


def test_address_flags_backcompat_without_new_inputs():
    from src.model_a.address_grounding import address_flags
    pdim = pd.DataFrame({"npi": ["1", "2"], "addr_key": ["PMB 12 X", "1 REAL ST Y"]})
    out = address_flags(pdim)                       # no cmra, no org col
    assert out.loc[out["npi"] == "1", "addr_is_mailbox"].iloc[0] == 1
    assert "addr_is_cmra" not in out.columns        # only present when a registry is given
    assert "addr_distinct_orgs" not in out.columns


# ----------------------------------------------------- Kneser-Ney sequence ---
def _claims():
    rows = []
    seqs = {"a": ["A", "B", "C"], "b": ["A", "B", "C"], "c": ["A", "B", "D"],
            "d": ["A", "C", "B"], "weird": ["Z", "Q"]}
    for npi, codes in seqs.items():
        for i, code in enumerate(codes):
            rows.append({"npi": npi, "hcpcs": code,
                         "service_month": f"2020-{i+1:02d}", "taxonomy_code": "T"})
    return pd.DataFrame(rows)


def test_kn_probabilities_sum_to_one_per_context():
    from src.model_a.billing_sequence_lm import build_kn_model, _kn_prob, BOS
    df = _claims()
    model = build_kn_model(df, df[["npi", "taxonomy_code"]].drop_duplicates(), order=2)
    tm = model["tax"]["T"]
    vocab = sorted(tm["vocab"])
    # a proper distribution: P(w | context) sums to ~1 over the vocabulary
    for ctx in [(BOS,), ("A",), ("B",)]:
        total = sum(_kn_prob(tm, ctx, w, order=2) for w in vocab)
        assert abs(total - 1.0) < 1e-6


def test_kn_surprisal_runs_bigram_and_trigram_and_flags_odd():
    from src.model_a.billing_sequence_lm import sequence_surprisal
    df = _claims()
    tax = df[["npi", "taxonomy_code"]].drop_duplicates()
    for order in (2, 3):
        sur = sequence_surprisal(df, taxonomy=tax, smoothing="kn", order=order).set_index("npi")
        assert sur["sequence_surprisal"].notna().all()
        # the off-distribution provider (codes never seen in this specialty) is most surprising
        assert sur.loc["weird", "sequence_surprisal"] >= sur.loc["a", "sequence_surprisal"]


def test_kn_differs_from_laplace():
    from src.model_a.billing_sequence_lm import sequence_surprisal
    df = _claims()
    tax = df[["npi", "taxonomy_code"]].drop_duplicates()
    kn = sequence_surprisal(df, taxonomy=tax, smoothing="kn").set_index("npi")["sequence_surprisal"]
    lap = sequence_surprisal(df, taxonomy=tax, smoothing="laplace").set_index("npi")["sequence_surprisal"]
    assert not np.allclose(kn.to_numpy(), lap.to_numpy())   # genuinely a different estimator
