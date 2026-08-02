"""
hcpcs_time_convert.py — CMS Physician Work Time file → the hcpcs,minutes map.

The impossible-day approximation needs per-code service minutes
(preclean/hcpcs_time/hcpcs_minutes*.csv with columns hcpcs,minutes). CMS
publishes the times in the PFS final rule's "Physician Work Time" supporting
file (per-HCPCS pre/intra/post times plus bundled post-op visit counts).
This converts it.

Design choice, stated: minutes = MEDIAN INTRA-SERVICE TIME, not total time.
Pre-evaluation and post-service work can fall outside the service day, so
counting only hands-on time UNDER-estimates a provider's implied hours and
the impossible-day flag can never over-accuse — the same one-sided
conservatism as the MUE pigeonhole bound. ``--use-total`` overrides for
sensitivity checks; never ship totals to the scheme.

  python -m src.ingest_cms.hcpcs_time_convert \
      --in  preclean/hcpcs_time/physician_time_2023.xlsx \
      --out preclean/hcpcs_time/hcpcs_minutes_2023.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# column aliases, the CY2023 layout first (identifiers stay strings — rule 1)
CODE_COLS = ["cpt_code", "CPT_Code", "HCPCS", "hcpcs", "HCPCS Code", "CPT"]
INTRA_COLS = ["Median_Intra_Service_Time", "Median Intra Service Time",
              "Intra_Service_Time", "INTRA TIME", "intra_time"]
TOTAL_COLS = ["Total_time", "Total_Time", "TOTAL TIME", "total_time"]


def _pick(cols: list[str], options: list[str]) -> str | None:
    low = {str(c).strip().lower(): c for c in cols}
    return next((low[o.lower()] for o in options if o.lower() in low), None)


def convert(raw: pd.DataFrame, use_total: bool = False) -> pd.DataFrame:
    """Physician Work Time frame → tidy (hcpcs, minutes). Drops codes with no
    usable time rather than inventing one (skip-missing downstream)."""
    code_c = _pick(list(raw.columns), CODE_COLS)
    time_c = _pick(list(raw.columns), TOTAL_COLS if use_total else INTRA_COLS)
    if not code_c or not time_c:
        raise ValueError(
            f"could not resolve code/time columns "
            f"({'total' if use_total else 'intra'} mode); "
            f"saw {list(raw.columns)[:10]}")
    hcpcs = (raw[code_c].astype(str).str.strip().str.upper()
             .str.replace(r"\.0$", "", regex=True))
    # numeric CPTs lose leading zeros in Excel — restore to 5 chars; alpha
    # codes (G/J/T…) are already well-formed
    hcpcs = hcpcs.where(~hcpcs.str.fullmatch(r"\d{1,5}"), hcpcs.str.zfill(5))
    minutes = pd.to_numeric(raw[time_c], errors="coerce")
    out = pd.DataFrame({"hcpcs": hcpcs, "minutes": minutes})
    out = out[out["hcpcs"].str.len().between(4, 5)
              & out["minutes"].notna() & (out["minutes"] > 0)]
    # one row per code: the largest published time (most permissive — fewer
    # false impossible-day flags on codes listed at multiple times)
    return (out.groupby("hcpcs", as_index=False)["minutes"].max()
            .sort_values("hcpcs").reset_index(drop=True))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--use-total", action="store_true",
                    help="total time instead of intra-service (sensitivity "
                         "checks only — never ship totals to the scheme)")
    args = ap.parse_args()
    p = Path(args.src)
    raw = (pd.read_excel(p) if p.suffix.lower() in (".xlsx", ".xls")
           else pd.read_csv(p, dtype=str))
    out = convert(raw, use_total=args.use_total)
    out.to_csv(args.out, index=False)
    print(f"[hcpcs_time_convert] {len(out):,} codes with "
          f"{'total' if args.use_total else 'intra-service'} minutes "
          f"(median {out['minutes'].median():.0f}, max {out['minutes'].max():.0f}) "
          f"-> {args.out}")


if __name__ == "__main__":
    main()
