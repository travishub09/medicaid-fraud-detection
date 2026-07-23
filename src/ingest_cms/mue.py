"""
mue.py — CMS Medically Unlikely Edits: the published impossible-units rule.

CMS publishes, per HCPCS/CPT code, the maximum units of service a provider
would ever plausibly report for one patient on one date of service (the MUE
table, quarterly, free, on cms.gov under NCCI). FCA settlements cite exceeding
these constantly; it is the canonical "billed more of a thing per day than is
physically plausible" rule — categorical, Escobar-material (goes to whether the
service as billed could be real), and citable to a CMS table rather than a
percentile.

Our spending fact is MONTHLY (npi × hcpcs × month × claim lines), so exact
per-day units are not observable. The detector therefore uses a CONSERVATIVE
pigeonhole bound: every claim line carries at least one unit, so if

    total_claim_lines(npi, code, month)  >  MUE(code) × days_in_month

then AT LEAST ONE day that month necessarily exceeded the published daily
maximum, no matter how the lines were distributed. Under-counts violations
(a provider can violate daily MUEs without tripping the monthly bound), never
over-counts — the right direction for a fraud flag.

Emits per NPI:
    mue_violation_share    share of the provider's paid dollars sitting on
                           (code, month) cells that violate the bound (0-1)
    mue_violation_dollars  those dollars
    n_mue_violation_cells  how many (code, month) cells violated
    worst_mue_ratio        max of lines / (MUE × days) across cells (>1 = flag)

Feeds the impossible_day scheme (mue_violation_share), which until now was
dormant for lack of a per-day source. Table drop point:
preclean/reference/mue/mue.csv (any CMS practitioner MUE csv export).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import read_csv_text

# CMS column headers vary by year/export; resolve by substring, case-blind.
_CODE_HINTS = ("hcpcs", "cpt")
_VALUE_HINTS = ("mue value", "mue_value", "practitioner services mue",
                "units", "mue")


def load_mue_table(path: str | Path) -> pd.DataFrame:
    """CMS MUE csv → tidy (hcpcs, mue_units). Tolerant of header variants;
    raises if no code or value column can be identified (wrong file, not an
    empty rule set)."""
    p = Path(path)
    df = (pd.read_parquet(p) if p.suffix == ".parquet" else read_csv_text(str(p)))
    low = {str(c).strip().lower(): c for c in df.columns}

    code_col = next((low[k] for k in low if any(h in k for h in _CODE_HINTS)), None)
    # value column: try hints MOST-specific-first, and never accept the
    # rationale/indicator text columns ("MUE Rationale" contains "mue" too)
    val_col = None
    for hint in _VALUE_HINTS:
        val_col = next((low[k] for k in low
                        if hint in k and low[k] != code_col
                        and "rationale" not in k and "indicator" not in k), None)
        if val_col:
            break
    if code_col is None or val_col is None:
        raise ValueError(f"MUE file {p.name}: could not find code/value columns; "
                         f"saw {list(df.columns)[:8]}")
    out = pd.DataFrame({
        "hcpcs": df[code_col].fillna("").astype(str).str.strip().str.upper(),
        "mue_units": pd.to_numeric(df[val_col], errors="coerce"),
    })
    out = out[(out["hcpcs"] != "") & out["mue_units"].notna() & (out["mue_units"] > 0)]
    # a code can appear per-quarter; keep the most permissive (highest) value so
    # the bound stays conservative across editions
    return (out.groupby("hcpcs", as_index=False)["mue_units"].max())


_DAYS = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30,
         10: 31, 11: 30, 12: 31}          # 29 for Feb: permissive = conservative


def compute_mue_violations(spending_path: str | Path,
                           mue: pd.DataFrame) -> pd.DataFrame:
    """Per-NPI conservative MUE-violation metrics from the monthly fact."""
    import duckdb
    if mue is None or not len(mue):
        return pd.DataFrame(columns=["npi", "mue_violation_share",
                                     "mue_violation_dollars",
                                     "n_mue_violation_cells", "worst_mue_ratio"])
    con = duckdb.connect()
    cols = con.execute(
        f"SELECT * FROM read_parquet('{spending_path}') LIMIT 0").df().columns
    low = {c.lower(): c for c in cols}

    def _c(*names):
        return next((low[n] for n in names if n in low), None)

    npi_c = _c("billing_npi", "npi")
    code_c = _c("hcpcs_code", "hcpcs", "hcpcs_cd")
    month_c = _c("service_month", "claim_from_month", "month")
    lines_c = _c("total_claim_lines", "claim_lines", "lines")
    paid_c = _c("total_paid", "net_paid", "paid")
    if not all((npi_c, code_c, month_c, lines_c, paid_c)):
        raise ValueError(f"spending fact missing npi/code/month/lines/paid; "
                         f"saw {list(cols)[:12]}")
    con.register("mue", mue)
    days_case = "CASE CAST(SUBSTR(m,6,2) AS INT) " + " ".join(
        f"WHEN {k} THEN {v}" for k, v in _DAYS.items()) + " ELSE 31 END"
    out = con.execute(f"""
        WITH cell AS (
            SELECT CAST(s."{npi_c}" AS VARCHAR)   AS npi,
                   UPPER(CAST(s."{code_c}" AS VARCHAR)) AS hcpcs,
                   CAST(s."{month_c}" AS VARCHAR) AS m,
                   SUM(CAST(s."{lines_c}" AS DOUBLE)) AS lines,
                   SUM(CAST(s."{paid_c}" AS DOUBLE))  AS paid
            FROM read_parquet('{spending_path}') s
            GROUP BY 1, 2, 3
        ),
        judged AS (
            SELECT c.npi, c.paid,
                   c.lines / (mue.mue_units * ({days_case})) AS ratio
            FROM cell c JOIN mue ON c.hcpcs = mue.hcpcs
        )
        SELECT npi,
               SUM(CASE WHEN ratio > 1 THEN paid ELSE 0 END)
                   / NULLIF(SUM(paid), 0)                  AS mue_violation_share,
               SUM(CASE WHEN ratio > 1 THEN paid ELSE 0 END) AS mue_violation_dollars,
               SUM(CASE WHEN ratio > 1 THEN 1 ELSE 0 END)  AS n_mue_violation_cells,
               MAX(ratio)                                   AS worst_mue_ratio
        FROM judged GROUP BY 1
    """).df()
    con.close()
    out["npi"] = out["npi"].astype(str)
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mue", required=True, help="CMS MUE csv")
    ap.add_argument("--spending", required=True, help="spending_fact parquet")
    ap.add_argument("--out", required=True, help="output parquet")
    args = ap.parse_args()
    table = load_mue_table(args.mue)
    res = compute_mue_violations(args.spending, table)
    res.to_parquet(args.out, index=False)
    n_hot = int((pd.to_numeric(res["mue_violation_share"], errors="coerce") > 0).sum())
    print(f"[mue] {len(table):,} codes in table; {len(res):,} NPIs scored; "
          f"{n_hot:,} with at least one violating (code, month) cell → {args.out}")


if __name__ == "__main__":
    main()
