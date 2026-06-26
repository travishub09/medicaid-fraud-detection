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
                    help="PECOS enrollment / POS file (csv or parquet)")
    ap.add_argument("--out", required=True, help="output ccn_to_npi parquet")
    args = ap.parse_args()
    from src.attempt_2.clean_data import read_csv_text
    p = Path(args.inp)
    raw = (pd.read_parquet(p) if p.suffix == ".parquet" else read_csv_text(p))
    xw = build_ccn_npi_crosswalk(raw)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    xw.to_parquet(out, index=False)
    print(f"Wrote {out} — {len(xw):,} (ccn, npi) pairs; "
          f"{xw['ccn'].nunique():,} CCNs, {xw['npi'].nunique():,} NPIs")


if __name__ == "__main__":
    main()
