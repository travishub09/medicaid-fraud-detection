# COLUMN CONTRACT INVENTORY — every raw-file adapter (code audit, 2026-10-21)

Read-only audit of the repository at `/home/user/medicaid-fraud-detection`. No data files exist in this environment; every statement below is about CODE, with `file:line` citations. "Scheme" references are to `src/model_a/scheme_subscores.py:54-197` (`DEFAULT_SCHEME_WEIGHTS`).

## 0. Conventions used in the tables

**Resolver semantics.** Most adapters resolve headers through `src/attempt_2/clean_data.py:166-180` (`_resolve_columns`): the header and each candidate are normalized by `_norm` (`:162-163` — strip, strip quotes, lower-case, remove spaces and underscores) and matched EXACTLY on that normalized token; the first candidate present wins. So `Rndrng_NPI`, `RNDRNG NPI`, `rndrngnpi` all match; `Rndrng_NPI_Type` does not. Adapters that use a different matcher are called out ("exact, case-sensitive", "regex", "substring").

**"if missing" vocabulary.**
- RAISE — the adapter raises `ValueError` naming the field. In the export's per-NPI loop (`src/model_a/provider_features_export.py:1563-1565`) a `ValueError` means "try the next-newest file in the folder", then "[name] skipped: <err>"; in the org/CCN blocks every exception is caught and logged as `skipped:` (`:1992`, `:2015`, `:2030`, `:2047`, `:2067`, `:2103`, `:2215`, `:2245`, `:2255`, `:2267`, `:2281`, `:2290`, `:2303`, `:2334`).
- CRASH — an unhandled non-`ValueError` (KeyError/AttributeError) in a stage that has no catch (integrate, graph build, the export's `address` block).
- SKIP — the adapter returns an empty frame or omits the column; the source audit shows "skipped: adapter returned no rows" or the column is simply absent (NULL in the matrix, which the subscore engine treats as "not scored").
- BLANK — the adapter fills a default (0, "", NaN, 1.0) and continues WITHOUT any log line; the downstream feature is computed on the default. These are the dangerous ones, and every BLANK that feeds a scheme is repeated in FINDINGS.

**Discovery helpers in the export** (`src/model_a/provider_features_export.py`):
- `_year_files(folder, max_year)` `:949-966` — all `*.csv|*.xlsx|*.parquet` in the folder, newest filename-year first; under a freeze, files whose filename year > cutoff year are excluded; **a filename with NO year passes the cap** (`:957-958`).
- `_read_one` `:975-991` — reads only the resolved columns (usecols) for csv; `.xlsx` is routed to `_read_any` `:984-985`, which has NO Excel reader (`:1002-1009` → `read_csv_text`), so an .xlsx admitted by `_year_files` is read as CSV and fails.
- `_asof_pick(base, exact, cutoff, pattern, fallback_newest)` `:1262-1295` — newest filename-dated (`_file_period` `:1252-1259`: `YYYY`, `YYYY-MM`, `YYYYMMDD` tokens) file on/before the cutoff; else the undated contract name; else (only if `fallback_newest`) the newest dated file.
- `_asof_files(files, cutoff)` `:1298-1329` — multi-file snapshot sources: dated files on/before the cutoff, else the undated ones ("vintage unverified"), else nothing.
- `_asof_newest(base, patterns, cutoff)` `:1332-1347` — `_asof_files` then the latest period; without a cutoff the alphabetically FIRST hit (`:1342-1343`).
- `_first_existing(base, *names)` `:1350-1372` — exact names first, then globs (sorted, first hit), then a sibling directory named after the stem.
- All `glob`/`rglob` patterns here are **case-sensitive on Linux (the VM)** and case-insensitive on the operator's Windows laptop.

**Registries compared.** (R1) `src/preflight.py` CORE/UNLOCKS/OPTIONAL `:44-156` (the operator's "do I have the file" check; `_scan` `:174-191` matches exact names case-insensitively, then ANY file with a listed extension in a subfolder). (R2) `src/readiness_audit.py` POLICY `:45-77` + COLS_IMPORT `:81-96` (iterates only CORE+UNLOCKS, `:236`). (R3) `src/vintage_inspect.py` REQUIRED_COLS `:75-92`, FACILITY_CHECKS `:94-100`, known-folder set `:440-456`.

---

## 1. CORE files — `src/attempt_2/ingest/integrate.py`

Located by CLI defaults `:661-673`: `preclean/Spending.csv`, `preclean/NPPES.csv`, `preclean/PECOS.csv`, `preclean/Caught.csv`, `preclean/owners/*.csv`, `preclean/revocations/revoked_providers.csv`. R1 CORE `:45-52` lists the same four root names (plus `leie.csv`/`LEIE.csv` aliases for the LEIE, which integrate's default does NOT try — see F-24).

### 1a. Spending.csv — NO column map; literals in SQL (`integrate.py:338-378`)

| field | accepted header | if missing | notes |
|---|---|---|---|
| billing_npi | `BILLING_PROVIDER_NPI_NUM` (`:343`, `:355`, `:375`) | CRASH (DuckDB binder error) | canonicalized via `billing_map` (Luhn) `:343-345` |
| servicing_npi | `SERVICING_PROVIDER_NPI_NUM` (`:346`, `:356`, `:376`) | CRASH | attribute only (rule 4) |
| hcpcs_code | `HCPCS_CODE` `:357` | CRASH | |
| service_month | `CLAIM_FROM_MONTH` `:358`, `:369`, `:371` | CRASH | string `YYYY-MM`; `|| '-01'` cast for deactivation compare |
| total_patients | `TOTAL_PATIENTS` `:359` | CRASH | |
| total_claim_lines | `TOTAL_CLAIM_LINES` `:360` | CRASH | |
| total_paid | `TOTAL_PAID` `:338`, `:361` | CRASH | row+dollar conservation asserted `:384-387` |

`clean_data.py:402-411` carries `_SPENDING_ALIASES` with variants (`billing_provider_npi`, `service_month`, …) but integrate does NOT use it (F-01). Emits `processed/spending_fact.parquet` with the canonical names every downstream SQL literal expects: `billing_npi, servicing_npi, hcpcs_code, service_month, total_patients, total_claim_lines, total_paid, provider_state, entity_type, taxonomy_code, …` (`:352-373`). Cutoff: none here; the export writes `interim/spending_asof_<cut>.parquet` via `asof_billing.write_asof_spending` (`export:2592-2605`).

### 1b. NPPES.csv — `NPPES_COLS` (`integrate.py:57-70`), resolver `_resolve_columns`

| field | accepted header (one candidate each) | if missing | notes |
|---|---|---|---|
| npi | `NPI` | RAISE (`qa.require` `:232-233`) | Luhn-quarantined `:238` |
| entity_type | `Entity Type Code` | RAISE (`:232-233`) | |
| org_name | `Provider Organization Name (Legal Business Name)` | CRASH `:246` (`df.get(...,"").fillna` on a str) | |
| last_name / first_name | `Provider Last Name (Legal Name)` / `Provider First Name` | CRASH `:247-248` | |
| taxonomy_code | `Healthcare Provider Taxonomy Code_1` | CRASH `:265` (KeyError) | the peer key for EVERY scheme |
| addr_line1/city/state/zip | `Provider First Line Business Practice Location Address` / `…City Name` / `…State Name` / `…Postal Code` | CRASH `:265` | `_standardize_address` tolerates absence `:255`, the select list does not |
| deactivation / reactivation | `NPI Deactivation Date` / `NPI Reactivation Date` | BLANK (`df.get` → NaT `:258-259`) | `is_active` then True for all |

Note: NO date-of-birth column is read from NPPES, so `provider_dim` never carries `dob` → see F-07 (death master structurally dead). Emits `processed/provider_dim.parquet`.

### 1c. PECOS.csv — `PECOS_COLS` (`:72-83`)

| field | header | if missing | notes |
|---|---|---|---|
| npi | `NPI` | CRASH `:286` | |
| pac_id / enrollment_id | `PECOS_ASCT_CNTL_ID` / `ENRLMT_ID` | CRASH `:294` (KeyError in `df[[...]]`) | strings, leading zeros |
| provider_type_code / provider_type_desc / state | `PROVIDER_TYPE_CD` / `PROVIDER_TYPE_DESC` / `STATE_CD` | CRASH `:309` | `_completeness` guards `:304-305` but the final select does not |
| first_name / last_name / org_name | `FIRST_NAME` / `LAST_NAME` / `ORG_NAME` | BLANK | only used for completeness ranking |
| multiple_npi_flag | `MULTIPLE_NPI_FLAG` | BLANK | unused |

No assertion names the five CRASH fields (F-02). Emits `npi_xwalk.parquet` (CCN↔NPI crosswalk input, ownership resolution) and `pecos_provider.parquet`.

### 1d. Caught.csv (LEIE) — `LEIE_COLS` (`:85-106`)

| field | header | if missing | notes |
|---|---|---|---|
| npi | `NPI` | BLANK (`:498` → column of None) | Luhn-quarantined when present |
| last_name / first_name / busname | `LASTNAME` / `FIRSTNAME` / `BUSNAME` | CRASH `:511-513` (`df.get(...,"").fillna`) | |
| excl_type | `EXCLTYPE` | **BLANK → `None`** `:507` | the statute `fca_relevance` tiers on; absent = every row "unmapped/weak" silently (F-04) |
| excl_date / reinstate_date | `EXCLDATE` / `REINDATE` (`%Y%m%d`; `0`/`00000000` → NaT) | BLANK (NaT) `:500-506` | excl_date is the as-of key for the graph (`temporal_sources.py:30,68`) and the label |
| address/city/state/zip, upin, general, specialty, waiver_state | `ADDRESS`/`CITY`/`STATE`/`ZIP`/`UPIN`/`GENERAL`/`SPECIALTY`/`WVRSTATE` | BLANK `""` `:519-522` | identifier aids for the name-only resolver |
| dob / waiver_date | `DOB` / `WAIVERDATE` | BLANK (NaT) `:523-524` | |

Emits `processed/exclusions.parquet`; `merge_exclusion_sources` `:530-558` unions the revocations CSV (§5a) with de-dup on `[npi,name_key,excl_type,excl_date]`.

### 1e. owners/*.csv (PECOS All-Owners) — `OWNER_COLS` `:108-126`, `OWNER_FLAG_COLS` `:129-144`, `OWNER_EXTRA_COLS` `:147-150`

| field | header | if missing | notes |
|---|---|---|---|
| facility_enrollment_id | `ENROLLMENT ID` | CRASH `:470` (KeyError) | the facility→NPI key (`ENRLMT_ID`) |
| owner_pac_id | `ASSOCIATE ID - OWNER` | BLANK `:479` | owner→NPI via unambiguous PAC |
| owner_last_name / owner_first_name | `LAST NAME - OWNER` / `FIRST NAME - OWNER` | CRASH `:454-456` (`.get(...,"").fillna`) | |
| owner_org_name | `ORGANIZATION NAME - OWNER` | CRASH `:457` (`_normalize_name("")`) | |
| owner_type / owner_role | `TYPE - OWNER` / `ROLE TEXT - OWNER` | BLANK | `O`/`I`; role weights `:565-571` |
| association_date | `ASSOCIATION DATE - OWNER` | BLANK (`:461-463`) | the as-of key for owner edges (`temporal_sources.py`); absent = every owner edge survives any freeze |
| pct_ownership, owner_addr_*, owner_dba, facility_pac_id, facility_name | listed `:109-126` | BLANK | |
| 14 boolean flags (+2 NursingOwners extras) | `CORPORATION - OWNER` … `PRIVATE EQUITY COMPANY - OWNER` … | BLANK | `is_private_equity` from `flag_private_equity` (`build_edges.py:145-146`) |

Discovery: every `*.csv` in `preclean/owners/` (`:661-662`); `_facility_type_from_name(stem)` tags the dataset. R1 canonical name `owners/owners.csv` (`preflight.py:142-143`) — but see §4c: the churn adapter needs `<Dataset>_All_Owners_<YYYY.MM.DD>` names (F-23).

---

## 2. Per-NPI CMS PUF adapters (export `_run_npi_adapters`, `export:1501-1897`)

Common mechanics: `_try(name, folder, cols, fn)` `:1522-1569` — `_year_files(folder, max_year)`; newest year first; on `ValueError` fall through to the next file; any other exception → `skipped`. Trends `:1592-1717` re-run the adapter on the adjacent prior-year file (`<col>_yoy`, `<col>_slope`). Vintage: **filename year only** (`vintage_cap = int(cutoff[:4])`, `export:2490`); no adapter below reads an in-file date.

### 2a. `src/ingest_cms/partb.py` — `PARTB_COLS` `:25-31`; folder `preclean/partb/` (R1 `partb/partb.csv` `preflight.py:56`)

| field | accepted headers | if missing | notes |
|---|---|---|---|
| npi | `Rndrng_NPI`, `RNDRNG_NPI`, `npi`, `NPI` | RAISE `:41-44` | |
| hcpcs | `HCPCS_Cd`, `HCPCS_CODE`, `hcpcs_code` | RAISE | |
| services | `Tot_Srvcs`, `TOT_SRVCS`, `line_srvc_cnt` | RAISE | |
| benes | `Tot_Benes`, `TOT_BENES`, `bene_unique_cnt` | BLANK → NaN `:61-62` | per-bene ratios NaN (honest) |
| avg_allowed | `Avg_Mdcr_Alowd_Amt`, `AVG_MDCR_ALOWD_AMT`, `average_Medicare_allowed_amt` | **BLANK → 0.0** `:52-56` | `allowed_per_bene` = 0 for everyone, `code_concentration_hhi` NaN (F-10) |

Emits `em_high_level_share`, `em_level_mean` → **upcoding**; `services_per_bene` → **overutilization**; `allowed_per_bene` → **payment_outlier**; `code_concentration_hhi` → **single_service_mill**; `total_services/benes/allowed` (raw aggregates). Trends on `em_high_level_share`, `em_level_mean` (`export:1593`).

### 2b. `src/ingest_cms/partd.py` — `PARTD_COLS` `:28-37`; folder `preclean/partd/`

| field | accepted headers | if missing | notes |
|---|---|---|---|
| npi | `Prscrbr_NPI`, `PRSCRBR_NPI`, `npi`, `NPI` | RAISE `:44-47` | |
| claims | `Tot_Clms`, `TOT_CLMS`, `total_claim_count` | RAISE | |
| cost | `Tot_Drug_Cst`, `TOT_DRUG_CST`, `total_drug_cost` | RAISE | |
| brand_name / generic_name | `Brnd_Name`,`BRND_NAME`,`drug_name` / `Gnrc_Name`,`GNRC_NAME`,`generic_name` | **BLANK → ""** `:58-60` | `is_brand` False everywhere → `brand_generic_cost_ratio` 0; `kickback_co_occurrence` (§2e) matches nothing; `nadac.brand_premium_by_prescriber` RAISES on generic_name `nadac.py:159-161` |
| day_supply | `Tot_Day_Suply`, `TOT_DAY_SUPLY`, `total_day_supply` | BLANK → per-claim fallback `:66-70` (documented) | |
| opioid_claims | `Opioid_Tot_Clms`, `OPIOID_TOT_CLMS` | SKIP (column omitted `:88-95`) | present only in the by-provider SUMMARY file |

Emits `high_cost_drug_share` → **drug_outlier**; `controlled_substance_share` (summary file only) → **drug_outlier**; `brand_generic_cost_ratio` (raw aggregate); `total_claims`, `total_cost`. Also the raw frame feeds §2e kickback and §2f NADAC name-grain.

### 2c. `src/ingest_cms/dmepos.py` — `DMEPOS_COLS` `:24-30`; folder `preclean/dmepos/`

| field | accepted headers | if missing | notes |
|---|---|---|---|
| npi | `Rfrg_NPI`, `RFRG_NPI`, `REFERRING_NPI`, `npi`, `NPI` | RAISE `:37-40` | |
| hcpcs | `HCPCS_Cd`, `HCPCS_CODE`, `hcpcs_code` | RAISE (the by-referring SUMMARY file lacks it → next-year fallback, `export:1535-1538`) | |
| services | `Tot_Suplr_Srvcs`, `TOT_SUPLR_SRVCS`, `number_of_supplier_services` | RAISE | |
| avg_allowed | `Avg_Suplr_Mdcr_Alowd_Amt`, `AVG_SUPLR_MDCR_ALOWD_AMT`, `avg_supplier_medicare_allowed_amt` | BLANK → 0 `:48-49` | shares NaN (where total>0) — honest; but `total_allowed`=0 feeds §3d referrer check as 0 dollars |

Emits `dme_high_cost_item_share` → **dme_ring**; `dme_code_concentration` (raw aggregate); `total_allowed` → §3d `dme_ineligible_referred_dollars`.

### 2d. `src/ingest_cms/opioid.py` — `OPIOID_COLS` `:26-33`; folder `preclean/opioid/`

| field | accepted headers | if missing | notes |
|---|---|---|---|
| npi | `Prscrbr_NPI`, `PRSCRBR_NPI`, `npi`, `NPI` | RAISE `:39-41` | |
| opioid_claims | `Opioid_Tot_Clms`, `OPIOID_TOT_CLMS`, `opioid_tot_clms` | RAISE | |
| total_claims | `Tot_Clms`, `TOT_CLMS`, `tot_clms` | BLANK → NaN `:48-54` → `opioid_claim_share` NaN | honest |
| opioid_la_claims | `Opioid_LA_Tot_Clms`, `OPIOID_LA_TOT_CLMS`, `opioid_la_tot_clms` | BLANK → NaN → `opioid_long_acting_share` NaN | honest |
| opioid_rate | `Opioid_Prscrbr_Rate` | unused | |

Emits `opioid_claim_share`, `opioid_long_acting_share` → **pill_mill**.

### 2e. `src/ingest_cms/openpayments.py` — `OP_COLS` `:32-57`; folder `preclean/open_payments/`

| field | accepted headers | if missing | notes |
|---|---|---|---|
| npi | `Covered_Recipient_NPI`, `COVERED_RECIPIENT_NPI`, `Physician_NPI`, `NPI` | RAISE `:64-67` | PY2021+ only |
| manufacturer | `Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name` (+UPPER) | RAISE | |
| amount | `Total_Amount_of_Payment_USDollars` (+UPPER) | RAISE | |
| payment_date | `Date_of_Payment`, `DATE_OF_PAYMENT`, `Payment_Date` | BLANK (→ `pay_month` None in `medicaid_ndc.medicaid_kickback` `:541-545`; `px_post_payment_lift` NULL, no cutoff on payments) | not used by `compute_openpayments_metrics` |
| product, product_2..5 | `Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_N`, `Name_of_Associated_Covered_Drug_or_BiologicalN`, `Product_Category_or_Therapeutic_Area_1` | **BLANK** — `kickback_co_occurrence` logs a `logging.warning` `:159-164` and returns 0 for everyone `:205` | (F-11) |

Emits `op_payment_concentration` → **pharma_kickback**; `op_total_dollars`, `n_manufacturers` (raw); `kickback` block (`export:1722-1736`): `op_payment_utilization_corr` → **pharma_kickback** (prescribers with no OP payments score 0.0, `:205`, not NULL). The OP raw is re-read via `_read_latest(..., max_year)` `:1728`.

### 2f. NADAC name-grain (`src/ingest_cms/nadac.py`) — `NADAC_COLS` `:28-34`; file `_asof_pick(preclean/nadac, "nadac.csv", cutoff=f"{max_year}-12")` `export:1745-1746`

| field | accepted headers | if missing | notes |
|---|---|---|---|
| ndc | `NDC`, `ndc` | RAISE in `compute_nadac_reference` `:42-44`; not needed by `name_price_index` | |
| per_unit | `NADAC Per Unit`, `nadac_per_unit`, `NADAC_Per_Unit` | RAISE (both functions) | |
| description | `NDC Description`, `ndc_description`, `Description` | RAISE in `name_price_index` `:127-129`; `opioid_ndc_flags` returns EMPTY silently `medicaid_ndc.py:263-264` | |
| classification | `Classification for Rate Setting`, `classification`, `Pharmacy Type Indicator` | **BLANK → ""** `:48-50`, `:132-134` | no row is `B`/`G` → `generic_per_unit`/`brand_per_unit` empty → `nadac_brand_premium_share` 0 for all; `px_brand_paid_share` 0 (§2g) |

In-file dates (`Effective Date`, `As of Date`) are NOT read; vintage is filename-only. Emits `nadac_brand_premium_share` (clean extra feature, `export:716`), `nadac_matched_share` (raw aggregate).

### 2g. `src/ingest_cms/medicaid_ndc.py` — `NDC_COLS` `:46-61`, `REQUIRED` `:62`; resolver `resolve_ndc_columns` `:77-84` (RAISE naming the fields; optional → `NULL` `:87-100`). Raw folder `preclean/medicaid_ndc/*.csv` (all csvs union_by_name; header resolved from `files[0]` only `:139`). The export reads `processed/ndc_fact.parquet`, or under a freeze builds `processed/ndc_fact__asof_<YYYY-MM>.parquet` (`export:1778-1782`; `service_month <= cutoff` `medicaid_ndc.py:143-144`).

| field | accepted headers | if missing | notes |
|---|---|---|---|
| billing_npi | `BILLING_PROVIDER_NPI_NUM`, `BILLING_NPI`, `PHARMACY_NPI`, `BILLING_PROVIDER_NPI`, `DISPENSING_PROVIDER_NPI_NUM`, `NPI` | RAISE | digits only; CLEAN_ROW needs len 10 `:116-117` |
| ndc | `NATIONAL_DRUG_CODE`, `NDC`, `NDC_CODE`, `NDC_NUM`, `PRODUCT_NDC` | RAISE | 11-digit; labeler5 = first 5 |
| service_month | `CLAIM_FROM_MONTH`, `SERVICE_MONTH`, `CLAIM_MONTH`, `FILL_MONTH`, `MONTH` | RAISE | the cutoff column |
| total_paid | `TOTAL_PAID`, `TOTAL_PAID_AMOUNT`, `TOTAL_AMOUNT_REIMBURSED`, `PAID_AMOUNT` | RAISE | conservation asserted `:162-164` |
| prescribing_npi | `PRESCRIBING_PROVIDER_NPI_NUM`, `PRESCRIBER_NPI`, `PRESCRIBING_NPI`, `PRESCRIBING_PROVIDER_NPI`, `SERVICING_PROVIDER_NPI_NUM`, `PRSCRBR_NPI` | BLANK → NULL | every `px_*` column empty, `rx_missing_prescriber_share` = 1, kickback empty — report lists "(absent)" `:633-634` only |
| total_patients | `TOTAL_UNIQUE_BENEFICIARIES`, `TOTAL_PATIENTS`, `TOTAL_BENEFICIARIES`, `TOTAL_RECIPIENTS`, `PATIENT_COUNT` | BLANK → NULL | `rx_claims_per_patient_month`, `px_paid_per_patient_month` NULL |
| total_claim_lines | `TOTAL_CLAIM_LINES`, `TOTAL_CLAIMS`, `TOTAL_PRESCRIPTIONS`, `CLAIM_COUNT`, `TOTAL_RX` | BLANK → NULL | `rx_spread_*` NULL (`total_claim_lines > 0` filter `:308`), `px_opioid_claims_share` NULL |
| units | `TOTAL_UNITS`, `UNITS`, `UNITS_REIMBURSED`, `QUANTITY`, `TOTAL_QUANTITY` | BLANK → NULL (documented: `ndc_claims.parquet` 0 rows `:208-221`) | |
| days_supply | `TOTAL_DAYS_SUPPLY`, `DAYS_SUPPLY` | BLANK → NULL | unused by features |
| provider_state | `PROVIDER_STATE`, `BILLING_PROVIDER_STATE`, `STATE`, `STATE_CODE` | BLANK → NULL | |

Companion inputs inside the export block (`:1783-1831`): deactivations via `_first_existing(preclean/nppes_deactivation, "deactivation.csv","deactivation.xlsx","*.zip","*.xlsx","*.csv")` `:1787-1788`; exclusion events = `graph/nodes/exclusion_nodes.parquet[npi, excl_date]` `:2523-2531`; NADAC reference `_asof_pick(..., fallback_newest=True)` `:1798` (post-cutoff edition allowed as a NAME lookup, logged `:1801-1803`); FDA labelers `preclean/fda_ndc/` (`*.txt` or `*.zip`) `:1815-1818` (§2i).
Emits `rx_*` (pharmacy grain: `rx_paid_total, rx_claims, rx_patient_months, rx_paid_per_claim, rx_claims_per_patient_month, rx_distinct_prescribers, rx_prescriber_hhi, rx_top_prescriber_share, rx_missing_prescriber_share, rx_self_prescribed_share, rx_prescriber_hhi_ext, rx_top_prescriber_share_ext, rx_external_paid_share, rx_distinct_ndc, rx_ndc_hhi, rx_excluded_prescriber_share, rx_deactivated_prescriber_share, rx_opioid_paid_share, rx_opioid_la_share, rx_spread_proxy, rx_spread_coverage, rx_spread_top_share`) and `px_*` (prescriber grain: `px_paid_total, px_claims, px_patient_months, px_paid_per_patient_month, px_claims_per_patient_month, px_paid_per_claim, px_distinct_pharmacies, px_pharmacy_hhi, px_top_pharmacy_share, px_self_billed_share, px_pharmacy_hhi_ext, px_top_pharmacy_share_ext, px_distinct_ndc, px_brand_paid_share, px_opioid_claims_share, px_opioid_paid_share, px_opioid_la_share, px_paid_mfr_share, px_paid_mfr_count, px_post_payment_lift, px_op_payment_total`). Scheme feeds: `rx_spread_proxy`, `rx_spread_top_share` → **drug_outlier**; `px_opioid_paid_share`, `px_opioid_la_share`, `rx_opioid_paid_share` → **pill_mill**; `px_paid_mfr_share`, `px_post_payment_lift` → **pharma_kickback**. All numeric `rx_*/px_*` are trainable (partition group `medicaid_ndc`).

### 2h. `src/ingest_cms/medicaid_enrollment.py` — `ENROLL_COLS` `:50-71`, `REQUIRED` `:72`; resolver `resolve_enrollment_columns` `:89-95` (RAISE); status/reason both absent → loud WARNING `:117-126` (features build, label source empty). Raw folder `preclean/medicaid_enrollment/*.csv`; export reads `processed/medicaid_enrollment_segments.parquet` (`export:1867`), computes features in DuckDB `enrollment_features_from_parquet` (`:561-633`).

| field | accepted headers (first present wins) | if missing | notes |
|---|---|---|---|
| npi | `NPI`, `PROVIDER_NPI_NUM`, `PROVIDER_NPI`, `BILLING_PROVIDER_NPI_NUM`, `NPI_NUM` | RAISE | |
| state | `STATE`, `STATE_CODE`, `STATE_CD`, `PROVIDER_STATE`, `ENROLLMENT_STATE`, `SUBMITTING_STATE` | RAISE | `mcd_primary_state` (state fill `export:496-505`) |
| start_date | `ENROLLMENT_START_DATE`, `SEGMENT_START_DATE`, `ENRL_START_DT`, `START_DATE`, `EFFECTIVE_DATE`, `BEGIN_DATE`, `PROVIDER_ENROLLMENT_EFF_DATE` | RAISE | parsed `%Y-%m-%d`, `%m/%d/%Y`, `%Y%m%d`, `%Y-%m-%d %H:%M:%S` `:132` |
| end_date | `ENROLLMENT_END_DATE`, `SEGMENT_END_DATE`, `ENRL_END_DT`, `END_DATE`, `TERMINATION_DATE`, `PROVIDER_ENROLLMENT_END_DATE` | BLANK → NULL | all segments open; `mcd_ended_for_cause` depends on status instead |
| status | `ENRL_STATUS_DESC`, `ENROLLMENT_STATUS`, `STATUS`, `STATUS_CODE`, `ENROLLMENT_STATUS_CODE`, `PROVIDER_STATUS`, `ENRL_STATUS_CD` | WARN (when reason also absent) → label empty | `BENIGN_END` regex `:77-82`; `LABEL_WORTHY` `:291-292` |
| reason | `TERMINATION_REASON`, `END_REASON`, `STATUS_REASON`, `DISENROLLMENT_REASON`, `ENROLLMENT_END_REASON`, `REASON` | WARN / NULL | the July-2026 file has none |
| program | `ENRL_PLAN_DESC`, `PROGRAM`, `ENROLLMENT_TYPE`, `ENROLLMENT_PROGRAM`, `PROGRAM_TYPE`, `MEDICAID_CHIP`, `ENRL_PLAN_CD` | BLANK | unused by features |
| provider_type | `PRVDR_TYPE_DESC`, `PROVIDER_TYPE`, `PROVIDER_TYPE_CODE`, `PROVIDER_CLASSIFICATION`, `CLASSIFICATION`, `PROVIDER_TYPE_DESC`, `PRVDR_TYPE` | BLANK | appended to `excl_type` as `[type]` |
| taxonomy | `TAXONOMY`, `TAXONOMY_CODE`, `PROVIDER_TAXONOMY` | BLANK | unused |

Cutoff: `start_date <= cutoff-month-end`; `end_date > cutoff` → open (`:570-573`, `:647-650`); placeholder ends (> today) nulled (`:586`, `:661`). **`paid_after_enrollment_end` is joined to `processed/spending_fact.parquet` (the FULL fact), not the as-of fact** (`export:1873-1875`) → F-05. Emits `mcd_states_enrolled, mcd_primary_state, mcd_segments, mcd_tenure_months, mcd_enrolled_months, mcd_gap_share, mcd_open_segments, mcd_ended_for_cause, mcd_paid_after_end, mcd_months_after_end, mcd_paid_after_end_share` (trainable, group `medicaid_enrollment`; no scheme weight); label rows via the CLI → `processed/exclusions_medicaid_enrollment.parquet` (`excl_type = mcd_enroll:<ST>:<status | reason> [type]`).

### 2i. `src/ingest_cms/fda_ndc.py` — `PRODUCT_COLS` `:33-40`; `load_fda_product` `:75-104` reads `product.txt`, the folder (product.txt else newest `*.zip`), or a zip (inner `*product.txt`); tab-delimited, UTF-8 then Latin-1. R1 `fda_ndc/product.txt|ndc.zip` (`preflight.py:84-87`).

| field | accepted headers | if missing | notes |
|---|---|---|---|
| product_ndc | `PRODUCTNDC`, `productndc`, `PRODUCT_NDC` | RAISE `:102-103` | `labeler-product`, zero-padded 5/4 `:107-114` |
| labeler | `LABELERNAME`, `labelername`, `LABELER_NAME` | RAISE | → `mfr_key` via `manufacturer_key` `:54-67` |
| dea | `DEASCHEDULE`, `deaschedule`, `DEA_SCHEDULE` | BLANK → "" `:131-132` (`is_controlled` 0) | `product_schedule` has no caller in the export |
| proprietary / nonproprietary / substance | listed `:35-38` | BLANK | unused |

Emits (through §2g) `px_paid_mfr_share`, `px_paid_mfr_count`, `px_post_payment_lift`, `px_op_payment_total`.

---

## 3. Org-grain and CCN-grain adapters (export `_run_org_grain_adapters` `:1900-2185`, `_run_ccn_grain` `:2188-2335`)

Spending input: `asof_spending or processed/spending_fact.parquet` (`:1920`). Org frames are broadcast to member NPIs (`_broadcast_org_to_npi` `:255-266`); CCN frames go CCN→NPI (`processed/ccn_to_npi.parquet`, `:2482-2483`) → org (max) → NPI.

### 3a. `src/ingest_cms/nppes_deactivation.py` — `DEACT_COLS` `:32-36`; discovery `_first_existing(preclean/nppes_deactivation, "deactivation.csv","deactivation.xlsx","*.zip","*.xlsx","*.csv")` (`export:1980-1981`, also `:1787`, `:2685`); `load_deactivation` `:49-81` unzips, finds the banner header row (`_find_header_row` `:39-46`: first row containing a cell `NPI`).

| field | accepted headers | if missing | notes |
|---|---|---|---|
| npi | `NPI`, `npi` | RAISE `:86-89` | Luhn |
| deactivation_date | `NPI Deactivation Date`, `Deactivation Date`, `deactivation_date`, `NPPES Deactivation Date` | **SKIP silently** — `df.get` → None → NaT → all rows dropped `:95-98` → export logs "adapter returned no rows" with the wrong reason (F-12) | min date per NPI |

Cutoff: none on the deactivation dates (acceptable: the compare is `service_month >= deact_date` against the as-of fact). Emits org `billing_after_deactivation` → **invalid_identity** (raw-driven, `export:85`; also label-widening `export:587-600` and `LEAKAGE_HARD`); NPI `deact_months_after/paid_after/first_after/last_after` (smoking-gun, `:2685-2693`); the NDC adapter's `rx_deactivated_prescriber_share`.

### 3b. `src/enforcement/death_master.py` — `DMF_COLS` `:30-35`, matched **exact, case-sensitive** (`c in raw.columns` `:40-41`); discovery `_first_existing(preclean/dmf, "dmf.csv", "*.csv")` `export:2000`.

| field | accepted headers | if missing | notes |
|---|---|---|---|
| last | `last_name`, `LAST`, `Last Name`, `lname` | RAISE `:42-43` | |
| dod | `date_of_death`, `DOD`, `death_date`, `DEATH` | RAISE | |
| first | `first_name`, `FIRST`, `First Name`, `fname` | BLANK "" | name key = LAST FIRST |
| dob | `date_of_birth`, `DOB`, `birth_date`, `BIRTH` | BLANK → NaT → every match `low` | `provider_dim` never has `dob` either (§1b) → `billing_after_death` can never be "high" (F-07) |

Emits org `billing_after_death` → **invalid_identity** (raw-driven); high-confidence only `:107-110`.

### 3c. NADAC drug spread (org) — `nadac.compute_nadac_reference` + `drug_spread_anomaly` `:63-96`; discovery `_asof_pick(preclean/nadac, "nadac.csv", cutoff)` `export:2021` + `processed/ndc_claims.parquet` `:2022` (written by `medicaid_ndc.write_ndc_claims_slice` `:208-221` or `claim_slices --kind ndc`). Required claim columns: `billing_npi, ndc, units, billed_cost` (units/billed_cost BLANK → 0 `:76-77`). **`ndc_claims.parquet` is not as-of filtered by the export** (F-06). Emits `drug_spread_anomaly` → **drug_outlier**.

### 3d. `src/ingest_cms/order_referring.py` — `ELIGIBLE_COLS` `:27-33`

Discovery: org-grain `_asof_pick(preclean/order_referring, "order_referring.csv", cutoff)` `export:2036-2037` + `processed/referred_claims.parquet` `:2038` (not as-of filtered); referrer-grain fallback `_first_existing(preclean/order_referring, "order_referring.csv", "*.csv")` `export:2563-2564` — **no cutoff, alphabetically first csv** (F-13).

| field | accepted headers | if missing | notes |
|---|---|---|---|
| npi | `NPI`, `npi` | RAISE `:40-43` | |
| dme | `DME`, `dme`, `Eligible to order and refer DME` | **BLANK → 0 for every NPI** `:50-53` → every referrer "ineligible": `dme_ineligible_referrer`=1 and `dme_ineligible_referred_dollars`=total for ALL (`:69-79`); `ineligible_referral_share`=1.0 for every org (`:110-113`) (F-03) | `_YES = {Y, YES, TRUE, 1}` |
| partb / hha / pmd | `PARTB`/`HHA`/`PMD` (+ long names) | BLANK → 0 | unused by the export (dme only) |

Emits org `ineligible_referral_share` and NPI `dme_ineligible_referrer`, `dme_ineligible_referred_dollars` → **dme_ring** (all three in `LEAKAGE_ADJACENT` `export:130-133`).

### 3e. `src/ingest_cms/hrsa_340b.py` — `OPAIS_COLS` `:94-106`; `load_opais` `:53-92` (3-worksheet xlsx: Contract Pharmacies sheet, `State` merged from Covered Entities on `340B ID`/`ID_340B`; banner header promoted `:33-50`); discovery `_first_existing(preclean/hrsa_340b, "opais.xlsx","opais.csv","*.xlsx","*.csv")` `export:2053-2054`.

| field | accepted headers | if missing | notes |
|---|---|---|---|
| name | `Entity Name`, `entity_name`, `Covered Entity Name`, `Name` | RAISE `:124-127` | matched to org nodes by `norm_org_name` (name + aliases) |
| begin_date | `Begin Date`, `begin_date`, `Contract Begin Date`, `CP Begin Date`, `Pharmacy Begin Date`, `Start Date` | RAISE under a cutoff `:130-134`; ignored otherwise | row-level as-of |
| term_date | `Term Date`, `term_date`, `Termination Date`, `Contract Termination Date`, `Contract Term Date`, `End Date` | BLANK (arrangement assumed open) `:138-140` | |
| contract_pharmacy | `Contract Pharmacy Name`, `contract_pharmacy_name`, `Pharmacy Name` | **BLANK → ""** `:143-146` → `n_contract_pharmacies` 0 → `contract_pharmacy_concentration` 0 for every matched entity (F-14) | |
| entity_id / entity_type / state | `340B ID`,`ID_340B`,`id_340b`,`Entity ID`,`entity_id` / `Entity Type`,`entity_type`,`Entity Subtype` / `State`,`state`,`Entity State` | BLANK "" | |

Emits org `contract_pharmacy_concentration` → **contract_pharmacy** (`is_340b_covered_entity` computed but dropped at `export:2060`).

### 3f. `src/ingest_cms/saturation.py` — `SATURATION_COLS` `:28-37`; discovery `_first_existing(preclean/saturation, "saturation.csv", "*.csv")` `export:2074`; ZIP→county `_first_existing(preclean/hud, "zip_county.csv","*.csv")` or `preclean/zip_county/` `:2086-2087` (`census_population.zip_to_county`, §5l).

| field | accepted headers | if missing | notes |
|---|---|---|---|
| service | `Type of Service`, `type_of_service`, `TYPE_OF_SERVICE` | RAISE `:86-90` | sector fragments `:43-50` |
| state | `State Name`, `state_name`, `State`, `STATE` | RAISE | full names mapped `:54-68` |
| n_providers / n_benes | `Number of Providers` / `Number of Fee-for-Service Beneficiaries` (+snake) | RAISE | |
| reference_period | literal `reference_period` after normalize `:96-97` (NOT in SATURATION_COLS) | BLANK → all periods pooled (`rp_col is None` → no filter, `:98`) | with a cutoff and no period ≤ cutoff → RAISE `:104-107` |
| fips / county | `State and County FIPS Code`, `State County FIPS Code`, `state_county_fips_code`, `FIPS` / `County Name`, `county_name`, `County` | BLANK "" `:113-115` → county grain silently off, state fallback | |

Emits org `market_saturation_index` → **saturation_fraud** (mapped sectors only; else NaN).

### 3g. Ownership churn — `src/entity_graph/owners_vintage.py` `OWNER_VINTAGE_COLS` `:44-47`; folders `preclean/owners_prior/` + `preclean/owners/` (`export:2119-2120`); edition date from filename `_DATE_RE` `:49` (`2025.10.01`, `2023-06`); dataset from `_DATASET_RE` `:86` (`<Name>_All_Owners`).

| field | header | if missing | notes |
|---|---|---|---|
| facility_enrollment_id | `ENROLLMENT ID` | **SKIP silently** (file ignored `:69-70`, `:100-101`) | |
| owner_pac_id | `ASSOCIATE ID - OWNER` | SKIP silently | |
| (filename date) | `YYYY.MM[.DD]` token | **SKIP silently** (`load_editions` `:123-124`) | a file named `owners.csv` (R1 canonical) never chains (F-23) |

Cutoff: `asof_editions` keeps editions dated ≤ cutoff (`:135-154`), ≥2 per dataset. Emits org `ownership_turnover` → **ownership_integrity**; `n_owner_entries`, `n_owner_exits` (raw). Fallback: `owner_snapshots/` (≥2 snapshots, `export:2159-2174`).

### 3h. `src/ingest_cms/facility.py` — five layouts in `preclean/facility/`

**PBJ** — `PBJ_COLS` `:44-53`; discovery `_asof_files(sorted(glob("pbj*.csv")), cutoff)` `export:2197` (case-sensitive prefix on Linux; all eligible quarters pooled; **no row-level date filter**).

| field | accepted headers | if missing | notes |
|---|---|---|---|
| ccn | `PROVNUM`, `provnum`, `CMS Certification Number (CCN)`, `cms_certification_number_ccn`, `CCN`, `ccn` | RAISE `:164-167` | `_canon_ccn` zero-pads 6 |
| census | `MDScensus`, `MDSCENSUS`, `mdscensus` | RAISE | days with census>0 only |
| hrs_rn | `Hrs_RN`, `HRS_RN`, `hrs_rn` | RAISE | |
| hrs_lpn / hrs_cna | `Hrs_LPN`/`Hrs_CNA` (+case) | **BLANK → 0** `:175-176` → HPRD understated silently (F-15) | |
| workdate | `WorkDate`, `WORKDATE`, `workdate`, `work_date` | SKIP (weekend gap empty `:407-408`); never used as an as-of filter (F-17) | |
| state | `STATE`, `State`, `state` | BLANK "" | peer ladder `(size_band, state)` collapses |

Emits CCN `pbj_understaffing` (negated HPRD), `pbj_weekend_understaffing` → **worthless_services**.

**Hospice (Care Compare provider measures, long)** — `HOSPICE_COLS` `:55-72`; discovery: every `hospice.csv`, `hospice*.csv`, `Hospice_Provider*.csv`, `*Hospice*.csv` (`export:2222-2225`) then `pick_edition` `:238-258` (frozen: earliest edition dated on/after the cutoff; `edition_date` `:225-235` reads `YYYYMM`, `YYYY-MM`, `MonYYYY`).

| field | accepted headers | if missing | notes |
|---|---|---|---|
| ccn | `CMS Certification Number (CCN)`, `cms_certification_number_ccn`, `CCN`, `ccn`, `PROVNUM` | RAISE `:273-276` | |
| score | `Score`, `score`, `Measure Value`, `measure_value` | RAISE | non-numeric quarantined |
| end_date (or date_range) | `End Date`, `end_date`, `Measure End Date`, `measure_end_date`, `MEASURE_END_DATE`, `Measure Date Range End` / `Measure Date Range`, `measure_date_range`, `MEASURE_DATE_RANGE`, `Date Range` (last date token `:279-283`) | RAISE under a cutoff (`_asof_filter` `:196-214`); ignored otherwise | row-level as-of |
| measure_code / measure_name | `Measure Code`/`measure_code`/`MEASURE_CODE`, `Measure Name`/… | BLANK "" → no row matches any pattern → every hospice metric SKIP (empty) | `H_012_03/04_OBSERVED` selection `:296-302` |

Emits CCN `hospice_live_discharge_rate`, `hospice_nf_care_share`, `hospice_gip_share`, `hospice_visit_gap`, `hospice_nurse_minute_deficit`, `hospice_spend_per_bene`, `hospice_dementia_share` → **hospice_ineligibility** (panel `:101-116`).

**NH ownership** — `NH_OWNERSHIP_COLS` `:118-126`; discovery `_asof_newest(preclean/facility, ("NH_Ownership*.csv","*Ownership*.csv"), cutoff)` `export:2248` (filename-dated only; **no row-level filter although the file carries an association date**, F-18). NOT in R1.

| field | accepted headers | if missing | notes |
|---|---|---|---|
| ccn | `CMS Certification Number (CCN)`, `cms_certification_number_ccn`, `Federal Provider Number`, `PROVNUM`, `CCN`, `ccn` | RAISE `:348-350` | |
| owner_name | `Owner Name`, `owner_name` | BLANK "" → `nh_owner_count` 0 for all (silent) | |
| stake | `Ownership Percentage`, `ownership_percentage` (`NN%` extracted) | BLANK → `nh_owner_concentration` NaN | |
| role / owner_type | listed `:121-123` | unused | |

Emits `nh_owner_count`, `nh_owner_concentration` → **ownership_integrity**.

**NH penalties** — `NH_PENALTY_COLS` `:128-134`; discovery `_asof_newest(…, ("NH_Penalties*.csv","*Penalt*.csv"))` `export:2258`. NOT in R1.

| field | accepted headers | if missing | notes |
|---|---|---|---|
| ccn | as above | RAISE `:377-379` | |
| penalty_date | `Penalty Date`, `penalty_date`, `PENALTY_DATE` | RAISE under cutoff `:381-382`; ignored otherwise | |
| fine | `Fine Amount`, `fine_amount`, `FINE_AMT` | BLANK → 0 → `nh_fine_total` = log1p(0) = 0 silently | |
| penalty_type | `Penalty Type`, `penalty_type` | unused | |

Emits `nh_penalty_count`, `nh_fine_total` → **worthless_services**.

**Deficiencies / health citations** — `DEFICIENCY_COLS` `:74-84`; discovery `_asof_newest(…, ("deficiencies.csv","*deficienc*.csv","NH_HealthCitations*.csv","*Citations*.csv","*citation*.csv"))` `export:2270-2273`.

| field | accepted headers | if missing | notes |
|---|---|---|---|
| ccn | `CMS Certification Number (CCN)`, `cms_certification_number_ccn`, `Federal Provider Number`, `federal_provider_number`, `PROVNUM`, `CCN`, `ccn` | RAISE `:440-442` | |
| survey_date | `Survey Date`, `survey_date`, `SURVEY_DATE`, `Inspection Date`, `inspection_date`, `Citation Date`, `Health Survey Date` | RAISE under cutoff `:443-447` | |
| severity | `Scope Severity Code`, `scope_severity_code`, `Scope Severity`, `SCOPE_SEVERITY_CODE` | BLANK → weight 1 (weighted == count) `:455-456` | documented |

Emits `deficiency_count`, `deficiency_severity_weighted` → **worthless_services**.

All five are percentile-ranked in `facility_peer_percentiles` `:465-497` (`size_band × state` ladder; `avg_daily_census` only exists for PBJ, so the other layouts rank in `size_band="all"` × state) and rolled CCN→NPI→org by `rollup_ccn_to_org` `:500-516` (max per org). `--skip-sources facility` also skips hcris and pos (`export:2177-2184`).

### 3i. `src/ingest_cms/hcris.py` — `HCRIS_COLS` `:48-59`; discovery `sorted((preclean/hcris).glob("*.csv"))` `export:2295` (top level, `.csv` only, **no cutoff of any kind**; F-19); `load_hcris` reads with `pd.read_csv(f, dtype=str)` `:111`, NOT `read_csv_text` (no Latin-1/BOM tolerance; F-20).

| field | accepted headers | if missing | notes |
|---|---|---|---|
| ccn | `PRVDR_NUM`, `prvdr_num`, `CCN`, `ccn`, `Provider CCN` | RAISE `:65-67` | |
| total_costs | `TOTAL_COSTS`, `total_costs`, `Total Costs`, `Total Cost` | **BLANK → 0** `:72-76` → every ratio NaN/0 silently | also the size proxy `:133` |
| total_charges | `TOTAL_CHARGES`, `total_charges`, `Total Charges`, `Combined Outpatient + Inpatient Total Charges`, `Total Episodes-Total Charges` | BLANK → 0 → `cost_to_charge_ratio` NaN | |
| admin_costs | `ADMIN_COSTS`, `admin_costs`, `G&A Costs`, `GA_COSTS`, `Overhead Non-Salary Costs` | BLANK → 0 → `admin_cost_share` 0 (silent) | |
| related_party_costs | `RELATED_PARTY_COSTS`, `related_party_costs`, `Related Org Costs` | BLANK → 0 (documented: public files lack it) | |
| state | `STATE`, `state`, `PRVDR_STATE`, `State Code` | BLANK "" | |
| (Fiscal Year Begin/End Date) | not read | — | R2 POLICY claims `row_asof ["Fiscal Year Begin Date"]` "wired" (`readiness_audit.py:56`) — it is NOT wired (F-19) |

Emits CCN `hcris_cost_anomaly` (max of three one-sided percentiles) → **cost_report_fraud**.

### 3j. `src/ingest_cms/pos.py` — `POS_COLS` `:32-40`; discovery `_asof_files(sorted((preclean/pos).rglob("*.csv")), cutoff)` `export:2314` (every csv under pos/, filename-dated; **no row-level date**, the POS certification/termination dates are not read; F-17).

| field | accepted headers | if missing | notes |
|---|---|---|---|
| ccn | `PRVDR_NUM`, `prvdr_num`, `CMS Certification Number (CCN)`, `cms_certification_number_ccn`, `CCN`, `ccn` | RAISE `:46-48` (per-file; a stray csv is logged and skipped `export:2318-2322`) | |
| bed_count | any raw column matching `bed_cnt$` (row-wise max `:55-59`), else `BED_CNT`, `bed_cnt`, `CRTFD_BED_CNT`, `crtfd_bed_cnt`, `Number of Certified Beds`, `bed_count` | BLANK → NaN | |
| facility_type / state | `GNRL_FAC_TYPE_CD`,`gnrl_fac_type_cd`,`Provider Category`,`facility_type` / `STATE_CD`,`state_cd`,`PRVDR_STATE`,`State`,`state` | BLANK "" | |

Emits org `pos_bed_count` (raw aggregate; institutional damping `scheme_subscores.py:208-210`) → `capacity_mismatch` = org `service_volume` / beds (`export:216-227`, `:532-535`) → **worthless_services**.

---

## 4. Reference tables and spending-fact-derived blocks that read a raw file

### 4a. `src/ingest_cms/sector_schemes.py` + `preclean/hcpcs_time/`
Spending literals in SQL `:53-62`: `billing_npi, hcpcs_code, service_month, total_paid, total_claim_lines, total_patients` (the integrate contract). Minutes map: `_first_existing(preclean/hcpcs_time, "hcpcs_minutes.csv", "*.csv")` `export:2717`; SQL literals `hcpcs`, `minutes` `:88-92`, **no resolver**; any other header → exception → the WHOLE block (NEMT + BH + impossible-day) is skipped (`export:2728`; F-21). R1 `hcpcs_time/hcpcs_minutes.csv` (`preflight.py:148-151`); `capacity.py:457` and `pi_facts.py:664` use `hcpcs_minutes*.csv` instead. Emits `nemt_paid_share`, `nemt_lines_per_patient` → **nemt_fraud**; `bh_paid_share`, `bh_lines_per_patient` → **behavioral_health**; `time_minutes_per_day` → **impossible_day**. Cutoff: the as-of fact (`export:2716`).

### 4b. `src/ingest_cms/hcpcs_time_convert.py` (CLI only) — `CODE_COLS` `:30`, `INTRA_COLS` `:31-32`, `TOTAL_COLS` `:33`; exact lower-case match `_pick` `:36-38`; RAISE `:46-50`. Output `hcpcs,minutes`.

### 4c. `src/ingest_cms/mue.py` — substring hints `_CODE_HINTS` `:52` (`hcpcs`, `cpt`), `_VALUE_HINTS` `:53-54` (`mue value`, `mue_value`, `practitioner services mue`, `units`, `mue`; never a column containing `rationale`/`indicator`); RAISE `:75-77`. Discovery `preclean/reference/mue/*.csv|*.parquet` `export:2782-2784` (NOT in R1; R2 POLICY has a `mue` key that is never audited; F-25). Fact columns resolved by lower-case exact `:118-127` (`billing_npi|npi`, `hcpcs_code|hcpcs|hcpcs_cd`, `service_month|claim_from_month|month`, `total_claim_lines|claim_lines|lines`, `total_paid|net_paid|paid`, `total_patients|patients|total_benes|benes` optional → one-patient fallback logged `:131-133`, `servicing_npi|rendering_npi` optional). Emits `mue_violation_share` → **impossible_day**; `mue_violation_dollars`, `n_mue_violation_cells`, `worst_mue_ratio`. Cutoff: as-of fact; MUE table static.

### 4d. `src/ingest_cms/nucc_taxonomy.py` — `NUCC_COLS` `:29-34`, `CROSSWALK_COLS` `:35-40`; `_resolve` is **exact, case-sensitive** (`c in raw.columns` `:43-45`); RAISE `:52-53`, `:70-71` (caught → "[nucc] skipped" `export:1089-1090`). Files: exact `preclean/nucc/nucc_taxonomy.csv` + optional `specialty_crosswalk.csv` (`export:1074`, `:1083`; no glob). Headers: `Code`/`code`/`taxonomy_code`, `Grouping`, `Classification` (required), `Specialization`; crosswalk `MEDICARE SPECIALTY CODE`/`Medicare Specialty Code`/`specialty_code`, `PROVIDER TAXONOMY CODE`/… Emits `peer_group_key`, `nucc_grouping`, `nucc_classification` (the peer ladder's fallback level, `export:288-290`) — affects EVERY `__peerpct` and therefore every subscore.

### 4e. `src/model_a/address_grounding.py` + `preclean/usps/cmra.csv`
`load_cmra_reference` `:48-56` expects columns named exactly `line1, city, state, zip` (no resolver; docstring: "rename your source's columns"); `_standardize_address` (`clean_data.py:377-393`) substitutes "" for any missing column, so a mis-headed file yields an EMPTY set and `addr_is_cmra` is silently never emitted (`:96-97`; F-22). Discovery `_first_existing(preclean/usps, "cmra.csv", "*.csv")` `export:2866`; the block is NOT wrapped in try/except (`:2855-2881`), so any other error there aborts the export. Address inputs come from `processed/provider_dim.parquet` (today's NPPES, `:2871`) even on a frozen run (vintage class `current_state`, `:799-801`). Emits `addr_is_mailbox, addr_provider_count, addr_shared, addr_is_cmra, addr_distinct_orgs, addr_cluster_degree, addr_geocoded, addr_no_match` (clean extras; no scheme weight).

### 4f. `src/model_a/capacity.py` (standalone CLI; not in the export)
Inputs: `--spending` (via `pi_facts.billing_facts`; minutes SQL literals `hcpcs`,`minutes` `pi_facts.py:203-207`); `processed/provider_dim.parquet` (`entity_type`, `addr_key`); `graph/npi_to_org.parquet`, `graph/edges/reassigns_to_edges.parquet`; POS `rglob("*.csv")` under `preclean/pos` `:436-446` (no cutoff); crosswalk `processed/ccn_to_npi.parquet` or `ccn_npi_crosswalk.parquet` `:449-452`; NPPES `pl_pfile` (`--practice-locations`; NPI column exact `NPI` `:193`, address column regex `address.*line\s*1|address_1|addr.*1` `:194` — absent → sites=1 silently `:195-196`); `processed/licensed_capacity_by_npi.parquet` `:464-466`; minutes `preclean/hcpcs_time/hcpcs_minutes*.csv` `:457`. Emits `capacity.csv` / `CAPACITY.md` (flags `impossible_hours`, `long_hours`, `patients_per_clinician`, `occupancy_over_100`, `capacity_not_in_record`). Not a matrix feature.

### 4g. `src/ingest_cms/licensed_capacity.py` (CLI → `processed/licensed_capacity.parquet` + `_by_npi.parquet`) — `ROSTER_COLS` `:45-81` (normalized-exact via `_resolve_columns`, many spellings); folder `preclean/state_capacity/<ST>_<what>.csv|xlsx` or `<ST>/…` (`merge_rosters` `:194-235`; state from the `^[A-Z]{2}[_-]` prefix or parent folder; otherwise skipped with a printed reason).

| field | if missing | notes |
|---|---|---|
| capacity | RAISE `:149-151` | numeric stripped; rows with ≤0 dropped `:190` |
| facility_name (or facility_id + link table) | RAISE `:149-151`, `:164-166` | |
| addr_line1 / city / zip / state | BLANK "" → no `addr_key` (name+state match only `:189`) | |
| license_type / license_number / status / facility_id / attribute_type / attribute_value | BLANK "" | long-format NY bed rows summed when `attribute_type` + `facility_id` present `:122-136` |

NOT in R1 (R3 knows the folder, `vintage_inspect.py:451`).

---

## 5. Graph-side and enforcement readers (consumed via `processed/*.parquet` → `python -m src.entity_graph`)

Graph loader `src/entity_graph/__main__.py:48-89`: required `provider_dim.parquet`; optional `npi_xwalk`, `owner_edges`, `exclusions`, `reassignment`; **every `processed/exclusions_*.parquet` is concatenated with NO de-duplication** `:63-69` (F-26); reassignment raw csv from `preclean/reassignment/*.csv` (first file only, `:76-85`); docgraph auto-discovered `preclean/docgraph/*.csv*` (first sorted, `:98`). As-of: `point_in_time_tables` filters exclusions on `excl_date` and owner edges on `association_date` (`temporal_sources.py:30,68`); `--asof-nppes` freezes addresses (§5i). Exclusion nodes keep `npi, entity_name, name_key, excl_type, excl_date, reinstate_date, currently_active, state, city, zip, upin, dob, general, specialty, listed_date` when present (`build_nodes.py:115-118`). The export's label reads NPI-matched nodes with `len(npi) >= 10` only — no Luhn (`export:1134`).

### 5a. `src/enforcement/medicare_revocations.py` — `REVOCATION_COLS` `:30-41`; input `preclean/revocations/revoked_providers.csv` (integrate default `:667-670`) and/or CLI → `processed/exclusions_medicare_revocations.parquet`.

| field | accepted headers | if missing | notes |
|---|---|---|---|
| npi | `NPI`, `npi` | BLANK (name-only rows kept `:58-59`, `:83`) | RAISE only if npi AND org_name AND last_name all absent `:52-55` |
| org_name / last_name / first_name | `ORGANIZATION_NAME`,`ORG_NAME`,`Organization Name`,`LEGAL_BUSINESS_NAME`,`PROVIDER_NAME`,`SUPPLIER_NAME`,`NAME` / `LAST_NAME`,… / `FIRST_NAME`,… | BLANK "" | |
| revocation_date | `REVOCATION_EFCTV_DT`, `Revocation Effective Date`, `revocation_efctv_dt` | **BLANK → NaT** `:67` | undated rows survive every as-of filter? — `asof_filter` behaviour on NaT is in temporal_sources (not audited here); the label audit counts undated rows |
| bar_expiration | `REENROLLMENT_BAR_EXPRTN_DT`, `Reenrollment Bar Expiration`, `reenrollment_bar_exprtn_dt` | BLANK → NaT → `currently_active`=1 `:80` | |
| reason | `REVOCATION_RSN`, `Revocation Reason`, `revocation_rsn` | **BLANK → `excl_type = "medicare_revocation"`** with no reason → `fca_relevance` cannot tier (silent) | |

### 5b. `src/enforcement/state_terminations.py` — `FIELD_ALIASES` `:50-76` are **regexes** against whitespace-collapsed lower-case headers (`resolve_fields` `:100-110`, first alias wins, a column is claimed once); header row found by scoring the first 60 rows `:84-97`; best sheet chosen by field quality `:113-134`; folder `preclean/state_terminations/<ST>_*.xlsx|csv` (`load_all` `:288-343`; skips `template`/`smmc` names). Output `processed/exclusions_state_terminations.parquet` (+ report). R1 `state_terminations/GA_terminations.xlsx` (`preflight.py:115-118`).

| field | regex aliases | if missing | notes |
|---|---|---|---|
| org_name or last_name | `^business name`, `provider organization name`, `^excluded entity`, `^individual/entity`, `^name of provider`, `^provider name`, `^name$`, `^entity` / `^last name`, `provider last name`, `^last name or practice name` | SKIP file (logged "no name column found" `:302-304`) | |
| npi | `^npi$`, `^npi/atypical id`, `^npi `, `npi number`, `national provider` | BLANK (name match by unique NPPES name in state `:241-285`; `non_npi_role` gate) | multi-NPI cells exploded `:186-190`; `is_valid_npi` |
| excl_date | `^sancdate`, `sanction date`, `^effective date`, `termination effective date`, `^action date`, `^exclusion date`, `^suspension date`, `^date of action`, `^suspension$`, `^termination date` | BLANK → NaT `:206` | `_dates` `:146-177` (7 formats + Excel serials; sentinels → NaT) |
| reason / reason2 | `^reason`, `reason for`, `termination reason`, `^sanction reason`, `^excltype`, `^excl\.? ?type`, `^exclusion type`, `^cause`, `^grounds`, `^violation`, `^offense`, `^category`, `^sanction type`, `^excluded/terminated`, `^action$`, `^type of action`, `^basis` / `^sanction type`, `^sanction source`, `^type of (sanction|action)`, `^action type`, `^sanction category`, `^basis` | BLANK → `excl_type = state_term:<ST>:unspecified` (tier weak by rule) `:210` | the report lists unused low-cardinality columns `:324-332` |
| reinstate_date | `termination end date`, `^end date`, `reinstat` | BLANK → NaT → active | |
| provider_type | `provider type`, `^general$`, `provider category`, `profession`, `^role$`, `specialty` | BLANK | appended `[type]`; feeds `non_npi_role` |
| state | `^state$`, `^st$` | BLANK → filename prefix | |

### 5c. `src/enforcement/opensanctions.py` — FtM JSON (`entities.ftm.json`, `*.json/*.jsonl`) or `targets.simple.csv` (`load_opensanctions_file` `:112-120`); output `processed/exclusions_opensanctions.parquet`. R1 `opensanctions/targets.simple.csv|entities.ftm.json`; Makefile `OPENSANCTIONS_FILE` `:165-167`.

| field | source property | if missing | notes |
|---|---|---|---|
| npi | `properties.npiCode`, else **`properties.idNumber`** `:65` | BLANK "" | **not Luhn-checked; `idNumber` may be a non-NPI id** (F-27) |
| name | `properties.name` (csv: `name`/`caption`) | SKIP row `:56-58` | |
| excl_type | `opensanctions:<datasets[0] or topics[0]>` | `entity` | never tier-able as a cause (documented) |
| excl_date | FtM `startDate` else `listingDate`; **csv path: `first_seen` else `listing_date`** (`:105`) | BLANK NaT | the csv path stores KNOWLEDGE time as `excl_date` (F-28) |
| reinstate_date / listed_date | `endDate` / `listingDate` or `first_seen` | BLANK | |

### 5d. `src/enforcement/preclusion.py` — `PRECLUSION_COLS` `:33-44`; same shape as 5a (RAISE only if npi+org_name+last_name all absent `:55-58`; dates/reason BLANK). Input `preclean/preclusion/preclusion_list.csv` (R1 `:138-139`, gated) → `processed/exclusions_preclusion.parquet`.

### 5e. `src/enforcement/sam_api.py` `load_sam_extract` — `EXTRACT_COLS` `:78-90` (lower-case exact match `:108-114`); file = explicit csv or newest dated `preclean/sam/sam_exclusions*.csv` `:101-106`; output `feeds/sam/exclusions_sam.parquet` (operator copies to processed/). NOT in R1.

| field | headers | if missing | notes |
|---|---|---|---|
| name / first,middle,last | `Name` / `First`,`Middle`,`Last` | SKIP row if both blank `:136` | |
| npi | `NPI` | BLANK | **10-digit check only, no Luhn** `:119-120` (the 11,234 bad-Luhn rows in CLAUDE.md are UEI/CAGE/DUNS; F-27) |
| excl_type / agency / program | `Exclusion Type` / `Excluding Agency` / `Exclusion Program` | BLANK → `sam:` + blanks | |
| active / termination / status | `Active Date` / `Termination Date` / `Record Status` | BLANK → NaT / active heuristic `:134-135` | |
| state / classification | `State / Province`,`State` / `Classification` | BLANK | |

### 5f. `src/enforcement/state_licensing.py` — requires an explicit `column_map` (`name` mandatory, RAISE `:46-48`; `npi`, `action`, `action_date`, `reinstate_date` optional); adverse-action keyword filter `:31-32`. **No CLI and no caller reads `preclean/state_licensing/`** (R1 claims it is wired, `preflight.py:122-124`; F-29).

### 5g. `src/enforcement/cia.py` — `CIA_COLS` `:42-49` via a local normalized `_pick` `:55-65`; file `preclean/cia/cia_list.csv` (docstring `:30-31`); party RAISE `:85-87`; state/effective_date/status/url BLANK. Feeds `label_expansion cia` / `label_audit`, not the matrix directly.

### 5h. `src/enforcement/cms_priority_lists.py` — lower-cased literal headers (`sector,scope,effective_date` `:31-37`; any column containing `npi` + any containing `due`/`date` `:42-43`; any containing `ccn`/`provnum` `:53`); returns empty frames silently. **`priority_flags` / `load_*` have no caller in `src/`** (grep); R1 lists `cms_priority` as wired (`preflight.py:144-147`; F-29).

### 5i. `src/entity_graph/asof_nppes.py` — `_NPPES_COLS` `:36-48`, matched **exact, case-sensitive** (`_pick` `:55-59`). File = `--asof-nppes` (runner: `preclean/nppes/npidata_pfile_*-<YYYYMM>??.csv`, `ops/vm/run_frozen_night.sh:44`; a zip RAISES `:76-80`). NOT in R1 (R3 knows `nppes`/`nppes_history`).

| field | headers | if missing | notes |
|---|---|---|---|
| npi | `NPI`, `npi` | RAISE `:87-88` | |
| line1/city/state/zip | `Provider First Line Business Practice Location Address`/`practice_address`/`addr_line1`, `…City Name`/`practice_city`/`addr_city`, `…State Name`/`practice_state`/`addr_state`, `…Postal Code`/`practice_zip`/`addr_zip` | BLANK "" (`_standardize_address`) → every provider gets the same blank key → the frozen co-location layer silently collapses | |
| enumeration | `Provider Enumeration Date`, `enumeration_date` | **BLANK → no enumeration filter** `:99-101` (silent) | |
| deactivation | `NPI Deactivation Date`, `deactivation` | BLANK → no deactivation filter `:102-104` (silent) | |

### 5j. `src/entity_graph/build_edges.py` reassignment — `_REASSIGN_COLS` `:40-46`; raw `preclean/reassignment/*.csv` (first file, usecols). NOT in R1.

| field | headers | if missing | notes |
|---|---|---|---|
| individual_npi | `Individual NPI`, `individual_npi`, `IND_NPI`, `NPI` | **SKIP silently** (empty edge table `:67-68`; the graph logs nothing) | |
| group_npi | `Group NPI`, `group_npi`, `GRP_NPI` | BLANK → PAC path | |
| group_pac_id | `Group PAC ID`, `group_pac_id`, `GRP_PAC_ID` | BLANK → rows dropped `:78-91` (silent) | `org:pac:<id>` must be a known org |
| group_name | `Group Legal Business Name`, … | unused | |

Emits `edges/reassigns_to_edges.parquet` → `case_labels` affiliations (`export:2822-2823`), `capacity.py` clinician counts, `n_reassigned_providers`.

### 5k. `src/ingest_cms/docgraph.py` — `DOCGRAPH_COLS` `:28-36`; `resolve_docgraph_columns` `:39-51` is **exact lower-case** match (not the normalizer). Streamed via DuckDB in the graph build.

| field | headers | if missing | notes |
|---|---|---|---|
| from_npi / to_npi | `from_npi`,`FROM_NPI`,`npi_1`,`npi1`,`npi_from`,`Provider 1 NPI`,`referring_npi`,`initial_npi` / `to_npi`,…,`subsequent_npi` | SKIP (`{}` → "docgraph SKIPPED: file lacks from/to NPI columns") | |
| patient_count | `patient_count`,`PATIENT_COUNT`,`shared_patient_count`,`pair_count`,`patients`,`patient_total`,`bene_count`,`benes`,`shared_count`,`transaction_count` | **BLANK**: DuckDB path → volume `1.0` `:80-81` and `min_patients=20` drops EVERY pair → 0 edges, logged as "kept 0 of 0"; pandas path → 0 `:125-126` → all edges weight 0 (F-30) | |

### 5l. `src/ingest_cms/census_population.py` — `POP_COLS` `:25-33` (fips or state_fips+county_fips RAISE `:49-55`; `POPESTIMATE\d{4}` auto-pick `:59-65`), `ZIP_COLS` `:34-38` (`zip` + `fips` RAISE `:79-81`; `res_ratio` default 1.0). `zip_to_county` is consumed by saturation (`export:2090-2091`); **`county_population` has no caller** → `local_volume_implausibility` (specialty_mismatch weight 0.2) is never produced (F-29). Neither `census` nor `hud` is a `pos`-style registry entry beyond `zip_county` (R1 `:152-155`).

### 5m. `src/ingest_cms/claim_slices.py` — `_NDC_WANTED` `:38-44`, `_REFERRAL_WANTED` `:45-50`; every field RAISE `:55-62`. Outputs `processed/ndc_claims.parquet` (`billing_npi, ndc, units, billed_cost`) and `processed/referred_claims.parquet` (`billing_npi, referring_npi, total_paid`). No cutoff (F-06).

### 5n. `src/ingest_cms/ccn_npi_crosswalk.py` — `_WANTED` `:39-43` (`ccn`: `ccn, prvdr_num, provider_number, cms_certification_number, medicare_id, ccn_number, enrollment_state_ccn, prvdrnum`; `npi`: `npi, npi_number, rndrng_npi, org_npi, associate_npi`); fallback to NPPES `Other Provider Identifier` type `06` `:124-150`; POS↔NPPES join `_POS_WANTED` `:154-162` (`mdcd_vndr_num` type-05 match, name+zip5); RAISE `:286-290`. Output `processed/ccn_to_npi.parquet` (the gate for §3h-3j; `export:2179-2182`).

### 5o. `src/ingest_cms/hcris_related_party.py` / `hcris_affiliates.py` — raw `SNF*FY*.zip` (or extracted folders) under `--hcris-dir` (`:168`, `hcris_affiliates.py:152`); header-less CSVs identified by `_RPT`/`_ALPHA`/`_NMRC` in the name (RAISE `:93-95`); positional columns `RPT_COLS` `:51-54`, `CELL_COLS` `:55`; worksheet prefixes `A81`, `A800`, `S2000` (`:59-60`), affiliates adds `G3`, `S3000` with fixed cells `:46-50`. No cutoff (fiscal years carried per row). Not a matrix feature; CLAUDE.md's `preclean/hcris/raw/` sits INSIDE the export's `preclean/hcris/` — `glob("*.csv")` is top-level only, so the zips are safe, but any `.csv` extracted next to them crashes `compute_hcris_metrics` (no CCN column in a header-less file) → the whole `hcris` block is skipped (F-20).

### 5p. `src/sourcing/warn_monitor.py` — `WARN_COLS` `:38-49` (lower-case exact) then `WARN_KEYWORDS` substring fallback `:58-66`; file(s) `preclean/warn/warn_<ST>_<yyyymm>.csv|xlsx` (`load_warn` `:137-150`; xlsx sheets probed 9 rows deep `:95-117`); state from filename `:51-53`. `employer` RAISE `:159-160`; `notice_date`/`layoff_date` BLANK → NaT (no outreach window, silent); `employees` BLANK. NOT in R1 (R3 knows `warn`).

---

## 6. Discovery cross-check — registry (R1 preflight) vs export/graph

| source | R1 folder / names / exts (`preflight.py`) | what actually reads it | agree? |
|---|---|---|---|
| spending/nppes/pecos/leie | root `Spending.csv`,`NPPES.csv`,`PECOS.csv`,`Caught.csv|leie.csv|LEIE.csv` (`:45-52`) | integrate defaults `:663-666` (only `Caught.csv`) | **leie.csv/LEIE.csv pass preflight, fail integrate's default** (F-24) |
| partb/partd/opioid/dmepos/open_payments | `<f>/<f>.csv` (`:56-68`) | `_year_files` any csv/xlsx/parquet, newest year (`export:1575-1584`) | folder yes; **canonical names are undated → bypass the vintage cap** (F-08); `.xlsx` admitted but unreadable (F-31) |
| nadac | `nadac/nadac.csv` | `_asof_pick` `export:1745`, `:1798`, `:2021` | undated canonical name = fallback "vintage unverified" (F-08) |
| medicaid_ndc / medicaid_enrollment / fda_ndc | `:71-87` | `export:1776-1782`, `:1867-1868`, `:1815` | yes |
| saturation | `saturation/saturation.csv` | `_first_existing(…"saturation.csv","*.csv")` `:2074` | yes |
| hrsa_340b | `opais.xlsx|opais.csv` | `:2053-2054` | yes |
| nppes_deactivation | `deactivation.zip|xlsx|csv` | `:1787`, `:1980`, `:2685` | yes (csv preferred over zip) |
| order_referring | `order_referring.csv` | `_asof_pick` `:2036`; **`_first_existing` `:2563`** | two discovery rules for one file (F-13) |
| pbj / hospice / deficiencies | `facility/pbj.csv`, `hospice.csv`, `deficiencies.csv`, ext `.csv` (`:100-105`) | `pbj*.csv` `:2197`; hospice patterns `:2222`; deficiencies patterns `:2270-2272` | **preflight marks all three [ok] when ANY csv is in facility/** (`_scan` `:184-187`); export patterns are case-sensitive on Linux (F-09) |
| NH ownership / NH penalties | **absent from R1** | `:2248`, `:2258` | registry gap (F-25) |
| hcris | `hcris/hcris.csv`, exts `.csv,.parquet` (`:106-107`) | `glob("*.csv")` `:2295` | **a `.parquet` passes preflight and is never read** (F-19) |
| pos | `pos/pos.csv` | `rglob("*.csv")` `:2314` | yes |
| nucc / nucc_xwalk | `nucc/nucc_taxonomy.csv`, `specialty_crosswalk.csv` | exact names `:1074`, `:1083` | yes (no glob) |
| revocations | `revocations/revoked_providers.csv|revocations.csv` | integrate `:667` (only `revoked_providers.csv`) + CLI → `exclusions_medicare_revocations.parquet` | **two paths, no cross-dedupe** (F-26) |
| state_terminations | `state_terminations/GA_terminations.xlsx` exts xlsx/csv | `load_all` `<ST>_*` | yes |
| docgraph | `docgraph/docgraph.csv|shared_patient.csv` | graph `preclean/docgraph/*.csv*` first | yes |
| state_licensing | `state_licensing/licenses.csv` | **nothing** | F-29 |
| usps_cmra | `usps/cmra.csv` | `:2866` | yes (column contract exact, F-22) |
| opensanctions / preclusion / dmf | `:132-141` | CLIs / `:2000` | yes |
| owners | `owners/owners.csv` | integrate `owners/*.csv`; churn needs `<DS>_All_Owners_<date>` | **canonical name defeats churn** (F-23) |
| cms_priority | `cms_priority/moratoria.csv…` | **nothing** | F-29 |
| hcpcs_time | `hcpcs_time/hcpcs_minutes.csv` | `"hcpcs_minutes.csv"`,`"*.csv"` `:2717`; `hcpcs_minutes*.csv` elsewhere | mixed (F-21) |
| zip_county | `hud/zip_county.csv` | `hud/` or `zip_county/` `:2086-2087` | yes |
| mue | **absent** | `preclean/reference/mue/` `:2782` | F-25 |
| reassignment, sam, cia, warn, state_capacity, nppes (as-of edition, pl_pfile), owners_prior, census | **absent from R1**; present in R3 known set (`vintage_inspect.py:440-456`) | graph `:76`; sam_api `:102`; cia docstring; warn `:141`; licensed_capacity; runner `:44`; `:2119`; — | F-25 |

---

## 7. Cutoff (as-of) behaviour per adapter

| adapter | mechanism | in-file dates present but NOT used |
|---|---|---|
| partb/partd/dmepos/opioid/open_payments (+trends, kickback) | filename year ≤ cutoff year (`_year_files`, `export:949-966`, `:1526`, `:1728`); undated name passes | OP `Date_of_Payment` (only `medicaid_kickback` cuts it, `medicaid_ndc.py:546-548`) |
| nadac (both grains) | filename (`_asof_pick`); `fallback_newest` for the NDC-block reference | `Effective Date`, `As of Date` |
| medicaid_ndc | fact rebuilt as-of (`service_month ≤ cut`) | — |
| medicaid_enrollment | segments as-of (`:570-573`, `:647-650`); **`paid_after_enrollment_end` on the FULL fact** (`export:1873`) | — (F-05) |
| nppes_deactivation / death_master | none on event dates; compare against the as-of fact | deactivation/death dates (safe by construction) |
| NADAC spread / order_referring (org) | reference file as-of; **`ndc_claims.parquet` / `referred_claims.parquet` not as-of** (`:2022`, `:2038`) | claim months in the slices (F-06) |
| order_referring referrer fallback | **none** (`:2563`); current snapshot | — (acknowledged `LEAKAGE_ADJACENT` `:125-133`, but discovery disagrees with the org block, F-13) |
| hrsa_340b | row-level Begin/Term (RAISE without Begin Date) | — |
| saturation | `reference_period ≤ cutoff` (RAISE if none) | — |
| ownership churn | editions dated ≤ cutoff (filename) | `association_date` not used here (the graph uses it) |
| PBJ | filename (`_asof_files`); **no row filter** | `WorkDate` (F-17) |
| hospice | edition pick + `End Date`/`Measure Date Range` row filter (RAISE without) | — |
| NH ownership | filename only | association date column (F-18) |
| NH penalties / deficiencies | filename + `Penalty Date` / `Survey Date` row filter (RAISE without) | — |
| hcris | **none** (`:2295`) | fiscal-year begin/end (F-19; R2 claims wired) |
| pos | filename (`_asof_files`) only | certification/termination dates (F-17) |
| sector_schemes / mue / drug_markup / facility_code / servicing / billing_lm / smoking_gun | the as-of spending fact (`:2716`, `:2786`, `:2735`, `:2755`, `:2937`, `:2896`, `:2672`) | — |
| address (CMRA) | **none** — today's `provider_dim` (`:2871`) even when the graph was built `--asof-nppes` | — (classed `current_state`) |
| nucc / hcpcs_time / mue table | static reference | — |
| exclusions (all sources) | graph `--asof` on `excl_date` (`temporal_sources.py:68`) | undated rows (NaT) — see label audit; revocations/preclusion/state lists with no date column produce NaT rows |

---

## FINDINGS (each with file:line)

**Silent-blank fields that feed a scheme**

- **F-03 (SEVERE)** `src/ingest_cms/order_referring.py:50-53` — if the eligibility file has no `DME`/`dme`/`Eligible to order and refer DME` column, every NPI gets `dme=0`; `referrer_ineligible_dme` `:69-79` then flags EVERY referrer (`dme_ineligible_referrer=1`, `dme_ineligible_referred_dollars=total_allowed`) and `ineligible_referral_share` `:110-113` returns 1.0 for every org. Both feed **dme_ring** (`scheme_subscores.py:138-144`) with no log line. The export's referrer block `export:2567-2574` would report "N not DME-eligible" = all referrers, the only tell.
- **F-10** `src/ingest_cms/partb.py:52-56` — `avg_allowed` absent → 0.0 → `allowed_per_bene` = 0 for every NPI (**payment_outlier**, weight 0.8) and `code_concentration_hhi` NaN (**single_service_mill**). No log.
- **F-11** `src/ingest_cms/openpayments.py:159-164, 205` — no product columns → `op_payment_utilization_corr` = 0 for every prescriber (**pharma_kickback** weight 0.7); only a `logging.warning`, which the export's `_SourceAudit` never captures (it captures `[name]` print lines, `export:1398-1424`). Also prescribers absent from OP score 0.0 rather than NULL (`:205`), contrary to the matrix's "NULL = absent" convention (`export:3126-3127`).
- **F-14** `src/ingest_cms/hrsa_340b.py:143-146` — `Contract Pharmacy Name` absent → `n_contract_pharmacies` 0 → `contract_pharmacy_concentration` 0 for every matched entity (**contract_pharmacy**), silent.
- **F-15** `src/ingest_cms/facility.py:175-176` — PBJ `Hrs_LPN`/`Hrs_CNA` absent → 0 → `pbj_understaffing` computed on RN hours only (**worthless_services** weight 0.7), silent; `compute_pbj_weekend_gap` `:416-418` same.
- **F-16** `src/ingest_cms/facility.py:354-365, 385-391` and `src/ingest_cms/hcris.py:72-76` — NH `Owner Name` absent → `nh_owner_count` 0 (**ownership_integrity**); `Fine Amount` absent → `nh_fine_total` 0 (**worthless_services**); HCRIS `admin_costs` absent → `admin_cost_share` 0 and `total_costs` absent → every ratio 0/NaN (**cost_report_fraud**); all silent (the constant column is later dropped as `dropped_constant` `export:762-771`, which is the only visibility).
- **F-04** `src/attempt_2/ingest/integrate.py:507` — LEIE `EXCLTYPE` absent → `excl_type` column of `None`; `fca_relevance` then tiers every LEIE row as unmapped (weak) with no warning; under `--label-tier hard` the training label would silently lose every LEIE positive.
- **F-12** `src/ingest_cms/nppes_deactivation.py:95-98` — deactivation-date column absent → empty table, and the export logs "adapter returned no rows … source present but produced nothing usable" (`export:1973-1975`), a misleading reason; `invalid_identity` and the smoking-gun `deact_*` columns go dark.
- **F-30** `src/ingest_cms/docgraph.py:80-81, 96; :125-126` — `patient_count` absent → DuckDB path uses `1.0` and the `>= 20` threshold drops every pair (graph logs "kept 0 of 0"); pandas path uses 0 → every edge has weight 0. Referral rings silently empty.
- **F-22** `src/model_a/address_grounding.py:48-56` + `src/attempt_2/clean_data.py:383-385` — the USPS CMRA file must carry EXACTLY `line1,city,state,zip`; otherwise `_standardize_address` returns blank keys, the set is empty, and `addr_is_cmra` is silently never emitted (`:96-97`). The export logs "loaded N USPS CMRA addresses" (`export:2869`) with N=0 as the only tell.
- **F-28** `src/enforcement/opensanctions.py:105` — the `targets.simple.csv` path writes `first_seen` (aggregator knowledge time) into `excl_date`, the field the as-of graph and forward ruler treat as the effective/conduct date; the FtM path uses `startDate`. Same source, two date semantics.
- Also silent: `partd.py:58-60` brand/generic names absent → `brand_generic_cost_ratio` 0 (raw aggregate only); `nadac.py:48-50, 132-134` classification absent → `nadac_brand_premium_share` and `px_brand_paid_share` 0 (trainable extras); `medicaid_ndc.py:87-100` prescribing_npi / total_claim_lines / total_patients absent → every `px_*`, `rx_spread_*`, kickback NULL (reported only in MEDICAID_NDC_REPORT "Column resolution"); `facility.py:286-288` hospice measure code/name absent → no measure matches → all seven hospice metrics empty with no message; `saturation.py:113-115` FIPS absent → county grain off silently.

**Adapters without a cutoff although the file carries dates (freeze-leak candidates)**

- **F-05 (SEVERE, leaks post-cutoff billing into a frozen matrix)** `export:1873-1875` — `paid_after_enrollment_end` is run on `processed/spending_fact.parquet`, never on `asof_spend_p`; `mcd_paid_after_end`, `mcd_months_after_end`, `mcd_paid_after_end_share` are trainable (`medicaid_enrollment` partition group) and `_SRC_VINTAGE["medicaid_enrollment"] = "point_in_time"` (`export:798`) mislabels them.
- **F-06** `export:2022`, `:2038` — `processed/ndc_claims.parquet` and `processed/referred_claims.parquet` are read as-is; `claim_slices.py` has no cutoff and `medicaid_ndc.write_ndc_claims_slice` writes from whichever fact was built; a current-day slice feeds `drug_spread_anomaly` / `ineligible_referral_share` in a frozen run.
- **F-19** `export:2295` + `src/ingest_cms/hcris.py` — no vintage guard at all (every csv in `preclean/hcris/`, no filename date, no row filter). `src/readiness_audit.py:56` declares `hcris: row_asof ["Fiscal Year Begin Date"] … (wired)` — false. `hcris_cost_anomaly` is `current_state` by default (`export:799-801`), so the manifest is honest, but the readiness audit is not.
- **F-17** PBJ `export:2197` + `facility.py:155-188` (WorkDate read only for the weekend gap, never as a filter); POS `export:2314` + `pos.py` (no date read). Both rely on filename dating; an undated filename is accepted with "vintage unverified" (`export:1320-1325`).
- **F-18** NH ownership `export:2248` + `facility.py:339-366` — filename only; the Care Compare ownership file's association-date column is not in `NH_OWNERSHIP_COLS` (`:118-126`), so a post-cutoff owner is counted in `nh_owner_count` under a freeze.
- **F-13** `export:2563-2564` — the referrer-grain O&R check uses `_first_existing(..., "order_referring.csv", "*.csv")` (no cutoff, alphabetically FIRST csv) while the org-grain block two screens earlier uses `_asof_pick` (`:2036`). Acknowledged as leakage-adjacent (`export:125-133`), but the two blocks can read DIFFERENT editions of the same folder.
- **F-08 (class)** every annual-PUF / snapshot vintage guard is filename-based, and the preflight registry's canonical names are all undated (`partb.csv`, `partd.csv`, `dmepos.csv`, `opioid.csv`, `open_payments.csv`, `nadac.csv`, `order_referring.csv`, `pos.csv`, `pbj.csv`, `hospice.csv`, `deficiencies.csv`; `preflight.py:56-109`). `_year_files` `export:957-958` lets a no-year filename through the cap; `_asof_pick` `:1282-1284` and `_asof_files` `:1320-1325` accept the undated name as a fallback. A file saved under the registry's own name silently passes every freeze guard.
- Address features: `export:2871` reads today's `provider_dim` even when the graph was built `--asof-nppes`; honest in the manifest (`current_state`) but `addr_*` sit in `extra_clean` (`export:716-724`).

**Registry / export / graph disagreements**

- **F-09** `preflight.py:184-187` — for any subfolder source, ANY file with a listed extension yields `[ok]`, so `pbj`, `hospice`, `deficiencies` (all `facility/`, ext `.csv`) are reported present when only one layout exists. The export then needs `pbj*.csv` (`export:2197`), hospice/deficiency patterns (`:2222`, `:2270-2272`), `NH_Ownership*`/`*Ownership*`, `NH_Penalties*`/`*Penalt*` — all case-sensitive on the Linux VM (`PBJ_Daily_Nurse_Staffing_….csv` does not match `pbj*.csv`; `Deficiencies.csv` matches neither `*deficienc*` nor `*Citations*`). The same file passes on Windows and vanishes on the VM.
- **F-23** `preflight.py:142-143` canonical `owners/owners.csv` vs `owners_vintage.py:49, 86, 123-124` — the churn adapter requires `<Dataset>_All_Owners_<YYYY.MM.DD>.csv`; an undated name is silently excluded from `load_editions`, and `ownership_turnover` (ownership_integrity weight 0.3) never fires, while integrate happily reads the same file.
- **F-24** `preflight.py:51-52` accepts `leie.csv`/`LEIE.csv`; `integrate.py:666` defaults to `Caught.csv` only (no fallback) — a file passing preflight fails the first stage unless `--leie` is passed.
- **F-25** sources the export/graph/CLIs read that R1 does not list (so preflight, readiness_audit `:236`, and data_refresh cannot see them): NH ownership + NH penalties (`export:2248, 2258`), `reference/mue/` (`:2782`), `reassignment/` (`entity_graph/__main__.py:76`), `nppes/npidata_pfile_*` as-of edition (`run_frozen_night.sh:44`) and `pl_pfile` (`capacity.py:455`), `state_capacity/` (`licensed_capacity.py:194`), `sam/` (`sam_api.py:102`), `cia/` (`cia.py:30`), `warn/` (`warn_monitor.py:141`), `owners_prior/` (`export:2119`), `census/` (R2 lists it, `readiness_audit.py:68, 95`). R2's POLICY carries `mue`, `census`, `facility`, `zip_county`, `hcpcs_time` keys (`:63-71`) that `audit_tree` (`:236`, CORE+UNLOCKS only) never visits — dead entries.
- **F-26** revocations are merged twice: `integrate.py:667-670, 704` unions `preclean/revocations/revoked_providers.csv` into `processed/exclusions.parquet`, and the DERIVED path `preflight.py:164-165` writes `processed/exclusions_medicare_revocations.parquet`, which `entity_graph/__main__.py:63-69` concatenates with NO de-duplication. Result: duplicate exclusion nodes/edges for every revoked NPI (the label de-dups by NPI, `export:1138`, but proximity, `excluded_in` edges and the audits do not).
- **F-29** R1 claims three wired adapters that nothing calls: `state_licensing` (`preflight.py:122-124`; `state_licensing.py` has no CLI and needs a `column_map`), `cms_priority` (`:144-147`; `priority_flags` has no caller), and `census_population.county_population` (`:152-155` only lists `zip_county`; `county_population` has no caller, so `local_volume_implausibility` in specialty_mismatch (`scheme_subscores.py:76`) can never exist).
- **F-21** `export:2717` picks `hcpcs_minutes.csv` else the first `*.csv` in `preclean/hcpcs_time/`; `capacity.py:457` / `pi_facts.py:664` pick `hcpcs_minutes*.csv`; `hcpcs_time_convert.py` writes `hcpcs_minutes_<year>.csv` and the raw PFS file may sit beside it. `sector_schemes.py:88-92` hard-codes `hcpcs`,`minutes` with no resolver and no try — the wrong csv kills NEMT + BH + impossible-day together (`export:2728`).
- **F-20** `hcris.py:111` uses `pd.read_csv(f, dtype=str)` instead of the shared `read_csv_text` (no BOM/UTF-16/Latin-1 tolerance, contrary to CLAUDE.md's "every raw-CSV entry point routes through it"); and any header-less `snf10_<yr>_RPT.CSV` extracted from the raw zips into `preclean/hcris/` (CLAUDE.md puts the zips in `preclean/hcris/raw/`) raises in `compute_hcris_metrics` and takes the whole `hcris` block down (`export:2303`). Note `glob("*.csv")` misses `.CSV` on Linux.
- **F-31** `export:962` admits `*.xlsx` in the PUF folders but `_read_one` `:984-985` → `_read_any` `:1002-1009` has no Excel reader; an .xlsx is read as text and the source is skipped with a decode error.
- **F-27** NPI validity is inconsistent across exclusion sources: `opensanctions.py:65` takes `idNumber` unverified; `sam_api.py:119-120` checks 10 digits only; `state_terminations.py:200`, `medicare_revocations.py:58`, `preclusion.py:61` Luhn-check. The export's label filter is `len(npi) >= 10` (`export:1134`), not Luhn, so a 10-digit non-NPI from SAM/OpenSanctions can become an NPI-matched positive if it collides with a real NPI string.
- R3 vs adapters: `vintage_inspect.py:75-92` requires `BED_CNT` for pos (the iQIES layout has only `*_bed_cnt` variants the adapter accepts, `pos.py:55-59`), `PARTB`+`DME` for order_referring (adapter requires only `NPI`), `Tot_Drug_Cst` etc. — consistent with the adapters for partb/partd/opioid but stricter for pos/order_referring and looser for dmepos (no `Tot_Suplr_Srvcs`) and open_payments (no manufacturer column). R2 `_col_check` `readiness_audit.py:151-153` matches aliases as SUBSTRINGS of the joined header, so it can pass a file the adapter's normalized-exact resolver rejects.

**Crash-class contract gaps (no assertion names the field)**

- **F-01** `integrate.py:338-378` — Spending headers are SQL literals (`BILLING_PROVIDER_NPI_NUM`, `SERVICING_PROVIDER_NPI_NUM`, `HCPCS_CODE`, `CLAIM_FROM_MONTH`, `TOTAL_PATIENTS`, `TOTAL_CLAIM_LINES`, `TOTAL_PAID`); `clean_data._SPENDING_ALIASES` (`:402-411`) exists but is unused. A renamed column fails as a DuckDB binder error, not a named contract failure.
- **F-02** `integrate.py:245-248, 265` (NPPES: `org_name`, `last_name`, `first_name`, `taxonomy_code`, `addr_*` → AttributeError/KeyError; only `npi`+`entity_type` are asserted `:232-233`); `:294, 309` (PECOS `pac_id`, `enrollment_id`, `provider_type_code`, `provider_type_desc`, `state` → KeyError); `:511-513` (LEIE names → AttributeError); `:454-457, 470` (owners `owner_last_name`, `owner_first_name`, `owner_org_name`, `facility_enrollment_id`).
- **F-07** `death_master.py:74, 88-90` requires `provider_dim.dob` for a "high" match, but `integrate.NPPES_COLS` `:57-70` never reads a DOB (NPPES has none) and nothing else adds it → every DMF match is `low` → `billing_after_death` (invalid_identity weight 0.6, label widening `export:587-600`) is structurally unreachable regardless of the DMF file.
- `asof_nppes.py:99-104` — enumeration/deactivation columns absent → no temporal filter, silently; `:105-106` blank address columns → every NPI gets the same blank `addr_key` (the frozen co-location layer collapses without a message).
- `build_edges.py:67-68, 78-91` — reassignment `individual_npi` absent or neither `group_npi` nor `group_pac_id` resolvable → empty edge table with no log line in the graph build.
- `export:2855-2881` — the `address` block has no try/except; an unexpected error in `load_cmra_reference`/`address_flags` aborts the whole export (every other source block is guarded).
- `export:2177-2184` — `--skip-sources facility` also skips `hcris` and `pos` (one gate for three sources).

**Scheme inputs with no producer anywhere (dormant by construction, for the scanner's "expected absent" list)**: `bene_per_day_p95` (impossible_day), `dme_ordering_md_concentration` (dme_ring), `local_volume_implausibility` (specialty_mismatch, F-29); `controlled_substance_share` exists only when the Part D BY-PROVIDER summary file (not the by-provider-and-drug file) is the newest in `preclean/partd/` (`partd.py:34-36, 88-91`).
