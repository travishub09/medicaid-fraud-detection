"""
order_referring.py — CMS Order & Referring eligibility (research sweep 2.6).

Source: data.cms.gov "Order and Referring" file (free) — the NPIs eligible to
order/refer DME, Part B services, home health, and power mobility. A DME or lab
claim whose ORDERING/REFERRING NPI is not on this list (or is excluded) is a
sharp kickback / phantom-order tell: legitimate equipment is ordered by an
eligible clinician, fraud rings route orders through whoever will sign.

  eligible_referrers          parse the file → per-NPI eligibility flags
                              (dme, partb, hha, pmd). String NPIs, quarantined.
  ineligible_referral_share   for DME/HHA claims carrying a referring NPI, the
                              share of a billing org's referred dollars whose
                              referrer is NOT eligible for that order type →
                              ``ineligible_referral_share`` (0–1), sharpening the
                              dme_ring scheme.

Dormant until the eligibility file + claims with a referring NPI are loaded.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series

ELIGIBLE_COLS = {
    "npi": ["NPI", "npi"],
    "partb": ["PARTB", "partb", "Eligible to order and refer Part B"],
    "dme": ["DME", "dme", "Eligible to order and refer DME"],
    "hha": ["HHA", "hha", "Eligible to order and refer HHA"],
    "pmd": ["PMD", "pmd", "Eligible to order and refer PMD"],
}
_YES = {"Y", "YES", "TRUE", "1"}


def eligible_referrers(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Parse the eligibility file → npi + per-type eligible flags (0/1).
    Returns (table, n_quarantined)."""
    resolved = _resolve_columns(list(raw.columns), ELIGIBLE_COLS)
    if "npi" not in resolved:
        raise ValueError(f"Order&Referring file missing an NPI column; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    npi = canonicalize_series(df["npi"])
    quarantined = int((npi.isna()
                       & df["npi"].fillna("").astype(str).str.strip().ne("")).sum())
    df = df.assign(npi=npi)[npi.notna()].copy()
    out = pd.DataFrame({"npi": df["npi"]})
    for c in ("partb", "dme", "hha", "pmd"):
        vals = (df[c] if c in df.columns
                else pd.Series("", index=df.index)).fillna("").astype(str).str.upper()
        out[c] = vals.isin(_YES).astype(int)
    return out.groupby("npi", as_index=False).max(), quarantined


def ineligible_referral_share(referred_claims: pd.DataFrame,
                              eligible: pd.DataFrame,
                              npi_to_org: pd.DataFrame,
                              order_type: str = "dme") -> pd.DataFrame:
    """Per-org share of referred dollars whose referrer isn't eligible for the
    order type.

    ``referred_claims``: billing_npi, referring_npi, total_paid. ``eligible``:
    the table from ``eligible_referrers`` (the ``order_type`` column gates it).
    A referring NPI absent from the eligible list OR flagged 0 for that type is
    ineligible. Returns org_node_id, ineligible_referred_paid, referred_paid,
    ineligible_referral_share (0–1 — the registry feature).
    """
    cols = ["org_node_id", "ineligible_referred_paid", "referred_paid",
            "ineligible_referral_share"]
    c = referred_claims.copy()
    c["billing_npi"] = c["billing_npi"].astype(str)
    c["referring_npi"] = c["referring_npi"].astype(str)
    c["total_paid"] = pd.to_numeric(c["total_paid"], errors="coerce").fillna(0.0)

    xw = npi_to_org["npi"].astype(str)
    assert xw.is_unique, "npi_to_org has duplicate NPIs (ambiguous attribution)"
    c["org_node_id"] = c["billing_npi"].map(
        dict(zip(xw, npi_to_org["org_node_id"].astype(str))))
    c = c[c["org_node_id"].notna()]
    if not len(c):
        return pd.DataFrame(columns=cols)

    elig = eligible.set_index(eligible["npi"].astype(str))
    type_col = order_type if order_type in elig.columns else "dme"
    eligible_set = set(elig.index[elig[type_col] == 1]) if type_col in elig.columns else set()
    c["referrer_ineligible"] = ~c["referring_npi"].isin(eligible_set)

    g = c.groupby("org_node_id")
    out = pd.DataFrame({
        "ineligible_referred_paid": g.apply(
            lambda x: float(x.loc[x["referrer_ineligible"], "total_paid"].sum())),
        "referred_paid": g["total_paid"].sum(),
    })
    out["ineligible_referral_share"] = (
        out["ineligible_referred_paid"] / out["referred_paid"]).where(
        out["referred_paid"] > 0, 0.0).clip(0, 1)
    return out.reset_index()[cols]
