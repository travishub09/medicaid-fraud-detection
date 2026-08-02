# DATA FETCH RUNBOOK — every pull, every year, exact steps

This is the operator's single list: what to download, where to get it, what to
name it, where to save it, and what to do right after. Written for the machine
at `C:\Users\treyr\OneDrive\Desktop\data`. The code reads the folders named
here (they come from the source registry, `python -m src.preflight --registry`).

**The three standing rules, before anything else:**

1. **Years belong in filenames.** Save `partb_2024.csv`, never `partb.csv`
   over an old file. The vintage audit reads filenames; an undated file is an
   UNKNOWN it will nag about forever.
2. **After every download session, run the ritual:**
   ```
   cd /d C:\Users\treyr\medicaid-fraud-detection
   python -m src.preflight --data-root C:\Users\treyr\OneDrive\Desktop\data
   python -m src.preflight --editions
   ```
   The first says whether the code can see what you saved. The second refreshes
   `preclean\EDITIONS.csv` — open it and fill `data_period` for anything
   undated (the period the DATA covers, not the day you downloaded it).
3. **Frozen builds must not see post-cutoff snapshots.** Current-quarter
   editions of snapshot files (POS, PBJ, Care Compare, OPAIS) go to
   `preclean\pos_current_hold\` (or a sibling hold folder) while a frozen
   build runs. Pre-cutoff editions live in the real folders.

---

## PART 1 — THIS WEEK (the fix list, in priority order)

### 1. POS, the 2023 editions (three datasets) — fixes a confirmed vintage leak
- **Why:** v4's frozen matrix read Q1-2026 POS editions; bed counts and
  certification status from 2026 leaked into a 2023 freeze.
- **Where:** data.cms.gov — search each exact title:
  1. "Provider of Services File - Internet Quality Improvement and Evaluation
     System" (the important one: hospice, SNF, home health)
  2. "Provider of Services File - Quality Improvement and Evaluation System"
  3. "Provider of Services File - Clinical Laboratories"
- **Pull:** click the dataset title → View Data → switch the version dropdown
  from "Q2 2026" to **Q4 2023** → export/download CSV. Do NOT use the
  quick Download link on search results (it grabs the latest).
- **Save:** `preclean\pos\`, keep the CMS filename (it carries Q4_2023).
- **Then:** move every `*2026*` file out:
  `move preclean\pos\*2026* preclean\pos_current_hold\`

### 2. PBJ staffing, 2023 Q4 — same vintage leak, second file
- **Where:** data.cms.gov — "Payroll Based Journal Daily Nurse Staffing"
- **Pull:** same version-dropdown dance → **2023 Q4**.
- **Save:** `preclean\facility\pbj_daily_staffing_2023Q4.csv`; move the
  2025Q4 file to the hold folder.

### 3. Care Compare 2023 archives — deficiencies + hospice
- **Where (NOT the main data.cms.gov search — these live on the provider-data
  section's Archived Data pages):**
  - data.cms.gov/provider-data/archived-data/**nursing-homes**
  - data.cms.gov/provider-data/archived-data/**hospice-care**
  (or open any Care Compare dataset page and click "Archived data" in the
  left menu.)
- **Pull:** each page lists monthly zip snapshots — take **December 2023**
  (or the closest month NOT after it). Extract the zips; from the
  nursing-home zip keep the csv whose name contains **HealthDeficiencies**;
  from the hospice zip keep the provider-level csv (it carries the
  live-discharge measures).
- **Save:** `preclean\facility\deficiencies_2023-12.csv` and
  `preclean\facility\hospice_2023-12.csv`. Move the undated `hospice.csv`
  and `deficiencies.csv` to a `preclean\facility_current_hold\` folder the
  same way as POS.

### 4. The minutes reference — wakes impossible_day's time input
- **CORRECTED 2026-08-02:** the RVU zip's PPRRVU file carries payment weights,
  NOT minutes (the trap the operator hit). The times live in the separate
  **Physician Time** file that ships with the fee-schedule rule.
- **Where:** cms.gov — search "PFS Federal Regulation Notices", open the
  **CY 2023 PFS Final Rule** page (CMS-1770-F), Downloads section, the zip
  named like **"CY 2023 PFS Final Rule Physician Time"**.
- **Pull:** the spreadsheet inside (HCPCS + pre/intra/post service times in
  minutes).
- **Save:** `preclean\hcpcs_time\physician_time_2023.xlsx` (keep the year);
  a converter turns it into the `hcpcs,minutes` map the adapter reads. The
  PPRRVU file, if downloaded, is still useful — park it at
  `preclean\reference\rvu\` (global-period days feed a future unbundling
  scheme), never in `hcpcs_time\`.

### 5. DocGraph referral edges — wakes dme_ring's missing inputs
- **Where:** docgraph.org (CareSet) — "Hop Teaming" datasets; the
  2015-2017 editions are free downloads.
- **Pull:** the newest free year. Older is fine for a 2023 freeze — the
  edges pre-date the cutoff, which is exactly what we want.
- **Save:** `preclean\docgraph\docgraph_2017.csv` (rename to carry the year).

### 6. Resume the case-label harvest (Manus, ~45 state windows left)
- **Why:** the single highest-leverage item — unlocks per-scheme case
  validation AND lifts v4's HOLD.
- **How:** double-click `start_harvest.bat` in Downloads (it paces itself;
  rerun it to continue the batch). Uses Manus credits.

### 7. Owner snapshots — start the monthly cadence
- **How:** `make owner-snapshot` (from the repo folder), then a calendar
  reminder for the 1st of every month. ownership_turnover lights at two.

---

## PART 2 — THE STANDING REFRESH CALENDAR

### Monthly (first of the month, ~15 minutes total)
| source | where | pull | save as |
|---|---|---|---|
| LEIE exclusions | oig.hhs.gov → Exclusions → download the updated database (csv) | the full updated csv | `preclean\Caught.csv` refresh + note month in EDITIONS.csv |
| NPPES deactivation report | cms.gov — search "NPPES Deactivated NPI Report" | the current zip | `preclean\nppes_deactivation\deactivation_YYYY-MM.zip` |
| Owner snapshot | (no download) | run `make owner-snapshot` | automatic |
| Order & Referring | data.cms.gov — "Order and Referring" | current csv | `preclean\order_referring\order_referring_YYYY-MM.csv` |

### Quarterly
| source | where | pull | save as |
|---|---|---|---|
| MUE tables (3) | cms.gov — search "Medically Unlikely Edits" (NCCI page) | practitioner + DME + facility outpatient csvs | `preclean\reference\mue\mue_practitioner.csv`, `mue_dme.csv`, `mue_facility.csv` (note quarter in EDITIONS.csv) |
| POS trio | data.cms.gov (the three titles in Part 1) | newest quarter | `preclean\pos_current_hold\` (current editions live in the hold folder; only pre-cutoff editions sit in `preclean\pos\`) |
| PBJ staffing | data.cms.gov | newest quarter | hold folder, same rule |
| NADAC | data.medicaid.gov — "NADAC (National Average Drug Acquisition Cost)" | current full csv | `preclean\nadac\nadac_YYYY-MM.csv` |
| Government-interest overlay | (no download) | refresh the curated OIG Work Plan table per CLAUDE.md | — |

### Annual (each summer, when CMS posts the new year)
| source | where (search title on data.cms.gov) | save as |
|---|---|---|
| Part B | "Medicare Physician & Other Practitioners - by Provider and Service" | `preclean\partb\partb_YYYY.csv` |
| Part D | "Medicare Part D Prescribers - by Provider and Drug" | `preclean\partd\partd_YYYY.csv` |
| Opioid | "Medicare Part D Opioid Prescriber Summary File" | `preclean\opioid\opioid_prescriber_YYYY.csv` |
| DMEPOS | "Medicare Durable Medical Equipment, Devices & Supplies - by Referring Provider and Service" | `preclean\dmepos\dmepos_YYYY_detail.csv` |
| Open Payments | openpaymentsdata.cms.gov → Download → General Payments, program year | `preclean\open_payments\open_payments_YYYY.csv` |
| Market Saturation | "Market Saturation & Utilization State-County" | `preclean\saturation\saturation_YYYY.csv` |
| HCRIS cost reports | cms.gov — "Cost Reports by Fiscal Year" (hospital, SNF, HHA) | `preclean\hcris\` with year in each name |
| RVU / minutes | "PFS Relative Value Files" RVU{YY}D | refresh `preclean\hcpcs_time\hcpcs_minutes.csv` + note year |
| NUCC taxonomy | nucc.org → code sets → csv | `preclean\nucc\nucc_taxonomy_YYYY.csv` |

**Keep the old years.** Never overwrite `partb_2023.csv` with 2024 — the
trends and frozen runs need the history. New year = new file beside the old.

### As-needed / already flowing
- **OpenSanctions state exclusions:** refresh the bulk targets file from
  us.opensanctions.org when doing a label rebuild →
  `preclean\opensanctions\targets.simple.csv` (note the pull date).
- **340B OPAIS:** 340bopais.hrsa.gov → reports → Covered Entity Daily Report
  (xlsx) → `preclean\hrsa_340b\opais_YYYY-MM.xlsx`.
- **HUD ZIP-county crosswalk:** huduser.gov (free account) → USPS crosswalk
  files → `preclean\hud\zip_county.csv` (changes rarely; note quarter).
- **Medicare revocations:** data.cms.gov — search "revocation" →
  `preclean\revocations\revoked_providers_YYYY-MM.csv`.

---

## PART 3 — DATE DISCIPLINE (what "verify the dates" means in practice)

1. **Filename first.** If the file name carries the period (`partb_2023.csv`,
   `*Q4_2023*`), you're done — the vintage audit reads it automatically.
2. **No date in the name?** Find the period: the download page said it
   ("Latest data available: Q2 2026"), or the file's Properties modified
   date approximates the download, or the first rows carry a date column.
   Then either rename with the period, or fill the `data_period` column in
   `preclean\EDITIONS.csv` (run `python -m src.preflight --editions` to
   refresh the sheet; your entries are never overwritten).
3. **The four core files need periods too:** `Spending.csv` (service months
   are inside the data — record the min/max month), `NPPES.csv`, `PECOS.csv`,
   `Caught.csv` (record the edition month for each).
4. **Frozen-run rule:** before any `frozen_*` build, snapshot-type sources in
   the live folders must be pre-cutoff editions; current editions wait in
   the hold folder. The SCHEME_TRIAGE vintage section is the check that this
   held — read it after every frozen run.

---

## PART 4 — GATED OR DECISION ITEMS (money / licensing; for Trey + Brad)

| item | what it unlocks | the decision |
|---|---|---|
| SSA Death Master File | billing_after_death (invalid_identity completes) | paid NTIS subscription |
| OpenSanctions commercial license | production use of the state-exclusion widening | the one Brad decision (per roadmap) |
| CMS Preclusion List | widens the PU label | sponsor-channel access, gated |
| NDC-grain claims | drug_spread_anomaly, controlled_substance_share | does not exist in our extract; do NOT chase a file for this — it needs a different data agreement |
| People-data vendor | Model B person↔employer resolution | FCRA review first, per legal guardrails |

---

## PART 5 — THE RE-DOWNLOAD-WITH-DATES CAMPAIGN (every undated file)

`preclean\EDITIONS.csv` is the authoritative list (currently ~34 files with
no known data period). For each file there are two ways to fix it — pick the
cheap one:

- **Fill:** if you know the period (download email, the page you got it from,
  the file's Properties date), just fill `data_period` in EDITIONS.csv.
- **Re-download fresh with a dated name:** for small files this is faster and
  more trustworthy than archaeology, and it refreshes the data at the same
  time.

**Re-download list (all small and quick; save with the date, keep the old
file until preflight shows [ok]):**

| file today | source | save as |
|---|---|---|
| `Caught.csv` (LEIE) | oig.hhs.gov → Exclusions database | refresh + record month in EDITIONS |
| `nppes_deactivation\deactivation.zip` | cms.gov "NPPES Deactivated NPI Report" | `deactivation_YYYY-MM.zip` |
| `order_referring\order_referring.csv` | data.cms.gov "Order and Referring" | `order_referring_YYYY-MM.csv` |
| `nadac\nadac.csv` | data.medicaid.gov "NADAC" | `nadac_YYYY-MM.csv` |
| `revocations\revoked_providers.csv` | data.cms.gov (search "revocation") | `revoked_providers_YYYY-MM.csv` |
| `opensanctions\targets.simple.csv` | us.opensanctions.org bulk | re-pull + record date |
| `hrsa_340b\opais.xlsx` | 340bopais.hrsa.gov daily report | `opais_YYYY-MM.xlsx` |
| `nucc\nucc_taxonomy.csv` | nucc.org code sets | `nucc_taxonomy_YYYY.csv` |
| `saturation\saturation.csv` | data.cms.gov "Market Saturation" | `saturation_YYYY.csv` |
| `facility\pbj.csv`, `hospice.csv`, `deficiencies.csv` | Part 1 items 2-3 | dated editions replace them |
| `pos\pos.csv`, `pos_facility.csv` | Part 1 item 1 | already replaced by Q4_2023 trio |
| `hcris\` (3 files) | cms.gov "Cost Reports by Fiscal Year" | re-pull with FY in each name |
| `docgraph\docgraph.csv` | docgraph.org | `docgraph_YYYY.csv` |
| `owners\owners.csv` | data.cms.gov "All Owners" files | `owners_YYYY-MM.csv` |
| `mue\` (3 tables) | cms.gov NCCI MUE page | record the quarter in EDITIONS |
| `hud\zip_county.csv` | huduser.gov crosswalk | record the quarter |

**Fill-only list (do NOT re-download — too big, and the period lives inside
the data):**

- `Spending.csv` — the service months ARE the period. Extract them:
  ```
  python -c "import duckdb; print(duckdb.sql(\"select min(service_month), max(service_month) from 'C:/Users/treyr/OneDrive/Desktop/data/processed/spending_fact.parquet'\"))"
  ```
  Put that range in EDITIONS.csv.
- `NPPES.csv`, `PECOS.csv` — the edition month is in your download history /
  the CMS file-name at download time; fill from memory or Properties date.
- Annual PUFs already carrying years (`partb_2023.csv` …) need nothing; the
  undated base copies (`partb.csv` etc.) can be DELETED once the dated years
  cover them — check with `python -m src.preflight` after.

## PART 6 — DID I GET THE RIGHT FILE? (the content check)

A downloaded file with the right name but wrong layout fails quietly ("could
not find X column" in SOURCES_REPORT) or, worse, half-works. After any new
download, this table is the 30-second check: open the csv, look at the header
row, confirm the key columns exist. If they don't, you likely grabbed the
summary/alternate version of the dataset — and the note column says the
known trap.

| source | the columns that MUST be there | known trap |
|---|---|---|
| partb | Rndrng_NPI, HCPCS_Cd, Tot_Srvcs, Tot_Benes | grabbing "by Provider" (no HCPCS) instead of "by Provider and Service" |
| partd | Prscrbr_NPI, Brnd_Name, Gnrc_Name, Tot_Clms, Tot_Drug_Cst | "by Provider" summary lacks the drug rows |
| dmepos | Rfrg_NPI, HCPCS_Cd, Tot_Suplr_Srvcs | the by-referring SUMMARY lacks HCPCS and fails — need "by Referring Provider and Service" |
| opioid | Prscrbr_NPI, Tot_Clms, Opioid_Tot_Clms, Opioid_LA_Tot_Clms | — |
| open_payments | Covered_Recipient_NPI, Total_Amount_of_Payment_USDollars | older years name the NPI column differently — send me the header if it fails |
| pos | PRVDR_NUM, BED_CNT, GNRL_FAC_TYPE_CD, STATE_CD | — |
| facility/PBJ | PROVNUM, WorkDate, MDScensus, Hrs_RN | the "non-nurse" PBJ file lacks Hrs_RN |
| hcris | PRVDR_NUM, TOTAL_COSTS, TOTAL_CHARGES | raw HCRIS is worksheet-format; we use the flattened cost-report csvs |
| nadac | NDC, NADAC Per Unit, Classification for Rate Setting | — |
| mue | an HCPCS/CPT column + an MUE value column | the AMA copyright worksheet extracts first from the zip — take the data sheet |
| hrsa_340b | 340B ID, Entity Name, Contract Pharmacy Name | must be the 3-worksheet Covered Entity DAILY report xlsx |
| nppes_deactivation | NPI, NPI Deactivation Date | keep as the zip; the loader unzips it |
| order_referring | NPI, PARTB, DME, HHA | — |
| docgraph | from_npi, to_npi, patient_count | — |
| nucc | Code, Grouping, Classification | — |

**When the file is wrong-but-interesting:** sometimes the "wrong" file
carries fields we never planned for. Do not delete it — paste me the header
row. Unplanned fields are exactly what the scheme-discovery loop mines for
new detectable schemes (the Rhode Island case came from a second look like
this). Wrong file AND boring → the table above tells you which right file to
fetch.

---

*Update this document whenever a source, folder, or cadence changes — it is
the operator contract, and a stale runbook is worse than none. Companion
references: docs/DATA_ACQUISITION_GUIDE.md (per-source deep detail),
docs/SOURCE_REGISTRY.md (the wired-adapter map, regenerate with
`python -m src.preflight --markdown`).*
