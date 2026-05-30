# Data Dictionary

This describes every dataset the pipeline reads and produces, in the order they flow through the
three stages. "Grain" means *what one row represents*.

---

## Inputs (Stage 1 reads these)

### Medicaid provider spending
Source: HHS/CMS Medicaid provider-spending extract (Parquet or CSV). **Grain:** one row per
provider, procedure code, and month.

| Field | Description |
|-------|-------------|
| `npi` | National Provider Identifier (10-digit) of the billing provider |
| `hcpcs_code` | Procedure code billed (HCPCS/CPT) |
| `service_month` | Month of service (`YYYY-MM`) |
| `total_beneficiaries` | Unique patients billed |
| `total_claims` | Number of claims |
| `total_paid_amount` | Dollars paid |

### NPPES provider registry
Source: CMS NPPES public file (`npidata_pfile.csv`). **Grain:** one row per provider. Only a handful
of its 300+ columns are used:

| Field | Description |
|-------|-------------|
| `npi` | National Provider Identifier |
| `entity_type` | Individual vs. organization |
| `provider_name` | Organization name, or "LAST, FIRST" for individuals |
| `practice_state` | Provider's practice-location state |
| `taxonomy_code` | Specialty code (used to define peer groups) |
| `npi_registration_date` | When the provider's NPI was issued |
| `npi_deactivation_date` | When the NPI was deactivated (if ever) |

### OIG LEIE exclusion list
Source: OIG List of Excluded Individuals/Entities. **Grain:** one row per excluded provider. This is
the "known bad" reference used only to *measure* the model — never to train it.

| Field | Description |
|-------|-------------|
| `npi` | NPI of the excluded provider |
| `excl_type` | Type/reason of exclusion |
| `excl_date` | Date the exclusion took effect |
| `reinstate_date` | Date reinstated (if applicable) |

---

## Intermediate tables (Stage 1 writes, Stage 2 reads)

All three are restricted to providers paid more than the `--min-total-paid` threshold (default $10M).

### `provider_monthly.parquet`
**Grain:** one row per provider, procedure code, and month. The cleaned, de-duplicated spending table.

| Field | Description |
|-------|-------------|
| `npi` | Provider NPI |
| `hcpcs_code` | Procedure code |
| `service_month` | Month of service (`YYYY-MM`) |
| `total_claims` | Claims that month |
| `total_beneficiaries` | Patients that month |
| `total_paid_amount` | Dollars paid that month |

### `provider_annual.parquet`
**Grain:** one row per provider per year. Used to compute the year-over-year growth signal (T7).

| Field | Description |
|-------|-------------|
| `npi` | Provider NPI |
| `service_year` | Year (`YYYY`) |
| `total_claims` | Claims that year |
| `total_beneficiaries` | Patients that year |
| `total_paid_amount` | Dollars paid that year |

### `providers_clean.csv`
**Grain:** one row per provider. Provider metadata joined from NPPES and LEIE, plus a few derived flags.

| Field | Description |
|-------|-------------|
| `npi` | Provider NPI |
| `provider_name` | Provider or organization name |
| `entity_type` | Individual vs. organization |
| `practice_state` | Practice-location state |
| `taxonomy_code` | Specialty code (defines peer groups) |
| `total_paid` | Lifetime Medicaid dollars paid |
| `n_distinct_hcpcs` | Distinct procedure codes ever billed |
| `n_active_months` | Distinct months with billing |
| `first_billing_month` / `last_billing_month` | First and last months seen in the data |
| `npi_registration_date` / `npi_deactivation_date` | NPI issue / deactivation dates |
| `in_leie` | 1 if the provider appears on the OIG exclusion list |
| `excl_date` / `reinstate_date` / `excl_type` | Exclusion details (if excluded) |
| `left_censored` | 1 if the provider was already billing on the first day of the data (so its "onset" and growth signals can't be measured) |

---

## Feature table (Stage 2 writes, Stage 3 reads)

### `fraud_features.csv`
**Grain:** one row per provider per year. The 15 signals fed to the model, plus three reference columns
that are **not** used for scoring.

**The 15 model signals** (see the README for plain-English meanings):
`avg_claims_per_beneficiary`, `avg_paid_per_claim`, `paid_vs_peer_ratio`, `claims_vs_peer_ratio`,
`n_distinct_hcpcs_vs_peer`, `hcpcs_concentration`, `billing_on_deactivated_npi`,
`npi_age_days_at_first_claim`, `mom_paid_growth_volatility` (T1), `cv_monthly_paid` (T2),
`peak_to_median_paid` (T3), `onset_ramp_slope` (T4), `post_peak_dropoff` (T5),
`new_hcpcs_fraction` (T6), `excess_yoy_growth` (T7).

**Reference-only columns (excluded from scoring):**

| Field | Description |
|-------|-------------|
| `total_paid` | Dollars paid that year. Kept for triage sorting, but deliberately left out of the model so size alone doesn't drive the score |
| `is_excluded` | 1 if the provider was on the OIG exclusion list during that year. Used only to score the model's accuracy after the fact |
| `taxonomy_code` | Specialty code, carried through for reference |

---

## Outputs (Stage 3 writes)

### `provider_rankings.csv` — the investigation shortlist
**Grain:** one row per provider per year, ranked most-to-least unusual. Top 200 by default.

| Field | Description |
|-------|-------------|
| `rank` | Rank by anomaly score (1 = most unusual) |
| `npi` | Provider NPI |
| `provider_name` | Provider name (if names file supplied) |
| `year` | Service year |
| `anomaly_score` | How unusual the provider-year is, 0 (normal) to 1 (highly unusual) |
| `is_anomaly` | 1 if the score crosses the anomaly threshold |
| `total_paid` | Dollars paid that year |
| `is_excluded` | 1 if the provider was on the OIG exclusion list that year |
| `taxonomy_code` | Specialty code |
| `top_driver` | The single signal that contributed most to the score |
| `description` | Plain-English summary of the top reasons the provider was flagged |
| *(the 15 signals)* | The underlying signal values for this provider-year |

### `provider_summary.csv` — the triage rollup (optional)
**Grain:** one row per provider, ranked by worst year. Top 200 by default. Produced only when
`--provider-summary` is supplied.

| Field | Description |
|-------|-------------|
| `rank` | Rank by `max_anomaly_score` |
| `npi` | Provider NPI |
| `provider_name` | Provider name |
| `max_anomaly_score` | The provider's most unusual single year |
| `mean_anomaly_score` | Average score across the provider's years |
| `worst_year` | The year with the highest score |
| `worst_year_top_driver` | The leading signal in that worst year |
| `n_years` | How many years the provider appears in the data |
| `n_years_flagged` | How many of those years crossed the anomaly threshold |
| `total_paid_all_years` | Lifetime Medicaid dollars — use this to prioritize by exposure |
| `ever_excluded` | 1 if the provider was on the OIG exclusion list in any year |
| `taxonomy_code` | Specialty code |
