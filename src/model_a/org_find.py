"""
org_find.py — "did we see this one?" and "who else bills like it?"

Two questions counsel asks about a name that arrived from outside the model
(a tip, a press release, a PI's observation), answered from the operator's own
data in one command, never from memory:

  1. NAME LOOKUP.  Where does <name> sit in our universe?  Every NPI whose
     NPPES organization or provider name matches (regex, case-insensitive) in
     processed/provider_dim.parquet, then the same NPIs in the run matrix (org
     node, state, dollars, the training label, the top scheme subscores) and in
     every priority list the run wrote (rank, score, top schemes).  "Not in the
     matrix" is an answer too: it means the NPI never billed the program in the
     spending window, or billed under another number.

  2. CODE CONCENTRATION.  Which billing NPIs carry the most dollars on a code
     set (e.g. the TMS codes 90867/90868/90869), what share of their own billing
     that is, how many distinct rendering clinicians sit under the number, and
     whether the number is an INDIVIDUAL's.  A technician-delivered service
     billed under one psychiatrist's individual NPI across many sites is the
     rendering-provider shape; the table is the candidate list for that shape,
     ranked by dollars.  With --name, the matched NPIs' own share on the codes
     is printed first.

Everything is a WHERE IN or a GROUP BY streamed through DuckDB; the spending
fact never enters pandas.  Output is Markdown (stdout, or --out).  Leads for
human review, never accusations: a share is a question for the field.

    python -m src.model_a.org_find --run-dir @champion --name "learning to achieve"
    python -m src.model_a.org_find --run-dir @champion --name "learning to achieve" --codes 90867,90868,90869
    python -m src.model_a.org_find --codes 90867,90868,90869 --top 50 --out tms_concentration.md
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import pandas as pd

from src.attempt_2.duck import connect as _duck_connect


def _sql_str(p) -> str:
    return str(p).replace("'", "''")


def _data_root(data_root: str | None) -> Path:
    return Path(data_root or os.environ.get("MEDICAID_DATA_ROOT")
                or (Path.home() / "Desktop" / "data"))


def _cols(con, parquet: Path) -> list[str]:
    return [c[0] for c in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{_sql_str(parquet)}')").fetchall()]


def _codes(spec: str | None) -> list[str]:
    if not spec:
        return []
    return sorted({c.strip().upper() for c in re.split(r"[,; ]+", spec) if c.strip()})


# ---------------------------------------------------------------- name lookup

def find_in_provider_dim(provider_dim: Path, pattern: str, con=None,
                         limit: int = 200) -> pd.DataFrame:
    """NPIs whose NPPES org or provider name matches ``pattern`` (regex, case-
    insensitive).  Returns npi, entity_type, name, city, state (<= limit rows)."""
    con = con or _duck_connect()
    cols = _cols(con, provider_dim)
    name_cols = [c for c in ("org_legal_name", "provider_name") if c in cols]
    if not name_cols:
        raise ValueError(f"{provider_dim}: no org_legal_name / provider_name column")
    name_expr = " || ' ' || ".join(f"COALESCE(CAST({c} AS VARCHAR), '')" for c in name_cols)
    sel = {"city": "addr_city", "state": "addr_state", "taxonomy": "taxonomy_code"}
    extra = ", ".join(f"CAST({v} AS VARCHAR) AS {k}" if v in cols else f"NULL AS {k}"
                      for k, v in sel.items())
    et = "CAST(entity_type AS VARCHAR)" if "entity_type" in cols else "NULL"
    return con.execute(f"""
        SELECT CAST(npi AS VARCHAR) AS npi, {et} AS entity_type,
               TRIM({name_expr}) AS name, {extra}
        FROM read_parquet('{_sql_str(provider_dim)}')
        WHERE regexp_matches(UPPER({name_expr}), ?)
        ORDER BY name LIMIT {int(limit)}""", [pattern.upper()]).df()


def find_in_matrix(matrix: Path, npis: list[str], pattern: str | None = None,
                   con=None, top_k: int = 5) -> pd.DataFrame:
    """Rows of the run matrix for ``npis`` (or, with ``pattern``, by name match on
    the matrix's own name columns too): org node, state, dollars, label, and the
    top-k scheme subscores per row as 'scheme=value|...'."""
    con = con or _duck_connect()
    cols = _cols(con, matrix)
    sub = [c for c in cols if c.startswith("subscore_")]
    label = next((c for c in ("provider_on_exclusion", "provider_on_leie") if c in cols), None)
    want = [c for c in ("npi", "org_node_id", "org_legal_name", "provider_name",
                        "practice_state", "net_paid", "rank", "assessable") if c in cols]
    con.execute("CREATE OR REPLACE TEMP TABLE want_npi(npi VARCHAR)")
    con.executemany("INSERT INTO want_npi VALUES (?)", [(str(n),) for n in npis])
    name_cols = [c for c in ("org_legal_name", "provider_name") if c in cols]
    where = "CAST(npi AS VARCHAR) IN (SELECT npi FROM want_npi)"
    params: list = []
    if pattern and name_cols:
        expr = " || ' ' || ".join(f"COALESCE(CAST({c} AS VARCHAR), '')" for c in name_cols)
        where += f" OR regexp_matches(UPPER({expr}), ?)"
        params.append(pattern.upper())
    sel = ", ".join(f"CAST({c} AS VARCHAR) AS {c}" if c in ("npi", "org_node_id") else c
                    for c in want)
    if label:
        sel += f", {label} AS label"
    if sub:
        sel += ", " + ", ".join(sub)
    df = con.execute(f"SELECT {sel} FROM read_parquet('{_sql_str(matrix)}') WHERE {where}",
                     params).df()
    if sub and len(df):
        def _top(r):
            vals = {c.replace("subscore_", ""): float(r[c]) for c in sub
                    if pd.notna(r[c]) and float(r[c]) > 0}
            best = sorted(vals, key=lambda k: -vals[k])[:top_k]
            return "|".join(f"{k}={vals[k]:.2f}" for k in best)
        df["top_subscores"] = df.apply(_top, axis=1)
        df = df.drop(columns=sub)
    return df


def find_in_lists(run_dir: Path, org_ids: list[str], pattern: str | None = None) -> pd.DataFrame:
    """Every provider_priority_list*.csv in the run: rows whose org_id is in
    ``org_ids`` or whose org_name matches ``pattern``; rank = 1-based row order."""
    out = []
    ids = {str(o) for o in org_ids}
    rx = re.compile(pattern, re.I) if pattern else None
    for p in sorted(run_dir.glob("provider_priority_list*.csv")):
        try:
            df = pd.read_csv(p, dtype=str)
        except Exception:
            continue
        if "org_id" not in df.columns:
            continue
        df["rank"] = range(1, len(df) + 1)
        hit = df["org_id"].astype(str).isin(ids)
        if rx is not None and "org_name" in df.columns:
            hit |= df["org_name"].fillna("").str.contains(rx)
        h = df.loc[hit].copy()
        h.insert(0, "list", p.name)
        h["list_rows"] = len(df)
        keep = [c for c in ("list", "rank", "list_rows", "org_id", "org_name", "n_providers",
                            "total_billed", "score_dollar_weighted", "score_max",
                            "score_p_any", "top_schemes") if c in h.columns]
        out.append(h[keep])
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["list", "rank", "list_rows", "org_id", "org_name"])


# --------------------------------------------------------- code concentration

def code_concentration(spending: Path, codes: list[str], con=None, top: int = 50,
                       npis: list[str] | None = None,
                       provider_dim: Path | None = None) -> pd.DataFrame:
    """Per billing NPI: dollars on ``codes``, share of the NPI's total, lines,
    patient-months, distinct rendering NPIs on the codes, months active, and
    (with provider_dim) name / entity type / state.  ``npis`` restricts to a
    set (the name-lookup hits); otherwise the national top ``top`` by dollars."""
    con = con or _duck_connect()
    if not codes:
        raise ValueError("code_concentration needs at least one code")
    cols = _cols(con, spending)
    p = _sql_str(spending)
    lines = "CAST(total_claim_lines AS DOUBLE)" if "total_claim_lines" in cols else "NULL"
    pats = "CAST(total_patients AS DOUBLE)" if "total_patients" in cols else "NULL"
    srv = "CAST(servicing_npi AS VARCHAR)" if "servicing_npi" in cols else "NULL"
    con.execute("CREATE OR REPLACE TEMP TABLE want_code(hcpcs VARCHAR)")
    con.executemany("INSERT INTO want_code VALUES (?)", [(c,) for c in codes])
    restrict = ""
    if npis:
        con.execute("CREATE OR REPLACE TEMP TABLE want_b(npi VARCHAR)")
        con.executemany("INSERT INTO want_b VALUES (?)", [(str(n),) for n in npis])
        restrict = "WHERE CAST(billing_npi AS VARCHAR) IN (SELECT npi FROM want_b)"
    # totals per billing NPI (all codes) and on the code set, one pass each
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE tot AS
        SELECT CAST(billing_npi AS VARCHAR) npi, SUM(CAST(total_paid AS DOUBLE)) paid_all,
               COUNT(DISTINCT {srv}) n_servicing_all
        FROM read_parquet('{p}') {restrict} GROUP BY 1""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE oncode AS
        SELECT CAST(billing_npi AS VARCHAR) npi,
               SUM(CAST(total_paid AS DOUBLE)) paid_codes,
               SUM({lines}) lines_codes, SUM({pats}) patient_months_codes,
               COUNT(DISTINCT {srv}) n_servicing_codes,
               COUNT(DISTINCT substr(CAST(service_month AS VARCHAR), 1, 7)) months_codes,
               MIN(substr(CAST(service_month AS VARCHAR), 1, 7)) first_month,
               MAX(substr(CAST(service_month AS VARCHAR), 1, 7)) last_month
        FROM read_parquet('{p}')
        WHERE UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) IN (SELECT hcpcs FROM want_code)
          {"AND CAST(billing_npi AS VARCHAR) IN (SELECT npi FROM want_b)" if npis else ""}
        GROUP BY 1""")
    df = con.execute(f"""
        SELECT o.npi, o.paid_codes, t.paid_all,
               CASE WHEN t.paid_all > 0 THEN o.paid_codes / t.paid_all END AS code_share,
               o.lines_codes, o.patient_months_codes, o.n_servicing_codes,
               t.n_servicing_all, o.months_codes, o.first_month, o.last_month
        FROM oncode o JOIN tot t USING (npi)
        ORDER BY o.paid_codes DESC LIMIT {int(top) if not npis else 100000}""").df()
    if provider_dim is not None and Path(provider_dim).exists() and len(df):
        con.execute("CREATE OR REPLACE TEMP TABLE hit(npi VARCHAR)")
        con.executemany("INSERT INTO hit VALUES (?)", [(n,) for n in df["npi"].astype(str)])
        pc = _cols(con, Path(provider_dim))
        name = " || ' ' || ".join(f"COALESCE(CAST({c} AS VARCHAR), '')"
                                  for c in ("org_legal_name", "provider_name") if c in pc) or "''"
        et = "CAST(entity_type AS VARCHAR)" if "entity_type" in pc else "NULL"
        st = "CAST(addr_state AS VARCHAR)" if "addr_state" in pc else "NULL"
        tx = "CAST(taxonomy_code AS VARCHAR)" if "taxonomy_code" in pc else "NULL"
        dim = con.execute(f"""
            SELECT CAST(npi AS VARCHAR) AS npi, TRIM({name}) AS name, {et} AS entity_type,
                   {st} AS state, {tx} AS taxonomy
            FROM read_parquet('{_sql_str(provider_dim)}')
            WHERE CAST(npi AS VARCHAR) IN (SELECT npi FROM hit)""").df()
        df = df.merge(dim, on="npi", how="left")
    # the shape: an individual's number carrying the codes with <= 1 rendering
    # clinician is "one person's number"; many renderers under an org number is
    # the ordinary agency shape
    if len(df):
        et = df.get("entity_type", pd.Series([None] * len(df))).astype(str)
        df["shape"] = [
            ("individual_npi_one_renderer" if e == "1" and (pd.isna(n) or n <= 1)
             else "individual_npi_many_renderers" if e == "1"
             else "org_npi_one_renderer" if e == "2" and (pd.isna(n) or n <= 1)
             else "org_npi" if e == "2" else "unknown_entity")
            for e, n in zip(et, df["n_servicing_codes"])]
    return df


# -------------------------------------------------------------------- report

def _money(x) -> str:
    return "" if x is None or pd.isna(x) else f"${float(x):,.0f}"


def _pct(x) -> str:
    return "" if x is None or pd.isna(x) else f"{100 * float(x):.0f}%"


def _md_table(df: pd.DataFrame, cols: list[str], fmt: dict | None = None, max_rows: int = 60) -> str:
    fmt = fmt or {}
    cols = [c for c in cols if c in df.columns]
    if not len(df) or not cols:
        return "_none_\n"
    L = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.head(max_rows).iterrows():
        cells = []
        for c in cols:
            v = r[c]
            cells.append(fmt[c](v) if c in fmt else ("" if pd.isna(v) else str(v)))
        L.append("| " + " | ".join(cells) + " |")
    if len(df) > max_rows:
        L.append(f"| ... {len(df) - max_rows} more rows | " + " | ".join([""] * (len(cols) - 1)) + " |")
    return "\n".join(L) + "\n"


def report(name: str | None, dim_hits: pd.DataFrame | None, mat_hits: pd.DataFrame | None,
           list_hits: pd.DataFrame | None, codes: list[str],
           conc_named: pd.DataFrame | None, conc_national: pd.DataFrame | None,
           sources: dict) -> str:
    L = ["# org_find", "",
         "_Leads for human review, never accusations. A share or a count is a question for "
         "the field; the innocent explanations (supervised services billed under the "
         "supervising physician, an agency billing NPI over contracted clinicians, "
         "a second site) are ruled out first._", ""]
    L.append("Sources: " + "; ".join(f"{k} = {v}" for k, v in sources.items()) + "\n")
    money = {"net_paid": _money, "total_billed": _money, "paid_codes": _money,
             "paid_all": _money, "code_share": _pct}
    if name is not None:
        L.append(f"## 1. Name lookup: `{name}`\n")
        n_dim = 0 if dim_hits is None else len(dim_hits)
        L.append(f"**NPPES (provider_dim): {n_dim} NPI(s) match.**\n")
        if dim_hits is not None and len(dim_hits):
            L.append(_md_table(dim_hits, ["npi", "entity_type", "name", "city", "state", "taxonomy"]))
        n_mat = 0 if mat_hits is None else len(mat_hits)
        L.append(f"\n**Run matrix: {n_mat} row(s).** "
                 + ("Not in the matrix = never billed the program in the spending window under "
                    "these NPIs, or billed under another number (an org NPI, a management "
                    "company); check the code-concentration table below for that shape."
                    if n_mat == 0 else "") + "\n")
        if mat_hits is not None and len(mat_hits):
            L.append(_md_table(mat_hits, ["npi", "org_node_id", "org_legal_name", "provider_name",
                                          "practice_state", "net_paid", "label", "top_subscores"],
                               money))
        n_lists = 0 if list_hits is None else len(list_hits)
        L.append(f"\n**Priority lists: {n_lists} row(s).**\n")
        if list_hits is not None and len(list_hits):
            L.append(_md_table(list_hits, ["list", "rank", "list_rows", "org_id", "org_name",
                                           "n_providers", "total_billed", "score_dollar_weighted",
                                           "score_max", "top_schemes"], money))
        if mat_hits is not None and len(mat_hits) and "org_node_id" in mat_hits.columns:
            oids = sorted({str(o) for o in mat_hits["org_node_id"].dropna() if str(o)})
            if oids:
                L.append("\nFact sheet for the field (the WHEN sheet and implied hours):\n")
                L.append("```\npython -m src.model_a.pi_facts --run-dir <run> --deep --orgs \""
                         + ";".join(oids) + "\" --out-prefix pi_facts_lookup\n```\n")
    if codes:
        sec = 2 if name is not None else 1
        L.append(f"## {sec}. Code concentration: {', '.join(codes)}\n")
        if conc_named is not None:
            L.append(f"**The matched NPIs on these codes ({len(conc_named)} with any dollars):**\n")
            L.append(_md_table(conc_named, ["npi", "name", "entity_type", "state", "paid_codes",
                                            "paid_all", "code_share", "lines_codes",
                                            "patient_months_codes", "n_servicing_codes",
                                            "months_codes", "first_month", "last_month", "shape"],
                               money))
        if conc_national is not None:
            L.append(f"\n**National top {len(conc_national)} billing NPIs by dollars on these codes** "
                     "(shape: an INDIVIDUAL's NPI carrying the codes with one or no rendering "
                     "clinician on the claims is one person's number; an org NPI over many "
                     "renderers is the ordinary agency shape):\n")
            L.append(_md_table(conc_national, ["npi", "name", "entity_type", "state", "taxonomy",
                                               "paid_codes", "paid_all", "code_share",
                                               "lines_codes", "patient_months_codes",
                                               "n_servicing_codes", "months_codes", "shape"],
                               money, max_rows=len(conc_national)))
            if "shape" in conc_national.columns:
                cnt = conc_national["shape"].value_counts()
                L.append("\nShapes: " + ", ".join(f"{k} {v}" for k, v in cnt.items()) + "\n")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default=None,
                    help="regex over NPPES org / provider name, case-insensitive")
    ap.add_argument("--run-dir", default=None, help="run folder (or '@champion')")
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--provider-dim", default=None, help="processed/provider_dim.parquet")
    ap.add_argument("--spending", default=None, help="processed/spending_fact.parquet")
    ap.add_argument("--codes", default=None, help="comma-separated HCPCS/CPT codes")
    ap.add_argument("--top", type=int, default=50, help="national rows for --codes")
    ap.add_argument("--no-national", action="store_true",
                    help="with --name and --codes: skip the national table")
    ap.add_argument("--out", default=None, help="write the Markdown here (default stdout)")
    a = ap.parse_args()
    if not a.name and not a.codes:
        ap.error("give --name and/or --codes")
    root = _data_root(a.data_root)
    dim = Path(a.provider_dim) if a.provider_dim else root / "processed" / "provider_dim.parquet"
    spend = Path(a.spending) if a.spending else root / "processed" / "spending_fact.parquet"
    run_dir = None
    if a.run_dir:
        from .run_registry import resolve_run_dir
        run_dir = Path(resolve_run_dir(a.run_dir, data_root=root))
    con = _duck_connect()
    sources = {"provider_dim": str(dim) if dim.exists() else f"{dim} (MISSING)",
               "spending": str(spend) if spend.exists() else f"{spend} (MISSING)",
               "run_dir": str(run_dir) if run_dir else "(none)"}
    codes = _codes(a.codes)
    dim_hits = mat_hits = list_hits = conc_named = conc_national = None
    npis: list[str] = []
    if a.name:
        if dim.exists():
            dim_hits = find_in_provider_dim(dim, a.name, con=con)
            npis = list(dim_hits["npi"].astype(str))
        else:
            print(f"[org_find] provider_dim missing: {dim}")
        if run_dir is not None:
            mp = run_dir / "provider_features_for_model.parquet"
            if mp.exists():
                mat_hits = find_in_matrix(mp, npis, pattern=a.name, con=con)
                if len(mat_hits):
                    npis = sorted(set(npis) | set(mat_hits["npi"].astype(str)))
            else:
                print(f"[org_find] matrix missing: {mp}")
            oids = [] if mat_hits is None else [str(o) for o in mat_hits.get("org_node_id", pd.Series(dtype=str)).dropna()]
            list_hits = find_in_lists(run_dir, oids, pattern=a.name)
    if codes and spend.exists():
        if npis:
            conc_named = code_concentration(spend, codes, con=con, npis=npis,
                                            provider_dim=dim if dim.exists() else None)
        if not (a.name and a.no_national):
            conc_national = code_concentration(spend, codes, con=con, top=a.top,
                                               provider_dim=dim if dim.exists() else None)
    elif codes:
        print(f"[org_find] spending fact missing: {spend}")
    md = report(a.name, dim_hits, mat_hits, list_hits, codes, conc_named, conc_national, sources)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print(f"[org_find] wrote {a.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
