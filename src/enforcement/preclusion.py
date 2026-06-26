"""
preclusion.py — CMS Preclusion List → exclusions schema (extra positive labels).

The CMS Preclusion List names providers barred from receiving payment for Part C
(Medicare Advantage) items/services and Part D drugs — providers who are revoked,
under an active enrollment bar, OR who have engaged in conduct detrimental to
Medicare. It overlaps LEIE/revocations but is NOT identical (it captures conduct
that never produced an OIG exclusion), so it widens the PU positive label.

Access note: unlike LEIE / SAM / the Revoked-Providers file (all public), the
Preclusion List is distributed by CMS to MA/Part D plan sponsors via HPMS and is
NOT a public download. Building this adapter needs no access; RUNNING it requires
a sponsor-channel copy of the file (a procurement/relationship gate, documented in
docs/platform/09). The normalizer is identical in shape to the other exclusion
adapters, so the row drops straight into the graph's exclusion nodes.

  normalize_preclusion(raw) → npi, entity_name, name_key,
      excl_type ("preclusion: <reason>"), excl_date (preclusion date),
      reinstate_date (end date if present), currently_active. CMS headers resolved
      via the shared ``_resolve_columns`` so real and sample layouts both load.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series
from src.entity_graph.resolve_entities import norm_org_name

EXCLUSION_COLS = ["npi", "entity_name", "name_key", "excl_type", "excl_date",
                  "reinstate_date", "currently_active"]

PRECLUSION_COLS = {
    "npi": ["NPI", "npi", "National Provider Identifier"],
    "org_name": ["ORGANIZATION_NAME", "ORG_NAME", "Organization Name",
                 "LEGAL_BUSINESS_NAME", "PROVIDER_NAME", "NAME"],
    "last_name": ["LAST_NAME", "Last Name", "LAST", "PROVIDER_LAST_NAME"],
    "first_name": ["FIRST_NAME", "First Name", "FIRST", "PROVIDER_FIRST_NAME"],
    "preclusion_date": ["PRECLUSION_DATE", "Preclusion Date", "preclusion_date",
                        "EFFECTIVE_DATE", "Effective Date"],
    "end_date": ["PRECLUSION_END_DATE", "Preclusion End Date", "END_DATE",
                 "End Date", "REINSTATEMENT_DATE"],
    "reason": ["PRECLUSION_REASON", "Preclusion Reason", "REASON", "Reason"],
}


def normalize_preclusion(raw: pd.DataFrame,
                         as_of: str | None = None) -> pd.DataFrame:
    """CMS Preclusion List rows → the shared exclusions schema (one row per NPI).

    ``as_of`` (default: today) decides ``currently_active`` — precluded when the
    end date is missing or still in the future.
    """
    resolved = _resolve_columns(list(raw.columns), PRECLUSION_COLS)
    if "npi" not in resolved and "org_name" not in resolved \
            and "last_name" not in resolved:
        raise ValueError(f"preclusion file missing NPI/name columns; "
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
    excl_date = pd.to_datetime(df.get("preclusion_date"), errors="coerce")
    end_date = pd.to_datetime(df.get("end_date"), errors="coerce")
    now = pd.Timestamp(as_of) if as_of else pd.Timestamp.now().normalize()

    out = pd.DataFrame({
        "npi": npi.fillna("").astype(str),
        "entity_name": entity_name,
        "name_key": entity_name.map(norm_org_name),
        "excl_type": "preclusion" + reason.map(lambda r: f": {r}" if r else ""),
        "excl_date": excl_date,
        "reinstate_date": end_date,
        "currently_active": (end_date.isna() | (end_date >= now)).astype(int),
    })
    out = out[(out["npi"] != "") | (out["name_key"] != "")]
    return out.reset_index(drop=True)[EXCLUSION_COLS]


def main() -> None:
    """CSV of the CMS Preclusion List → processed/exclusions_preclusion.parquet
    (the graph merges any processed/exclusions_*.parquet into exclusion nodes,
    widening within_2_hops_of_exclusion AND the PU positive label)."""
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", required=True, help="CMS Preclusion List csv")
    ap.add_argument("--out", required=True, help="output exclusions parquet")
    args = ap.parse_args()
    from src.attempt_2.clean_data import read_csv_text
    df = normalize_preclusion(read_csv_text(args.inp))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    print(f"preclusion list: {len(df):,} rows "
          f"({int((df['npi'] != '').sum()):,} NPI-matched) -> {args.out}")


if __name__ == "__main__":
    main()
