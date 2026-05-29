"""
clean_data.py

Ingests Medicaid Part B professional claims (and optionally Part D prescription
drug events) and produces a per-provider feature table using size-invariant
metrics. Every feature is expressed as a rate, ratio, or concentration index
so that a solo practitioner and a high-volume clinic are compared fairly.

Expected input: claims_part_b CSV (columns per DATA_DICTIONARY.md).
Optional:       claims_part_d CSV for prescription-level features.

Usage:
    python -m src.clean_data --claims data/raw/claims_part_b.csv
    python -m src.clean_data --claims data/raw/claims_part_b.csv \
                              --rx     data/raw/claims_part_d.csv \
                              --output data/processed/provider_features.csv
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import skew


# ---------------------------------------------------------------------------
# Highest-complexity E&M codes — high share is an upcoding signal
# ---------------------------------------------------------------------------
HIGH_COMPLEXITY_EM = {"99215", "99214", "99205", "99204", "99223", "99233"}

# CMS place-of-service codes that represent non-office/atypical settings
WEEKEND_DAYS = {5, 6}  # Saturday, Sunday


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_claims(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["service_date", "submitted_date"])
    required = {
        "claim_id", "npi", "beneficiary_id", "service_date",
        "procedure_code", "units", "billed_amount", "allowed_amount",
        "paid_amount",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Claims file missing columns: {missing}")

    df["billed_amount"]   = pd.to_numeric(df["billed_amount"],   errors="coerce")
    df["allowed_amount"]  = pd.to_numeric(df["allowed_amount"],  errors="coerce")
    df["paid_amount"]     = pd.to_numeric(df["paid_amount"],     errors="coerce")
    df["units"]           = pd.to_numeric(df["units"],           errors="coerce").fillna(1)
    df = df.dropna(subset=["npi", "beneficiary_id", "service_date"])
    return df


def load_rx(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["fill_date"])
    required = {"prescriber_npi", "beneficiary_id", "fill_date", "is_controlled", "total_cost"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Rx file missing columns: {missing}")
    df["is_controlled"] = df["is_controlled"].astype(bool)
    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hhi(series: pd.Series) -> float:
    shares = series.value_counts(normalize=True)
    return float((shares ** 2).sum())


def _cv(series: pd.Series) -> float:
    m = series.mean()
    return float(series.std() / m) if m != 0 else 0.0


def _payment_ratio(paid: pd.Series, allowed: pd.Series) -> float:
    denom = allowed.replace(0, np.nan)
    ratios = paid / denom
    return float(ratios.dropna().mean()) if not ratios.dropna().empty else 1.0


# ---------------------------------------------------------------------------
# Per-provider feature engineering
# ---------------------------------------------------------------------------

def build_provider_features(claims: pd.DataFrame, rx: pd.DataFrame | None = None) -> pd.DataFrame:
    claims = claims.copy()
    claims["is_weekend"]   = claims["service_date"].dt.dayofweek.isin(WEEKEND_DAYS)
    claims["is_denied"]    = claims["paid_amount"].fillna(0) == 0
    claims["is_high_em"]   = claims["procedure_code"].isin(HIGH_COMPLEXITY_EM)
    claims["is_em"]        = claims["procedure_code"].str.startswith("992", na=False)
    claims["lag_days"]     = (
        claims.groupby("npi")["service_date"]
        .transform(lambda s: s.sort_values().diff().dt.days)
    )

    records = []
    for npi, grp in claims.groupby("npi"):
        n_claims      = len(grp)
        n_beneficiary = grp["beneficiary_id"].nunique()
        date_span     = max((grp["service_date"].max() - grp["service_date"].min()).days, 1)

        em_claims = grp[grp["is_em"]]
        upcoding_index = (
            em_claims["is_high_em"].mean() if len(em_claims) >= 5 else np.nan
        )

        lag = grp["lag_days"].dropna()

        records.append({
            "npi": npi,
            "n_claims": n_claims,

            # Volume rate — normalised by active days so volume doesn't inflate the score
            "claim_rate_per_day": n_claims / date_span,

            # Beneficiary breadth — very low = one patient seen repeatedly (suspicious)
            "unique_beneficiary_ratio": n_beneficiary / n_claims,

            # Billing diversity — low HHI = varied services; high HHI = single repeated code
            "procedure_concentration": _hhi(grp["procedure_code"]),

            # Unit intensity per claim
            "avg_units_per_claim": grp["units"].mean(),
            "units_cv": _cv(grp["units"]),

            # Upcoding signal
            "upcoding_index": upcoding_index,

            # Off-hours billing
            "weekend_service_ratio": grp["is_weekend"].mean(),

            # Claim velocity — high CV = erratic bursts
            "lag_cv": _cv(lag) if len(lag) >= 3 else np.nan,

            # Payment quality
            "denial_rate": grp["is_denied"].mean(),
            "avg_payment_ratio": _payment_ratio(grp["paid_amount"], grp["allowed_amount"]),

            # Billed-vs-allowed inflation
            "avg_billed_to_allowed": _payment_ratio(grp["billed_amount"], grp["allowed_amount"]),
        })

    features = pd.DataFrame(records).set_index("npi")

    # Merge prescription features if available
    if rx is not None:
        rx_features = _build_rx_features(rx)
        features = features.join(rx_features, how="left")

    features = features.fillna(features.median(numeric_only=True))
    return features


def _build_rx_features(rx: pd.DataFrame) -> pd.DataFrame:
    records = []
    for npi, grp in rx.groupby("prescriber_npi"):
        n_scripts = len(grp)
        records.append({
            "npi": npi,
            "controlled_rx_ratio": grp["is_controlled"].mean(),
            "avg_days_supply": grp["days_supply"].mean() if "days_supply" in grp else np.nan,
            "rx_cost_cv": _cv(grp["total_cost"]),
            "unique_drug_ratio": grp["ndc"].nunique() / n_scripts if "ndc" in grp else np.nan,
        })
    return pd.DataFrame(records).set_index("npi")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_features(features: pd.DataFrame, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(path)
    print(f"Saved {len(features)} provider feature rows → {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Clean Medicaid claims and produce size-invariant provider features."
    )
    parser.add_argument("--claims", required=True, help="Path to Part B claims CSV")
    parser.add_argument("--rx",     default=None,  help="Path to Part D prescription CSV (optional)")
    parser.add_argument("--output", default="data/processed/provider_features.csv")
    args = parser.parse_args()

    print(f"Loading claims from {args.claims} …")
    claims = load_claims(args.claims)
    print(f"  {len(claims):,} claims across {claims['npi'].nunique():,} providers")

    rx = None
    if args.rx:
        print(f"Loading prescriptions from {args.rx} …")
        rx = load_rx(args.rx)
        print(f"  {len(rx):,} prescription events")

    print("Building provider features …")
    features = build_provider_features(claims, rx)

    save_features(features, args.output)


if __name__ == "__main__":
    main()
