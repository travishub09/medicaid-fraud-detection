"""
billing_lm.py — a self-supervised "billing language model" (the moonshot).

The aspiration (docs/platform/17): pre-train a transformer on the sequence of
claims so every provider gets an embedding and fraud shows up as low-probability
("surprising") billing. A full transformer is out of scope here; this delivers the
SAME two ideas dependency-light (numpy/scipy), which is what a tree model can use:

  code embeddings        treat each provider as a "document" of HCPCS codes; codes
                         that co-occur across providers are embedded near each other
                         (PPMI over the code co-occurrence matrix → truncated SVD —
                         the word2vec-as-matrix-factorization shortcut).
  provider embedding     the claim-weighted mean of its code vectors → ``billing_emb_*``,
                         a dense representation learned from billing CONTENT (no
                         labels, no graph, no hand-engineering).
  billing_surprisal      cross-entropy of a provider's code mix against its
                         taxonomy's code distribution — how *unlikely* this billing
                         is for the specialty. High surprisal = the "model is
                         surprised," the scheme-agnostic novelty signal.

All self-supervised (no labels) and clean (billing content only). Deterministic
(sign-canonicalized SVD). Scales via a DuckDB pre-aggregation to (npi, code, claims).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EMB_PREFIX = "billing_emb_"


def _ppmi_svd(cooc, dim: int):
    """PPMI over a code×code co-occurrence matrix → truncated SVD code vectors,
    sign-canonicalized for determinism."""
    import scipy.sparse as sp
    from scipy.sparse.linalg import svds
    C = sp.csr_matrix(cooc, dtype=float)
    total = float(C.sum())
    if total <= 0:
        return np.zeros((C.shape[0], dim))
    rowsum = np.asarray(C.sum(1)).ravel()
    cC = C.tocoo()
    pmi = np.log((cC.data * total) / (rowsum[cC.row] * rowsum[cC.col] + 1e-12) + 1e-12)
    P = sp.csr_matrix((np.maximum(pmi, 0.0), (cC.row, cC.col)), shape=C.shape)
    k = int(min(dim, min(P.shape) - 1))
    if k < 1:
        return np.zeros((C.shape[0], dim))
    try:
        U, S, _ = svds(P.asfptype(), k=k, random_state=0)  # deterministic basis
    except Exception:
        return np.zeros((C.shape[0], dim))
    order = np.argsort(-S)
    emb = U[:, order] * np.sqrt(np.maximum(S[order], 0.0))
    for j in range(emb.shape[1]):
        col = emb[:, j]
        if col[np.argmax(np.abs(col))] < 0:
            emb[:, j] = -col
    if emb.shape[1] < dim:
        emb = np.hstack([emb, np.zeros((emb.shape[0], dim - emb.shape[1]))])
    return emb


def build_code_embeddings(npi_code: pd.DataFrame, dim: int = 16, npi_col: str = "npi",
                          code_col: str = "hcpcs") -> tuple[list, np.ndarray]:
    """Provider×code presence → code co-occurrence → PPMI/SVD code vectors."""
    import scipy.sparse as sp
    codes = sorted(npi_code[code_col].astype(str).unique())
    npis = sorted(npi_code[npi_col].astype(str).unique())
    if not codes or not npis:
        return codes, np.zeros((len(codes), dim))
    cidx = {c: i for i, c in enumerate(codes)}
    nidx = {n: i for i, n in enumerate(npis)}
    r = npi_code[npi_col].astype(str).map(nidx).to_numpy()
    c = npi_code[code_col].astype(str).map(cidx).to_numpy()
    M = sp.csr_matrix((np.ones(len(r)), (r, c)), shape=(len(npis), len(codes)))
    M.data[:] = 1.0                                   # presence (provider bills code)
    cooc = (M.T @ M)                                  # code×code: providers billing both
    return codes, _ppmi_svd(cooc, dim)


def build_code_embeddings_duckdb(spending_path: str, dim: int = 16,
                                 con=None) -> tuple[list, np.ndarray]:
    """DuckDB-native code embeddings: the code co-occurrence (providers billing both
    codes) is computed by a self-join in DuckDB — the provider×code matrix never
    materializes in pandas — then PPMI/SVD runs on the small code×code matrix."""
    import duckdb
    import scipy.sparse as sp
    own = con is None
    if con is None:
        con = duckdb.connect()
        con.execute("PRAGMA memory_limit='4GB'")   # 16GB box: leave room for pandas
    p = str(spending_path).replace("'", "''")
    cooc = con.execute(f"""
        WITH pres AS (
            SELECT DISTINCT CAST(billing_npi AS VARCHAR) npi,
                   UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) hcpcs
            FROM read_parquet('{p}') WHERE hcpcs_code IS NOT NULL
        )
        SELECT a.hcpcs c1, b.hcpcs c2, COUNT(*) n
        FROM pres a JOIN pres b ON a.npi = b.npi GROUP BY 1, 2
    """).df()
    if own:
        con.close()
    if not len(cooc):
        return [], np.zeros((0, dim))
    codes = sorted(set(cooc["c1"]) | set(cooc["c2"]))
    idx = {c: i for i, c in enumerate(codes)}
    r = cooc["c1"].map(idx).to_numpy(); c = cooc["c2"].map(idx).to_numpy()
    C = sp.csr_matrix((cooc["n"].to_numpy(float), (r, c)), shape=(len(codes), len(codes)))
    return codes, _ppmi_svd(C, dim)


def provider_embeddings_duckdb(spending_path: str, codes: list, code_vecs: np.ndarray,
                               con=None) -> pd.DataFrame:
    """DuckDB-native provider embedding: the dollar-weighted mean of each provider's
    code vectors is computed by a join + GROUP BY in DuckDB (no per-(npi,code) pandas
    frame). Returns npi + billing_emb_*."""
    import duckdb
    dim = code_vecs.shape[1]
    vecs = pd.DataFrame(code_vecs, columns=[f"e{i}" for i in range(dim)])
    vecs.insert(0, "hcpcs", list(codes))
    own = con is None
    if con is None:
        con = duckdb.connect()
        con.execute("PRAGMA memory_limit='4GB'")   # 16GB box: leave room for pandas
    con.register("vecs", vecs)
    p = str(spending_path).replace("'", "''")
    sums = ", ".join(f"SUM(w * e{i}) / SUM(w) AS {EMB_PREFIX}{i}" for i in range(dim))
    out = con.execute(f"""
        WITH w AS (
            SELECT CAST(billing_npi AS VARCHAR) npi,
                   UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) hcpcs,
                   SUM(CAST(total_paid AS DOUBLE)) + 1e-9 w
            FROM read_parquet('{p}') WHERE hcpcs_code IS NOT NULL GROUP BY 1, 2
        )
        SELECT w.npi, {sums}
        FROM w JOIN vecs ON w.hcpcs = vecs.hcpcs GROUP BY w.npi
    """).df()
    if own:
        con.close()
    return out


def billing_surprisal_duckdb(spending_path: str, provider_dim: pd.DataFrame,
                             con=None) -> pd.DataFrame:
    """DuckDB-native surprisal: cross-entropy of each provider's code mix vs its
    taxonomy's code distribution, computed entirely in SQL (matches billing_surprisal)."""
    import duckdb
    pdim = provider_dim[["npi", "taxonomy_code"]].copy()
    pdim["npi"] = pdim["npi"].astype(str)
    pdim["taxonomy_code"] = pdim["taxonomy_code"].fillna("").astype(str)
    own = con is None
    if con is None:
        con = duckdb.connect()
        con.execute("PRAGMA memory_limit='4GB'")   # 16GB box: leave room for pandas
    con.register("pdim", pdim)
    p = str(spending_path).replace("'", "''")
    out = con.execute(f"""
        WITH base AS (
            SELECT CAST(s.billing_npi AS VARCHAR) npi,
                   UPPER(TRIM(CAST(s.hcpcs_code AS VARCHAR))) hcpcs,
                   SUM(CAST(s.total_paid AS DOUBLE)) w, pdim.taxonomy_code tax
            FROM read_parquet('{p}') s JOIN pdim ON CAST(s.billing_npi AS VARCHAR)=pdim.npi
            WHERE s.hcpcs_code IS NOT NULL GROUP BY 1, 2, 4
        ),
        tax_code AS (SELECT tax, hcpcs, SUM(w) cw FROM base GROUP BY 1, 2),
        tax_tot AS (SELECT tax, SUM(cw) tot, COUNT(*) k FROM tax_code GROUP BY 1),
        prob AS (
            SELECT tc.tax, tc.hcpcs, (tc.cw + 1.0)/(tt.tot + tt.k) p
            FROM tax_code tc JOIN tax_tot tt ON tc.tax = tt.tax
        )
        SELECT base.npi, SUM(base.w * -ln(prob.p)) / SUM(base.w) AS billing_surprisal
        FROM base JOIN prob ON base.tax = prob.tax AND base.hcpcs = prob.hcpcs
        GROUP BY base.npi
    """).df()
    if own:
        con.close()
    return out


def provider_embeddings(npi_code: pd.DataFrame, codes: list, code_vecs: np.ndarray,
                        npi_col: str = "npi", code_col: str = "hcpcs",
                        weight_col: str = "weight") -> pd.DataFrame:
    """Claim-weighted mean of a provider's code vectors → billing_emb_* per NPI."""
    dim = code_vecs.shape[1]
    cidx = {c: i for i, c in enumerate(codes)}
    df = npi_code.copy()
    df["_ci"] = df[code_col].astype(str).map(cidx)
    df["_w"] = pd.to_numeric(df.get(weight_col, 1.0), errors="coerce").fillna(0.0).clip(lower=0) + 1e-9
    rows = []
    for npi, g in df.dropna(subset=["_ci"]).groupby(npi_col):
        vecs = code_vecs[g["_ci"].astype(int).to_numpy()]
        w = g["_w"].to_numpy()
        rows.append((str(npi), (vecs * w[:, None]).sum(0) / w.sum()))
    if not rows:
        return pd.DataFrame(columns=["npi"] + [f"{EMB_PREFIX}{i}" for i in range(dim)])
    emb = pd.DataFrame([r[1] for r in rows], columns=[f"{EMB_PREFIX}{i}" for i in range(dim)])
    emb.insert(0, "npi", [r[0] for r in rows])
    return emb


def billing_surprisal(npi_code: pd.DataFrame, taxonomy: pd.DataFrame,
                      npi_col: str = "npi", code_col: str = "hcpcs",
                      weight_col: str = "weight") -> pd.DataFrame:
    """Per-NPI cross-entropy of the provider's code mix vs its taxonomy's code
    distribution → ``billing_surprisal`` (high = unusual codes for the specialty)."""
    tax = taxonomy[["npi", "taxonomy_code"]].copy()
    tax["npi"] = tax["npi"].astype(str)
    df = npi_code.drop(columns=["taxonomy_code"], errors="ignore").copy()
    df[npi_col] = df[npi_col].astype(str)
    df["_w"] = pd.to_numeric(df.get(weight_col, 1.0), errors="coerce").fillna(0.0).clip(lower=0)
    df = df.merge(tax, left_on=npi_col, right_on="npi", how="left").drop(columns=["npi"]) \
        if npi_col != "npi" else df.merge(tax, on="npi", how="left")
    df["taxonomy_code"] = df["taxonomy_code"].fillna("").astype(str)
    df[code_col] = df[code_col].astype(str)

    # p(code | taxonomy) from the taxonomy's aggregate weighted code distribution
    tw = df.groupby(["taxonomy_code", code_col])["_w"].sum().rename("cw").reset_index()
    tot = tw.groupby("taxonomy_code")["cw"].transform("sum")
    tw["p"] = (tw["cw"] + 1.0) / (tot + tw.groupby("taxonomy_code")["cw"].transform("size"))
    pmap = {(r.taxonomy_code, getattr(r, code_col)): r.p for r in tw.itertuples()}

    rows = []
    for npi, g in df.groupby(npi_col):
        w = g["_w"].to_numpy()
        if w.sum() <= 0:
            rows.append((npi, np.nan)); continue
        nll = [-np.log(pmap.get((t, c), 1e-6)) for t, c in zip(g["taxonomy_code"], g[code_col])]
        rows.append((npi, float(np.average(nll, weights=w))))
    return pd.DataFrame(rows, columns=["npi", "billing_surprisal"])
