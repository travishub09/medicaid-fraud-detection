# 09 — Data Procurement Map

What we have, what is missing, exactly where to get it, where to drop it, and what
each file powers — so that when a file lands, ingestion works immediately.

**Drop convention (matches `integrate.py`):** raw files go to `~/Desktop/data/preclean/`
under the subdirectory named below. Identifiers must stay strings (the pipeline reads
all-VARCHAR; do not pre-process in Excel, which strips leading zeros). Every new
source gets: a column map in its adapter, NPI canonicalization through
`clean_data.canonicalize_series`, and row-count/dollar assertions.

## Already ingested (do not re-procure)

| File | Source | Powers |
|---|---|---|
| `Spending.csv` | Medicaid provider spending | billing-outlier features, the whole detection core |
| `NPPES.csv` | npiregistry / NPPES monthly file | provider identity, taxonomy, address |
| `PECOS.csv` | CMS PECOS enrollment | NPI↔PAC↔enrollment crosswalk |
| `Caught.csv` | OIG LEIE | exclusions: integrity flags + backtest labels |
| `owners/*.csv` | CMS All-Owners (5 facility types) | ownership edges, Layer-3, graph |

---

## Priority 1 — unlocks Model A productionization (procure first)

### 1. Medicare Physician & Other Practitioners (Part B PUF)
- **Get:** https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners — "by Provider and Service" annual CSVs, latest 3–5 years.
- **Drop:** `preclean/partb/partb_YYYY.csv` (one per year).
- **Grain:** NPI × HCPCS × place-of-service × year. **Join:** NPI.
- **Why:** the richest public window into *how a provider bills* — home of the upcoding,
  impossible-day, code-concentration, and services-per-beneficiary signals; the engine
  behind the public lookup tool. Adds the Medicare side to our Medicaid spending view.
- **Powers:** `em_high_level_share`, `em_level_mean`, `services_per_bene`,
  `bene_per_day_p95`, `code_concentration_hhi`, `allowed_per_bene`, YoY growth
  (04-model-a.md feature dictionary). Multi-year files give the temporal features.
- **Cadence:** annual (~2-year lag — structural detection, by design).

### 2. Medicare Part D Prescribers
- **Get:** https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers — "by Provider and Drug" annual CSVs.
- **Drop:** `preclean/partd/partd_YYYY.csv`.
- **Grain:** prescriber NPI × drug (brand/generic) × year. **Join:** NPI.
- **Why:** controlled-substance/opioid over-prescribing (pill mills), brand-over-generic
  steering, high-cost-drug concentration (pharma kickbacks).
- **Powers:** `controlled_substance_share`, `brand_generic_cost_ratio`,
  `high_cost_drug_share`; crossed with #4 it becomes the kickback correlation.
- **Cadence:** annual.

### 3. Medicare DMEPOS (Durable Medical Equipment)
- **Get:** https://data.cms.gov/provider-summary-by-type-of-service/medicare-durable-medical-equipment-devices-supplies — supplier and referring-provider files.
- **Drop:** `preclean/dmepos/dmepos_supplier_YYYY.csv`, `dmepos_referring_YYYY.csv`.
- **Grain:** supplier/referring NPI × HCPCS × year. **Join:** NPI (both sides).
- **Why:** DME is a perennial top fraud category; ordering-physician concentration
  (one physician feeding a supplier) is the classic ring/kickback shape.
- **Powers:** `dme_high_cost_item_share`, `dme_ordering_md_concentration`, and the
  graph's `refers_to`-like referring-MD ↔ supplier edges (ring detection).
- **Cadence:** annual.

### 4. Open Payments
- **Get:** https://openpaymentsdata.cms.gov/datasets — General + Research payments, latest 3 years.
- **Drop:** `preclean/openpayments/op_general_YYYY.csv`.
- **Grain:** manufacturer → physician payment record. **Join:** physician NPI (the file
  carries covered-recipient NPI), manufacturer ID.
- **Why:** alone it's just payments; crossed with Part B/D utilization of that
  manufacturer's product it is the **anti-kickback correlation** behind many AKS cases.
- **Powers:** `op_payment_utilization_corr`, `op_payment_concentration`; adds `pays`
  edges (Manufacturer → Provider) to the entity graph.
- **Cadence:** annual (June publication).

### 5. Market Saturation & Utilization
- **Get:** https://data.cms.gov/tools (Market Saturation & Utilization State-County) — CSV export.
- **Drop:** `preclean/saturation/market_saturation.csv`.
- **Grain:** county × service line (home health, hospice, DME…). **Join:** county FIPS
  ← provider practice address (we already standardize addresses).
- **Why:** CMS's own geographic over-supply indicator for the most fraud-prone lines —
  a ready-made program-integrity prior.
- **Powers:** `market_saturation_index` (a sector-prior multiplier in the ERV formula).
- **Cadence:** semi-annual.

### 6. DOJ FCA settlements + OIG enforcement / CIAs → the case database
- **Get:** https://www.justice.gov/civil/false-claims-act + justice.gov press releases;
  https://oig.hhs.gov/fraud/enforcement/ + https://oig.hhs.gov/compliance/corporate-integrity-agreements/
- **Drop:** `preclean/enforcement/doj_cases.csv` (we must *structure* this ourselves:
  scrape/transcribe releases into defendant, scheme type, amount, intervention status,
  jurisdiction, date). A build script is a named gap (see GAPS.md).
- **Grain:** one row per case/action. **Join:** defendant name → entity graph org
  resolution (name canonicalization), NPI where stated.
- **Why:** the ground truth of what was actually prosecuted: the scheme taxonomy,
  sector base rates, Model A's enforcement-prior + **positive labels**, and Model C's
  **primary training labels** (intervened/declined, recovery size, jurisdiction).
- **Powers:** Model A sector prior + supervised graduation; Model C cold-start rules
  and labels; `named_in` graph edges.
- **Cadence:** continuous (monitor); backfill 10 years.

### 7. SAM.gov exclusions
- **Get:** https://sam.gov/data-services (Exclusions public extract, CSV).
- **Drop:** `preclean/sam/sam_exclusions.csv`.
- **Grain:** one row per excluded entity. **Join:** name + UEI + (sparse) NPI/EIN.
- **Why:** government-wide debarment beyond LEIE; UEI registry also aids org
  canonicalization.
- **Powers:** more `excluded_in` edges and exclusion nodes; entity-resolution aid.
- **Cadence:** monthly.

---

## Priority 2 — facilities + Model C labels

### 8. Care Compare + PBJ staffing + provider data catalog
- **Get:** https://data.cms.gov/provider-data/ (Care Compare); PBJ Daily Nurse Staffing
  at https://data.cms.gov (search "Payroll-Based Journal").
- **Drop:** `preclean/facility/care_compare_<setting>.csv`, `preclean/facility/pbj_YYYYQq.csv`.
- **Grain:** CCN (×quarter for PBJ). **Join:** CCN ← PECOS/enrollment (extend
  `npi_xwalk` with CCN where derivable).
- **Why:** PBJ staffing vs billed acuity is the worthless-services/understaffing
  signal; hospice live-discharge rate flags ineligibility; deficiencies flag quality fraud.
- **Powers:** `pbj_staffing_z`, `hospice_live_discharge_rate`, deficiency counts.

### 9. HCRIS cost reports
- **Get:** https://www.cms.gov/data-research/statistics-trends-reports/cost-reports
  (SNF/hospital/HHA/hospice annual files).
- **Drop:** `preclean/hcris/<type>_YYYY.csv`.
- **Grain:** provider number (CCN) × cost-report year. **Join:** CCN.
- **Why:** cost-report fraud is a distinct scheme (DSH, wage index, cost allocation);
  also identifies the finance/reimbursement persona for Model B.
- **Powers:** `hcris_cost_alloc_anomaly`.

### 10. PACER / CourtListener (RECAP) docket monitoring
- **Get:** https://www.courtlistener.com/recap/ (free, API) + PACER account for gaps.
- **Drop:** `preclean/dockets/qui_tam_dockets.csv` (continuous append), plus
  `preclean/dockets/employment_retaliation.csv`.
- **Grain:** one row per docket/case event. **Join:** defendant name → org resolution;
  plaintiff name (for retaliation suits) → Model B grievance signal.
- **Why:** three jobs — (a) **first-to-file diligence** (existential for Model C),
  (b) unsealed FCA outcomes = Model C labels, (c) retaliation/wrongful-termination
  plaintiffs = Model B's warmest leads.
- **Cadence:** continuous alerts.

### 11. State Medicaid open data (3–5 priority states)
- **Get:** each state's Medicaid open-data portal / provider directories (start with
  the states most represented in our Spending coverage).
- **Drop:** `preclean/state/<ST>_<dataset>.csv`.
- **Why:** Medicaid is the highest-fraud-density, least-mined domain; published state
  files substitute for the DUA-restricted T-MSIS RIFs.
- **Powers:** state-level peer baselines and EVV/personal-care signals where published.

---

## Priority 3 — the Model B people layer (GATED: license + FCRA review first)

### 12. People-data vendor (pick one to start: People Data Labs; alt: Apollo/ZoomInfo)
- **Get:** commercial license — **must explicitly permit this use case in writing.**
- **Drop:** `preclean/people/workforce.parquet` (vendor export: person id, name-hash/
  contact per license, employer string, title, seniority, start/end dates, location).
- **Join:** employer string → canonical Org via `entity_graph/person_resolver.py`
  (Splink probabilistic linkage — the stub this data activates).
- **Why:** the individual-resolution layer; Model B does not exist without it. Tenure
  dates power the point-in-time **tenure-overlap gate**.
- **Guardrails:** privacy/FCRA review before purchase; the inference is sensitive from
  creation (01-legal-compliance.md).

### 13. WARN notices
- **Get:** free — https://www.dol.gov/agencies/eta/layoffs/warn + each state workforce
  agency's posting (start with our top billing states).
- **Drop:** `preclean/warn/warn_<ST>.csv`.
- **Grain:** employer × notice date × headcount. **Join:** employer name → org resolution.
- **Why:** a free, timed, *involuntary*-departure cohort signal — one of the strongest
  propensity predictors; power multiplies when the WARN'd employer is Model-A-flagged.
- **Powers:** Model B2 involuntary-departure flag + recency; campaign surge timing.

### 14. Glassdoor / Indeed review text
- **Get:** subject to platform terms — prefer licensed aggregators; counsel review on
  collection method before any scraping.
- **Drop:** `preclean/reviews/reviews.parquet` (employer, date, text, rating).
- **Why:** fraud-adjacent grievance language ("told to upcode", "billing for visits
  that didn't happen") → per-employer fraud-grievance score.
- **Powers:** weak Model A corroboration; Model B2 grievance density; message angles.

### 15. Professional-organization rosters (AAPC / HCCA / AHIMA)
- **Get:** member directories / conference lists, per each org's terms.
- **Drop:** `preclean/people/certified_<org>.csv`.
- **Why:** directories of exactly the highest-credibility personas (certified coders,
  compliance professionals); a credential is a professional-identity propensity marker.

### 16. State licensing boards
- **Get:** state board rosters + disciplinary actions (per state).
- **Drop:** `preclean/licensing/<ST>_<board>.csv`.
- **Why:** person-level discipline = grievance/credibility marker for Model B;
  license-change events are departure/distress signals.

---

## Priority 4 — corroboration (sequence after the core)

| # | Source | Get | Why / powers |
|---|---|---|---|
| 17 | NADAC drug pricing | data.medicaid.gov (NADAC weekly) | normalize pharmacy cost; spread/markup anomalies; 340B detection |
| 18 | Hospital/payer price transparency | each hospital/payer site (machine-readable) | pricing-anomaly corroboration; heavy ETL — defer |
| 19 | State APCDs (~20 states) | apcdcouncil.org directory; per-state licenses | better peer denominators; corroboration |
| 20 | Form 990 (Sch R) / SEC / Secretary of State | ProPublica Nonprofit Explorer; EDGAR; opencorporates | owner/officer graph enrichment; related parties |
| 21 | USAspending | usaspending.gov | entity enrichment; contract-fraud angle |

## Explicitly out of scope (do not procure)

- **T-MSIS RIFs / CMS LDS** — DUAs restrict to approved research; litigation-targeting
  use very likely barred. Use published state Medicaid files instead.
- **CMS Preclusion List** — not publicly downloadable; no hard dependency.
- **Komodo/IQVIA/Optum/Merative claims** — licenses bar litigation-targeting; at most a
  future corroboration layer with explicit written permission.

## Ingestion checklist for every new source (the adapter contract)

1. Column map (canonical name → candidate headers) like `integrate.py`'s `NPPES_COLS`.
2. All-VARCHAR read; NPIs through `canonicalize_series` with quarantine.
3. Names/addresses through the shared normalizers.
4. Row-count (and dollar, where applicable) assertions that raise.
5. Output: one Parquet per table + a section in the QA report.
6. Register the join in `DATA_DICTIONARY.md` and the feature(s) it powers in
   `docs/platform/04-model-a.md` (or 05/06).
