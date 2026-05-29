"""
build_features.py

Reads the output files from clean_data.py and produces a fraud-signal feature
table at the (npi, year) grain — one row per billing NPI per service year.

Inputs
------
--monthly   : data/processed/provider_monthly.parquet
                cols: npi, hcpcs_code, service_month, total_claims,
                      total_beneficiaries, total_paid_amount
--providers : data/processed/providers_clean.csv
                cols: npi, first_billing_month, last_billing_month,
                      taxonomy_code, practice_state, npi_registration_date,
                      npi_deactivation_date, in_leie, excl_date, reinstate_date
--annual    : data/processed/provider_annual.parquet (for temporal T1–T7)

Static features (per provider-year)
-----------------------------------
avg_claims_per_beneficiary   Σclaims / Σbeneficiaries
avg_paid_per_claim           Σpaid / Σclaims
paid_vs_peer_ratio           per-HCPCS paid/claims vs same-specialty same-code
                             median (>=20 peers), claims-weighted geo-mean
claims_vs_peer_ratio         same machinery on claims/beneficiaries
n_distinct_hcpcs_vs_peer     distinct HCPCS / specialty peer-median count
hcpcs_concentration          Herfindahl index of claim shares (Σ share²)
billing_on_deactivated_npi   binary: last claim month > NPI deactivation date
npi_age_days_at_first_claim  (first-ever claim month − enumeration date), >=0

Temporal features T1–T7 (per provider-year, from the monthly series)
--------------------------------------------------------------------
mom_paid_growth_volatility, cv_monthly_paid, peak_to_median_paid,
onset_ramp_slope, post_peak_dropoff, new_hcpcs_fraction, excess_yoy_growth

Non-model columns
-----------------
is_excluded   validation label (LEIE), never a model input
total_paid    kept for triage sorting only
taxonomy_code specialty, for reporting

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
        --annual    data/processed/provider_annual.parquet \\
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
    date_cols = ["npi_registration_date", "npi_deactivation_date",
                 "first_billing_month", "last_billing_month",
                 "excl_date", "reinstate_date"]
    head = pd.read_csv(path, nrows=0)
    present_dates = [c for c in date_cols if c in head.columns]
    df = pd.read_csv(path, dtype={"npi": str}, parse_dates=present_dates)

    required = {
        "npi", "first_billing_month", "last_billing_month",
        "taxonomy_code", "npi_registration_date",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Providers file missing columns: {missing}")

    for c in date_cols:
        if c not in df.columns:
            df[c] = pd.NaT

    df["npi"] = df["npi"].str.strip()
    return df.set_index("npi")


# ---------------------------------------------------------------------------
# Static features — all computed at the (npi, year) grain per the spec
# ("one row per billing NPI per service year").
# ---------------------------------------------------------------------------

_MIN_PEERS = 20   # peer-group size floor for any peer-median benchmark


def _hcpcs_year_cells(monthly: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse the monthly table to one row per (npi, year, hcpcs_code) with
    summed claims / beneficiaries / paid. This is the building block for the
    per-HCPCS peer ratios and the within-year concentration index.
    """
    m = monthly[["npi", "hcpcs_code", "total_claims",
                 "total_beneficiaries", "total_paid_amount"]].copy()
    m["year"] = monthly["month_dt"].dt.year
    cells = (
        m.groupby(["npi", "year", "hcpcs_code"], dropna=True)
        .agg(claims=("total_claims", "sum"),
             beneficiaries=("total_beneficiaries", "sum"),
             paid=("total_paid_amount", "sum"))
        .reset_index()
    )
    return cells


def _winsorize(s: pd.Series, lo: float = 0.01, hi: float = 0.99) -> pd.Series:
    if s.notna().sum() == 0:
        return s
    ql, qh = s.quantile([lo, hi])
    return s.clip(ql, qh)


def feat_avg_claims_per_beneficiary(cells: pd.DataFrame) -> pd.Series:
    """Σtotal_claims / Σtotal_beneficiaries over the same (hcpcs × month) cells, per provider-year."""
    g = cells.groupby(["npi", "year"]).agg(c=("claims", "sum"), b=("beneficiaries", "sum"))
    return (g["c"] / g["b"].replace(0, np.nan)).rename("avg_claims_per_beneficiary")


def feat_avg_paid_per_claim(cells: pd.DataFrame) -> pd.Series:
    """Σtotal_paid / Σtotal_claims, per provider-year."""
    g = cells.groupby(["npi", "year"]).agg(p=("paid", "sum"), c=("claims", "sum"))
    return (g["p"] / g["c"].replace(0, np.nan)).rename("avg_paid_per_claim")


def _peer_code_ratio(
    cells: pd.DataFrame,
    taxonomy: pd.Series,
    numer: str,
    denom: str,
    name: str,
    min_peers: int = _MIN_PEERS,
) -> pd.Series:
    """
    Per-HCPCS rate (numer/denom) divided by the same-specialty same-code median
    (requiring >= min_peers distinct providers for that taxonomy × year × code),
    then aggregated to provider-year as a claims-weighted geometric mean.
    Winsorized at the 1st/99th percentile. Implements paid_vs_peer_ratio
    (paid/claims) and claims_vs_peer_ratio (claims/beneficiaries).
    """
    df = cells.copy()
    df["taxonomy"] = df["npi"].map(taxonomy)
    df["rate"] = df[numer] / df[denom].replace(0, np.nan)
    df = df[df["rate"].notna() & (df["rate"] > 0) & df["taxonomy"].notna()]

    grp = df.groupby(["taxonomy", "year", "hcpcs_code"])
    df["peer_median"] = grp["rate"].transform("median")
    df["n_peers"]     = grp["npi"].transform("nunique")

    valid = (df["n_peers"] >= min_peers) & (df["peer_median"] > 0)
    df = df[valid].copy()
    df["ratio"] = df["rate"] / df["peer_median"]
    df = df[df["ratio"] > 0]

    # Claims-weighted geometric mean of the per-code ratios within provider-year.
    df["wln"] = df["claims"] * np.log(df["ratio"])
    agg = df.groupby(["npi", "year"]).agg(wln=("wln", "sum"), w=("claims", "sum"))
    gm = np.exp(agg["wln"] / agg["w"].replace(0, np.nan))
    return _winsorize(gm).rename(name)


def feat_paid_vs_peer_ratio(cells: pd.DataFrame, taxonomy: pd.Series) -> pd.Series:
    return _peer_code_ratio(cells, taxonomy, "paid", "claims", "paid_vs_peer_ratio")


def feat_claims_vs_peer_ratio(cells: pd.DataFrame, taxonomy: pd.Series) -> pd.Series:
    return _peer_code_ratio(cells, taxonomy, "claims", "beneficiaries", "claims_vs_peer_ratio")


def feat_npi_age_days_at_first_claim(providers: pd.DataFrame) -> pd.Series:
    """
    (first-ever claim month in data − NPPES enumeration date) in days, negatives
    clamped to 0. Anchored to the provider's first-ever claim, so it is constant
    across that provider's service years.
    """
    days = (
        providers["first_billing_month"] - providers["npi_registration_date"]
    ).dt.days.clip(lower=0)
    return days.rename("npi_age_days_at_first_claim")


def feat_n_distinct_hcpcs_vs_peer(cells: pd.DataFrame, taxonomy: pd.Series) -> pd.Series:
    """
    Distinct HCPCS count for the provider-year divided by the same-specialty
    median distinct-count for that year. Requires >= MIN_PEERS providers in the
    taxonomy × year group, else NaN.
    """
    n_codes = (
        cells.groupby(["npi", "year"])["hcpcs_code"].nunique().rename("n_distinct_hcpcs")
    )
    df = n_codes.reset_index()
    df["taxonomy"] = df["npi"].map(taxonomy)
    grp = df.groupby(["taxonomy", "year"])
    df["peer_median"] = grp["n_distinct_hcpcs"].transform("median")
    df["n_peers"]     = grp["npi"].transform("nunique")
    ratio = np.where(
        (df["n_peers"] >= _MIN_PEERS) & (df["peer_median"] > 0),
        df["n_distinct_hcpcs"] / df["peer_median"],
        np.nan,
    )
    out = pd.Series(ratio, index=pd.MultiIndex.from_frame(df[["npi", "year"]]))
    return out.rename("n_distinct_hcpcs_vs_peer")


def feat_hcpcs_concentration(cells: pd.DataFrame) -> pd.Series:
    """
    Herfindahl index of claim shares across codes within a provider-year
    (Σ share²). 1.0 means every claim was for a single code — a hallmark of
    single-code phantom billing.
    """
    def _hhi(s: pd.Series) -> float:
        total = s.sum()
        if total <= 0:
            return np.nan
        shares = s / total
        return float((shares ** 2).sum())

    return (
        cells.groupby(["npi", "year"])["claims"].apply(_hhi).rename("hcpcs_concentration")
    )


def feat_billing_on_deactivated_npi(
    providers: pd.DataFrame, index: pd.MultiIndex
) -> pd.Series:
    """
    Binary flag: 1 if the NPI has an NPPES deactivation date AND the provider's
    last claim month is after that date. Provider-level signal broadcast to each
    of the provider's service-years.
    """
    deact = providers["npi_deactivation_date"]
    last  = providers["last_billing_month"]
    flag = (deact.notna() & (last > deact)).astype(int)
    npi_level = index.get_level_values("npi")
    return pd.Series(flag.reindex(npi_level).fillna(0).astype(int).values,
                     index=index, name="billing_on_deactivated_npi")


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
    Aggregate raw monthly data to (npi, year, month_num) and pivot into
    12-column wide matrices — one row per (npi, year).  Fully vectorised;
    no Python loops over providers.

    Returns a DataFrame indexed by (npi, year) with columns:
      p1..p12       float  zero-filled monthly paid (Jan=p1)
      c1..c12       float  zero-filled monthly claims
      active_months int    months with paid > 0
      first_active_month int  1-based index of first active month in year
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

    # Pivot: rows = (npi, year), cols = month_num 1..12, fill missing with 0
    paid_wide = (
        agg.pivot_table(index=["npi", "year"], columns="month_num",
                        values="paid", aggfunc="sum", fill_value=0.0)
        .reindex(columns=range(1, 13), fill_value=0.0)
    )
    paid_wide.columns = [f"p{c}" for c in paid_wide.columns]

    claims_wide = (
        agg.pivot_table(index=["npi", "year"], columns="month_num",
                        values="claims", aggfunc="sum", fill_value=0.0)
        .reindex(columns=range(1, 13), fill_value=0.0)
    )
    claims_wide.columns = [f"c{c}" for c in claims_wide.columns]

    vecs = paid_wide.join(claims_wide)

    paid_arr = paid_wide.values  # (N, 12)
    vecs["active_months"]      = (paid_arr > 0).sum(axis=1)
    # first active month: argmax on boolean mask gives index of first True
    has_any = (paid_arr > 0)
    vecs["first_active_month"] = np.where(
        has_any.any(axis=1),
        has_any.argmax(axis=1) + 1,   # 1-based
        np.nan,
    )
    return vecs


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
    P = vecs[[f"p{i}" for i in range(1, 13)]].values  # (N, 12)
    log_P = np.log(P + _EPS)
    g = np.diff(log_P, axis=1)                         # (N, 11)
    std_g = np.std(g, axis=1, ddof=1)
    mask = vecs["active_months"].values < _MIN_ACTIVE
    std_g[mask] = np.nan
    return pd.Series(std_g, index=vecs.index, name="mom_paid_growth_volatility")


def feat_T2_cv_monthly_paid(vecs: pd.DataFrame) -> pd.Series:
    """
    T2: std(paid[1..12]) / (mean(paid[1..12]) + EPS) — scale-invariant erraticness.
    Uses the full 12-slot zero-filled vector.
    Guard: active_months >= 6.
    """
    P    = vecs[[f"p{i}" for i in range(1, 13)]].values
    cv   = np.std(P, axis=1, ddof=1) / (np.mean(P, axis=1) + _EPS)
    mask = vecs["active_months"].values < _MIN_ACTIVE
    cv[mask] = np.nan
    return pd.Series(cv, index=vecs.index, name="cv_monthly_paid")


def feat_T3_peak_to_median_paid(vecs: pd.DataFrame) -> pd.Series:
    """
    T3: max(paid over active months) / (median(paid over active months) + EPS).
    Median is over active months only so suppressed gaps don't inflate the ratio.
    Guard: active_months >= 3. Winsorized at 99th percentile.
    """
    P = vecs[[f"p{i}" for i in range(1, 13)]].values.copy().astype(float)
    # Mask inactive months with NaN for median calculation
    P_active = np.where(P > 0, P, np.nan)
    peak      = np.nanmax(P_active, axis=1)
    median    = np.nanmedian(P_active, axis=1)
    ratio     = peak / (median + _EPS)
    mask      = vecs["active_months"].values < _MIN_ACTIVE3
    ratio[mask] = np.nan
    s   = pd.Series(ratio, index=vecs.index, name="peak_to_median_paid")
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
    T4 is inherently per-provider-onset so a small Python loop is unavoidable;
    it applies only to first-year, non-censored rows (a small fraction).
    """
    # Pre-filter to only onset rows — avoids iterating over 7M rows
    onset_idx = []
    for npi, pm in meta.iterrows():
        if pm["left_censored"]:
            continue
        first_year = pm["first_obs_month"].year
        key = (npi, first_year)
        if key in vecs.index:
            onset_idx.append(key)

    results = pd.Series(np.nan, index=vecs.index, name="onset_ramp_slope")
    if not onset_idx:
        return results

    onset_vecs = vecs.loc[onset_idx]
    for (npi, year), row in onset_vecs.iterrows():
        start = int(row["first_active_month"] or 1) - 1
        paid_cols = [f"p{i+1}" for i in range(start, 12)]
        active_pts = [(i + start, row[f"p{i+start+1}"])
                      for i in range(12 - start)
                      if row[f"p{i+start+1}"] > 0][:6]
        if len(active_pts) < _MIN_ONSET:
            continue
        t    = np.array([p[0] for p in active_pts], dtype=float)
        logp = np.array([np.log(p[1] + _EPS) for p in active_pts])
        slope, *_ = linregress(t, logp)
        results.loc[(npi, year)] = float(slope)
    return results


def feat_T5_post_peak_dropoff(vecs: pd.DataFrame) -> pd.Series:
    """
    T5: 1 − (mean of last 3 calendar months / (peak + EPS)).
    ~0 = steady billing; ~1 = collapsed after a peak.
    Guard: active_months >= 6.
    """
    P          = vecs[[f"p{i}" for i in range(1, 13)]].values
    peak       = P.max(axis=1)
    last3_mean = P[:, -3:].mean(axis=1)
    dropoff    = 1.0 - last3_mean / (peak + _EPS)
    mask       = vecs["active_months"].values < _MIN_ACTIVE
    dropoff[mask] = np.nan
    return pd.Series(dropoff, index=vecs.index, name="post_peak_dropoff")


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

    temporal = pd.DataFrame({
        "mom_paid_growth_volatility": t1,
        "cv_monthly_paid":            t2,
        "peak_to_median_paid":        t3,
        "onset_ramp_slope":           t4,
        "post_peak_dropoff":          t5,
        "new_hcpcs_fraction":         t6,
        "excess_yoy_growth":          t7,
    })
    # T6/T7 build their index from dict-of-tuples, which drops the level names;
    # normalise so the (npi, year) join in build_fraud_features lines up.
    temporal.index = temporal.index.set_names(["npi", "year"])
    return temporal


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def label_is_excluded(providers: pd.DataFrame, index: pd.MultiIndex) -> pd.Series:
    """
    Validation label (kept separate, never a model input):
    1 if the NPI is in LEIE with EXCLDATE <= end of the service year AND
    (REINDATE is null OR REINDATE > end of the service year). Computed per
    (npi, year) because exclusion status depends on the service year.
    """
    idx_df = index.to_frame(index=False)
    eoy = pd.to_datetime(idx_df["year"].astype(int).astype(str) + "-12-31")
    excl = idx_df["npi"].map(providers["excl_date"]) if "excl_date" in providers else pd.Series(pd.NaT, index=idx_df.index)
    rein = idx_df["npi"].map(providers["reinstate_date"]) if "reinstate_date" in providers else pd.Series(pd.NaT, index=idx_df.index)
    cond = excl.notna() & (excl <= eoy) & (rein.isna() | (rein > eoy))
    return pd.Series(cond.astype(int).values, index=index, name="is_excluded")


def build_fraud_features(
    monthly: pd.DataFrame,
    providers: pd.DataFrame,
    provider_annual: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Build the full feature table at the (npi, year) grain — static features
    plus temporal features T1–T7 if provider_annual is supplied.

    The returned table carries three non-model columns:
      is_excluded   — validation label (never fed to the model)
      total_paid    — kept for triage sorting only
      taxonomy_code — specialty, for reporting / optional peer grouping
    """
    taxonomy = providers["taxonomy_code"]

    print("  Aggregating (npi, year, hcpcs) cells …")
    cells = _hcpcs_year_cells(monthly)

    # The (npi, year) universe is every provider-year with billing activity.
    base_index = pd.MultiIndex.from_frame(
        cells[["npi", "year"]].drop_duplicates().sort_values(["npi", "year"])
    )
    features = pd.DataFrame(index=base_index)

    print("  Static features (per provider-year) …")
    features["avg_claims_per_beneficiary"]  = feat_avg_claims_per_beneficiary(cells)
    features["avg_paid_per_claim"]          = feat_avg_paid_per_claim(cells)
    features["paid_vs_peer_ratio"]          = feat_paid_vs_peer_ratio(cells, taxonomy)
    features["claims_vs_peer_ratio"]        = feat_claims_vs_peer_ratio(cells, taxonomy)
    features["n_distinct_hcpcs_vs_peer"]    = feat_n_distinct_hcpcs_vs_peer(cells, taxonomy)
    features["hcpcs_concentration"]         = feat_hcpcs_concentration(cells)
    features["billing_on_deactivated_npi"]  = feat_billing_on_deactivated_npi(providers, base_index)

    # npi_age_days_at_first_claim is provider-level; broadcast to each year.
    age = feat_npi_age_days_at_first_claim(providers)
    features["npi_age_days_at_first_claim"] = age.reindex(
        base_index.get_level_values("npi")
    ).values

    # Temporal features T1–T7 (already at the (npi, year) grain)
    if provider_annual is not None:
        temporal = build_temporal_features(monthly, provider_annual, providers)
        features = features.join(temporal, how="left")

    # --- Non-model columns -------------------------------------------------
    # Label (kept separate, never modelled).
    features["is_excluded"] = label_is_excluded(providers, base_index)
    # total_paid for triage sorting only (NOT a model input).
    features["total_paid"] = (
        cells.groupby(["npi", "year"])["paid"].sum().reindex(base_index)
    )
    # Specialty for reporting.
    features["taxonomy_code"] = pd.Series(taxonomy).reindex(
        base_index.get_level_values("npi")
    ).values

    features.index.names = ["npi", "year"]
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
    parser.add_argument("--monthly",   required=True, help="provider_monthly.parquet from clean_data.py")
    parser.add_argument("--providers", required=True, help="providers_clean.csv from clean_data.py")
    parser.add_argument("--annual",    default=None,  help="provider_annual.parquet for T1–T7 temporal features")
    parser.add_argument("--output",    default="data/processed/fraud_features.csv")
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

    print("Computing fraud features …")
    features = build_fraud_features(monthly, providers, provider_annual=provider_annual)

    save_features(features, args.output)


if __name__ == "__main__":
    main()
