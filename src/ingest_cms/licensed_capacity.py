"""
licensed_capacity.py — licensed capacity (beds / slots) from STATE facility
rosters, for the providers the CMS Provider of Services file never covers:
waiver group homes, residential habilitation, adult day, assisted living,
intermediate care facilities, behavioral residential programs.

Each state publishes its licensed-facility list under a different name and
header set, so this adapter is header-tolerant: it resolves the facility
name, street address, city, ZIP, license type and licensed capacity from any
of the common spellings (or an explicit ``column_map``) and emits one shared
schema keyed the same way the graph keys providers (``name_key`` via the
shared name normalizer, ``addr_key`` via the shared address normalizer), so
a roster row joins to a billing NPI by address first and by name + state
second.

  load_roster(path, state, column_map=None) -> state, facility_name, name_key,
        addr_line1, city, zip, addr_key, license_type, capacity, source_file
  merge_rosters(dir)  -> every <dir>/<ST>_*.csv (or <dir>/<ST>/*.csv) stacked
  capacity_by_npi(roster, provider_dim) -> npi, licensed_capacity, match_basis,
        license_type  (address match, else name+state; max over matches)

Files go under preclean/state_capacity/<ST>_<what>.csv (e.g. RI_hospice.csv,
FL_adult_day.csv); procurement steps per state live in the operator
reminders and docs/platform/12. Capacity here is a public licensing fact, not
a finding about any provider.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import _normalize_name, _resolve_columns, _standardize_address, read_csv_text

ROSTER_COLS = {
    "facility_name": ["facility name", "facility_name", "provider name", "provider_name", "name",
                      "licensee", "legal name", "dba", "business name", "program name", "entity name"],
    "addr_line1": ["address", "address 1", "address1", "street", "street address", "addr_line1",
                   "physical address", "location address", "facility address", "site address",
                   "facility address 1", "facility address1", "address line 1", "street address 1",
                   "provider address", "program address"],
    "city": ["city", "facility city", "location city", "provider city", "program city"],
    "state": ["state", "st", "facility state", "provider state"],
    "zip": ["zip", "zip code", "zipcode", "postal code", "zip5", "facility zip", "facility zip code",
            "provider zip", "program zip", "zip code (5)"],
    "license_type": ["license type", "licence type", "facility type", "provider type", "type",
                     "program type", "category", "license category", "service type"],
    "capacity": ["licensed capacity", "capacity", "beds", "bed count", "licensed beds", "total beds",
                 "number of beds", "slots", "licensed slots", "max capacity", "certified beds",
                 "bed_count", "licensed_capacity", "capacity (beds)", "occupancy limit",
                 "total capacity", "certified capacity", "licensed bed count", "bed capacity",
                 "total licensed beds", "approved capacity", "certified bed count", "number of slots"],
    "license_number": ["license number", "license #", "license no", "license", "licence number",
                       "permit number", "id"],
    "status": ["status", "license status", "active"],
}
OUT_COLS = ["state", "facility_name", "name_key", "addr_line1", "city", "zip", "addr_key",
            "license_type", "license_number", "status", "capacity", "source_file"]


def load_roster(path: str | Path, state: str, column_map: dict[str, str] | None = None) -> pd.DataFrame:
    """One state roster file -> the shared licensed-capacity schema."""
    p = Path(path)
    raw = read_csv_text(p) if p.suffix.lower() == ".csv" else pd.read_excel(p, dtype=str)
    if column_map:
        df = raw.rename(columns={v: k for k, v in column_map.items()})
    else:
        resolved = _resolve_columns(list(raw.columns), ROSTER_COLS)
        df = raw.rename(columns={v: k for k, v in resolved.items()})
    if "facility_name" not in df.columns or "capacity" not in df.columns:
        raise ValueError(f"{p.name}: could not find a facility name and a capacity column; "
                         f"headers were {list(raw.columns)[:15]} — pass column_map")
    n = len(df)

    def col(k):
        return df[k].fillna("").astype(str).str.strip() if k in df.columns else pd.Series([""] * n, index=df.index)

    out = pd.DataFrame({
        "state": state.upper(),
        "facility_name": col("facility_name"),
        "addr_line1": col("addr_line1"),
        "city": col("city"),
        "zip": col("zip").str.replace(r"\D", "", regex=True).str.slice(0, 5),
        "license_type": col("license_type"),
        "license_number": col("license_number"),
        "status": col("status"),
        "capacity": pd.to_numeric(col("capacity").str.replace(r"[^\d.]", "", regex=True), errors="coerce"),
        "source_file": p.name,
    }, index=df.index)
    out["name_key"] = _normalize_name(out["facility_name"])
    out["addr_key"] = _standardize_address(pd.DataFrame({
        "line1": out["addr_line1"], "city": out["city"], "state": out["state"], "zip": out["zip"]}))
    out = out[(out["facility_name"] != "") & out["capacity"].notna() & (out["capacity"] > 0)]
    return out.reset_index(drop=True)[OUT_COLS]


def merge_rosters(dir_path: str | Path) -> pd.DataFrame:
    """Stack every roster under ``dir_path``: files named <ST>_<what>.csv/.xlsx,
    or files inside a <ST>/ subfolder. Unparseable files are reported and skipped."""
    d = Path(dir_path)
    frames, skipped = [], []
    for p in sorted(d.rglob("*")):
        if p.suffix.lower() not in (".csv", ".xlsx", ".xls") or p.name.startswith("~"):
            continue
        m = re.match(r"^([A-Za-z]{2})[_\-]", p.name)
        st = m.group(1) if m else (p.parent.name if re.fullmatch(r"[A-Za-z]{2}", p.parent.name) else None)
        if not st:
            skipped.append((p.name, "no state prefix (<ST>_name.csv) or <ST>/ folder"))
            continue
        try:
            frames.append(load_roster(p, st))
        except Exception as e:  # noqa: BLE001
            skipped.append((p.name, str(e)[:120]))
    for name, why in skipped:
        print(f"  [licensed_capacity] skipped {name}: {why}")
    if not frames:
        return pd.DataFrame(columns=OUT_COLS)
    return pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["state", "name_key", "addr_key", "license_type", "capacity"]).reset_index(drop=True)


def capacity_by_npi(roster: pd.DataFrame, provider_dim: pd.DataFrame) -> pd.DataFrame:
    """Join licensed capacity to billing NPIs: exact address key first, then
    name key + state. Several licensed sites under one NPI sum; several
    rows for one site take the max. Returns npi, licensed_capacity,
    match_basis, license_type, n_sites."""
    cols = ["npi", "licensed_capacity", "match_basis", "license_type", "n_sites"]
    if roster is None or not len(roster) or provider_dim is None or not len(provider_dim):
        return pd.DataFrame(columns=cols)
    pdim = provider_dim.copy()
    pdim["npi"] = pdim["npi"].astype(str)
    for c in ("addr_key", "name_key", "addr_state"):
        if c not in pdim.columns:
            pdim[c] = ""
        pdim[c] = pdim[c].fillna("").astype(str)
    r = roster.copy()
    # one row per licensed site: max capacity over duplicate rows of a site
    site = (r[r["addr_key"] != ""].groupby(["addr_key"], as_index=False)
             .agg(capacity=("capacity", "max"), license_type=("license_type", "first")))
    by_addr = pdim[pdim["addr_key"] != ""][["npi", "addr_key"]].merge(site, on="addr_key", how="inner")
    a = by_addr.groupby("npi").agg(licensed_capacity=("capacity", "sum"), n_sites=("capacity", "size"),
                                   license_type=("license_type", "first")).reset_index()
    a["match_basis"] = "address"
    rem = pdim[~pdim["npi"].isin(a["npi"])]
    byname = (r[r["name_key"] != ""].groupby(["state", "name_key"], as_index=False)
               .agg(capacity=("capacity", "sum"), license_type=("license_type", "first"),
                    n_sites=("capacity", "size")))
    b = rem[["npi", "name_key", "addr_state"]].rename(columns={"addr_state": "state"}).merge(
        byname, on=["state", "name_key"], how="inner")
    b = b.groupby("npi").agg(licensed_capacity=("capacity", "max"), n_sites=("n_sites", "max"),
                             license_type=("license_type", "first")).reset_index()
    b["match_basis"] = "name+state"
    out = pd.concat([a, b], ignore_index=True)
    return out[cols] if len(out) else pd.DataFrame(columns=cols)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rosters", required=True, help="folder of <ST>_<what>.csv state rosters")
    ap.add_argument("--provider-dim", default=None, help="processed/provider_dim.parquet (adds the NPI join)")
    ap.add_argument("--out", required=True, help="processed/licensed_capacity.parquet")
    a = ap.parse_args()
    roster = merge_rosters(a.rosters)
    print(f"[licensed_capacity] {len(roster):,} licensed sites from "
          f"{roster['source_file'].nunique() if len(roster) else 0} files; "
          f"states: {sorted(roster['state'].unique()) if len(roster) else []}")
    roster.to_parquet(a.out, index=False)
    if a.provider_dim and len(roster):
        m = capacity_by_npi(roster, pd.read_parquet(a.provider_dim))
        mp = Path(a.out).with_name("licensed_capacity_by_npi.parquet")
        m.to_parquet(mp, index=False)
        print(f"[licensed_capacity] {len(m):,} NPIs matched "
              f"({(m['match_basis'] == 'address').sum():,} by address) -> {mp}")


if __name__ == "__main__":
    main()
