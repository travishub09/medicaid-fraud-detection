"""
billing_sequence_lm.py — an ORDER-AWARE billing language model (moonshot, depth).

``billing_lm.py`` is bag-of-codes (PPMI/SVD co-occurrence) — it ignores ORDER. The
moonshot's real ambition is a sequence model: how *surprising* is the order in which
a provider adopts and bills codes. A neural transformer is the eventual form, but it
needs a GPU and line-level claim ordering (dormant data) — so this delivers the
order-aware core dependency-light, behind a ``sequence_surprisal`` interface a
transformer can later drop into unchanged.

The sequence here is each provider's CODE-ADOPTION trajectory — codes ordered by the
month they first appear — and the model is a smoothed bigram transition LM per
taxonomy: P(next adopted code | previous), learned across the specialty's providers.
A provider whose adoption *transitions* are improbable for its specialty (a hospice
that adopts surgical codes right after enrolling) scores high ``sequence_surprisal``
— a signal bag-of-codes and per-code prevalence both miss.

  build_transition_model(claims, ...)  → per-taxonomy bigram transition probs
  sequence_surprisal(claims, model)     → per-NPI mean negative log-prob of its
                                          adoption transitions (true sequence NLL)

numpy/pandas only, deterministic. Swap the bigram estimator for a transformer LM
(torch, optional) behind the same interface when a GPU + line-level data exist.
"""

from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd

ADD_K = 1.0          # additive (Laplace) smoothing on transitions
BOS = "<bos>"        # start-of-sequence token


def _adoption_sequences(claims: pd.DataFrame, npi_col: str, code_col: str,
                        month_col: str) -> dict[str, list[str]]:
    """Per provider: codes ordered by the month each first appears (adoption order)."""
    df = claims.copy()
    df[npi_col] = df[npi_col].astype(str)
    df[code_col] = df[code_col].astype(str)
    df[month_col] = df[month_col].astype(str)
    first = df.groupby([npi_col, code_col])[month_col].min().reset_index()
    first = first.sort_values([npi_col, month_col, code_col])
    return {npi: list(g[code_col]) for npi, g in first.groupby(npi_col)}


def build_transition_model(claims: pd.DataFrame, taxonomy: pd.DataFrame,
                           npi_col: str = "npi", code_col: str = "hcpcs",
                           month_col: str = "service_month") -> dict:
    """Per-taxonomy smoothed bigram transition model over adoption sequences."""
    seqs = _adoption_sequences(claims, npi_col, code_col, month_col)
    tax_map = dict(zip(taxonomy["npi"].astype(str),
                       taxonomy["taxonomy_code"].fillna("").astype(str)))
    # bigram[tax][prev][cur] = count ; vocab[tax] = set of codes
    bigram: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    ctx_tot: dict = defaultdict(lambda: defaultdict(float))
    vocab: dict = defaultdict(set)
    for npi, seq in seqs.items():
        tax = tax_map.get(npi, "")
        prev = BOS
        for code in seq:
            bigram[tax][prev][code] += 1.0
            ctx_tot[tax][prev] += 1.0
            vocab[tax].add(code)
            prev = code
    return {"bigram": bigram, "ctx_tot": ctx_tot, "vocab": vocab}


def _logp(model: dict, tax: str, prev: str, cur: str) -> float:
    v = len(model["vocab"].get(tax, ())) or 1
    cnt = model["bigram"].get(tax, {}).get(prev, {}).get(cur, 0.0)
    tot = model["ctx_tot"].get(tax, {}).get(prev, 0.0)
    return math.log((cnt + ADD_K) / (tot + ADD_K * v))


def sequence_surprisal(claims: pd.DataFrame, model: dict | None = None,
                       taxonomy: pd.DataFrame | None = None,
                       npi_col: str = "npi", code_col: str = "hcpcs",
                       month_col: str = "service_month") -> pd.DataFrame:
    """Per-NPI mean negative log-prob of its code-adoption transitions under its
    taxonomy's bigram model (high = unusual billing ORDER for the specialty)."""
    if model is None:
        if taxonomy is None:
            raise ValueError("pass a fitted model or a taxonomy to fit one")
        model = build_transition_model(claims, taxonomy, npi_col, code_col, month_col)
    seqs = _adoption_sequences(claims, npi_col, code_col, month_col)
    tax_map = dict(zip((taxonomy["npi"].astype(str) if taxonomy is not None else []),
                       (taxonomy["taxonomy_code"].fillna("").astype(str)
                        if taxonomy is not None else [])))
    rows = []
    for npi, seq in seqs.items():
        tax = tax_map.get(npi, "")
        prev, nll, n = BOS, 0.0, 0
        for code in seq:
            nll += -_logp(model, tax, prev, code)
            prev = code
            n += 1
        rows.append((npi, nll / n if n else float("nan")))
    return pd.DataFrame(rows, columns=["npi", "sequence_surprisal"])
