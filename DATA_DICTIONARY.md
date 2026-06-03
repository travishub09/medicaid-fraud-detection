# Data Dictionary (attempt_2)

Every dataset the attempt_2 pipeline reads and produces, in flow order. **Grain** = what one row
represents. All identifier columns (NPI, PAC ID, enrollment ID, ZIP) are kept as **strings**.

> The original attempt_1 tables (`provider_monthly`, `fraud_features`, `provider_rankings`, …) are
> superseded; see git history if needed.

---

## 1. Raw inputs (`~/Desktop/data/preclean/`)

### `Spending.csv` — Medicaid provider spending (the fact table)
**Grain:** one row per billing NPI × servicing NPI × HCPCS × month.

| Field | Description |
|-------|-------------|
| `BILLING_PROVIDER_NPI_NUM` | NPI that was paid — **the only key used to attribute dollars** |
| `SERVICING_PROVIDER_NPI_NUM` | NPI that performed the service — attribute only, never used for attribution |
| `HCPCS_CODE` | Procedure code billed |
| `CLAIM_FROM_MONTH` | Month of service (`YYYY-MM`) |
| `TOTAL_PATIENTS` | Distinct patients **within that row** (never summed as distinct patients) |
| `TOTAL_CLAIM_LINES` | Claim lines |
| `TOTAL_PAID` | Dollars paid |

### `NPPES.csv` — national provider registry
**Grain:** one row per NPI (330 columns; only ~12 read): NPI, entity type, legal/last/first name,
primary taxonomy, practice address, deactivation/reactivation dates.

### `PECOS.csv` — Medicare enrollment base
**Grain:** one row per NPI × enrollment. Carries `NPI`, `PECOS_ASCT_CNTL_ID` (PAC ID),
`ENRLMT_ID`, provider type, state, names — the **crosswalk** that ties owners/facilities to NPIs.

### `Caught.csv` — OIG LEIE exclusion list
**Grain:** one row per exclusion record (a provider may have several). Columns include `NPI`,
`EXCLTYPE`, `EXCLDATE`, `REINDATE`, `WAIVERDATE`, `WVRSTATE`, and name fields.

### `owners/*.csv` — CMS "All-Owners" (FQHC / HHA / Hospice / Hospital / Nursing)
**Grain:** one row per facility × owner. Facilities keyed by `ENROLLMENT ID` / `ASSOCIATE ID`;
owners by `ASSOCIATE ID - OWNER`, with owner type/role, name, address, and ownership-type flags.

---

## 2. Integration outputs (`integrate.py` → `~/Desktop/data/integrated/`)

### `provider_dim.parquet` — the provider dimension
**Grain:** exactly one row per canonical NPI (asserted unique).

| Field | Description |
|-------|-------------|
| `npi` | Canonical 10-digit NPI (Luhn-validated) |
| `entity_type` | 1 = individual, 2 = organization |
| `org_legal_name`, `provider_name`, `name_key` | Legal/display name + normalized key |
| `taxonomy_code` | Primary taxonomy (peer-group key) |
| `addr_line1/city/state/zip`, `addr_key` | Practice address + normalized key |
| `deactivation_date`, `reactivation_date`, `is_active` | NPI lifecycle |
| `provider_type_desc`, `pecos_state` | Enriched many-to-one from PECOS |

### `spending_fact.parquet` — cleaned, attributed spending
**Grain:** one row per spending record (row count and `SUM(total_paid)` identical to raw — proof of
zero fan-out / zero dropped dollars). Adds `billing_npi` (canonical), `provider_matched`, the
`provider_dim` attributes, and `active_at_claim`.

### Other integration tables
| Table | Grain / purpose |
|-------|-----------------|
| `npi_xwalk.parquet` | NPI ↔ PAC ↔ enrollment (deduped; never joined to spending) |
| `pecos_provider.parquet` | one row per NPI (deterministic enrollment collapse) |
| `owner_edges.parquet` | facility → owner edges; facility NPI resolved via enrollment id |
| `exclusions.parquet` | cleaned LEIE: `npi`, `excl_date`, `reinstate_date`, `excl_type`, `name_key` |
| `facility_owner_exclusion_flags.parquet` | per facility NPI: excluded-owner counts by tier (high/probable) |
| `npi_quarantine.parquet` | identifiers that failed Luhn/format (source + raw value + reason) |

---

## 3. Corruption audit (`audit_corruption.py`)

| Table | Grain / purpose |
|-------|-----------------|
| `spending_corruption_quarantine.parquet` | rows with `TOTAL_PAID > $500M` (physically impossible) + `reason`, `hcpcs_malformed` |
| `spending_aggregate_billing.parquet` | legitimate non-provider/aggregate billing (blank-NPI, plausible) |

Rule: `$500M`/row cleanly separates the legitimate distribution (matched max ≈ $119M, largest
aggregate ≈ $470M) from corruption (smallest corrupt ≈ $505M). Real total after quarantine ≈ $1.42 T.

---

## 4. Feature base (`features.py` → `~/Desktop/data/features/`)

### `spending_provider_base.parquet`
**Grain:** the clean base — `spending_fact` where `provider_matched & total_paid ≤ $500M`
(≈230 M rows / $1.10 T; reconciled to the audit). All features read **only** this.

### `provider_features.parquet` — the feature table
**Grain:** exactly one row per billing NPI (617,062). Selected columns:

| Group | Fields |
|-------|--------|
| Volume / payment | `gross_paid`, `net_paid`, `reversal_amount`, `reversal_ratio`, `total_claim_lines`, `service_volume` (= Σ`TOTAL_PATIENTS`, a volume **proxy** — not distinct patients), `n_distinct_hcpcs`, `n_active_months`, `tenure_months` |
| Ratios | `paid_per_claim_line`, `paid_per_patient_instance`, `lines_per_patient_instance` |
| Concentration | `top_hcpcs_paid_share`, `hcpcs_hhi` |
| Peer-relative | robust z + percentile for net_paid / paid-per-claim / lines-per-patient / paid-per-patient, vs taxonomy and taxonomy×state, + `peer_group_size_*`, `peer_group_too_small_*` |
| Temporal (mature months only) | `yoy_growth_net_paid`, `month_to_month_volatility`, `max_single_month_net_paid`, `new_biller_surge_onset` |
| Specialty proxy | `rare_for_taxonomy_paid_share` |
| Linkage | `provider_on_leie`, `facility_has_excluded_owner_high/_probable`, `excluded_owner_role` |
| Carried dims | `entity_type`, `primary_taxonomy`, `practice_state`, `org_legal_name` |

`provider_month.parquet` / `provider_hcpcs.parquet` are the supporting intermediates.

---

## 5. Detection outputs (`detect.py`, `refine_layer2*.py` → `~/Desktop/data/detection/`)

### `fraud_leads.parquet` / `fraud_leads_v2.parquet` / `fraud_leads_v3.parquet`
**Grain:** one row per billing NPI. `v3` is the latest. Columns:

| Field | Description |
|-------|-------------|
| `priority_tier` / `priority_rank` | `1_L1_billed_after_exclusion` > `2_L1_implausible_rate` > `3_L1_excluded_after_billing` > `4_L2_anomaly` > `5_L3_probable_owner` > `6_none` |
| `provider_on_leie`, `billed_after_exclusion`, `excluded_after_billing`, `rule_reasons` | Layer-1 flags + reasons |
| `anomaly_score_v3`, `n_concept_signals`, `anomaly_contributing_concepts` | Layer-2 (v3): mean concept percentile; count of concepts ≥ in-group P99; which concepts fired |
| `iforest_score_secondary` | Isolation Forest cross-check (secondary) |
| `peer_basis`, `not_scored`, `not_scored_reason` | how/whether scored (`low_volume_unreliable`, `degenerate_zero_gross_paid`, `missing_taxonomy`, `peer_group_too_small`) |
| `layer3_probable_owner`, `facility_excluded_owner_n_probable`, `excluded_owner_role` | Layer-3 ownership track |
| `entity_type`, `primary_taxonomy`, `practice_state`, `net_paid`, `gross_paid` | context |

Layer-2 concepts (v3): `concentration`, `payment_intensity`, `service_intensity`,
`specialty_mismatch`, `temporal` — correlated features collapse to one so a single fact can't
double-count.

### `layer1_candidate_cases.parquet` (`verify_layer1.py`)
**Grain:** one row per LEIE-matched billing NPI (578). `disposition` (`QUALIFIED / AMBIGUOUS /
DISQUALIFIED / CONTEXT_ONLY`), `paid_after`, `n_clean_after_months`, `claim_lines_after`,
`top_hcpcs_after`, `excl_months`, `reindates`, `waiver_states`, `excltype` + `fraud_related_excltype`,
`name_match`, NPPES + LEIE names.

---

## 6. Final CSVs (`export_final_leads.py`)

### `final_leads_over_10m.csv`
**Grain:** one row per NPI; every lead (tiers 1–5) with billing ≥ threshold, sorted tier then dollars.

| Field | Notes |
|-------|-------|
| `npi` | written as TEXT (quoted) |
| `provider_name` | display name (orgs + individuals) |
| `entity_type`, `primary_taxonomy`, `taxonomy_description`, `state`, `priority_tier` | |
| `total_billing_size_proxy_not_case_value` | = `net_paid` — a **size proxy, not a case value** |
| Layer-1 | `provider_on_leie`, `billed_after_exclusion`, `layer1_disposition`, `paid_after`, `excl_date`, `excl_type` |
| Layer-2 | `anomaly_score_v3`, `n_concept_signals`, `contributing_concepts` |
| Layer-3 | `probable_excluded_owner` |
| `reasons` | one-line summary of why the lead surfaced |

### `high_precision_excluded_leads.csv`
Same columns; the billed-while-excluded leads (tier 1 + any `QUALIFIED` disposition), kept regardless
of dollar.

---

## 7. Company grain (`company_rollup.py`, `company_lead_tracker.py`)

### `npi_to_company_map.parquet` (`company_rollup.py`)
**Grain:** one row per billing NPI → `company_id` (+ per-NPI `net_paid`, `merge_basis_raw`). The audit
trail that makes the rollup droppable back to claim level.

### `company_rollup.parquet` / `company_leads_over_10m.csv` (`company_rollup.py`)
**Grain:** one row per company (all 538,232). NPIs linked by PAC > shared-owner > exact-name
(`merge_basis` + `merge_confidence`). Aggregates: `company_net_paid`, `company_gross_paid`,
`npi_count`, `states`, `primary_taxonomies`, `entity_types`, `npi_list`, and aggregated signals
(`any_provider_on_leie`, `any_billed_after_exclusion`, `any_probable_excluded_owner`,
`max_anomaly_score_v3`, `best_priority_tier`, `flagged`). The CSV is the ≥$10M flagged subset (+ a
`reasons` column).

### `company_lead_tracker.csv` / `.parquet` (`company_lead_tracker.py`)
**Grain:** one row per company lead, **companies with `company_net_paid` ≥ $10 M** (all signal tiers),
ranked direct → company-anomaly → ownership. Unlike the rollup's aggregated "max anomaly across NPIs",
this carries a **real company-vs-company v3 anomaly** (`rate_features` + `score_concepts` applied at
company grain).

| Field | Notes |
|-------|-------|
| `company_id`, `company_name`, `npi_count`, `states` | `company_id`/`npi_list` written as TEXT |
| `company_total_billing_size_proxy_not_case_value` | = `company_net_paid` — **size proxy, not case value** |
| `priority_tier` | `1_billed_after_exclusion` > `2_on_leie` > `3_company_anomaly` > `4_probable_owner` |
| direct | `any_provider_on_leie`, `any_billed_after_exclusion`, `paid_after` |
| company anomaly | `company_anomaly_score`, `n_concept_signals`, `contributing_concepts` |
| `fragmentation_signal` | company is a Layer-2 lead but NO constituent NPI was (≥2 NPIs) — possible split-billing |
| ownership | `any_probable_excluded_owner` |
| `merge_basis`, `merge_confidence`, `reasons`, `npi_list` | linkage provenance + why-flagged + constituents |

### `company_tracker_direct_under_10m.csv` (`company_lead_tracker.py`)
Same columns; the sub-$10 M billed-while-excluded / on-LEIE direct leads, preserved (highest precision,
naturally below the threshold).
