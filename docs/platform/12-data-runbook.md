# 12 — Data Acquisition Runbook (click-by-click)

The [procurement map (09)](09-data-procurement.md) explains *what* each source is
and *why* it matters. This runbook is the operator's version: exactly where to
click, what to download, what to name it, where to put it, and how to verify it
worked. Work top to bottom; each block ends with a verification step.

**Ground rules for every download:**
- Never open/re-save a file in Excel (it silently destroys leading zeros in IDs).
- Put files exactly at the paths shown; the pipeline's defaults expect them.
- Large files: prefer the CSV download; if only ZIP is offered, unzip, keep the CSV.

```
All paths below are under:  ~/Desktop/data/preclean/
```

---

## Block 0 — The five core files (the existing pipeline's inputs)

These power the Medicaid detection core. If you already have them, skip to Block 1.

### 0.1 NPPES (provider registry) → `NPPES.csv`
1. Go to https://download.cms.gov/nppes/NPI_Files.html
2. Download the **Full Replacement Monthly NPI File** (a large ZIP, ~1 GB).
3. Unzip. The main file is named like `npidata_pfile_YYYYMMDD-YYYYMMDD.csv`.
4. Rename to `NPPES.csv`, place at `preclean/NPPES.csv`.
- **Verify:** `head -1 ~/Desktop/data/preclean/NPPES.csv` shows a header starting
  with `"NPI","Entity Type Code",...`

### 0.2 LEIE (exclusion list) → `Caught.csv`
1. Go to https://oig.hhs.gov/exclusions/exclusions_list.asp
2. Download the **UPDATED LEIE Database** CSV (monthly refresh).
3. Rename to `Caught.csv`, place at `preclean/Caught.csv`.
- **Verify:** header contains `LASTNAME,FIRSTNAME,...,EXCLTYPE,EXCLDATE,...,NPI`.
- **Refresh monthly** — exclusions are the integrity backbone.

### 0.3 PECOS enrollment → `PECOS.csv`
1. Go to https://data.cms.gov/provider-characteristics/medicare-provider-supplier-enrollment/medicare-fee-for-service-public-provider-enrollment
2. Download the latest quarterly **Provider Enrollment** CSV.
3. Rename to `PECOS.csv`, place at `preclean/PECOS.csv`.
- **Verify:** header contains `NPI`, `PECOS_ASCT_CNTL_ID`, `ENRLMT_ID`.

### 0.4 CMS ownership files → `owners/*.csv`
1. Go to https://data.cms.gov and search **"All Owners"**.
2. Download the All-Owners CSV for each of: **Hospital, Home Health Agency
   (HHA), Hospice, Skilled Nursing Facility (Nursing), FQHC**.
3. Place all five under `preclean/owners/` (keep names containing the facility
   type, e.g. `HospiceOwners.csv` — the loader infers type from the filename).
- **Verify:** each header contains `ENROLLMENT ID`, `ASSOCIATE ID - OWNER`,
  `ROLE TEXT - OWNER`, `PERCENTAGE OWNERSHIP`.

### 0.5 Medicaid spending → `Spending.csv`
The billing-NPI × servicing-NPI × HCPCS × month extract (with patients/lines/
paid) the detection core was built on. Required columns (exact):
`BILLING_PROVIDER_NPI_NUM, SERVICING_PROVIDER_NPI_NUM, HCPCS_CODE,
CLAIM_FROM_MONTH, TOTAL_PATIENTS, TOTAL_CLAIM_LINES, TOTAL_PAID`.

**Public source (Feb 2026): HHS Open Data — "Medicaid Provider Spending by
HCPCS."** This is a published, cell-suppressed aggregate *derived* from T-MSIS
(2018–2024; FFS + managed care + CHIP), with the **exact seven columns above** —
it drops in with no transformation.
1. https://opendata.hhs.gov/datasets/medicaid-provider-spending/ → Download ZIP
   (~3.5 GB). (Mirror: Hugging Face `HHS-Official/medicaid-provider-spending`.)
2. Unzip → rename the CSV to `Spending.csv`, place at `preclean/Spending.csv`.
- **Legal note:** this is a PUBLISHED public file (no DUA) — the lawful
  substitute the data policy anticipates, NOT the gated raw T-MSIS RIF/TAF
  (which remains barred for litigation-targeting). Confirm the open-data terms
  and get a quick counsel read given the sensitive purpose, then proceed.
- **Suppression:** rows under 12 claim lines AND 12 beneficiaries are dropped,
  so very low-volume provider/procedure/months are absent. Fine for outlier
  targeting — the high-dollar universe is fully present.
- **If you instead get a private/arranged extract**, just match the same seven
  columns and the same steps apply.

### 0.6 Revoked Medicare providers → `revocations/revoked_providers.csv`
HHS Open Data — "Revoked Medicare Providers and Suppliers" (PECOS, ~250 KB):
revoked enrollments still under an active re-enrollment bar.
1. https://opendata.hhs.gov/datasets/medicare-revoked-providers-and-suppliers/ →
   Download ZIP → place the CSV at `preclean/revocations/revoked_providers.csv`.
- **Verify:** header has `NPI`, `REVOCATION_EFCTV_DT`, `REENROLLMENT_BAR_EXPRTN_DT`.
- **Powers:** an integrity/exclusion source alongside LEIE/SAM — normalized to
  the exclusions schema by `src/enforcement/medicare_revocations.py`
  (`normalize_revocations`), adding revoked-provider nodes + a billing-after-
  revocation signal.

**Snapshot rule (start now, costs nothing):** when you refresh the owners
files each quarter, do NOT overwrite — keep dated copies
(`owners/2026-06/HospiceOwners.csv`, …). Diffing snapshots powers the
ownership-churn detector (docs 14, A7).

**After Block 0, run:** `python3 -m src.attempt_2.ingest.integrate` and check
`processed/QA_REPORT.md` shows all assertions ✅.

---

## Block 1 — Priority-1 CMS files (light up the dormant Model A schemes)

Adapters are already built and tested against these files' real headers
(`src/ingest_cms/`) — download, drop, run.

### 1.1 Medicare Part B → `partb/partb_YYYY.csv`
1. Go to https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners
2. Open **"Medicare Physician & Other Practitioners – by Provider and Service"**.
3. For each of the latest 3 years: download the CSV (each ~2–4 GB).
4. Place at `preclean/partb/partb_2022.csv` (etc., one per year).
- **Verify:** header contains `Rndrng_NPI`, `HCPCS_Cd`, `Tot_Srvcs`, `Tot_Benes`,
  `Avg_Mdcr_Alowd_Amt`.
- **Powers:** upcoding, overutilization, impossible-day, single-code-mill
  schemes — and the future public lookup tool.

### 1.2 Medicare Part D → `partd/partd_YYYY.csv`
1. Same site, dataset **"Medicare Part D Prescribers – by Provider and Drug"**.
2. Latest 3 years → `preclean/partd/partd_2022.csv` etc.
- **Verify:** header contains `Prscrbr_NPI`, `Brnd_Name`, `Gnrc_Name`,
  `Tot_Clms`, `Tot_Drug_Cst`.
- **Powers:** brand-steering and high-cost-drug schemes; opioid share via the
  companion "by Provider" summary file (optional, same page).

### 1.3 Medicare DMEPOS → `dmepos/dmepos_referring_YYYY.csv`
1. Same site, dataset **"Medicare Durable Medical Equipment, Devices & Supplies
   – by Referring Provider and Service"**.
2. Latest 2 years → `preclean/dmepos/dmepos_referring_2022.csv` etc.
- **Verify:** header contains `Rfrg_NPI`, `HCPCS_Cd`, `Tot_Suplr_Srvcs`,
  `Avg_Suplr_Mdcr_Alowd_Amt`.
- **Powers:** DME high-cost-item and concentration schemes (ICP 2).

### 1.4 Market Saturation → `saturation/market_saturation.csv`
1. Go to https://data.cms.gov and search **"Market Saturation & Utilization
   State-County"**.
2. Download the latest CSV → `preclean/saturation/market_saturation.csv`.
- **Verify:** header contains `Type of Service`, `State and County FIPS Code`,
  `Number of Providers`, `Number of Fee-for-Service Beneficiaries`.
- **Powers:** `market_saturation_index` / the `saturation_fraud` scheme — the
  CMS program-integrity over-supply prior for home health/hospice/SNF/lab
  (adapter: `src/ingest_cms/saturation.py`; state-grain attach until the
  Census ZIP→county mapping lands).

### 1.6 PBJ nurse staffing + Care Compare → `facility/`
The ICP-1 facility signal package (adapter: `src/ingest_cms/facility.py`;
CCN grain, joined to orgs via the PECOS enrollment CCN↔NPI crosswalk).
1. data.cms.gov → search **"Payroll Based Journal Daily Nurse Staffing"** →
   latest quarter CSV → `preclean/facility/pbj_daily_staffing_YYYYQn.csv`.
   - **Verify:** header contains `PROVNUM`, `MDScensus`, `Hrs_RN`, `Hrs_LPN`,
     `Hrs_CNA`.
2. data.cms.gov/provider-data → search **"Hospice - Provider Data"** (the
   measure-level file) → `preclean/facility/hospice_measures.csv`.
   - **Verify:** header has a CCN column, `Measure Code`/`Measure Name`,
     `Score`; live-discharge measures are matched by name.
3. data.cms.gov/provider-data → search **"Health Deficiencies"** (nursing
   homes) → `preclean/facility/health_deficiencies.csv`.
- **Powers:** `pbj_understaffing` + `deficiency_count` → the
  `worthless_services` scheme; `hospice_live_discharge_rate` → the
  `hospice_ineligibility` scheme. Facility peers = size band × state.

### 1.5 SAM.gov exclusions → `sam/sam_exclusions.csv`
1. Go to https://sam.gov/data-services → **Exclusions** → Public V2 extract (CSV).
   (Free account required.)
2. Place at `preclean/sam/sam_exclusions.csv`.
- **Powers:** government-wide debarments beyond LEIE; more exclusion nodes in
  the graph.

### 1.7 June-2026 sweep files (see docs/platform/15 for the full rationale)
All free; each lights up a scheme automatically once loaded.
1. **Opioid prescriber rates** → `opioid/opioid_prescriber_YYYY.csv`
   - data.cms.gov → "Medicare Part D Opioid Prescriber Summary File".
   - **Verify:** header has `Prscrbr_NPI`, `Opioid_Tot_Clms`, `Tot_Clms`.
   - **Powers:** the `pill_mill` scheme (`ingest_cms/opioid.py`).
2. **NPPES deactivation** → `nppes/npi_deactivation.csv`
   - download.cms.gov/nppes → monthly NPI deactivation file.
   - **Verify:** header has `NPI`, `NPI Deactivation Date`.
   - **Powers:** the `invalid_identity` scheme — billing after deactivation
     (`ingest_cms/nppes_deactivation.py`; needs the spending fact too).
3. **HRSA 340B OPAIS** → `hrsa/opais_daily.csv`
   - 340bopais.hrsa.gov → Daily Report (covered entities + contract pharmacies).
   - **Verify:** header has an entity-name column + `Contract Pharmacy Name`.
   - **Powers:** the `contract_pharmacy` scheme (`ingest_cms/hrsa_340b.py`).
4. **Provider of Services (POS)** → `pos/pos_facility.csv`
   - data.cms.gov → "Provider of Services" (facility / clinical-lab files).
   - **Verify:** header has `PRVDR_NUM` (CCN) + a bed-count column.
   - **Powers:** `capacity_mismatch` → `worthless_services` (`ingest_cms/pos.py`;
     needs a per-CCN billed-volume table too).
5. **Order & Referring** → `order_referring/order_referring.csv`
   - data.cms.gov → "Order and Referring".
   - **Verify:** header has `NPI` + `DME`/`PARTB`/`HHA`/`PMD` eligibility flags.
   - **Powers:** `ineligible_referral_share` → sharpens `dme_ring`
     (`ingest_cms/order_referring.py`; needs DME claims with a referring NPI).
6. **NADAC drug pricing** → `nadac/nadac.csv` (data.medicaid.gov, weekly):
   `drug_spread_anomaly` → drug_outlier (needs NDC-level claims; `nadac.py`).
7. **HCRIS cost reports** → `hcris/hcris_flat.csv` (cms.gov; the team flattens the
   worksheet extract to per-CCN cost fields): the `cost_report_fraud` scheme
   (`hcris.py`).
8. **DocGraph shared-patient** → `docgraph/shared_patient.csv` (archived
   CMS/DocGraph): `refers_to` edges + closed-loop ring detection
   (`docgraph.py` + `ring_detection.referral_rings`). Vintages are old → treat as
   historical corroboration.
9. **Census county population + ZIP→county** → `census/` (census.gov / HUD):
   the local-denominator half of A5 (`census_population.py`).
10. **SSA Death Master File** → `dmf/dmf.csv` (public/NTIS): DOB-corroborated
    deceased-provider billing → `invalid_identity` (`enforcement/death_master.py`).
11. **State licensing boards** → `state_licensing/<ST>.csv` (per-state): adverse
    board actions → exclusion nodes (`enforcement/state_licensing.py`, per-state
    column map).
12. **openFDA recalls** (live API, no file): drug/device recall events matched to
    orgs (`feeds/openfda.py`).
13. **OpenSanctions** (commercial license — Brad): aggregated LEIE + ~45 state
    exclusion lists → exclusion nodes (`enforcement/opensanctions.py`).

### 1.8 Neo4j interactive graph (optional)
After the graph build, also emit a Neo4j bulk import:
`python3 -m src.entity_graph --input <dir> --out <graph> --neo4j-bulk <neo4j_dir>`,
then run the generated `import.sh` against a stopped Neo4j (Community or Aura).
For an idempotent online load instead, `pip install neo4j` and call
`export_to_neo4j(graph_dir, session=...)`. No PHI in the graph (IDs/structure
only), but host it on the same secured machine.

---

## Block 2 — Free people-side signals (no license needed)

### 2.1 WARN notices → `warn/warn_<ST>.csv`
1. Each state posts its own list. Start with your top billing states. Examples:
   - Texas: https://www.twc.texas.gov/businesses/worker-adjustment-and-retraining-notification-warn-notices
   - California: https://edd.ca.gov/en/jobs_and_training/Layoff_Services_WARN
   - Florida: https://floridajobs.org (search "WARN")
   - National aggregate (unofficial but convenient): https://layoffdata.com
2. Download/export the notice list as CSV → `preclean/warn/warn_TX.csv` etc.
   (Column names vary by state — the loader handles common variants.)
- **Run:** `python3 -m src.sourcing.warn_monitor --warn preclean/warn/warn_TX.csv
  --graph-dir ~/Desktop/data/graph --erv ~/Desktop/data/model_a/erv_ranked.parquet
  --out ~/Desktop/data/sourcing`
- **Verify:** the run prints matched/unmatched counts; surge leads land in
  `sourcing/warn_surge_leads.parquet`.

### 2.1b Two free API signups (5 minutes total)
The automated feeds (docs 13) need two free keys in your `.env`:
1. **CourtListener** — courtlistener.com → create account → profile → API token → `COURTLISTENER_TOKEN`.
2. **SAM.gov** — sign in → Account Details → request public API key → `SAM_API_KEY`.
Then `make feeds-backfill` once, and `make feeds-refresh` on a weekly cron.

### 2.2 DOJ enforcement backfill → `enforcement/doj_cases.csv`
**SUPERSEDED by the automated DOJ feed** (`make feeds-backfill`, docs 13) — the
manual path below remains only as a fallback if the API is unavailable.
Until the automated fetcher ships, this is a manual/assisted task:
1. Go to https://www.justice.gov/news and filter by topic **"False Claims Act"**
   (also: https://www.justice.gov/civil/false-claims-act).
2. For each healthcare settlement announcement, paste the release text through
   `src.enforcement.parse_press_release` (a small driver script over a folder of
   saved texts works well), or hand-fill a CSV with the columns in
   `src/enforcement/case_db.py::CASE_COLUMNS`.
3. Save at `preclean/enforcement/doj_cases.csv`. Aim for 5–10 years of
   healthcare cases; even 100 rows makes the sector priors real.
- **Powers:** evidence-based sector priors (replacing the placeholders),
  Model A labels, Model C cold-start.

---

## Block 3 — Licensed / gated (do NOT acquire without the listed review)

| Source | Gate | Then |
|---|---|---|
| People-data vendor (PDL etc.) | written use-case permission + FCRA/privacy review (GAPS #19) | activates Model B |
| Glassdoor/Indeed review text | terms/counsel review of collection method | grievance NLP |
| State EVV / APCD | state-by-state agreements | visit-level Medicaid signals |

And the **never-acquire** list (legally barred from this use): T-MSIS research
files, CMS LDS/RIF, Komodo/IQVIA/Optum-style commercial claims. See
[02-data-sources.md](02-data-sources.md).

---

## The refresh calendar

| Cadence | What |
|---|---|
| Monthly | LEIE, SAM, WARN states, DOJ releases |
| Quarterly | PECOS, ownership files, NPPES (or monthly) |
| Annually (new vintage) | Part B, Part D, DMEPOS, Market Saturation |
| Quarterly | PBJ staffing, Care Compare hospice/deficiencies, OIG Work Plan table (`model_a/government_interest.py`) |
| After every refresh | re-run the pipeline order in [GETTING_STARTED](../GETTING_STARTED.md) Part 3 |
