"""
exposure.py — real per-org annual program payments for the ERV formula.

ERV = adjusted_prob × exposure, where exposure = annual program payments ×
scheme_recovery_multiplier. Until now `payments` was a column the caller had to
supply; this module computes it from the actual spending base:

    spending (billing_npi × service_month × total_paid)
      → map billing NPI → canonical org (npi_to_org from src.entity_graph)
      → annualize → mean annual payments per org (+ latest year, total, years)

Discipline (matches integrate.py): dollar conservation is asserted — matched +
unresolved must equal the input total to the cent; unresolved billing NPIs are
reported, never silently dropped.

Input spending columns (the spending_fact / spending_provider_base shape):
    billing_npi · service_month ("YYYY-MM") · total_paid
"""

from __future__ import annotations

import pandas as pd


def load_spending_aggregated(path: str) -> pd.DataFrame:
    """Stream spending_fact via DuckDB to the (billing_npi, service_month,
    hcpcs_code) grain with summed total_paid.

    The published spending_fact carries ~18 columns (servicing NPI + provider-dim
    enrichment); loading all 238M rows into pandas OOMs a laptop. Every Model A
    exposure consumer (annual/scoped payments, growth, plausibility) needs only
    these four fields and re-aggregates by year/month/hcpcs anyway, so summing at
    this grain is exact and reads a fraction of the bytes. DuckDB does the GROUP BY
    streaming (spills to disk if needed) rather than materializing the full table.
    """
    import duckdb
    return duckdb.connect().execute(
        "SELECT billing_npi, service_month, hcpcs_code, "
        "SUM(total_paid) AS total_paid "
        f"FROM read_parquet('{path}') "
        "GROUP BY billing_npi, service_month, hcpcs_code").df()


def annual_payments_per_org(spending: pd.DataFrame,
                            npi_to_org: pd.DataFrame
                            ) -> tuple[pd.DataFrame, dict]:
    """Per-org payment aggregates + a reconciliation dict (assert-checked).

    Returns ``(payments, recon)`` where payments has one row per org_node_id:
    ``payments`` (mean annual — the exposure input), ``payments_latest_year``,
    ``payments_total``, ``years_observed``; and recon carries the conservation
    numbers for the QA report.
    """
    s = spending.copy()
    s["billing_npi"] = s["billing_npi"].astype(str)
    s["total_paid"] = pd.to_numeric(s["total_paid"], errors="coerce").fillna(0.0)
    s["year"] = s["service_month"].astype(str).str.slice(0, 4)
    total_in = float(s["total_paid"].sum())

    # a duplicate NPI in the crosswalk means ambiguous attribution — that must
    # hard-fail (repo rule 2), not silently resolve to whichever row came last
    xw_npis = npi_to_org["npi"].astype(str)
    assert xw_npis.is_unique, \
        f"npi_to_org has duplicate NPIs (ambiguous attribution): " \
        f"{xw_npis[xw_npis.duplicated()].head().tolist()}"
    npi2org = dict(zip(xw_npis, npi_to_org["org_node_id"].astype(str)))
    s["org_node_id"] = s["billing_npi"].map(npi2org)

    unresolved = s[s["org_node_id"].isna()]
    matched = s[s["org_node_id"].notna()]
    total_unresolved = float(unresolved["total_paid"].sum())
    total_matched = float(matched["total_paid"].sum())
    # dollar conservation: nothing lost in the mapping
    assert abs((total_matched + total_unresolved) - total_in) <= max(0.01, 1e-9 * abs(total_in)), \
        f"dollar conservation broken: {total_matched + total_unresolved} vs {total_in}"

    per_year = (matched.groupby(["org_node_id", "year"], as_index=False)["total_paid"].sum())
    latest_year = per_year["year"].max() if len(per_year) else None
    agg = per_year.groupby("org_node_id").agg(
        payments=("total_paid", "mean"),            # mean annual = the exposure input
        payments_total=("total_paid", "sum"),
        years_observed=("year", "nunique"),
    ).reset_index()
    if latest_year is not None:
        latest = (per_year[per_year["year"] == latest_year]
                  .set_index("org_node_id")["total_paid"])
        agg["payments_latest_year"] = agg["org_node_id"].map(latest).fillna(0.0)
    else:
        agg["payments_latest_year"] = 0.0

    recon = {
        "total_in": total_in,
        "total_matched": total_matched,
        "total_unresolved": total_unresolved,
        "unresolved_npis": int(unresolved["billing_npi"].nunique()),
        "pct_dollars_matched": (total_matched / total_in) if total_in else 1.0,
    }
    return agg, recon


def annual_payments_per_org_duckdb(spending_path: str,
                                   npi_to_org: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Memory-safe annual_payments_per_org for full-scale data.

    Identical output to annual_payments_per_org, but the spending parquet
    (238M rows) is joined to npi_to_org and aggregated to per-org annual totals
    entirely inside DuckDB — it never materializes in pandas, so a laptop holds
    only the ~hundreds-of-thousands-of-orgs result. Dollar conservation asserted.
    """
    import duckdb
    con = duckdb.connect()
    xw = npi_to_org[["npi", "org_node_id"]].copy()
    xw["npi"] = xw["npi"].astype(str)
    xw["org_node_id"] = xw["org_node_id"].astype(str)
    con.register("xw", xw)
    con.execute(f"""
        CREATE TEMP TABLE m AS
        SELECT xw.org_node_id,
               substr(CAST(s.service_month AS VARCHAR), 1, 4) AS year,
               CAST(s.total_paid AS DOUBLE)                   AS total_paid,
               CAST(s.billing_npi AS VARCHAR)                 AS billing_npi
        FROM read_parquet('{spending_path}') s
        LEFT JOIN xw ON CAST(s.billing_npi AS VARCHAR) = xw.npi
    """)
    total_in = con.execute("SELECT COALESCE(SUM(total_paid), 0) FROM m").fetchone()[0]
    total_matched = con.execute(
        "SELECT COALESCE(SUM(total_paid), 0) FROM m WHERE org_node_id IS NOT NULL"
    ).fetchone()[0]
    unresolved_npis = con.execute(
        "SELECT COUNT(DISTINCT billing_npi) FROM m WHERE org_node_id IS NULL"
    ).fetchone()[0]
    total_unresolved = total_in - total_matched
    assert abs((total_matched + total_unresolved) - total_in) <= max(0.01, 1e-9 * abs(total_in)), \
        "dollar conservation broken"

    con.execute("""
        CREATE TEMP TABLE py AS
        SELECT org_node_id, year, SUM(total_paid) AS yr
        FROM m WHERE org_node_id IS NOT NULL GROUP BY org_node_id, year
    """)
    agg = con.execute("""
        SELECT org_node_id, AVG(yr) AS payments, SUM(yr) AS payments_total,
               COUNT(DISTINCT year) AS years_observed
        FROM py GROUP BY org_node_id
    """).df()
    latest_year = con.execute("SELECT MAX(year) FROM py").fetchone()[0]
    if latest_year is not None:
        latest = con.execute(
            "SELECT org_node_id, yr AS payments_latest_year FROM py WHERE year = ?",
            [latest_year]).df()
        agg = agg.merge(latest, on="org_node_id", how="left")
        agg["payments_latest_year"] = agg["payments_latest_year"].fillna(0.0)
    else:
        agg["payments_latest_year"] = 0.0
    con.close()
    recon = {
        "total_in": float(total_in), "total_matched": float(total_matched),
        "total_unresolved": float(total_unresolved),
        "unresolved_npis": int(unresolved_npis),
        "pct_dollars_matched": (total_matched / total_in) if total_in else 1.0,
    }
    return agg, recon


def scoped_payments_per_org(spending: pd.DataFrame,
                            npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """Mean-annual payments per org WITHIN each scheme's code family (A1).

    Input spending must carry ``hcpcs_code`` alongside billing_npi /
    service_month / total_paid (spending_fact does). Output: one row per
    org_node_id with one column per scheme that has a real family
    (``scoped__<scheme>``) — schemes in "all" mode are omitted (callers fall
    back to total payments). Scoped dollars are asserted ≤ total dollars.
    """
    from .scheme_code_families import SCHEME_FAMILIES

    s = spending.copy()
    s["billing_npi"] = s["billing_npi"].astype(str)
    s["total_paid"] = pd.to_numeric(s["total_paid"], errors="coerce").fillna(0.0)
    s["year"] = s["service_month"].astype(str).str.slice(0, 4)
    s["hcpcs"] = s.get("hcpcs_code", "").fillna("").astype(str).str.strip().str.upper()

    xw_npis = npi_to_org["npi"].astype(str)
    assert xw_npis.is_unique, "npi_to_org has duplicate NPIs (ambiguous attribution)"
    s["org_node_id"] = s["billing_npi"].map(
        dict(zip(xw_npis, npi_to_org["org_node_id"].astype(str))))
    s = s[s["org_node_id"].notna()]
    if not len(s):
        return pd.DataFrame(columns=["org_node_id"])

    def mean_annual(sub: pd.DataFrame) -> pd.Series:
        per_year = sub.groupby(["org_node_id", "year"])["total_paid"].sum()
        return per_year.groupby("org_node_id").mean()

    total = mean_annual(s)
    out = pd.DataFrame({"org_node_id": total.index})

    # the org's dominant code, for top_code-mode schemes
    by_code = s.groupby(["org_node_id", "hcpcs"])["total_paid"].sum()
    top_code = by_code.groupby("org_node_id").idxmax().map(lambda t: t[1])

    for scheme, (mode, payload) in SCHEME_FAMILIES.items():
        if mode == "all":
            continue
        if mode == "codes":
            mask = s["hcpcs"].isin(payload)
        elif mode == "prefixes":
            mask = s["hcpcs"].str.startswith(tuple(payload))
        elif mode == "top_code":
            mask = s["hcpcs"] == s["org_node_id"].map(top_code)
        else:                                            # defensive
            continue
        scoped = mean_annual(s[mask]).reindex(total.index).fillna(0.0)
        assert (scoped <= total + 0.01).all(), \
            f"scoped payments exceed total for scheme {scheme}"
        out[f"scoped__{scheme}"] = scoped.values
    return out.reset_index(drop=True)


def attach_payments(features: pd.DataFrame, payments: pd.DataFrame) -> pd.DataFrame:
    """Join computed payments onto a features table (real payments win over any
    pre-existing ``payments`` column); many-to-one, no fan-out."""
    pre = len(features)
    cols = ["org_node_id", "payments", "payments_latest_year",
            "payments_total", "years_observed"]
    out = features.drop(columns=[c for c in cols[1:] if c in features.columns],
                        errors="ignore").merge(payments[cols], on="org_node_id", how="left")
    assert len(out) == pre, f"payments join fan-out: {len(out)} vs {pre}"
    out["payments"] = out["payments"].fillna(0.0)
    return out
