"""
build_features.py

Reads the two output files from clean_data.py and produces a per-provider
fraud-signal feature table.

Inputs
------
--monthly      : data/processed/provider_monthly.parquet
                   cols: npi, hcpcs_code, service_month, total_claims,
                         total_beneficiaries, total_paid_amount
--providers    : data/processed/providers_clean.csv
                   cols: npi, total_claims, total_beneficiaries, total_paid,
                         first_billing_month, last_billing_month,
                         n_distinct_hcpcs, n_active_months, taxonomy_code,
                         practice_state, npi_registration_date,
                         npi_deactivation_date, is_excluded
--taxonomy-map : data/reference/taxonomy_hcpcs_map.csv (optional)
                   cols: taxonomy_code, hcpcs_code

Features produced
-----------------
avg_claims_per_beneficiary  Phantom billing
avg_paid_per_claim          Upcoding
paid_vs_peer_ratio          Overcharging vs specialty norm
claims_vs_peer_ratio        Volume anomaly vs peers
billing_cv                  Erratic / burst billing pattern
days_to_first_claim         Brand-new NPI billing at scale
npi_age_days_at_first_claim NPI registration age at first claim
n_distinct_hcpcs            HCPCS code diversity
hcpcs_concentration         Single-code phantom billing (HHI)
billing_on_deactivated_npi  Identity / NPI-theft fraud
state_mismatch              Geographic implausibility (NaN if data unavailable)
pct_hcpcs_outside_taxonomy  Specialty misclassification

Note on beneficiary counts
--------------------------
The CMS spending data reports total_beneficiaries per NPI × HCPCS × month,
not unique beneficiaries across the full provider record. Summing across
HCPCS codes double-counts beneficiaries seen for multiple services.
avg_claims_per_beneficiary and claims_vs_peer_ratio are therefore
directionally correct but inflated; the inflation is systematic across all
providers so relative rankings are unaffected.

Usage
-----
    python -m src.build_features \\
        --monthly   data/processed/provider_monthly.parquet \\
        --providers data/processed/providers_clean.csv \\
        --taxonomy-map data/reference/taxonomy_hcpcs_map.csv \\
        --output    data/processed/fraud_features.csv
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import linregress


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_monthly(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)

    required = {"npi", "hcpcs_code", "service_month", "total_claims",
                "total_beneficiaries", "total_paid_amount"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Monthly file missing columns: {missing}")

    df["npi"]          = df["npi"].astype(str).str.strip()
    df["hcpcs_code"]   = df["hcpcs_code"].astype(str).str.strip()
    df["total_claims"] = pd.to_numeric(df["total_claims"], errors="coerce").fillna(0)
    df["total_beneficiaries"] = pd.to_numeric(df["total_beneficiaries"], errors="coerce").fillna(0)
    df["total_paid_amount"]   = pd.to_numeric(df["total_paid_amount"],   errors="coerce").fillna(0)
    # Parse YYYY-MM service_month into a sortable period
    df["month_dt"] = pd.to_datetime(df["service_month"], format="%Y-%m", errors="coerce")
    return df


def load_providers(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        dtype={"npi": str},
        parse_dates=["npi_registration_date", "npi_deactivation_date",
                     "first_billing_month",   "last_billing_month"],
    )
    required = {
        "npi", "total_claims", "total_beneficiaries", "total_paid",
        "first_billing_month", "n_distinct_hcpcs", "taxonomy_code", "practice_state",
        "npi_registration_date",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Providers file missing columns: {missing}")

    if "npi_deactivation_date" not in df.columns:
        df["npi_deactivation_date"] = pd.NaT

    df["npi"] = df["npi"].str.strip()
    return df.set_index("npi")


def load_taxonomy_map(path: str | Path) -> dict[str, set[str]]:
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

def _peer_ratio(provider_series: pd.Series, taxonomy_series: pd.Series) -> pd.Series:
    """
    Divide each provider's value by the median of providers in the same
    taxonomy group. Falls back to global median for unmatched taxonomies.
    Clipped at 100× to prevent extreme values from outlier denominators.
    """
    combined = pd.DataFrame({"value": provider_series, "taxonomy": taxonomy_series})
    combined["peer_median"] = combined.groupby("taxonomy")["value"].transform("median")
    combined["peer_median"] = combined["peer_median"].fillna(combined["value"].median())
    ratio = combined["value"] / combined["peer_median"].replace(0, np.nan)
    return ratio.clip(upper=100).fillna(1.0)


# ---------------------------------------------------------------------------
# Feature functions
# ---------------------------------------------------------------------------

def feat_avg_claims_per_beneficiary(providers: pd.DataFrame) -> pd.Series:
    ratio = providers["total_claims"] / providers["total_beneficiaries"].replace(0, np.nan)
    return ratio.rename("avg_claims_per_beneficiary")


def feat_avg_paid_per_claim(providers: pd.DataFrame) -> pd.Series:
    ratio = providers["total_paid"] / providers["total_claims"].replace(0, np.nan)
    return ratio.rename("avg_paid_per_claim")


def feat_paid_vs_peer_ratio(avg_paid: pd.Series, providers: pd.DataFrame) -> pd.Series:
    taxonomy = providers["taxonomy_code"].reindex(avg_paid.index)
    return _peer_ratio(avg_paid, taxonomy).rename("paid_vs_peer_ratio")


def feat_claims_vs_peer_ratio(
    avg_claims: pd.Series, providers: pd.DataFrame
) -> pd.Series:
    taxonomy = providers["taxonomy_code"].reindex(avg_claims.index)
    return _peer_ratio(avg_claims, taxonomy).rename("claims_vs_peer_ratio")


def feat_billing_cv(monthly: pd.DataFrame) -> pd.Series:
    """
    Coefficient of variation of total monthly claim counts per provider.
    High CV indicates erratic burst-and-stop billing — a common fraud pattern.
    """
    monthly_totals = (
        monthly.groupby(["npi", "service_month"])["total_claims"]
        .sum()
        .reset_index()
    )

    def _cv(s: pd.Series) -> float:
        m = s.mean()
        return float(s.std() / m) if m > 0 and len(s) > 1 else 0.0

    return monthly_totals.groupby("npi")["total_claims"].apply(_cv).rename("billing_cv")


def feat_days_to_first_claim(providers: pd.DataFrame) -> pd.Series:
    """
    Days from the provider's first billing month to the start of their NPI
    registration. A very small value means the provider began billing almost
    immediately after receiving their NPI — high-volume instant billing is a
    fraud signal.
    """
    days = (
        providers["first_billing_month"] - providers["npi_registration_date"]
    ).dt.days.clip(lower=0)
    return days.rename("days_to_first_claim")


def feat_npi_age_days_at_first_claim(providers: pd.DataFrame) -> pd.Series:
    """
    Absolute age of the NPI (in days since NPPES registration) at the time of
    the provider's first claim. Complements days_to_first_claim by anchoring
    to the NPI issuance date rather than the dataset window.
    """
    days = (
        providers["first_billing_month"] - providers["npi_registration_date"]
    ).dt.days.clip(lower=0)
    return days.rename("npi_age_days_at_first_claim")


def feat_n_distinct_hcpcs(providers: pd.DataFrame) -> pd.Series:
    return providers["n_distinct_hcpcs"].rename("n_distinct_hcpcs")


def feat_hcpcs_concentration(monthly: pd.DataFrame) -> pd.Series:
    """
    HHI of HCPCS codes weighted by total_claims per code.
    Score of 1.0 means every claim was for a single code — a hallmark of
    single-code phantom billing.
    """
    hcpcs_totals = (
        monthly.groupby(["npi", "hcpcs_code"])["total_claims"]
        .sum()
        .reset_index()
    )

    def _hhi(s: pd.Series) -> float:
        shares = s / s.sum() if s.sum() > 0 else s
        return float((shares ** 2).sum())

    return (
        hcpcs_totals.groupby("npi")["total_claims"]
        .apply(_hhi)
        .rename("hcpcs_concentration")
    )


def feat_billing_on_deactivated_npi(
    monthly: pd.DataFrame, providers: pd.DataFrame
) -> pd.Series:
    """
    Fraction of total claims submitted in months on or after the NPI
    deactivation date. Any non-zero value is a hard NPI-theft / identity
    fraud signal.
    """
    deact = providers["npi_deactivation_date"]

    # Vectorised path: join deactivation date onto monthly rows then aggregate
    monthly_aug = monthly.join(deact.rename("deact_date"), on="npi")
    monthly_aug["after_deact"] = (
        monthly_aug["month_dt"] >= monthly_aug["deact_date"]
    ) & monthly_aug["deact_date"].notna()

    total_by_npi = monthly_aug.groupby("npi")["total_claims"].sum()
    after_by_npi = (
        monthly_aug[monthly_aug["after_deact"]]
        .groupby("npi")["total_claims"]
        .sum()
        .reindex(total_by_npi.index, fill_value=0)
    )

    ratio = after_by_npi / total_by_npi.replace(0, np.nan)
    return ratio.fillna(0.0).rename("billing_on_deactivated_npi")


def feat_state_mismatch(providers: pd.DataFrame) -> pd.Series:
    """
    Placeholder — geographic state comparison requires a service-level state
    column not present in the CMS aggregate spending data. Returns NaN for
    all providers; populate if a claims extract with service_state is joined.
    """
    return pd.Series(np.nan, index=providers.index, name="state_mismatch")


def feat_pct_hcpcs_outside_taxonomy(
    monthly: pd.DataFrame,
    providers: pd.DataFrame,
    taxonomy_map: dict[str, set[str]] | None,
) -> pd.Series:
    """
    Fraction of a provider's billed HCPCS codes (weighted by claims) that fall
    outside the expected set for their taxonomy. Requires taxonomy_map.
    """
    if taxonomy_map is None:
        npis = monthly["npi"].unique()
        return pd.Series(np.nan, index=npis, name="pct_hcpcs_outside_taxonomy")

    hcpcs_totals = (
        monthly.groupby(["npi", "hcpcs_code"])["total_claims"]
        .sum()
        .reset_index()
    )

    result = {}
    for npi, grp in hcpcs_totals.groupby("npi"):
        taxonomy = providers["taxonomy_code"].get(npi)
        if not taxonomy or taxonomy not in taxonomy_map:
            result[npi] = np.nan
            continue
        allowed     = taxonomy_map[taxonomy]
        total       = grp["total_claims"].sum()
        outside     = grp.loc[~grp["hcpcs_code"].isin(allowed), "total_claims"].sum()
        result[npi] = float(outside / total) if total > 0 else 0.0

    return pd.Series(result, name="pct_hcpcs_outside_taxonomy")


# ---------------------------------------------------------------------------
# Temporal features (T1–T7)
# All computed per (npi, year). Joined onto the static feature table by the
# caller after selecting the target year.
# ---------------------------------------------------------------------------

_EPS = 1.0          # dollar/claim floor so ln() and ratios survive zeros
_MIN_ACTIVE  = 6    # months with activity required for volatility features
_MIN_ACTIVE3 = 3    # minimum for peak_to_median_paid
_MIN_ONSET   = 4    # minimum active months for onset_ramp_slope
_MIN_PEERS   = 20   # peer-group size floor for T7 benchmark


def _build_monthly_vectors(monthly: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate raw monthly data to (npi, year, month_num) and build
    12-slot zero-filled paid/claims arrays stored as one row per (npi, year).

    Returns a DataFrame indexed by (npi, year) with columns:
      paid_vec     list[float]  length-12, zero-filled, Jan=index 0
      claims_vec   list[float]  same
      active_months int         count of months with any positive paid
      first_month  int          calendar month of first active month in year
    """
    m = monthly.copy()
    m["month_dt"]  = pd.to_datetime(m["service_month"], format="%Y-%m", errors="coerce")
    m["year"]      = m["month_dt"].dt.year
    m["month_num"] = m["month_dt"].dt.month

    agg = (
        m.groupby(["npi", "year", "month_num"])
        .agg(paid=("total_paid_amount", "sum"),
             claims=("total_claims", "sum"))
        .reset_index()
    )

    records = []
    for (npi, year), grp in agg.groupby(["npi", "year"]):
        paid_vec   = [0.0] * 12
        claims_vec = [0.0] * 12
        for _, row in grp.iterrows():
            idx = int(row["month_num"]) - 1
            paid_vec[idx]   = float(row["paid"])
            claims_vec[idx] = float(row["claims"])
        active = sum(1 for p in paid_vec if p > 0)
        first_active = next((i + 1 for i, p in enumerate(paid_vec) if p > 0), None)
        records.append({
            "npi": npi, "year": year,
            "paid_vec": paid_vec, "claims_vec": claims_vec,
            "active_months": active, "first_active_month": first_active,
        })

    return pd.DataFrame(records).set_index(["npi", "year"])


def _provider_temporal_meta(monthly: pd.DataFrame) -> pd.DataFrame:
    """Return per-NPI first_obs_month (Timestamp) and left_censored flag."""
    m = monthly.copy()
    m["month_dt"] = pd.to_datetime(m["service_month"], format="%Y-%m", errors="coerce")
    first_obs    = m.groupby("npi")["month_dt"].min()
    dataset_start = first_obs.min()
    return pd.DataFrame({
        "first_obs_month": first_obs,
        "left_censored":   (first_obs == dataset_start),
    })


def feat_T1_mom_paid_growth_volatility(vecs: pd.DataFrame) -> pd.Series:
    """
    T1: std of month-on-month log differences of paid within each provider-year.
    g[m] = ln(paid[m] + EPS) − ln(paid[m-1] + EPS),  m = 2..12
    Guard: active_months >= 6.
    """
    results = {}
    for (npi, year), row in vecs.iterrows():
        if row["active_months"] < _MIN_ACTIVE:
            results[(npi, year)] = np.nan
            continue
        log_paid = np.log(np.array(row["paid_vec"]) + _EPS)
        g = np.diff(log_paid)
        results[(npi, year)] = float(np.std(g, ddof=1)) if len(g) > 1 else np.nan
    return pd.Series(results, name="mom_paid_growth_volatility")


def feat_T2_cv_monthly_paid(vecs: pd.DataFrame) -> pd.Series:
    """
    T2: std(paid[1..12]) / (mean(paid[1..12]) + EPS) — scale-invariant erraticness.
    Uses the full 12-slot zero-filled vector.
    Guard: active_months >= 6.
    """
    results = {}
    for (npi, year), row in vecs.iterrows():
        if row["active_months"] < _MIN_ACTIVE:
            results[(npi, year)] = np.nan
            continue
        p = np.array(row["paid_vec"], dtype=float)
        results[(npi, year)] = float(np.std(p, ddof=1) / (np.mean(p) + _EPS))
    return pd.Series(results, name="cv_monthly_paid")


def feat_T3_peak_to_median_paid(vecs: pd.DataFrame) -> pd.Series:
    """
    T3: max(paid over active months) / (median(paid over active months) + EPS).
    Median is over active months only so suppressed gaps don't inflate the ratio.
    Guard: active_months >= 3. Winsorized at 99th percentile by caller.
    """
    results = {}
    for (npi, year), row in vecs.iterrows():
        if row["active_months"] < _MIN_ACTIVE3:
            results[(npi, year)] = np.nan
            continue
        active_paid = [p for p in row["paid_vec"] if p > 0]
        peak   = max(active_paid)
        median = float(np.median(active_paid))
        results[(npi, year)] = peak / (median + _EPS)
    s = pd.Series(results, name="peak_to_median_paid")
    p99 = s.quantile(0.99)
    return s.clip(upper=p99)


def feat_T4_onset_ramp_slope(
    vecs: pd.DataFrame,
    meta: pd.DataFrame,
) -> pd.Series:
    """
    T4: OLS slope of ln(paid + EPS) on month index over first 6 active months
    at onset. Computed ONLY for first observed year and NOT for left-censored
    providers (no visible start).
    Guard: left_censored → NaN; fewer than 4 active months → NaN.
    """
    results = {}
    for (npi, year), row in vecs.iterrows():
        provider_meta = meta.loc[npi] if npi in meta.index else None
        if provider_meta is None:
            results[(npi, year)] = np.nan
            continue
        first_year = provider_meta["first_obs_month"].year
        if year != first_year or provider_meta["left_censored"]:
            results[(npi, year)] = np.nan
            continue
        # Take first 6 active months starting from first_active_month
        paid_vec = row["paid_vec"]
        start    = (row["first_active_month"] or 1) - 1
        active_slice = [(i, paid_vec[i]) for i in range(start, 12) if paid_vec[i] > 0][:6]
        if len(active_slice) < _MIN_ONSET:
            results[(npi, year)] = np.nan
            continue
        t    = np.array([s[0] for s in active_slice], dtype=float)
        logp = np.array([np.log(s[1] + _EPS) for s in active_slice])
        slope, *_ = linregress(t, logp)
        results[(npi, year)] = float(slope)
    return pd.Series(results, name="onset_ramp_slope")


def feat_T5_post_peak_dropoff(vecs: pd.DataFrame) -> pd.Series:
    """
    T5: 1 − (mean of last 3 calendar months / (peak + EPS)).
    ~0 = steady billing; ~1 = collapsed after a peak.
    Guard: active_months >= 6.
    """
    results = {}
    for (npi, year), row in vecs.iterrows():
        if row["active_months"] < _MIN_ACTIVE:
            results[(npi, year)] = np.nan
            continue
        p    = np.array(row["paid_vec"], dtype=float)
        peak = float(p.max())
        last3_mean = float(p[-3:].mean())
        results[(npi, year)] = 1.0 - last3_mean / (peak + _EPS)
    return pd.Series(results, name="post_peak_dropoff")


def feat_T6_new_hcpcs_fraction(
    monthly: pd.DataFrame,
    meta: pd.DataFrame,
) -> pd.Series:
    """
    T6: fraction of HCPCS codes billed in year y that were never billed before y.
    Guard: first observed year or left_censored → NaN (no prior baseline).
    """
    m = monthly.copy()
    m["month_dt"] = pd.to_datetime(m["service_month"], format="%Y-%m", errors="coerce")
    m["year"]     = m["month_dt"].dt.year

    # All codes per NPI per year
    codes_by_year = (
        m.groupby(["npi", "year"])["hcpcs_code"]
        .apply(set)
        .reset_index()
        .rename(columns={"hcpcs_code": "codes"})
        .sort_values(["npi", "year"])
    )

    results = {}
    for npi, grp in codes_by_year.groupby("npi"):
        grp = grp.sort_values("year")
        prov_meta = meta.loc[npi] if npi in meta.index else None
        prior_codes: set = set()
        for _, row in grp.iterrows():
            year  = row["year"]
            codes = row["codes"]
            if prov_meta is None or year == prov_meta["first_obs_month"].year or prov_meta["left_censored"]:
                results[(npi, year)] = np.nan
            else:
                new = codes - prior_codes
                results[(npi, year)] = len(new) / len(codes) if codes else np.nan
            prior_codes |= codes
    return pd.Series(results, name="new_hcpcs_fraction")


def feat_T7_excess_yoy_growth(
    provider_annual: pd.DataFrame,
    providers: pd.DataFrame,
) -> pd.Series:
    """
    T7: provider log YoY growth minus specialty-median log YoY growth.
    provider_annual must have columns: npi, service_year, total_paid_amount.
    Requires taxonomy_code in providers (NPPES join).
    Guard: prior year absent → NaN; fewer than MIN_PEERS specialty peers → NaN.
    """
    ann = provider_annual.copy()
    ann["year"] = ann["service_year"].astype(int)

    # Pivot to (npi) × (year) for easy diff
    paid_wide = ann.pivot_table(index="npi", columns="year",
                                values="total_paid_amount", aggfunc="sum")

    # Log-growth per provider per year
    years = sorted(paid_wide.columns)
    log_growth = pd.DataFrame(index=paid_wide.index)
    for i in range(1, len(years)):
        y, y_prev = years[i], years[i - 1]
        prev = paid_wide[y_prev].replace(0, np.nan)
        curr = paid_wide[y].replace(0, np.nan)
        log_growth[y] = np.log(curr + _EPS) - np.log(prev + _EPS)

    # Join taxonomy for specialty benchmarking
    taxonomy = providers["taxonomy_code"].reindex(log_growth.index)
    log_growth["_tax"] = taxonomy

    results = {}
    for year_col in log_growth.columns:
        if year_col == "_tax":
            continue
        col = log_growth[[year_col, "_tax"]].dropna(subset=[year_col])
        # Specialty peer median (require MIN_PEERS)
        peer_med = (
            col.groupby("_tax")[year_col]
            .filter(lambda s: len(s) >= _MIN_PEERS)
        )
        if peer_med.empty:
            for npi in col.index:
                results[(npi, year_col)] = np.nan
            continue
        spec_median = col.groupby("_tax")[year_col].median()
        for npi, row in col.iterrows():
            tax = row["_tax"]
            if pd.isna(tax) or tax not in spec_median.index:
                results[(npi, year_col)] = np.nan
            else:
                n_peers = (col["_tax"] == tax).sum()
                if n_peers < _MIN_PEERS:
                    results[(npi, year_col)] = np.nan
                else:
                    results[(npi, year_col)] = float(row[year_col] - spec_median[tax])

    s = pd.Series(results, name="excess_yoy_growth")
    p1, p99 = s.quantile([0.01, 0.99])
    return s.clip(p1, p99)


def build_temporal_features(
    monthly: pd.DataFrame,
    provider_annual: pd.DataFrame,
    providers: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute T1–T7 and return a DataFrame indexed by (npi, year).
    Caller should select the target year before joining onto the static features.
    """
    print("  Building monthly vectors …")
    vecs = _build_monthly_vectors(monthly)
    meta = _provider_temporal_meta(monthly)

    print("  T1 — mom_paid_growth_volatility …")
    t1 = feat_T1_mom_paid_growth_volatility(vecs)

    print("  T2 — cv_monthly_paid …")
    t2 = feat_T2_cv_monthly_paid(vecs)

    print("  T3 — peak_to_median_paid …")
    t3 = feat_T3_peak_to_median_paid(vecs)

    print("  T4 — onset_ramp_slope …")
    t4 = feat_T4_onset_ramp_slope(vecs, meta)

    print("  T5 — post_peak_dropoff …")
    t5 = feat_T5_post_peak_dropoff(vecs)

    print("  T6 — new_hcpcs_fraction …")
    t6 = feat_T6_new_hcpcs_fraction(monthly, meta)

    print("  T7 — excess_yoy_growth …")
    t7 = feat_T7_excess_yoy_growth(provider_annual, providers)

    return pd.DataFrame({
        "mom_paid_growth_volatility": t1,
        "cv_monthly_paid":            t2,
        "peak_to_median_paid":        t3,
        "onset_ramp_slope":           t4,
        "post_peak_dropoff":          t5,
        "new_hcpcs_fraction":         t6,
        "excess_yoy_growth":          t7,
    })


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_fraud_features(
    monthly: pd.DataFrame,
    providers: pd.DataFrame,
    taxonomy_map: dict[str, set[str]] | None = None,
    provider_annual: pd.DataFrame | None = None,
    target_year: int | None = None,
) -> pd.DataFrame:
    """
    Build the full feature table — static features (1–9) plus temporal
    features T1–T7 if provider_annual is supplied.

    target_year: which year's temporal features to join onto the static
    table. Defaults to the most recent year present in provider_annual.
    """
    avg_paid   = feat_avg_paid_per_claim(providers)
    avg_claims = feat_avg_claims_per_beneficiary(providers)

    features = pd.DataFrame(index=providers.index)
    features.index.name = "npi"

    # Static features (unchanged)
    features["avg_claims_per_beneficiary"]  = avg_claims
    features["avg_paid_per_claim"]          = avg_paid
    features["paid_vs_peer_ratio"]          = feat_paid_vs_peer_ratio(avg_paid, providers)
    features["claims_vs_peer_ratio"]        = feat_claims_vs_peer_ratio(avg_claims, providers)
    features["billing_cv"]                  = feat_billing_cv(monthly).reindex(features.index)
    features["days_to_first_claim"]         = feat_days_to_first_claim(providers)
    features["npi_age_days_at_first_claim"] = feat_npi_age_days_at_first_claim(providers)
    features["n_distinct_hcpcs"]            = feat_n_distinct_hcpcs(providers)
    features["hcpcs_concentration"]         = feat_hcpcs_concentration(monthly).reindex(features.index)
    features["billing_on_deactivated_npi"]  = feat_billing_on_deactivated_npi(monthly, providers)
    features["state_mismatch"]              = feat_state_mismatch(providers)
    features["pct_hcpcs_outside_taxonomy"]  = feat_pct_hcpcs_outside_taxonomy(
                                                 monthly, providers, taxonomy_map
                                             ).reindex(features.index)

    # Temporal features T1–T7
    if provider_annual is not None:
        temporal = build_temporal_features(monthly, provider_annual, providers)

        # Select target year and join — default to most recent year
        if target_year is None:
            target_year = int(temporal.index.get_level_values("year").max())

        year_slice = (
            temporal.xs(target_year, level="year")
            if target_year in temporal.index.get_level_values("year")
            else pd.DataFrame(index=features.index, columns=temporal.columns)
        )
        for col in year_slice.columns:
            features[col] = year_slice[col].reindex(features.index)

    # Carry taxonomy through for peer-group scoring in analyze_anomalies.py
    features["taxonomy_code"] = providers["taxonomy_code"]

    return features


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_features(features: pd.DataFrame, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(path)
    print(f"Saved {len(features):,} provider rows → {path}")
    nulls = features.isnull().sum()
    nulls = nulls[nulls > 0]
    if not nulls.empty:
        print("  Columns with nulls:")
        for col, n in nulls.items():
            print(f"    {col}: {n}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build fraud-signal features from cleaned Medicaid provider data."
    )
    parser.add_argument("--monthly",      required=True, help="provider_monthly.parquet from clean_data.py")
    parser.add_argument("--providers",    required=True, help="providers_clean.csv from clean_data.py")
    parser.add_argument("--annual",       default=None,  help="provider_annual.parquet for T1–T7 temporal features")
    parser.add_argument("--target-year",  default=None,  type=int,
                        help="Service year to extract temporal features for (default: most recent year)")
    parser.add_argument("--taxonomy-map", default=None,  help="CSV mapping taxonomy_code → hcpcs_code")
    parser.add_argument("--output",       default="data/processed/fraud_features.csv")
    args = parser.parse_args()

    print(f"Loading monthly data from {args.monthly} …")
    monthly = load_monthly(args.monthly)
    print(f"  {len(monthly):,} rows, {monthly['npi'].nunique():,} providers")

    print(f"Loading providers from {args.providers} …")
    providers = load_providers(args.providers)
    print(f"  {len(providers):,} providers")

    provider_annual = None
    if args.annual:
        print(f"Loading annual aggregates from {args.annual} …")
        p = Path(args.annual)
        provider_annual = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
        print(f"  {len(provider_annual):,} provider-year rows")

    taxonomy_map = None
    if args.taxonomy_map:
        print(f"Loading taxonomy map from {args.taxonomy_map} …")
        taxonomy_map = load_taxonomy_map(args.taxonomy_map)
        print(f"  {len(taxonomy_map):,} taxonomy codes")

    print("Computing fraud features …")
    features = build_fraud_features(
        monthly, providers, taxonomy_map,
        provider_annual=provider_annual,
        target_year=args.target_year,
    )

    save_features(features, args.output)


if __name__ == "__main__":
    main()
