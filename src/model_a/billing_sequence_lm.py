"""
billing_sequence_lm.py — an ORDER-AWARE billing language model (moonshot, depth).

``billing_lm.py`` is bag-of-codes (PPMI/SVD co-occurrence) — it ignores ORDER. The
moonshot's real ambition is a sequence model: how *surprising* is the order in which
a provider adopts and bills codes. A neural transformer is the eventual form, but it
needs a GPU and line-level claim ordering (dormant data) — so this delivers the
order-aware core dependency-light, behind a ``sequence_surprisal`` interface a
transformer can later drop into unchanged.

The sequence here is each provider's CODE-ADOPTION trajectory — codes ordered by the
month they first appear — and the model is a smoothed transition LM per taxonomy:
P(next adopted code | previous), learned across the specialty's providers. A provider
whose adoption *transitions* are improbable for its specialty (a hospice that adopts
surgical codes right after enrolling) scores high ``sequence_surprisal`` — a signal
bag-of-codes and per-code prevalence both miss.

Two estimators behind the same interface:
  * ``laplace`` — additive-smoothed bigram (the simple default).
  * ``kn`` — interpolated Kneser-Ney of configurable ``order`` (bigram/trigram). KN's
    continuation probability ("how many DISTINCT contexts has this code followed",
    not raw frequency) handles the heavy tail of rare HCPCS codes far better than
    add-k, so the surprisal is calibrated rather than dominated by smoothing mass.

  build_transition_model(claims, ...)  → per-taxonomy bigram transition probs (laplace)
  build_kn_model(claims, ..., order)    → per-taxonomy interpolated-KN model
  sequence_surprisal(claims, ...)       → per-NPI mean negative log-prob of its
                                          adoption transitions (true sequence NLL)

numpy/pandas only, deterministic. Swap the estimator for a transformer LM (torch,
optional) behind the same interface when a GPU + line-level data exist.
"""

from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd

ADD_K = 1.0          # additive (Laplace) smoothing on transitions
BOS = "<bos>"        # start-of-sequence token
KN_DISCOUNT = 0.75   # default absolute discount when n1/n2 can't be estimated


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


# ----------------------------------------------------- interpolated Kneser-Ney ---
def _ngrams(seq: list[str], n: int):
    """Yield the n-grams of a sequence padded with (n-1) BOS tokens at the front."""
    padded = [BOS] * (n - 1) + seq
    for i in range(n - 1, len(padded)):
        yield tuple(padded[i - n + 1:i + 1])


def _estimate_discount(count_hist: dict) -> float:
    """Absolute discount D = n1 / (n1 + 2 n2) from the count-of-counts (Ney et al.)."""
    n1 = count_hist.get(1, 0)
    n2 = count_hist.get(2, 0)
    return n1 / (n1 + 2 * n2) if (n1 + 2 * n2) > 0 else KN_DISCOUNT


def build_kn_model(claims: pd.DataFrame, taxonomy: pd.DataFrame, order: int = 2,
                   npi_col: str = "npi", code_col: str = "hcpcs",
                   month_col: str = "service_month") -> dict:
    """Per-taxonomy interpolated Kneser-Ney model of the code-adoption sequence.

    For each taxonomy we store, for every order m=1..order: the highest-order raw
    counts and, for the lower orders, CONTINUATION counts (the number of distinct
    left-extensions a gram has) — the Kneser-Ney refinement that asks "in how many
    contexts does this code appear" rather than "how often". Returns a model dict
    consumed by ``_logp_kn``."""
    seqs = _adoption_sequences(claims, npi_col, code_col, month_col)
    tax_map = dict(zip(taxonomy["npi"].astype(str),
                       taxonomy["taxonomy_code"].fillna("").astype(str)))
    by_tax: dict[str, list[list[str]]] = defaultdict(list)
    for npi, seq in seqs.items():
        if seq:
            by_tax[tax_map.get(npi, "")].append(seq)

    model: dict = {"order": order, "tax": {}}
    for tax, sequences in by_tax.items():
        # highest-order counts c(context, w); lower orders use continuation sets
        top = defaultdict(lambda: defaultdict(float))           # ctx(order-1) -> w -> count
        cont_left = {m: defaultdict(set) for m in range(1, order)}  # m-gram -> {left words}
        for seq in sequences:
            for g in _ngrams(seq, order):
                top[g[:-1]][g[-1]] += 1.0
            for m in range(1, order):                           # continuation evidence
                for g in _ngrams(seq, m + 1):
                    cont_left[m][g[1:]].add(g[0])
        # absolute discount from the top-order count-of-counts
        hist: dict = defaultdict(int)
        for ctx, ws in top.items():
            for w, c in ws.items():
                hist[int(c)] += 1
        D = _estimate_discount(hist)
        # continuation counts: ckn[m][gram] = #distinct left words; totals per (m-1) ctx
        ckn = {m: {g: len(s) for g, s in cont_left[m].items()} for m in range(1, order)}
        vocab = set()
        for ctx, ws in top.items():
            vocab.update(ws.keys())
        model["tax"][tax] = {"D": D, "order": order,
                             "top": {k: dict(v) for k, v in top.items()},
                             "ckn": ckn, "vocab": vocab}
    return model


def _kn_prob(tm: dict, context: tuple, w: str, order: int) -> float:
    """Interpolated Kneser-Ney P(w | context) for one taxonomy model ``tm``.

    Recurses from the requested order down to the unigram continuation distribution,
    discounting each level and interpolating with the lower-order continuation model.
    """
    D = tm["D"]
    V = max(len(tm["vocab"]), 1)
    if order == 1:                                      # unigram continuation
        ck = tm["ckn"].get(1, {})
        tot = sum(ck.values())
        if tot <= 0:
            return 1.0 / V
        return max(ck.get((w,), 0) - D, 0.0) / tot + D / tot * (len(ck) or 1) * (1.0 / V)
    if order == tm["order"]:                            # highest order: raw counts
        ws = tm["top"].get(context, {})
        tot = sum(ws.values())
        num = ws.get(w, 0.0)
        followers = len(ws)
    else:                                               # middle order: continuation counts
        ck = tm["ckn"].get(order, {})
        num = ck.get(context + (w,), 0)
        tot = sum(v for g, v in ck.items() if g[:-1] == context)
        followers = sum(1 for g in ck if g[:-1] == context)
    if tot <= 0:
        return _kn_prob(tm, context[1:], w, order - 1)
    lam = D / tot * (followers or 1)
    return max(num - D, 0.0) / tot + lam * _kn_prob(tm, context[1:], w, order - 1)


def _logp_kn(model: dict, tax: str, context: tuple, w: str) -> float:
    tm = model["tax"].get(tax)
    if tm is None:
        return math.log(1e-6)
    order = model["order"]
    ctx = context[-(order - 1):] if order > 1 else ()
    p = _kn_prob(tm, ctx, w, order)
    return math.log(max(p, 1e-12))


def sequence_surprisal(claims: pd.DataFrame, model: dict | None = None,
                       taxonomy: pd.DataFrame | None = None,
                       npi_col: str = "npi", code_col: str = "hcpcs",
                       month_col: str = "service_month",
                       smoothing: str = "laplace", order: int = 2) -> pd.DataFrame:
    """Per-NPI mean negative log-prob of its code-adoption transitions under its
    taxonomy's model (high = unusual billing ORDER for the specialty).

    ``smoothing='laplace'`` (default) uses the add-k bigram; ``smoothing='kn'`` uses
    interpolated Kneser-Ney of ``order`` (2=bigram, 3=trigram) — better-calibrated on
    the rare-code tail. Pass a pre-fitted ``model`` or a ``taxonomy`` to fit one."""
    is_kn = smoothing == "kn" or (model is not None and "tax" in model)
    if model is None:
        if taxonomy is None:
            raise ValueError("pass a fitted model or a taxonomy to fit one")
        model = (build_kn_model(claims, taxonomy, order, npi_col, code_col, month_col)
                 if is_kn else
                 build_transition_model(claims, taxonomy, npi_col, code_col, month_col))
    seqs = _adoption_sequences(claims, npi_col, code_col, month_col)
    tax_map = dict(zip((taxonomy["npi"].astype(str) if taxonomy is not None else []),
                       (taxonomy["taxonomy_code"].fillna("").astype(str)
                        if taxonomy is not None else [])))
    n_order = model.get("order", 2) if is_kn else 2
    rows = []
    for npi, seq in seqs.items():
        tax = tax_map.get(npi, "")
        nll, n = 0.0, 0
        history = [BOS] * (n_order - 1)
        for code in seq:
            if is_kn:
                nll += -_logp_kn(model, tax, tuple(history), code)
            else:
                nll += -_logp(model, tax, history[-1], code)
            history.append(code)
            n += 1
        rows.append((npi, nll / n if n else float("nan")))
    return pd.DataFrame(rows, columns=["npi", "sequence_surprisal"])
