"""
medicare_revocations.py — CMS Revoked Medicare Providers/Suppliers → exclusions.

Source: the HHS Open Data "Revoked Medicare Providers and Suppliers" file
(public, ~250 KB, derived from PECOS). A point-in-time list of providers/
suppliers whose Medicare enrollment was revoked and who remain under an active
re-enrollment bar. This is integrity/exclusion intelligence in the same family
as LEIE and SAM, so we normalize it into the shared ``exclusions`` schema and it
becomes exclusion nodes in the graph exactly like the others.

Grain: one row per ENRLMT_ID × NPI (a revoked enrollment can carry multiple
NPIs; each is its own row). We emit one exclusion row per NPI.

  normalize_revocations(raw) → npi, entity_name, name_key, excl_type
      ("medicare_revocation: <reason>"), excl_date (REVOCATION_EFCTV_DT),
      reinstate_date (REENROLLMENT_BAR_EXPRTN_DT), currently_active (the bar
      hasn't expired). Real CMS headers resolved via the shared ``_resolve_columns``.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series
from src.entity_graph.resolve_entities import norm_org_name

EXCLUSION_COLS = ["npi", "entity_name", "name_key", "excl_type", "excl_date",
                  "reinstate_date", "currently_active"]

REVOCATION_COLS = {
    "npi": ["NPI", "npi"],
    "org_name": ["ORGANIZATION_NAME", "ORG_NAME", "Organization Name",
                 "LEGAL_BUSINESS_NAME", "PROVIDER_NAME", "SUPPLIER_NAME", "NAME"],
    "last_name": ["LAST_NAME", "Last Name", "LAST", "PROVIDER_LAST_NAME"],
    "first_name": ["FIRST_NAME", "First Name", "FIRST", "PROVIDER_FIRST_NAME"],
    "revocation_date": ["REVOCATION_EFCTV_DT", "Revocation Effective Date",
                        "revocation_efctv_dt"],
    "bar_expiration": ["REENROLLMENT_BAR_EXPRTN_DT", "Reenrollment Bar Expiration",
                       "reenrollment_bar_exprtn_dt"],
    "reason": ["REVOCATION_RSN", "Revocation Reason", "revocation_rsn"],
}


def normalize_revocations(raw: pd.DataFrame,
                          as_of: str | None = None) -> pd.DataFrame:
    """CMS revocation rows → the shared exclusions schema (one row per NPI).

    ``as_of`` (default: today) decides ``currently_active`` — the bar is active
    when its expiration is missing or still in the future.
    """
    resolved = _resolve_columns(list(raw.columns), REVOCATION_COLS)
    if "npi" not in resolved and "org_name" not in resolved \
            and "last_name" not in resolved:
        raise ValueError(f"revocation file missing NPI/name columns; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()

    npi = (canonicalize_series(df["npi"]) if "npi" in df.columns
           else pd.Series(pd.NA, index=df.index))
    org = df.get("org_name", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    last = df.get("last_name", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    first = df.get("first_name", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    person = (last + ", " + first).str.strip(", ")
    entity_name = org.where(org != "", person)

    reason = df.get("reason", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    excl_date = pd.to_datetime(df.get("revocation_date"), errors="coerce")
    bar_exp = pd.to_datetime(df.get("bar_expiration"), errors="coerce")
    now = pd.Timestamp(as_of) if as_of else pd.Timestamp.now().normalize()

    out = pd.DataFrame({
        "npi": npi.fillna("").astype(str),
        "entity_name": entity_name,
        "name_key": entity_name.map(norm_org_name),
        "excl_type": ("medicare_revocation" + reason.map(
            lambda r: f": {r}" if r else "")),
        "excl_date": excl_date,
        "reinstate_date": bar_exp,
        # the bar is active when it has no expiration or expires in the future
        "currently_active": (bar_exp.isna() | (bar_exp >= now)).astype(int),
    })
    # keep rows that have at least an NPI or a name to match on
    out = out[(out["npi"] != "") | (out["name_key"] != "")]
    return out.reset_index(drop=True)[EXCLUSION_COLS]
