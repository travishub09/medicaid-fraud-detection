"""
ccn_npi_crosswalk.py — build the PECOS CCN↔NPI crosswalk (B-track unlock).

Three Model-A scenarios resolve at the facility (CCN) grain — worthless_services
(PBJ understaffing / capacity), hospice_ineligibility, and cost_report_fraud
(HCRIS) — but every downstream join in this platform is keyed on NPI. The facility
adapters (`ingest_cms/facility.py::rollup_ccn_to_org`, `pos.py`, `hcris.py`) all
take a ``ccn_to_npi`` crosswalk; nothing produced one, so those schemes were
data-blocked. This module builds it.

Source: any enrollment-style file that carries BOTH a CMS Certification Number
(CCN / provider number) and an NPI — the PECOS "Public Provider Enrollment"
institutional file, or the Provider-of-Services (POS) file. Column names vary
across vintages, so headers are resolved by token (the shared `_resolve_columns`).
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


def build_ccn_npi_crosswalk(raw: pd.DataFrame) -> pd.DataFrame:
    """Enrollment/POS-style rows → deduplicated (ccn, npi) crosswalk.

    Raises a clear error naming the missing field if the input carries no
    recognizable CCN or NPI column (so a wrong file fails loud, not silent)."""
    resolved = _resolve_columns(list(raw.columns), _WANTED)
    missing = [k for k in ("ccn", "npi") if k not in resolved]
    if missing:
        raise ValueError(
            f"input has no recognizable {', '.join(missing)} column "
            f"(looked for {{{', '.join(_WANTED[missing[0]])}}}); "
            f"saw headers: {list(raw.columns)[:12]}")
    df = pd.DataFrame({
        "ccn": _canon_ccn(raw[resolved["ccn"]]),
        "npi": canonicalize_series(raw[resolved["npi"]]),
    })
    df = df.dropna(subset=["ccn", "npi"]).drop_duplicates()
    return df.reset_index(drop=True)


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
