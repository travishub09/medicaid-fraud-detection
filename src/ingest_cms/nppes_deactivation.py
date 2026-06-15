"""
nppes_deactivation.py — billing under a deactivated NPI (research sweep E1).

Source: the CMS NPPES monthly deactivation file (NPI + deactivation date; free
at download.cms.gov/nppes). An NPI that keeps billing AFTER it was deactivated is
a clean integrity signal — a fly-by-night entity, a stolen/retired identity, or
billing that should have stopped. Unlike the SSA Death Master File (which needs
SSN/name linkage we don't hold publicly), this is keyed by NPI, so the match is
exact and the signal is defensible.

  deactivated_npis        parse NPI → deactivation_date (string IDs, real headers)
  billing_after_deactivation  join to the spending fact + npi_to_org → per-org
                          dollars billed on/after a constituent NPI's
                          deactivation date, and the share of org billing that
                          is. Feeds the new ``invalid_identity`` scheme — a high
                          recovery-multiplier scheme, since billing under a dead
                          NPI is near-fully unsupported.

Dormant until the deactivation file + spending are loaded; exact-match, no fuzz.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series

DEACT_COLS = {
    "npi": ["NPI", "npi"],
    "deactivation_date": ["NPI Deactivation Date", "Deactivation Date",
                          "deactivation_date", "NPPES Deactivation Date"],
}


def deactivated_npis(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Parse the deactivation file → npi, deactivation_date. (table, quarantined)."""
    resolved = _resolve_columns(list(raw.columns), DEACT_COLS)
    if "npi" not in resolved:
        raise ValueError(f"deactivation file missing an NPI column; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    npi = canonicalize_series(df["npi"])
    quarantined = int((npi.isna()
                       & df["npi"].fillna("").astype(str).str.strip().ne("")).sum())
    df = df.assign(npi=npi)[npi.notna()].copy()
    df["deactivation_date"] = pd.to_datetime(
        df.get("deactivation_date"), errors="coerce")
    out = (df[df["deactivation_date"].notna()]
           .groupby("npi", as_index=False)["deactivation_date"].min())
    return out, quarantined


def billing_after_deactivation(spending: pd.DataFrame, deactivated: pd.DataFrame,
                               npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """Per-org dollars billed on/after a constituent NPI's deactivation date.

    ``spending``: billing_npi, service_month (YYYY-MM), total_paid.
    Returns org_node_id, post_deactivation_paid, total_paid,
    billing_after_deactivation (0–1 share — the registry feature).
    """
    cols = ["org_node_id", "post_deactivation_paid", "total_paid",
            "billing_after_deactivation"]
    s = spending.copy()
    s["billing_npi"] = s["billing_npi"].astype(str)
    s["total_paid"] = pd.to_numeric(s["total_paid"], errors="coerce").fillna(0.0)
    s["month_ts"] = pd.to_datetime(s["service_month"].astype(str).str.slice(0, 7),
                                   format="%Y-%m", errors="coerce")

    xw = npi_to_org["npi"].astype(str)
    assert xw.is_unique, "npi_to_org has duplicate NPIs (ambiguous attribution)"
    s["org_node_id"] = s["billing_npi"].map(
        dict(zip(xw, npi_to_org["org_node_id"].astype(str))))
    s = s[s["org_node_id"].notna()]
    if not len(s):
        return pd.DataFrame(columns=cols)

    deact = dict(zip(deactivated["npi"].astype(str),
                     pd.to_datetime(deactivated["deactivation_date"])))
    s["deact_date"] = s["billing_npi"].map(deact)
    s["is_post"] = s["deact_date"].notna() & (s["month_ts"] >= s["deact_date"])

    g = s.groupby("org_node_id")
    out = pd.DataFrame({
        "post_deactivation_paid": g.apply(
            lambda x: float(x.loc[x["is_post"], "total_paid"].sum())),
        "total_paid": g["total_paid"].sum(),
    })
    out["billing_after_deactivation"] = (
        out["post_deactivation_paid"] / out["total_paid"]).where(
        out["total_paid"] > 0, 0.0).clip(0, 1)
    return out.reset_index()[cols]
