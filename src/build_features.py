"""
build_features.py

Reads the output files from clean_data.py and produces a fraud-signal feature
table at the (npi, year) grain — one row per billing NPI per service year.

Scale strategy
--------------
The monthly table is ~130M rows nationally, far too large for pandas. All
row-level aggregation and the per-HCPCS peer-median math run inside DuckDB
(reading the parquet directly, spilling to disk as needed). Only the
per-(npi, year) results (~2.4M rows) are ever materialised in pandas, where the
lighter numpy/temporal math happens.

Inputs
------
--monthly   : data/processed/provider_monthly.parquet
                cols: npi, hcpcs_code, service_month, total_claims,
                      total_beneficiaries, total_paid_amount
--providers : data/processed/providers_clean.csv
                cols: npi, first_billing_month, last_billing_month,
                      taxonomy_code, npi_registration_date,
                      npi_deactivation_date, in_leie, excl_date,
                      reinstate_date, left_censored
--annual    : data/processed/provider_annual.parquet (for temporal T7)

Static features (per provider-year)
-----------------------------------
avg_claims_per_beneficiary, avg_paid_per_claim, paid_vs_peer_ratio,
claims_vs_peer_ratio, n_distinct_hcpcs_vs_peer, hcpcs_concentration,
billing_on_deactivated_npi, npi_age_days_at_first_claim

Temporal features T1-T7 (per provider-year)
-------------------------------------------
mom_paid_growth_volatility, cv_monthly_paid, peak_to_median_paid,
onset_ramp_slope, post_peak_dropoff, new_hcpcs_fraction, excess_yoy_growth

Non-model columns
-----------------
is_excluded (validation label), total_paid (triage only), taxonomy_code

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

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import linregress


# ---------------------------------------------------------------------------
# Constants / guards
# ---------------------------------------------------------------------------

_EPS         = 1.0   # dollar/claim floor so ln() and ratios survive zeros
_MIN_ACTIVE  = 6     # months with activity required for volatility features
_MIN_ACTIVE3 = 3     # minimum for peak_to_median_paid
_MIN_ONSET   = 4     # minimum active months for onset_ramp_slope
_MIN_PEERS   = 20    # peer-group size floor for any peer-median benchmark


# ---------------------------------------------------------------------------
# Loading (only the small providers table comes into pandas)
# ---------------------------------------------------------------------------

def load_providers(path: str | Path) -> pd.DataFrame:
    date_cols = ["npi_registration_date", "npi_deactivation_date",
                 "first_billing_month", "last_billing_month",
                 "excl_date", "reinstate_date"]
    head = pd.read_csv(path, nrows=0)
    present_dates = [c for c in date_cols if c in head.columns]
    df = pd.read_csv(path, dtype={"npi": str}, parse_dates=present_dates)

    required = {"npi", "first_billing_month", "last_billing_month",
                "taxonomy_code", "npi_registration_date"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Providers file missing columns: {missing}")

    for c in date_cols:
        if c not in df.columns:
            df[c] = pd.NaT
    if "left_censored" not in df.columns:
        df["left_censored"] = 0

    df["npi"] = df["npi"].str.strip()
    return df.set_index("npi")


def _winsorize(s: pd.Series, lo: float = 0.01, hi: float = 0.99) -> pd.Series:
    if s.notna().sum() == 0:
        return s
    ql, qh = s.quantile([lo, hi])
    return s.clip(ql, qh)


# ---------------------------------------------------------------------------
# DuckDB staging — build the heavy intermediates once, on disk
# ---------------------------------------------------------------------------

def _connect(spill_dir: str = "/tmp/duckdb_spill", mem_limit: str = "8GB") -> duckdb.DuckDBPyConnection:
    Path(spill_dir).mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{spill_dir}'")
    con.execute(f"SET memory_limit='{mem_limit}'")
    return con


def _stage_tables(con: duckdb.DuckDBPyConnection, monthly_path: str,
                  providers: pd.DataFrame) -> None:
    """Create the two heavy intermediates (cells, monthly-vectors) in DuckDB."""
    # Register the small providers table (npi → taxonomy) for peer joins.
    prov = providers.reset_index()[["npi", "taxonomy_code"]].copy()
    prov["npi"] = prov["npi"].astype(str)
    con.register("prov_df", prov)
    con.execute("CREATE TEMP TABLE prov AS SELECT npi, taxonomy_code FROM prov_df")

    # cells: one row per (npi, year, hcpcs) — building block for peer ratios,
    # concentration and the T6 first-seen logic.
    con.execute(f"""
        CREATE TEMP TABLE cells AS
        SELECT
            npi,
            TRY_CAST(LEFT(service_month, 4) AS INTEGER) AS year,
            hcpcs_code,
            SUM(total_claims)        AS claims,
            SUM(total_beneficiaries) AS beneficiaries,
            SUM(total_paid_amount)   AS paid
        FROM read_parquet('{monthly_path}')
        WHERE TRY_CAST(LEFT(service_month, 4) AS INTEGER) IS NOT NULL
        GROUP BY npi, year, hcpcs_code
    """)

    # monthly vectors: one row per (npi, year, month_num) — feeds T1-T5.
    con.execute(f"""
        CREATE TEMP TABLE mv AS
        SELECT
            npi,
            TRY_CAST(LEFT(service_month, 4) AS INTEGER)    AS year,
            TRY_CAST(SUBSTR(service_month, 6, 2) AS INTEGER) AS month_num,
            SUM(total_paid_amount) AS paid,
            SUM(total_claims)      AS claims
        FROM read_parquet('{monthly_path}')
        WHERE TRY_CAST(LEFT(service_month, 4) AS INTEGER) IS NOT NULL
          AND TRY_CAST(SUBSTR(service_month, 6, 2) AS INTEGER) BETWEEN 1 AND 12
        GROUP BY npi, year, month_num
    """)


# ---------------------------------------------------------------------------
# Static features (computed in DuckDB, returned as a (npi, year) DataFrame)
# ---------------------------------------------------------------------------

def _static_features_sql(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per-(npi, year) static features that come straight out of SQL aggregates."""
    df = con.execute(f"""
        WITH py AS (
            SELECT npi, year,
                   SUM(claims)        AS claims,
                   SUM(beneficiaries) AS beneficiaries,
                   SUM(paid)          AS paid,
                   COUNT(DISTINCT hcpcs_code) AS n_hcpcs,
                   SUM(claims * claims)       AS sum_claims_sq
            FROM cells
            GROUP BY npi, year
        )
        SELECT
            npi, year,
            claims / NULLIF(beneficiaries, 0)              AS avg_claims_per_beneficiary,
            paid   / NULLIF(claims, 0)                     AS avg_paid_per_claim,
            CASE WHEN claims > 0
                 THEN sum_claims_sq / (claims * claims) END AS hcpcs_concentration,
            n_hcpcs,
            paid                                            AS total_paid
        FROM py
    """).df()
    return df


def _peer_ratios_sql(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    paid_vs_peer_ratio and claims_vs_peer_ratio: per-HCPCS rate vs same-specialty
    same-code median (>= MIN_PEERS distinct providers), aggregated to provider-year
    as a claims-weighted geometric mean.
    """
    df = con.execute(f"""
        WITH c AS (
            SELECT cells.npi, cells.year, cells.hcpcs_code, cells.claims,
                   prov.taxonomy_code AS tax,
                   cells.paid   / NULLIF(cells.claims, 0)        AS paid_rate,
                   cells.claims / NULLIF(cells.beneficiaries, 0) AS claims_rate
            FROM cells JOIN prov ON cells.npi = prov.npi
            WHERE prov.taxonomy_code IS NOT NULL
        ),
        pm AS (
            SELECT tax, year, hcpcs_code,
                   MEDIAN(paid_rate)   AS med_paid,
                   MEDIAN(claims_rate) AS med_claims,
                   COUNT(DISTINCT npi) AS n_peers
            FROM c
            GROUP BY tax, year, hcpcs_code
        ),
        j AS (
            SELECT c.npi, c.year, c.claims,
                   CASE WHEN pm.n_peers >= {_MIN_PEERS} AND pm.med_paid > 0 AND c.paid_rate > 0
                        THEN ln(c.paid_rate / pm.med_paid) END   AS lp,
                   CASE WHEN pm.n_peers >= {_MIN_PEERS} AND pm.med_claims > 0 AND c.claims_rate > 0
                        THEN ln(c.claims_rate / pm.med_claims) END AS lc
            FROM c JOIN pm USING (tax, year, hcpcs_code)
        )
        SELECT npi, year,
               exp(SUM(claims * lp) FILTER (WHERE lp IS NOT NULL)
                   / NULLIF(SUM(claims) FILTER (WHERE lp IS NOT NULL), 0)) AS paid_vs_peer_ratio,
               exp(SUM(claims * lc) FILTER (WHERE lc IS NOT NULL)
                   / NULLIF(SUM(claims) FILTER (WHERE lc IS NOT NULL), 0)) AS claims_vs_peer_ratio
        FROM j
        GROUP BY npi, year
    """).df()
    # Winsorize 1st/99th percentile across provider-years.
    for col in ["paid_vs_peer_ratio", "claims_vs_peer_ratio"]:
        df[col] = _winsorize(df[col])
    return df


def _n_distinct_vs_peer_sql(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """distinct-HCPCS count / same-specialty median count (>= MIN_PEERS), per year."""
    return con.execute(f"""
        WITH nh AS (
            SELECT cells.npi, cells.year,
                   COUNT(DISTINCT cells.hcpcs_code) AS n_hcpcs,
                   prov.taxonomy_code AS tax
            FROM cells JOIN prov ON cells.npi = prov.npi
            GROUP BY cells.npi, cells.year, prov.taxonomy_code
        ),
        pmed AS (
            SELECT tax, year, MEDIAN(n_hcpcs) AS med, COUNT(DISTINCT npi) AS n_peers
            FROM nh GROUP BY tax, year
        )
        SELECT nh.npi, nh.year,
               CASE WHEN pmed.n_peers >= {_MIN_PEERS} AND pmed.med > 0
                    THEN nh.n_hcpcs / pmed.med END AS n_distinct_hcpcs_vs_peer
        FROM nh JOIN pmed USING (tax, year)
    """).df()


def _new_hcpcs_fraction_sql(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    T6: fraction of codes billed in year y that were never billed by that NPI
    before y. Guards (first observed year / left-censored) are applied later.
    """
    return con.execute("""
        WITH fy AS (
            SELECT npi, hcpcs_code, MIN(year) AS first_year
            FROM cells GROUP BY npi, hcpcs_code
        ),
        per AS (
            SELECT cells.npi, cells.year,
                   COUNT(DISTINCT cells.hcpcs_code) AS total_codes,
                   COUNT(DISTINCT CASE WHEN fy.first_year = cells.year
                                       THEN cells.hcpcs_code END) AS new_codes
            FROM cells JOIN fy
              ON cells.npi = fy.npi AND cells.hcpcs_code = fy.hcpcs_code
            GROUP BY cells.npi, cells.year
        )
        SELECT npi, year,
               new_codes::DOUBLE / NULLIF(total_codes, 0) AS new_hcpcs_fraction
        FROM per
    """).df()


def _monthly_vectors_sql(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    Pivot mv into one row per (npi, year) with p1..p12 (paid) and c1..c12 (claims),
    zero-filled. Returns a DataFrame indexed by (npi, year) plus active_months and
    first_active_month, matching what the T1-T5 numpy helpers expect.
    """
    # Real claims data carries negative paid (refunds/adjustments); floor each
    # month at 0 so ln(paid + EPS) stays valid and active-month logic is clean.
    paid_cols   = ",\n".join(f"GREATEST(SUM(CASE WHEN month_num={i} THEN paid ELSE 0 END), 0) AS p{i}"   for i in range(1, 13))
    claims_cols = ",\n".join(f"GREATEST(SUM(CASE WHEN month_num={i} THEN claims ELSE 0 END), 0) AS c{i}" for i in range(1, 13))
    vecs = con.execute(f"""
        SELECT npi, year, {paid_cols}, {claims_cols}
        FROM mv GROUP BY npi, year
    """).df()
    vecs = vecs.set_index(["npi", "year"])

    P = vecs[[f"p{i}" for i in range(1, 13)]].values
    vecs["active_months"] = (P > 0).sum(axis=1)
    has_any = (P > 0)
    vecs["first_active_month"] = np.where(has_any.any(axis=1), has_any.argmax(axis=1) + 1, np.nan)
    return vecs


# ---------------------------------------------------------------------------
# Provider-level features (small — computed in pandas from providers_clean)
# ---------------------------------------------------------------------------

def feat_npi_age_days_at_first_claim(providers: pd.DataFrame) -> pd.Series:
    """(first-ever claim month − NPPES enumeration date) in days, clamped >= 0."""
    days = (providers["first_billing_month"] - providers["npi_registration_date"]).dt.days.clip(lower=0)
    return days.rename("npi_age_days_at_first_claim")


def feat_billing_on_deactivated_npi(providers: pd.DataFrame) -> pd.Series:
    """Binary: 1 if a deactivation date exists AND last claim month is after it."""
    deact = providers["npi_deactivation_date"]
    last  = providers["last_billing_month"]
    return (deact.notna() & (last > deact)).astype(int).rename("billing_on_deactivated_npi")


def label_is_excluded(providers: pd.DataFrame, index: pd.MultiIndex) -> pd.Series:
    """
    Validation label (never a model input): 1 if NPI in LEIE with
    EXCLDATE <= end of service year AND (REINDATE null OR > end of service year).
    """
    idx_df = index.to_frame(index=False)
    eoy = pd.to_datetime(idx_df["year"].astype(int).astype(str) + "-12-31")
    excl = idx_df["npi"].map(providers["excl_date"])
    rein = idx_df["npi"].map(providers["reinstate_date"])
    cond = excl.notna() & (excl <= eoy) & (rein.isna() | (rein > eoy))
    return pd.Series(cond.astype(int).values, index=index, name="is_excluded")


# ---------------------------------------------------------------------------
# Temporal numpy features T1-T5 (operate on the small wide-vector frame)
# ---------------------------------------------------------------------------

def feat_T1_mom_paid_growth_volatility(vecs: pd.DataFrame) -> pd.Series:
    P = vecs[[f"p{i}" for i in range(1, 13)]].values
    with np.errstate(invalid="ignore", divide="ignore"):
        g = np.diff(np.log(P + _EPS), axis=1)
    std_g = np.std(g, axis=1, ddof=1)
    std_g[vecs["active_months"].values < _MIN_ACTIVE] = np.nan
    return pd.Series(std_g, index=vecs.index, name="mom_paid_growth_volatility")


def feat_T2_cv_monthly_paid(vecs: pd.DataFrame) -> pd.Series:
    P = vecs[[f"p{i}" for i in range(1, 13)]].values
    cv = np.std(P, axis=1, ddof=1) / (np.mean(P, axis=1) + _EPS)
    cv[vecs["active_months"].values < _MIN_ACTIVE] = np.nan
    return pd.Series(cv, index=vecs.index, name="cv_monthly_paid")


def feat_T3_peak_to_median_paid(vecs: pd.DataFrame) -> pd.Series:
    P = vecs[[f"p{i}" for i in range(1, 13)]].values.astype(float)
    P_active = np.where(P > 0, P, np.nan)
    with np.errstate(invalid="ignore"):
        peak   = np.nanmax(P_active, axis=1)
        median = np.nanmedian(P_active, axis=1)
    ratio  = peak / (median + _EPS)
    ratio[vecs["active_months"].values < _MIN_ACTIVE3] = np.nan
    s = pd.Series(ratio, index=vecs.index, name="peak_to_median_paid")
    return s.clip(upper=s.quantile(0.99))


def feat_T4_onset_ramp_slope(vecs: pd.DataFrame, meta: pd.DataFrame) -> pd.Series:
    """OLS slope of ln(paid+EPS) over the first 6 active months; first year only,
    not left-censored, >= MIN_ONSET active points."""
    onset_idx = []
    for npi, pm in meta.iterrows():
        if pm["left_censored"]:
            continue
        key = (npi, int(pm["first_year"]))
        if key in vecs.index:
            onset_idx.append(key)

    results = pd.Series(np.nan, index=vecs.index, name="onset_ramp_slope")
    if not onset_idx:
        return results
    for (npi, year), row in vecs.loc[onset_idx].iterrows():
        fam = row["first_active_month"]
        if pd.isna(fam):                       # no active month → no onset slope
            continue
        start = int(fam) - 1
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
    P = vecs[[f"p{i}" for i in range(1, 13)]].values
    peak = P.max(axis=1)
    dropoff = 1.0 - P[:, -3:].mean(axis=1) / (peak + _EPS)
    dropoff[vecs["active_months"].values < _MIN_ACTIVE] = np.nan
    return pd.Series(dropoff, index=vecs.index, name="post_peak_dropoff")


def feat_T7_excess_yoy_growth(provider_annual: pd.DataFrame, providers: pd.DataFrame) -> pd.Series:
    """provider log YoY growth − specialty-median log YoY growth (>= MIN_PEERS peers)."""
    ann = provider_annual.copy()
    ann["npi"]  = ann["npi"].astype(str)
    ann["year"] = ann["service_year"].astype(int)
    paid_wide = ann.pivot_table(index="npi", columns="year", values="total_paid_amount", aggfunc="sum")

    years = sorted(paid_wide.columns)
    log_growth = pd.DataFrame(index=paid_wide.index)
    for i in range(1, len(years)):
        prev = paid_wide[years[i - 1]].replace(0, np.nan)
        curr = paid_wide[years[i]].replace(0, np.nan)
        log_growth[years[i]] = np.log(curr + _EPS) - np.log(prev + _EPS)

    log_growth["_tax"] = providers["taxonomy_code"].reindex(log_growth.index)

    results = {}
    for year_col in [c for c in log_growth.columns if c != "_tax"]:
        col = log_growth[[year_col, "_tax"]].dropna(subset=[year_col])
        counts = col.groupby("_tax")[year_col].transform("count")
        medians = col.groupby("_tax")[year_col].transform("median")
        valid = counts >= _MIN_PEERS
        excess = np.where(valid, col[year_col] - medians, np.nan)
        for npi, val in zip(col.index, excess):
            results[(npi, year_col)] = val

    s = pd.Series(results, name="excess_yoy_growth")
    if s.notna().any():
        p1, p99 = s.quantile([0.01, 0.99])
        s = s.clip(p1, p99)
    s.index = s.index.set_names(["npi", "year"])
    return s


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_fraud_features(
    monthly_path: str,
    providers: pd.DataFrame,
    provider_annual: pd.DataFrame | None = None,
) -> pd.DataFrame:
    con = _connect()
    print("  Staging cells + monthly vectors in DuckDB …")
    _stage_tables(con, monthly_path, providers)

    print("  Static features (SQL) …")
    stat = _static_features_sql(con).set_index(["npi", "year"])
    base_index = stat.index

    features = pd.DataFrame(index=base_index)
    features["avg_claims_per_beneficiary"] = stat["avg_claims_per_beneficiary"]
    features["avg_paid_per_claim"]         = stat["avg_paid_per_claim"]

    print("  Peer ratios (SQL) …")
    peer = _peer_ratios_sql(con).set_index(["npi", "year"])
    features["paid_vs_peer_ratio"]   = peer["paid_vs_peer_ratio"]
    features["claims_vs_peer_ratio"] = peer["claims_vs_peer_ratio"]

    print("  Distinct-HCPCS vs peer (SQL) …")
    ndp = _n_distinct_vs_peer_sql(con).set_index(["npi", "year"])
    features["n_distinct_hcpcs_vs_peer"] = ndp["n_distinct_hcpcs_vs_peer"]

    features["hcpcs_concentration"] = stat["hcpcs_concentration"]

    # provider-level features (broadcast to each year)
    npi_level = base_index.get_level_values("npi")
    features["billing_on_deactivated_npi"]  = feat_billing_on_deactivated_npi(providers).reindex(npi_level).fillna(0).astype(int).values
    features["npi_age_days_at_first_claim"] = feat_npi_age_days_at_first_claim(providers).reindex(npi_level).values

    # temporal T1-T5 from monthly vectors
    print("  Monthly vectors + T1-T5 (SQL pivot → numpy) …")
    vecs = _monthly_vectors_sql(con)
    meta = providers[["first_billing_month", "left_censored"]].copy()
    meta["first_year"]    = meta["first_billing_month"].dt.year
    meta["left_censored"] = meta["left_censored"].astype(bool)
    features["mom_paid_growth_volatility"] = feat_T1_mom_paid_growth_volatility(vecs)
    features["cv_monthly_paid"]            = feat_T2_cv_monthly_paid(vecs)
    features["peak_to_median_paid"]        = feat_T3_peak_to_median_paid(vecs)
    features["onset_ramp_slope"]           = feat_T4_onset_ramp_slope(vecs, meta)
    features["post_peak_dropoff"]          = feat_T5_post_peak_dropoff(vecs)

    # T6 (SQL) with first-year / left-censored guards
    print("  T6 new_hcpcs_fraction (SQL) …")
    t6 = _new_hcpcs_fraction_sql(con).set_index(["npi", "year"])["new_hcpcs_fraction"]
    features["new_hcpcs_fraction"] = t6
    first_year = providers["first_billing_month"].dt.year
    yr = base_index.get_level_values("year")
    is_first = (yr == first_year.reindex(npi_level).values)
    is_lc    = providers["left_censored"].astype(bool).reindex(npi_level).fillna(False).values
    features.loc[is_first | is_lc, "new_hcpcs_fraction"] = np.nan

    # T7 (pandas — provider_annual is small) with left-censored guard
    if provider_annual is not None:
        print("  T7 excess_yoy_growth …")
        features["excess_yoy_growth"] = feat_T7_excess_yoy_growth(provider_annual, providers)
        features.loc[is_lc, "excess_yoy_growth"] = np.nan
    else:
        features["excess_yoy_growth"] = np.nan

    # non-model columns
    features["is_excluded"]   = label_is_excluded(providers, base_index)
    features["total_paid"]    = stat["total_paid"]
    features["taxonomy_code"] = providers["taxonomy_code"].reindex(npi_level).values

    con.close()
    features.index.names = ["npi", "year"]
    return features.sort_index()


def save_features(features: pd.DataFrame, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(path)
    print(f"Saved {len(features):,} provider-year rows → {path}")
    nulls = features.isnull().sum()
    nulls = nulls[nulls > 0]
    if not nulls.empty:
        print("  Columns with nulls:")
        for col, n in nulls.items():
            print(f"    {col}: {n:,}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build fraud-signal features (DuckDB-backed) at the (npi, year) grain."
    )
    parser.add_argument("--monthly",   required=True, help="provider_monthly.parquet from clean_data.py")
    parser.add_argument("--providers", required=True, help="providers_clean.csv from clean_data.py")
    parser.add_argument("--annual",    default=None,  help="provider_annual.parquet for temporal T7")
    parser.add_argument("--output",    default="data/processed/fraud_features.csv")
    args = parser.parse_args()

    print(f"Loading providers from {args.providers} …")
    providers = load_providers(args.providers)
    print(f"  {len(providers):,} providers")

    provider_annual = None
    if args.annual:
        print(f"Loading annual aggregates from {args.annual} …")
        p = Path(args.annual)
        provider_annual = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
        print(f"  {len(provider_annual):,} provider-year rows")

    print(f"Computing fraud features from {args.monthly} …")
    features = build_fraud_features(args.monthly, providers, provider_annual=provider_annual)

    save_features(features, args.output)


if __name__ == "__main__":
    main()
