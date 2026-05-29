# Data Dictionary

## claims_part_b (Professional Claims)

| Field | Type | Description |
|-------|------|-------------|
| `claim_id` | string | Unique claim identifier |
| `npi` | string | National Provider Identifier (10-digit) |
| `beneficiary_id` | string | Anonymised beneficiary identifier |
| `service_date` | date | Date of service |
| `submitted_date` | date | Date claim was submitted to payer |
| `procedure_code` | string | CPT or HCPCS procedure code |
| `modifier` | string | Procedure modifier (up to 4), pipe-delimited |
| `diagnosis_code` | string | Primary ICD-10-CM diagnosis code |
| `place_of_service` | string | CMS place-of-service code (e.g. 11 = office, 21 = inpatient) |
| `units` | int | Units of service billed |
| `billed_amount` | float | Amount billed by provider (USD) |
| `allowed_amount` | float | Payer-allowed amount (USD) |
| `paid_amount` | float | Amount actually paid (USD) |
| `denial_reason` | string | CARC/RARC denial code if claim was denied, else null |

## claims_part_d (Prescription Drug Events)

| Field | Type | Description |
|-------|------|-------------|
| `pde_id` | string | Prescription drug event identifier |
| `prescriber_npi` | string | NPI of the prescribing provider |
| `dispensing_npi` | string | NPI of the dispensing pharmacy |
| `beneficiary_id` | string | Anonymised beneficiary identifier |
| `fill_date` | date | Date prescription was dispensed |
| `ndc` | string | National Drug Code (11-digit) |
| `drug_name` | string | Brand or generic drug name |
| `days_supply` | int | Days of supply dispensed |
| `quantity` | float | Quantity dispensed (units per the drug's unit of measure) |
| `is_controlled` | bool | True if drug is a DEA Schedule II–V controlled substance |
| `dea_schedule` | string | DEA schedule (II, III, IV, V) or null |
| `ingredient_cost` | float | Drug ingredient cost (USD) |
| `dispensing_fee` | float | Pharmacy dispensing fee (USD) |
| `total_cost` | float | Total plan cost (USD) |

## providers

| Field | Type | Description |
|-------|------|-------------|
| `npi` | string | National Provider Identifier |
| `provider_name` | string | Individual or organisation name |
| `entity_type` | string | `individual` or `organisation` |
| `specialty_code` | string | CMS provider specialty code |
| `specialty_desc` | string | Specialty description |
| `practice_state` | string | US state abbreviation |
| `practice_zip` | string | 5-digit ZIP code |
| `enrollment_date` | date | Date enrolled in Medicaid |
| `is_excluded` | bool | True if on OIG exclusion list at any point |
| `exclusion_date` | date | Date added to OIG exclusion list (null if not excluded) |

## beneficiaries

| Field | Type | Description |
|-------|------|-------------|
| `beneficiary_id` | string | Anonymised beneficiary identifier |
| `birth_year` | int | Year of birth (not full DOB, for de-identification) |
| `sex` | string | `M`, `F`, or `U` |
| `eligibility_start` | date | Start of Medicaid eligibility period |
| `eligibility_end` | date | End of eligibility (null if currently enrolled) |
| `death_date` | date | Date of death (null if alive) |
| `dual_eligible` | bool | True if enrolled in both Medicaid and Medicare |
| `managed_care_plan` | string | Managed care organisation name, or null for fee-for-service |
| `state` | string | State of enrollment |

## provider_features (output of clean_data.py)

| Field | Type | Description |
|-------|------|-------------|
| `npi` | string | Provider NPI (index) |
| `claim_rate_per_day` | float | Claims per active enrollment day |
| `unique_beneficiary_ratio` | float | Unique beneficiaries / total claims |
| `procedure_concentration` | float | HHI of billed procedure codes |
| `avg_units_per_claim` | float | Average units billed per claim |
| `upcoding_index` | float | Share of E&M claims at the highest complexity levels (99214–99215) |
| `weekend_service_ratio` | float | Proportion of claims with weekend service dates |
| `avg_payment_ratio` | float | Mean(paid / allowed) — low values suggest systematic denials |
| `denial_rate` | float | Proportion of claims denied |
| `controlled_rx_ratio` | float | Controlled substance scripts / total scripts (Part D providers) |
| `multi_provider_beneficiary_ratio` | float | Share of beneficiaries also seen by ≥5 other providers for same dx |
| `diagnosis_procedure_mismatch_rate` | float | Rate of procedure-diagnosis pairs flagged by NCCI edits |

## provider_rankings (output of analyze_anomalies.py)

| Field | Type | Description |
|-------|------|-------------|
| `rank` | int | Fraud risk rank (1 = highest risk) |
| `npi` | string | Provider NPI |
| `provider_name` | string | Provider name |
| `specialty` | string | Specialty description |
| `anomaly_score` | float | Composite anomaly score in [0, 1] |
| `top_driver` | string | Feature with the highest DIFFI importance |
| `description` | string | Plain-English summary of the top risk drivers |
