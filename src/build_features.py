"""
build_features.py

Takes the parsed (ingested, minimally cleaned) Medicaid claims dataset and
produces a per-provider feature table targeting twelve specific fraud signals.
All features are scale-invariant — ratios, concentrations, or normalised
counts — so that high-volume providers are not penalised and solo practitioners
are not artificially elevated.

Inputs
------
--claims       : parsed Part B claims CSV (columns per DATA_DICTIONARY.md,
                 plus optional `service_state` column)
--providers    : provider/NPI metadata CSV with columns:
                     npi, taxonomy_code, practice_state,
                     npi_registration_date, medicaid_enrollment_date,
                     npi_deactivation_date (null if still active)
--taxonomy-map : CSV with columns `taxonomy_code`, `hcpcs_code` listing
                 every HCPCS code that is typical for a given taxonomy.
                 If omitted, pct_hcpcs_outside_taxonomy is set to NaN.
--output       : destination CSV path

Features produced
-----------------
avg_claims_per_beneficiary  Phantom billing
avg_paid_per_claim          Upcoding
paid_vs_peer_ratio          Overcharging vs specialty norm
claims_vs_peer_ratio        Volume anomaly vs peers
billing_cv                  Erratic / sudden billing pattern
days_to_first_claim         Brand-new Medicaid enrolment billing at scale
npi_age_days_at_first_claim NPI registration age at first claim
n_distinct_hcpcs            HCPCS code diversity
hcpcs_concentration         Single-code phantom billing (HHI)
billing_on_deactivated_npi  Identity / NPI-theft fraud
state_mismatch              Geographic implausibility
pct_hcpcs_outside_taxonomy  Specialty misclassification

Usage
-----
    python -m src.build_features \\
        --claims    data/raw/claims_part_b.csv \\
        --providers data/raw/providers.csv \\
        --taxonomy-map data/reference/taxonomy_hcpcs_map.csv \\
        --output    data/processed/fraud_features.csv
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_claims(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["service_date"])
    required = {
        "claim_id", "npi", "beneficiary_id", "service_date",
        "procedure_code", "paid_amount",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Claims file missing columns: {missing}")

    df["npi"]         = df["npi"].astype(str).str.strip()
    df["paid_amount"] = pd.to_numeric(df["paid_amount"], errors="coerce").fillna(0)
    df = df.dropna(subset=["npi", "beneficiary_id", "service_date"])
    return df


def load_providers(path: str | Path) -> pd.DataFrame:
    needed = {
        "npi", "taxonomy_code", "practice_state",
        "npi_registration_date", "medicaid_enrollment_date",
    }
    df = pd.read_csv(path, parse_dates=["npi_registration_date", "medicaid_enrollment_date"])

    # npi_deactivation_date is optional — treat missing column as all-null
    if "npi_deactivation_date" in df.columns:
        df["npi_deactivation_date"] = pd.to_datetime(
            df["npi_deactivation_date"], errors="coerce"
        )
    else:
        df["npi_deactivation_date"] = pd.NaT

    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"Providers file missing columns: {missing}")

    df["npi"] = df["npi"].astype(str).str.strip()
    return df.set_index("npi")


def load_taxonomy_map(path: str | Path) -> dict[str, set[str]]:
    """Return {taxonomy_code: {hcpcs_code, ...}}."""
    df = pd.read_csv(path, dtype=str)
    if not {"taxonomy_code", "hcpcs_code"}.issubset(df.columns):
        raise ValueError("Taxonomy map must have columns: taxonomy_code, hcpcs_code")
    mapping: dict[str, set[str]] = {}
    for tax, hcpcs in zip(df["taxonomy_code"], df["hcpcs_code"]):
        mapping.setdefault(tax.strip(), set()).add(hcpcs.strip())
    return mapping


# ---------------------------------------------------------------------------
# Peer-group helpers
# ---------------------------------------------------------------------------

def _peer_medians(provider_series: pd.Series, taxonomy_lookup: pd.Series) -> pd.Series:
    """
    For each NPI compute the median of `provider_series` within its taxonomy
    peer group. NPIs with no taxonomy mapping get the global median.
    """
    combined = pd.DataFrame({"value": provider_series, "taxonomy": taxonomy_lookup})
    combined["peer_median"] = combined.groupby("taxonomy")["value"].transform("median")
    combined["peer_median"] = combined["peer_median"].fillna(combined["value"].median())
    return combined["peer_median"]


def _peer_ratio(provider_series: pd.Series, taxonomy_lookup: pd.Series) -> pd.Series:
    """provider value / peer median. Clipped to avoid extreme ratios from near-zero denominators."""
    medians = _peer_medians(provider_series, taxonomy_lookup)
    ratio = provider_series / medians.replace(0, np.nan)
    return ratio.clip(upper=100).fillna(1.0)


# ---------------------------------------------------------------------------
# Individual feature computations
# ---------------------------------------------------------------------------

def feat_avg_claims_per_beneficiary(claims: pd.DataFrame) -> pd.Series:
    n_claims = claims.groupby("npi")["claim_id"].count()
    n_benes  = claims.groupby("npi")["beneficiary_id"].nunique()
    return (n_claims / n_benes.replace(0, np.nan)).rename("avg_claims_per_beneficiary")


def feat_avg_paid_per_claim(claims: pd.DataFrame) -> pd.Series:
    total_paid = claims.groupby("npi")["paid_amount"].sum()
    n_claims   = claims.groupby("npi")["claim_id"].count()
    return (total_paid / n_claims.replace(0, np.nan)).rename("avg_paid_per_claim")


def feat_paid_vs_peer_ratio(
    avg_paid: pd.Series, taxonomy_lookup: pd.Series
) -> pd.Series:
    return _peer_ratio(avg_paid, taxonomy_lookup).rename("paid_vs_peer_ratio")


def feat_claims_vs_peer_ratio(
    claims: pd.DataFrame, taxonomy_lookup: pd.Series
) -> pd.Series:
    """Claims per unique beneficiary, normalised by specialty peer median."""
    avg_claims = feat_avg_claims_per_beneficiary(claims)
    return _peer_ratio(avg_claims, taxonomy_lookup).rename("claims_vs_peer_ratio")


def feat_billing_cv(claims: pd.DataFrame) -> pd.Series:
    """
    Coefficient of variation of monthly claim counts.
    High CV signals burst / erratic billing — a pattern common in phantom billing
    schemes that ramp up quickly before shutting down.
    """
    monthly = (
        claims.assign(month=claims["service_date"].dt.to_period("M"))
        .groupby(["npi", "month"])["claim_id"]
        .count()
        .reset_index()
    )

    def _cv(s: pd.Series) -> float:
        m = s.mean()
        return s.std() / m if m > 0 and len(s) > 1 else 0.0

    return monthly.groupby("npi")["claim_id"].apply(_cv).rename("billing_cv")


def feat_days_to_first_claim(
    claims: pd.DataFrame, providers: pd.DataFrame
) -> pd.Series:
    """Days from Medicaid enrollment date to first claim in the dataset."""
    first_claim = claims.groupby("npi")["service_date"].min()
    enroll_date = providers["medicaid_enrollment_date"].reindex(first_claim.index)
    days = (first_claim - enroll_date).dt.days.clip(lower=0)
    return days.rename("days_to_first_claim")


def feat_npi_age_days_at_first_claim(
    claims: pd.DataFrame, providers: pd.DataFrame
) -> pd.Series:
    """
    Age of the NPI (from NPPES registration) at the time of the provider's
    first claim. Newly registered NPIs submitting high volumes immediately
    are a strong fraud indicator.
    """
    first_claim = claims.groupby("npi")["service_date"].min()
    reg_date    = providers["npi_registration_date"].reindex(first_claim.index)
    days = (first_claim - reg_date).dt.days.clip(lower=0)
    return days.rename("npi_age_days_at_first_claim")


def feat_n_distinct_hcpcs(claims: pd.DataFrame) -> pd.Series:
    return (
        claims.groupby("npi")["procedure_code"]
        .nunique()
        .rename("n_distinct_hcpcs")
    )


def feat_hcpcs_concentration(claims: pd.DataFrame) -> pd.Series:
    """
    Herfindahl-Hirschman Index of billed HCPCS codes.
    Score of 1.0 means every claim used the same code — a hallmark of
    single-code phantom billing schemes.
    """
    def _hhi(s: pd.Series) -> float:
        shares = s.value_counts(normalize=True)
        return float((shares ** 2).sum())

    return (
        claims.groupby("npi")["procedure_code"]
        .apply(_hhi)
        .rename("hcpcs_concentration")
    )


def feat_billing_on_deactivated_npi(
    claims: pd.DataFrame, providers: pd.DataFrame
) -> pd.Series:
    """
    Fraction of claims submitted on or after the NPI deactivation date.
    Any non-zero value is a hard identity-fraud / NPI-theft signal.
    """
    deactivation = providers["npi_deactivation_date"]
    npis = claims["npi"].unique()
    result = {}

    for npi in npis:
        deact = deactivation.get(npi, pd.NaT)
        if pd.isna(deact):
            result[npi] = 0.0
            continue
        npi_claims = claims[claims["npi"] == npi]
        result[npi] = float((npi_claims["service_date"] >= deact).mean())

    return pd.Series(result, name="billing_on_deactivated_npi")


def feat_state_mismatch(
    claims: pd.DataFrame, providers: pd.DataFrame
) -> pd.Series:
    """
    1 if the provider's registered practice state differs from the modal
    state appearing in their claims (via `service_state` column if present,
    otherwise via beneficiary state if joined, otherwise NaN).

    Geographic implausibility: a provider registered in Texas whose claims
    consistently show New York service locations cannot plausibly have
    delivered those services in person.
    """
    if "service_state" not in claims.columns:
        npis = claims["npi"].unique()
        return pd.Series(np.nan, index=npis, name="state_mismatch")

    modal_service_state = (
        claims.dropna(subset=["service_state"])
        .groupby("npi")["service_state"]
        .agg(lambda s: s.mode().iloc[0] if not s.mode().empty else np.nan)
    )
    practice_state = providers["practice_state"].reindex(modal_service_state.index)
    mismatch = (modal_service_state != practice_state).astype(float)
    return mismatch.rename("state_mismatch")


def feat_pct_hcpcs_outside_taxonomy(
    claims: pd.DataFrame,
    providers: pd.DataFrame,
    taxonomy_map: dict[str, set[str]] | None,
) -> pd.Series:
    """
    Fraction of a provider's billed HCPCS codes that fall outside the set
    of codes expected for their taxonomy/specialty.

    A cardiologist billing predominantly dermatology codes suggests either
    misclassification or a stolen/misused NPI.
    """
    npis = claims["npi"].unique()

    if taxonomy_map is None:
        return pd.Series(np.nan, index=npis, name="pct_hcpcs_outside_taxonomy")

    result = {}
    for npi in npis:
        taxonomy = providers["taxonomy_code"].get(npi)
        if not taxonomy or taxonomy not in taxonomy_map:
            result[npi] = np.nan
            continue
        allowed  = taxonomy_map[taxonomy]
        npi_codes = claims.loc[claims["npi"] == npi, "procedure_code"]
        result[npi] = float((~npi_codes.isin(allowed)).mean())

    return pd.Series(result, name="pct_hcpcs_outside_taxonomy")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_fraud_features(
    claims: pd.DataFrame,
    providers: pd.DataFrame,
    taxonomy_map: dict[str, set[str]] | None = None,
) -> pd.DataFrame:
    taxonomy_lookup = providers["taxonomy_code"].reindex(
        claims["npi"].unique()
    )

    avg_paid   = feat_avg_paid_per_claim(claims)
    avg_claims = feat_avg_claims_per_beneficiary(claims)

    features = pd.DataFrame(index=claims["npi"].unique())
    features.index.name = "npi"

    features["avg_claims_per_beneficiary"]  = avg_claims
    features["avg_paid_per_claim"]          = avg_paid
    features["paid_vs_peer_ratio"]          = feat_paid_vs_peer_ratio(avg_paid, taxonomy_lookup)
    features["claims_vs_peer_ratio"]        = feat_claims_vs_peer_ratio(claims, taxonomy_lookup)
    features["billing_cv"]                  = feat_billing_cv(claims)
    features["days_to_first_claim"]         = feat_days_to_first_claim(claims, providers)
    features["npi_age_days_at_first_claim"] = feat_npi_age_days_at_first_claim(claims, providers)
    features["n_distinct_hcpcs"]            = feat_n_distinct_hcpcs(claims)
    features["hcpcs_concentration"]         = feat_hcpcs_concentration(claims)
    features["billing_on_deactivated_npi"]  = feat_billing_on_deactivated_npi(claims, providers)
    features["state_mismatch"]              = feat_state_mismatch(claims, providers)
    features["pct_hcpcs_outside_taxonomy"]  = feat_pct_hcpcs_outside_taxonomy(
                                                 claims, providers, taxonomy_map
                                             )

    # Attach taxonomy for downstream peer-group scoring (not used as a model feature)
    features["taxonomy_code"] = taxonomy_lookup.values

    return features


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_features(features: pd.DataFrame, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(path)
    print(f"Saved {len(features)} provider rows → {path}")
    nulls = features.isnull().sum()
    nulls = nulls[nulls > 0]
    if not nulls.empty:
        print("  Columns with nulls (may need taxonomy-map or service_state column):")
        for col, n in nulls.items():
            print(f"    {col}: {n} null(s)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build fraud-signal features from parsed Medicaid claims."
    )
    parser.add_argument("--claims",       required=True, help="Parsed Part B claims CSV")
    parser.add_argument("--providers",    required=True, help="Provider/NPI metadata CSV")
    parser.add_argument("--taxonomy-map", default=None,
                        help="CSV mapping taxonomy codes to expected HCPCS codes")
    parser.add_argument("--output", default="data/processed/fraud_features.csv")
    args = parser.parse_args()

    print(f"Loading claims from {args.claims} …")
    claims = load_claims(args.claims)
    print(f"  {len(claims):,} claims across {claims['npi'].nunique():,} providers")

    print(f"Loading providers from {args.providers} …")
    providers = load_providers(args.providers)
    print(f"  {len(providers):,} providers")

    taxonomy_map = None
    if args.taxonomy_map:
        print(f"Loading taxonomy map from {args.taxonomy_map} …")
        taxonomy_map = load_taxonomy_map(args.taxonomy_map)
        print(f"  {len(taxonomy_map):,} taxonomy codes mapped")

    print("Computing fraud features …")
    features = build_fraud_features(claims, providers, taxonomy_map)

    save_features(features, args.output)


if __name__ == "__main__":
    main()
