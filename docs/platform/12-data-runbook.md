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
This is the proprietary/arranged extract the detection core was built on
(billing NPI × servicing NPI × HCPCS × month with patients/lines/paid). It comes
from your data arrangement, not a public click-path. Required columns:
`BILLING_PROVIDER_NPI_NUM, SERVICING_PROVIDER_NPI_NUM, HCPCS_CODE,
CLAIM_FROM_MONTH, TOTAL_PATIENTS, TOTAL_CLAIM_LINES, TOTAL_PAID`.

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
- **Powers:** the county over-supply prior for home health/hospice/DME (both ICPs).

### 1.5 SAM.gov exclusions → `sam/sam_exclusions.csv`
1. Go to https://sam.gov/data-services → **Exclusions** → Public V2 extract (CSV).
   (Free account required.)
2. Place at `preclean/sam/sam_exclusions.csv`.
- **Powers:** government-wide debarments beyond LEIE; more exclusion nodes in
  the graph.

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

### 2.2 DOJ enforcement backfill → `enforcement/doj_cases.csv`
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
| After every refresh | re-run the pipeline order in [GETTING_STARTED](../GETTING_STARTED.md) Part 3 |
