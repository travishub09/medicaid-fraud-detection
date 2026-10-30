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

Split exports are joined automatically: a state file with a facility id and a
street address but no capacity column (NY "Health Facility General
Information") is the address directory; a file with facility ids and
long-format bed/service rows (NY "Health Facility Certification Information":
Attribute Type = BED rows summed per site) gets its addresses from that
directory on the facility id. NJ open-data exports (facid / licensed_name /
fac_city / hospbedtotal) resolve through the header aliases.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import _normalize_name, _resolve_columns, _standardize_address, read_csv_text

ROSTER_COLS = {
    "facility_name": ["facility name", "facility_name", "licensed facility name", "provider name",
                      "provider_name", "name",
                      "licensed name", "licensed_name", "licensee", "legal name", "dba", "business name",
                      "program name", "entity name", "agency name", "organization name"],
    "addr_line1": ["address", "address 1", "address1", "street", "street address", "addr_line1",
                   "physical address", "location address", "facility address", "site address",
                   "facility address 1", "facility address1", "address line 1", "street address 1",
                   "provider address", "program address", "fac_addr_1", "fac addr 1", "fac_addr",
                   "res_street_addr", "res street addr",
                   "street 1", "address line1", "physical address 1", "location address 1"],
    "city": ["city", "facility city", "location city", "provider city", "program city", "fac_city",
             "fac city", "physical city", "site city", "res_city", "res city"],
    "state": ["state", "st", "facility state", "provider state", "fac_st", "fac st", "physical state",
              "res_state", "res state"],
    "zip": ["zip", "zip code", "zipcode", "postal code", "zip5", "facility zip", "facility zip code",
            "provider zip", "program zip", "zip code (5)", "fac_zip", "physical zip", "site zip",
            "res_zip_code", "res zip code"],
    "license_type": ["fac_type_desc", "fac type desc", "facility type description", "license type",
                     "licence type", "facility type", "provider type", "type",
                     "program type", "category", "license category", "service type", "facility_type",
                     "description", "short description"],
    "capacity": ["licensed capacity", "capacity", "beds", "bed count", "licensed beds", "total beds",
                 "number of beds", "slots", "licensed slots", "max capacity", "certified beds",
                 "bed_count", "licensed_capacity", "capacity (beds)", "occupancy limit",
                 "total capacity", "certified capacity", "licensed bed count", "bed capacity",
                 "total licensed beds", "approved capacity", "certified bed count", "number of slots",
                 "hospbedtotal", "hosp bed total", "hospital bed total", "licensed bed total", "bed total",
                 "total licensed", "ltc beds", "nursing beds", "lic_beds_slots", "licensed beds/slots",
                 "beds/slots", "beds slots", "measure value", "measure_value"],
    "license_number": ["license number", "license #", "license no", "lic#", "license", "licence number",
                       "permit number", "lic", "operating certificate number", "certificate number", "fac_nbr",
                       "facility number", "id"],
    "status": ["status", "license status", "active"],
    # site identifier shared by a state's split exports (NY HFIS: beds live in the
    # Certification file, addresses in the General Information file, joined on Facility ID)
    "facility_id": ["facility id", "facility_id", "facid", "fac id", "fac_id", "site id",
                    "provider id", "license id", "hfis id"],
    # long-format bed/service rows (NY HFIS Certification): keep only the bed rows
    "attribute_type": ["attribute type", "attribute_type", "measure type", "certification type"],
    "attribute_value": ["attribute value", "attribute_value", "bed type", "service/bed"],
}
BED_ATTRIBUTE = re.compile(r"bed", re.I)
OUT_COLS = ["state", "facility_name", "name_key", "addr_line1", "city", "zip", "addr_key",
            "license_type", "license_number", "status", "capacity", "facility_id", "source_file"]


def _read(path: Path) -> pd.DataFrame:
    return read_csv_text(path) if path.suffix.lower() == ".csv" else pd.read_excel(path, dtype=str)


def _canonical(raw: pd.DataFrame, column_map: dict[str, str] | None = None) -> pd.DataFrame:
    """Rename the recognised source headers to the canonical field names."""
    if column_map:
        return raw.rename(columns={v: k for k, v in column_map.items()})
    resolved = _resolve_columns(list(raw.columns), ROSTER_COLS)
    return raw.rename(columns={v: k for k, v in resolved.items()})


def is_link_table(path: str | Path) -> bool:
    """A site-directory export: facility id + street address but NO capacity
    column (NY 'Health Facility General Information'). It carries the addresses
    for a sibling bed file and is never a roster on its own."""
    df = _canonical(_read(Path(path)))
    return "facility_id" in df.columns and "addr_line1" in df.columns and "capacity" not in df.columns


def load_link_table(path: str | Path) -> pd.DataFrame:
    """facility_id -> facility_name, addr_line1, city, zip, license_type (one row per id)."""
    df = _canonical(_read(Path(path)))
    n = len(df)

    def col(k):
        return df[k].fillna("").astype(str).str.strip() if k in df.columns else pd.Series([""] * n, index=df.index)

    out = pd.DataFrame({"facility_id": col("facility_id"), "facility_name": col("facility_name"),
                        "addr_line1": col("addr_line1"), "city": col("city"), "zip": col("zip"),
                        "license_type": col("license_type")})
    out = out[out["facility_id"] != ""]
    return out.drop_duplicates("facility_id").reset_index(drop=True)


def _bed_rows_to_sites(df: pd.DataFrame) -> pd.DataFrame:
    """Long-format certification rows (one row per bed type / service per site)
    -> one row per site with the BED rows summed. Service rows are dropped."""
    keep = df["attribute_type"].fillna("").astype(str).str.contains(BED_ATTRIBUTE)
    beds = df[keep].copy()
    beds["capacity"] = pd.to_numeric(beds["capacity"].fillna("").astype(str).str.replace(r"[^\d.]", "", regex=True),
                                     errors="coerce").fillna(0.0)
    first = {c: (c, "first") for c in ("facility_name", "addr_line1", "city", "state", "zip", "license_type",
                                       "license_number", "status") if c in beds.columns}
    agg = beds.groupby("facility_id", as_index=False).agg(capacity=("capacity", "sum"), **first)
    if "attribute_value" in beds.columns:
        kinds = beds.groupby("facility_id")["attribute_value"].agg(
            lambda s: "; ".join(sorted(set(x for x in s.fillna("").astype(str) if x)))[:120])
        agg["bed_types"] = agg["facility_id"].map(kinds)
    return agg


def load_roster(path: str | Path, state: str, column_map: dict[str, str] | None = None,
                link: pd.DataFrame | None = None) -> pd.DataFrame:
    """One state roster file -> the shared licensed-capacity schema.

    ``link`` is an optional facility_id-keyed address table (see
    ``load_link_table``) merged in when the roster has ids but no street
    address, the NY HFIS Certification + General Information pair."""
    p = Path(path)
    raw = _read(p)
    df = _canonical(raw, column_map)
    if "capacity" not in df.columns or ("facility_name" not in df.columns and "facility_id" not in df.columns):
        raise ValueError(f"{p.name}: could not find a facility name and a capacity column; "
                         f"headers were {list(raw.columns)[:15]} — pass column_map")
    if "attribute_type" in df.columns and "facility_id" in df.columns:
        df = _bed_rows_to_sites(df)
    if link is not None and len(link) and "facility_id" in df.columns and "addr_line1" not in df.columns:
        df = df.drop(columns=[c for c in ("facility_name", "license_type") if c in df.columns and
                              df[c].fillna("").astype(str).str.strip().eq("").all()])
        lk = link.rename(columns={c: f"{c}__link" for c in link.columns if c != "facility_id"})
        df = df.merge(lk, on="facility_id", how="left")
        for c in ("facility_name", "addr_line1", "city", "zip", "license_type"):
            if f"{c}__link" in df.columns:
                df[c] = df[c].fillna("").astype(str).str.strip() if c in df.columns else ""
                df[c] = df[c].where(df[c] != "", df[f"{c}__link"])
        df = df.drop(columns=[c for c in df.columns if c.endswith("__link")])
    if "facility_name" not in df.columns:
        raise ValueError(f"{p.name}: bed rows carry a facility id but no facility name and no address "
                         f"file was found for {state.upper()} — download the site directory too")
    n = len(df)

    def col(k):
        return df[k].fillna("").astype(str).str.strip() if k in df.columns else pd.Series([""] * n, index=df.index)

    out = pd.DataFrame({
        "state": state.upper(),
        "facility_name": col("facility_name"),
        # NJ LTC packs "street \n city, ST zip" into one cell: keep the street line
        "addr_line1": col("addr_line1").str.split(r"[\r\n]+", regex=True).str[0].str.strip(),
        "city": col("city"),
        "zip": col("zip").str.replace(r"\D", "", regex=True).str.slice(0, 5),
        "license_type": col("license_type"),
        "license_number": col("license_number"),
        "status": col("status"),
        "capacity": pd.to_numeric(col("capacity").str.replace(r"[^\d.]", "", regex=True), errors="coerce"),
        "facility_id": col("facility_id"),
        "source_file": p.name,
    }, index=df.index)
    out["name_key"] = _normalize_name(out["facility_name"])
    out["addr_key"] = _standardize_address(pd.DataFrame({
        "line1": out["addr_line1"], "city": out["city"], "state": out["state"], "zip": out["zip"]}))
    out.loc[out["addr_line1"] == "", "addr_key"] = ""          # no street -> no address key (name match only)
    out = out[(out["facility_name"] != "") & out["capacity"].notna() & (out["capacity"] > 0)]
    return out.reset_index(drop=True)[OUT_COLS]


def merge_rosters(dir_path: str | Path) -> pd.DataFrame:
    """Stack every roster under ``dir_path``: files named <ST>_<what>.csv/.xlsx,
    or files inside a <ST>/ subfolder. Unparseable files are reported and skipped."""
    d = Path(dir_path)
    frames, skipped, files = [], [], []
    for p in sorted(d.rglob("*")):
        if p.suffix.lower() not in (".csv", ".xlsx", ".xls") or p.name.startswith("~"):
            continue
        m = re.match(r"^([A-Za-z]{2})[_\-]", p.name)
        st = m.group(1) if m else (p.parent.name if re.fullmatch(r"[A-Za-z]{2}", p.parent.name) else None)
        if not st:
            skipped.append((p.name, "no state prefix (<ST>_name.csv) or <ST>/ folder"))
            continue
        files.append((p, st.upper()))
    # pass 1: site directories (id + address, no capacity) become the per-state link table
    links: dict[str, list[pd.DataFrame]] = {}
    rosters = []
    for p, st in files:
        try:
            if is_link_table(p):
                links.setdefault(st, []).append(load_link_table(p))
                hdr = list(_read(p).columns)[:20]
                print(f"  [licensed_capacity] {p.name}: address directory for {st} (joined on facility id); "
                      f"no capacity column recognised among headers {hdr}")
                continue
        except Exception as e:  # noqa: BLE001
            skipped.append((p.name, str(e)[:120]))
            continue
        rosters.append((p, st))
    # pass 2: bed rosters, with the state's link table when they lack addresses
    for p, st in rosters:
        link = pd.concat(links[st], ignore_index=True).drop_duplicates("facility_id") if st in links else None
        try:
            frames.append(load_roster(p, st, link=link))
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
    for c in ("addr_key", "name_key", "state"):
        if c not in r.columns:
            r[c] = ""
        r[c] = r[c].fillna("").astype(str)
    # one row per licensed site: max capacity over duplicate rows of a site (an address
    # with two roster rows is one building, never two); rows without an address stay as
    # their own sites so the name path still counts them
    site = (r[r["addr_key"] != ""].groupby(["addr_key"], as_index=False)
             .agg(capacity=("capacity", "max"), license_type=("license_type", "first"),
                  name_key=("name_key", "first"), state=("state", "first")))
    sites = pd.concat([site, r[r["addr_key"] == ""][["addr_key", "capacity", "license_type", "name_key", "state"]]],
                      ignore_index=True)
    by_addr = pdim[pdim["addr_key"] != ""][["npi", "addr_key"]].merge(
        site[["addr_key", "capacity", "license_type"]], on="addr_key", how="inner")
    a = by_addr.groupby("npi").agg(licensed_capacity=("capacity", "sum"), n_sites=("capacity", "size"),
                                   license_type=("license_type", "first")).reset_index()
    a["match_basis"] = "address"
    rem = pdim[~pdim["npi"].isin(a["npi"])]
    byname = (sites[sites["name_key"] != ""].groupby(["state", "name_key"], as_index=False)
               .agg(capacity=("capacity", "sum"), license_type=("license_type", "first"),
                    n_sites=("capacity", "size")))
    b = rem[["npi", "name_key", "addr_state"]].rename(columns={"addr_state": "state"}).merge(
        byname, on=["state", "name_key"], how="inner")
    b = b.groupby("npi").agg(licensed_capacity=("capacity", "max"), n_sites=("n_sites", "max"),
                             license_type=("license_type", "first")).reset_index()
    b["match_basis"] = "name+state"
    # An operator that bills several licensed homes under ONE number from ONE address
    # (the California assisted-living-waiver shape: six-bed RCFEs under a brand name,
    # 2026-10-30: Right Choice Senior Living read 2.7x a six-bed licence) matched one
    # site by address. When the licensee's exact name key covers MORE capacity in the
    # same state, the operator's capacity is that sum (basis address+name); the single
    # site stays in n_sites_addr for the card.
    if len(a):
        a_named = a.merge(pdim[["npi", "name_key", "addr_state"]].rename(columns={"addr_state": "state"}),
                          on="npi", how="left").merge(
            byname.rename(columns={"capacity": "name_capacity", "n_sites": "name_sites",
                                   "license_type": "name_license_type"}),
            on=["state", "name_key"], how="left")
        wider = a_named["name_capacity"].fillna(0) > a_named["licensed_capacity"]
        a_named.loc[wider, "licensed_capacity"] = a_named.loc[wider, "name_capacity"]
        a_named.loc[wider, "n_sites"] = a_named.loc[wider, "name_sites"]
        a_named.loc[wider, "match_basis"] = "address+name"
        a = a_named.groupby("npi", as_index=False).agg(
            licensed_capacity=("licensed_capacity", "max"), n_sites=("n_sites", "max"),
            license_type=("license_type", "first"), match_basis=("match_basis", "first"))
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
