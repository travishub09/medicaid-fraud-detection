"""
dossier_evidence.py — per-org billing evidence from source data, for dossiers.

A dossier should tell a story built from *this* organization's actual numbers,
with provenance — not a category template. For the top-K ranked orgs only (cheap:
one filtered scan of the spending fact, joined to those orgs' member NPIs), this
assembles the concrete facts a data-built narrative needs:

  * total Medicaid dollars + date range + beneficiary count + dollars/beneficiary
  * the top procedure codes by dollars and each one's share of the org's billing
    (the concentration story, in real codes and real dollars)
  * the monthly trajectory — first vs. last activity, peak month (the ramp story)

Every figure carries its source (the Medicaid spending file). Peer-relative
framing still comes from the model's percentiles; this grounds those percentiles
in the underlying dollars. One DuckDB scan for all top-K orgs (filtered to a few
hundred NPIs), so it is cheap even though the fact table is ~238M rows.
"""

from __future__ import annotations

import pandas as pd

SPENDING_PROVENANCE = ("Medicaid Provider Spending by HCPCS (HHS Open Data, "
                       "T-MSIS-derived, 2018–2024)")


def gather_evidence(spending_path: str, npi_org_map: pd.DataFrame) -> dict[str, dict]:
    """Billing evidence per org for the top-K orgs.

    ``npi_org_map``: columns ``npi``, ``org_node_id`` — only the members of the
    orgs we will render (keeps the scan tiny). Returns {org_node_id: evidence}.
    """
    import duckdb
    m = npi_org_map.copy()
    m["npi"] = m["npi"].astype(str)
    m["org_node_id"] = m["org_node_id"].astype(str)
    if not len(m):
        return {}
    con = duckdb.connect()
    con.register("m", m)
    # one scan of the fact table → a small per-(org,code,month) materialization
    con.execute(f"""
        CREATE TEMP TABLE e AS
        SELECT m.org_node_id AS org, CAST(s.hcpcs_code AS VARCHAR) AS code,
               CAST(s.service_month AS VARCHAR) AS month,
               CAST(s.total_paid AS DOUBLE) AS paid,
               CAST(s.total_patients AS DOUBLE) AS pats
        FROM read_parquet('{spending_path}') s
        JOIN m ON CAST(s.billing_npi AS VARCHAR) = m.npi
    """)
    totals = con.execute("""
        SELECT org, SUM(paid) paid, SUM(pats) pats,
               COUNT(DISTINCT code) ncodes, COUNT(DISTINCT month) nmonths,
               MIN(month) first_m, MAX(month) last_m
        FROM e GROUP BY org""").df()
    by_code = con.execute("""
        SELECT org, code, SUM(paid) paid FROM e GROUP BY org, code""").df()
    by_month = con.execute("""
        SELECT org, month, SUM(paid) paid FROM e GROUP BY org, month""").df()
    con.close()

    out: dict[str, dict] = {}
    for r in totals.itertuples():
        paid = float(r.paid or 0.0)
        if paid <= 0:
            continue
        codes = (by_code[by_code["org"] == r.org]
                 .sort_values("paid", ascending=False).head(5))
        top_codes = [(c.code, float(c.paid), float(c.paid) / paid)
                     for c in codes.itertuples()]
        months = by_month[by_month["org"] == r.org].sort_values("month")
        ramp = None
        if len(months) >= 2:
            ms = months.set_index("month")["paid"]
            ramp = {"peak_month": str(ms.idxmax()), "peak_paid": float(ms.max()),
                    "first_month_paid": float(ms.iloc[0]),
                    "last_month_paid": float(ms.iloc[-1])}
        out[str(r.org)] = {
            "provenance": SPENDING_PROVENANCE,
            "total_paid": paid,
            "n_patients": int(r.pats or 0),
            "n_codes": int(r.ncodes or 0),
            "n_months": int(r.nmonths or 0),
            "first_month": str(r.first_m), "last_month": str(r.last_m),
            "paid_per_patient": (paid / float(r.pats)) if r.pats else None,
            "top_codes": top_codes,
            "ramp": ramp,
        }
    return out
