"""
ccn_npi_crosswalk.py — build the PECOS CCN↔NPI crosswalk (B-track unlock).

Three Model-A scenarios resolve at the facility (CCN) grain — worthless_services
(PBJ understaffing / capacity), hospice_ineligibility, and cost_report_fraud
(HCRIS) — but every downstream join in this platform is keyed on NPI. The facility
adapters (`ingest_cms/facility.py::rollup_ccn_to_org`, `pos.py`, `hcris.py`) all
take a ``ccn_to_npi`` crosswalk; nothing produced one, so those schemes were
data-blocked. This module builds it.

Source: any file that carries BOTH a CMS Certification Number (CCN / provider
number) and an NPI — the PECOS "Public Provider Enrollment" institutional file, or
the Provider-of-Services (POS) file. If you only have the PECOS *enrollment* extract
(NPI, no CCN) and the standard POS file (CCN, no NPI) — neither of which pairs the
two — point this at the raw NPPES file instead: it falls back to extracting the
Medicare CCN from the NPPES "Other Provider Identifier" type-06 fields. Column names
vary across vintages, so headers are resolved by token (the shared `_resolve_columns`).
CCNs are canonicalized exactly as the facility adapters expect (`_canon_ccn`:
all-digit CCNs zero-padded to 6); NPIs through the shared Luhn-checked
`canonicalize_series`. Output: one (ccn, npi) row per pair, deduplicated.

CLI:
    python -m src.ingest_cms.ccn_npi_crosswalk \
        --in ~/Desktop/data/preclean/pecos/enrollment.csv \
        --out ~/Desktop/data/processed/ccn_to_npi.parquet
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import canonicalize_series, _resolve_columns
from .facility import _canon_ccn

# canonical field → candidate source headers (token-matched, first present wins)
_WANTED = {
    "ccn": ["ccn", "prvdr_num", "provider_number", "cms_certification_number",
            "medicare_id", "ccn_number", "enrollment_state_ccn", "prvdrnum"],
    "npi": ["npi", "npi_number", "rndrng_npi", "org_npi", "associate_npi"],
}


# NPPES "Other Provider Identifier" type code for the Medicare OSCAR / CCN.
_NPPES_CCN_TYPE = "06"


def _nppes_opi_columns(cols: list[str]) -> list[tuple[str, str]]:
    """Pair NPPES 'Other Provider Identifier_N' value columns with their matching
    'Other Provider Identifier Type Code_N' columns (by the trailing index N)."""
    import re
    vals, types = {}, {}
    for c in cols:
        m = re.search(r"other\s*provider\s*identifier", str(c).lower())
        if not m:
            continue
        idx_m = re.search(r"(\d+)\s*$", str(c).strip())
        idx = idx_m.group(1) if idx_m else ""
        if "type" in str(c).lower() and "code" in str(c).lower():
            types[idx] = c
        elif "state" not in str(c).lower() and "issuer" not in str(c).lower():
            vals[idx] = c
    return [(vals[i], types[i]) for i in vals if i in types]


def crosswalk_from_nppes(raw: pd.DataFrame) -> pd.DataFrame:
    """Build (ccn, npi) from the NPPES 'Other Provider Identifier' fields.

    Institutional NPIs record their Medicare CCN in NPPES as an Other Provider
    Identifier with Type Code ``06`` (Medicare OSCAR/Certification). This scans every
    (identifier, type-code) pair, keeps the type-06 values, and pairs them with the
    row's NPI — a CCN↔NPI bridge from the NPPES file you already hold (no POS/PECOS
    CCN column needed). Coverage is the subset of institutions that listed their CCN."""
    npi_col = next((raw[c].name for c in raw.columns
                    if str(c).strip().lower() in ("npi", "npi_number")), None)
    pairs = _nppes_opi_columns(list(raw.columns))
    if npi_col is None or not pairs:
        raise ValueError(
            "NPPES file lacks an NPI column or 'Other Provider Identifier' fields; "
            f"saw headers: {list(raw.columns)[:12]}")
    npi = canonicalize_series(raw[npi_col])
    rows = []
    for val_c, type_c in pairs:
        tcode = raw[type_c].fillna("").astype(str).str.strip().str.zfill(2)
        mask = tcode == _NPPES_CCN_TYPE
        if mask.any():
            rows.append(pd.DataFrame({"ccn": _canon_ccn(raw[val_c][mask]),
                                      "npi": npi[mask]}))
    if not rows:
        return pd.DataFrame(columns=["ccn", "npi"])
    df = pd.concat(rows, ignore_index=True).dropna(subset=["ccn", "npi"])
    return df.drop_duplicates().reset_index(drop=True)


# POS-side header candidates (token-matched).
_POS_WANTED = {
    "ccn": ["prvdr_num", "ccn", "provider_number", "prvdrnum"],
    "name": ["fac_name", "facility_name", "name"],
    "line1": ["st_adr", "street_address", "address", "addr"],
    "city": ["city_name", "city"],
    "state": ["state_cd", "state", "st"],
    "zip": ["zip_cd", "zip", "zip_code", "postal"],
    "mdcd": ["mdcd_vndr_num", "medicaid_vendor", "medicaid_id"],
}


def _find_col(header: list[str], needles: list[str]) -> str | None:
    """First header containing any needle (case-insensitive substring)."""
    low = [(c, str(c).lower()) for c in header]
    for n in needles:
        for c, cl in low:
            if n in cl:
                return c
    return None


def load_nppes_for_ccn(path: str | Path):
    """Read ONLY the NPPES columns needed to bridge to facility CCNs (so the 330-col
    file stays manageable): NPI, legal business name, practice-location address, and
    the Other-Provider-Identifier slots. Returns (facilities, medicaid) where
    facilities = [npi, name_key, zip5] and medicaid = [npi, mdcd] (exploded type-05)."""
    from src.attempt_2.clean_data import read_csv_text, _standardize_address
    from src.entity_graph.resolve_entities import norm_org_name
    header = list(read_csv_text(path, nrows=0).columns)
    npi_c = _find_col(header, ["npi"])
    name_c = _find_col(header, ["legal business name", "organization name"])
    l1_c = _find_col(header, ["first line business practice location"])
    city_c = _find_col(header, ["practice location address city"])
    state_c = _find_col(header, ["practice location address state"])
    zip_c = _find_col(header, ["practice location address postal"])
    opi = _nppes_opi_columns(header)
    if npi_c is None:
        raise ValueError(f"NPPES file has no NPI column; saw {header[:8]}")
    usecols = [c for c in [npi_c, name_c, l1_c, city_c, state_c, zip_c] if c]
    usecols += [c for pair in opi for c in pair]
    raw = read_csv_text(path, usecols=list(dict.fromkeys(usecols)))
    npi = canonicalize_series(raw[npi_c])

    addr_df = pd.DataFrame({
        "line1": raw[l1_c] if l1_c else "", "city": raw[city_c] if city_c else "",
        "state": raw[state_c] if state_c else "", "zip": raw[zip_c] if zip_c else ""})
    fac = pd.DataFrame({
        "npi": npi,
        "name_key": (raw[name_c].map(norm_org_name) if name_c else ""),
        "zip5": (raw[zip_c].fillna("").astype(str).str.replace(r"\D", "", regex=True)
                 .str.slice(0, 5) if zip_c else ""),
        "addr_key": _standardize_address(addr_df),
    })
    fac = fac[npi.notna()].drop_duplicates()

    med_rows = []
    for val_c, type_c in opi:
        t = raw[type_c].fillna("").astype(str).str.strip().str.zfill(2)
        m = t == "05"
        if m.any():
            med_rows.append(pd.DataFrame({
                "npi": npi[m],
                "mdcd": raw[val_c][m].fillna("").astype(str).str.strip().str.upper()}))
    medicaid = (pd.concat(med_rows, ignore_index=True).dropna(subset=["npi"])
                if med_rows else pd.DataFrame(columns=["npi", "mdcd"]))
    medicaid = medicaid[medicaid["mdcd"] != ""].drop_duplicates()
    return fac.reset_index(drop=True), medicaid.reset_index(drop=True)


def crosswalk_from_pos_nppes(pos_raw: pd.DataFrame, facilities: pd.DataFrame,
                             medicaid: pd.DataFrame) -> pd.DataFrame:
    """Bridge CCN↔NPI by joining the POS facility file to NPPES two ways and unioning:
      1. Medicaid vendor number — POS ``mdcd_vndr_num`` == an NPPES type-05 identifier
         (an exact key match).
      2. Normalized facility name + ZIP5 — the platform's standard org-matching key.
    Returns deduped (ccn, npi, match_source). Approximate by design (facility name/ID
    matching), so the facility signals it unlocks stay corroborative."""
    from src.attempt_2.clean_data import _standardize_address
    from src.entity_graph.resolve_entities import norm_org_name
    res = _resolve_columns(list(pos_raw.columns), _POS_WANTED)
    if "ccn" not in res:
        raise ValueError(f"POS file has no CCN (prvdr_num) column; "
                         f"saw {list(pos_raw.columns)[:12]}")
    ccn = _canon_ccn(pos_raw[res["ccn"]])
    pos = pd.DataFrame({"ccn": ccn})
    pos["name_key"] = (pos_raw[res["name"]].map(norm_org_name) if "name" in res else "")
    pos["zip5"] = (pos_raw[res["zip"]].fillna("").astype(str).str.replace(r"\D", "", regex=True)
                   .str.slice(0, 5) if "zip" in res else "")
    pos["mdcd"] = (pos_raw[res["mdcd"]].fillna("").astype(str).str.strip().str.upper()
                   if "mdcd" in res else "")
    pos = pos[ccn.notna()]

    out = []
    # 1. Medicaid vendor number (exact)
    if "mdcd" in res and len(medicaid):
        j = pos[pos["mdcd"] != ""].merge(medicaid, on="mdcd", how="inner")
        out.append(j[["ccn", "npi"]].assign(match_source="medicaid_vendor"))
    # 2. name + zip5
    nz = pos[(pos["name_key"] != "") & (pos["zip5"] != "")]
    facnz = facilities[(facilities["name_key"] != "") & (facilities["zip5"] != "")]
    if len(nz) and len(facnz):
        j = nz.merge(facnz[["name_key", "zip5", "npi"]], on=["name_key", "zip5"], how="inner")
        out.append(j[["ccn", "npi"]].assign(match_source="name_zip"))

    if not out:
        return pd.DataFrame(columns=["ccn", "npi", "match_source"])
    xw = pd.concat(out, ignore_index=True).dropna(subset=["ccn", "npi"])
    # keep one row per (ccn, npi); prefer the exact Medicaid-vendor match in the label
    xw["_rank"] = (xw["match_source"] == "medicaid_vendor").astype(int)
    xw = (xw.sort_values("_rank", ascending=False)
            .drop_duplicates(["ccn", "npi"]).drop(columns="_rank"))
    return xw.reset_index(drop=True)


def build_ccn_npi_crosswalk(raw: pd.DataFrame) -> pd.DataFrame:
    """Enrollment/POS-style rows → deduplicated (ccn, npi) crosswalk.

    Prefers a direct (CCN, NPI) pair when the file carries both columns. If the file
    has an NPI but no CCN column yet exposes NPPES 'Other Provider Identifier' fields,
    falls back to extracting the Medicare CCN (type 06) from those. Raises a clear
    error naming the missing field only when neither path is possible."""
    resolved = _resolve_columns(list(raw.columns), _WANTED)
    if "ccn" in resolved and "npi" in resolved:
        df = pd.DataFrame({
            "ccn": _canon_ccn(raw[resolved["ccn"]]),
            "npi": canonicalize_series(raw[resolved["npi"]]),
        })
        return df.dropna(subset=["ccn", "npi"]).drop_duplicates().reset_index(drop=True)
    # no direct CCN column — try the NPPES Other-Provider-Identifier bridge
    if "npi" in resolved and _nppes_opi_columns(list(raw.columns)):
        return crosswalk_from_nppes(raw)
    missing = [k for k in ("ccn", "npi") if k not in resolved]
    raise ValueError(
        f"input has no recognizable {', '.join(missing)} column "
        f"(looked for {{{', '.join(_WANTED[missing[0]])}}}), and no NPPES "
        f"'Other Provider Identifier' fields to bridge from; "
        f"saw headers: {list(raw.columns)[:12]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True,
                    help="CCN source: a single file with both CCN+NPI, the raw NPPES "
                         "(type-06 bridge), or the POS file when --nppes is given")
    ap.add_argument("--nppes", default=None,
                    help="raw NPPES file: when set, --in is treated as the POS file and "
                         "CCN↔NPI is built by the POS↔NPPES (Medicaid# + name/ZIP) join")
    ap.add_argument("--out", required=True, help="output ccn_to_npi parquet")
    args = ap.parse_args()
    from src.attempt_2.clean_data import read_csv_text
    p = Path(args.inp)
    raw = (pd.read_parquet(p) if p.suffix == ".parquet" else read_csv_text(p))
    if args.nppes:
        print(f"  reading NPPES facility/Medicaid columns from {args.nppes} …")
        facilities, medicaid = load_nppes_for_ccn(args.nppes)
        print(f"  NPPES: {len(facilities):,} NPIs (name+ZIP), {len(medicaid):,} type-05 Medicaid IDs")
        xw = crosswalk_from_pos_nppes(raw, facilities, medicaid)
        if "match_source" in xw.columns and len(xw):
            print("  matches by source: "
                  + ", ".join(f"{k}={v}" for k, v in xw["match_source"].value_counts().items()))
            xw = xw[["ccn", "npi"]]
    else:
        xw = build_ccn_npi_crosswalk(raw)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    xw.to_parquet(out, index=False)
    print(f"Wrote {out} — {len(xw):,} (ccn, npi) pairs; "
          f"{xw['ccn'].nunique():,} CCNs, {xw['npi'].nunique():,} NPIs")


if __name__ == "__main__":
    main()
