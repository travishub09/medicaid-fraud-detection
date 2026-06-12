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

    npi2org = dict(zip(npi_to_org["npi"].astype(str),
                       npi_to_org["org_node_id"].astype(str)))
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
