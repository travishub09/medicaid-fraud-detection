"""
claim_slices.py — derive the two specialized claim slices two schemes need.

Most adapters read a published CMS PUF, but two registry features need a cut of
the *claims* that the by-HCPCS spending fact doesn't carry:

  drug_outlier · drug_spread_anomaly   needs NDC-level drug claims
      (billing_npi, ndc, units, billed_cost) so NADAC's per-unit benchmark can be
      compared to what was actually billed.
  dme_ring · ineligible_referral_share  needs claims that carry the REFERRING NPI
      (billing_npi, referring_npi, total_paid) so a referrer's eligibility can be
      checked.

These are derivations off a richer claims extract (T-MSIS line/claim level, or a
drug-claims / referral file), NOT new procurements. This module reshapes whatever
claims file has the needed columns into the exact frame the adapters expect, and
fails loud (naming the missing column) when the source can't support the slice —
so a thin by-HCPCS file is rejected rather than silently producing an empty slice.

CLI:
    python -m src.ingest_cms.claim_slices --kind ndc \
        --in ~/Desktop/data/preclean/rx_claims.csv \
        --out ~/Desktop/data/processed/ndc_claims.parquet
    python -m src.ingest_cms.claim_slices --kind referral \
        --in ~/Desktop/data/preclean/claims_with_referrer.csv \
        --out ~/Desktop/data/processed/referred_claims.parquet
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import canonicalize_series, _resolve_columns

_NDC_WANTED = {
    "billing_npi": ["billing_npi", "npi", "prscrbr_npi", "rndrng_npi", "provider_npi"],
    "ndc": ["ndc", "ndc_code", "product_ndc", "ndc11", "ndc_number"],
    "units": ["units", "qty", "quantity", "units_reimbursed", "number_of_units"],
    "billed_cost": ["billed_cost", "total_paid", "amount_reimbursed", "medicaid_amount_reimbursed",
                    "total_amount_reimbursed", "cost", "ingredient_cost"],
}
_REFERRAL_WANTED = {
    "billing_npi": ["billing_npi", "npi", "rndrng_npi", "supplier_npi", "provider_npi"],
    "referring_npi": ["referring_npi", "rfrg_npi", "ordering_npi", "referring_provider_npi",
                      "ordering_referring_npi"],
    "total_paid": ["total_paid", "billed_cost", "amount_reimbursed", "paid", "medicaid_amount_reimbursed"],
}


def _build(raw: pd.DataFrame, wanted: dict, npi_cols: list[str],
           num_cols: list[str]) -> pd.DataFrame:
    resolved = _resolve_columns(list(raw.columns), wanted)
    missing = [k for k in wanted if k not in resolved]
    if missing:
        raise ValueError(
            f"claims source is missing {', '.join(missing)} "
            f"(looked for {{{', '.join(wanted[missing[0]])}}}); this slice needs a "
            f"richer claims extract than the by-HCPCS spending file. "
            f"Saw headers: {list(raw.columns)[:12]}")
    out = pd.DataFrame()
    for k in wanted:
        col = raw[resolved[k]]
        if k in npi_cols:
            out[k] = canonicalize_series(col)
        elif k in num_cols:
            out[k] = pd.to_numeric(col, errors="coerce").fillna(0.0)
        else:
            out[k] = col.fillna("").astype(str).str.strip()
    out = out.dropna(subset=npi_cols)
    return out.reset_index(drop=True)


def build_ndc_claims(raw: pd.DataFrame) -> pd.DataFrame:
    """Reshape a drug-claims file → (billing_npi, ndc, units, billed_cost)."""
    return _build(raw, _NDC_WANTED, npi_cols=["billing_npi"],
                  num_cols=["units", "billed_cost"])


def build_referred_claims(raw: pd.DataFrame) -> pd.DataFrame:
    """Reshape a referral-bearing claims file → (billing_npi, referring_npi,
    total_paid). Rows without a valid referring NPI are dropped (nothing to check)."""
    return _build(raw, _REFERRAL_WANTED, npi_cols=["billing_npi", "referring_npi"],
                  num_cols=["total_paid"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", required=True, choices=["ndc", "referral"])
    ap.add_argument("--in", dest="inp", required=True, help="claims file (csv/parquet)")
    ap.add_argument("--out", required=True, help="output parquet")
    args = ap.parse_args()
    from src.attempt_2.clean_data import read_csv_text
    p = Path(args.inp)
    raw = (pd.read_parquet(p) if p.suffix == ".parquet" else read_csv_text(p))
    out_df = build_ndc_claims(raw) if args.kind == "ndc" else build_referred_claims(raw)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_parquet(out, index=False)
    print(f"Wrote {out} — {len(out_df):,} {args.kind} claim rows")


if __name__ == "__main__":
    main()
