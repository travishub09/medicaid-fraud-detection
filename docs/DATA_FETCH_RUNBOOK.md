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
- **Where:** data.cms.gov provider-data section — search "Nursing Home
  Provider Information" (deficiencies/citations) and "Hospice Provider Data"
  (live-discharge). Both have archived monthly/quarterly snapshots.
- **Pull:** the edition closest to (but not after) December 2023.
- **Save:** `preclean\facility\deficiencies_2023.csv` and
  `preclean\facility\hospice_2023.csv`.

### 4. The minutes reference — wakes impossible_day's time input
- **Where:** cms.gov — search "PFS Relative Value Files", download **RVU23D**
  (the 2023 final release, a zip).
- **Pull:** inside the zip take the **PPRRVU** csv (per-code work times).
- **Save:** `preclean\hcpcs_time\hcpcs_minutes.csv`. If the columns don't
  line up when the run reads it, paste me the header row — I'll cut a
  converter the same day.

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

*Update this document whenever a source, folder, or cadence changes — it is
the operator contract, and a stale runbook is worse than none. Companion
references: docs/DATA_ACQUISITION_GUIDE.md (per-source deep detail),
docs/SOURCE_REGISTRY.md (the wired-adapter map, regenerate with
`python -m src.preflight --markdown`).*
