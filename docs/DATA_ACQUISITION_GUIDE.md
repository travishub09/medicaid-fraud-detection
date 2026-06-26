# Data Acquisition Guide — what to download, where to save it, what it unlocks

This is the step-by-step companion to `PROVIDER_FEATURES_FOR_MODEL.md`. Every
source below is **optional and skip-missing**: drop the file at the exact path
shown and the next `make provider-features` run lights up its scheme(s). The export
report (`PROVIDER_FEATURES_EXPORT_REPORT.md`) tells you, on each run, which sources
it found and which it skipped and why.

**Conventions**
- `DATA_ROOT` defaults to `~/Desktop/data` (override with `DATA_ROOT=/path make …`).
- Provider IDs are strings — never let a spreadsheet strip leading zeros from NPIs/ZIPs/CCNs. Save raw as CSV; the loaders read everything as text first.
- Paths below are exact: the file-finders look in `preclean/<source>/` and accept the named file or any `*.csv` in that folder.
- Exact dataset slugs on data.cms.gov change between vintages; where a deep link may drift, search the **dataset title** in quotes on the host shown.

---

## A. Core pipeline inputs (you almost certainly already have these)

These come out of the 13-stage detection pipeline (`make pipeline`); the export reads them from `processed/`.

| File | Path | Produced by |
|---|---|---|
| Spending fact (billing_npi × service_month × HCPCS × paid) | `processed/spending_fact.parquet` | `attempt_2.ingest.integrate` |
| Provider dimension (npi, taxonomy, name, …) | `processed/provider_dim.parquet` | `attempt_2.ingest.integrate` |
| Per-NPI v3 leads (the 5 anomaly concepts + label) | `detection/fraud_leads_v3.parquet` | `attempt_2.leads.refine_layer2_v3` |
| Entity graph (npi_to_org, org features, owned_by edges) | `graph/` | `make graph` |

The raw Medicaid spending source behind `spending_fact` is the **T-MSIS-derived
"Medicaid Provider Spending by HCPCS"** open-data extract; see
`docs/platform/09-data-procurement.md` and `12-data-runbook.md` for the full
pipeline procurement. Everything below is *additive* to this core.

---

## B. Quick-win adapter files (one CSV each → a new scheme)

For each: download the file, drop it at the path, re-run the export. No builder step.

### B1. Medicare Part B → `upcoding`
- **Get:** https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners — "**Medicare Physician & Other Practitioners — by Provider and Service**", latest 3–5 annual CSVs.
- **Save:** `preclean/partb/partb.csv` (or drop the annual files in `preclean/partb/`).
- **Needs columns:** rendering NPI, HCPCS, services, beneficiaries, average allowed amount (names auto-resolved).

### B2. Medicare Part D → `drug_outlier` + (with Open Payments) `pharma_kickback`
- **Get:** https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers — the "**Medicare Part D Prescribers - by Provider and Drug**" dataset (drug-level; **Download** the annual CSV, not the API).
- **Save:** `preclean/partd/partd.csv`.
- **Verify columns:** `Prscrbr_NPI`, `Brnd_Name`, `Gnrc_Name`, `Tot_Clms`, `Tot_Drug_Cst`.

### B3. DMEPOS → `dme_ring`
- **Get:** https://data.cms.gov/provider-summary-by-type-of-service/medicare-durable-medical-equipment-devices-supplies — pick the **"by Referring Provider and Service"** dataset (NOT plain "by Referring Provider", which is pre-aggregated with no HCPCS detail, and NOT the "by Supplier" files, which key on the supplier rather than the ordering physician). Grain: referring NPI × HCPCS.
- **Save:** `preclean/dmepos/dmepos.csv` (latest annual CSV; drop multiple years if desired).
- **Verify columns:** `Rfrg_NPI`, `HCPCS_Cd`, `Tot_Suplr_Srvcs`, `Avg_Suplr_Mdcr_Alowd_Amt` (auto-resolved). The per-HCPCS detail is what powers high-cost-item share + code concentration.
- **Note:** the dormant `dme_ordering_md_concentration` piece needs supplier↔referrer *pair* data no public DMEPOS file carries — it stays dormant.

### B4. CMS opioid metrics → `pill_mill`
The opioid breakout columns the adapter needs are per-provider. Two ways to get them:
- **Preferred (most reliable today):** the "**Medicare Part D Prescribers - by Provider**" *summary* file (one row per NPI — the per-provider aggregate, NOT "by Provider and Drug"). It carries `Opioid_Tot_Clms`, `Opioid_LA_Tot_Clms`, `Opioid_Prscrbr_Rate` alongside `Tot_Clms`. This is the file to use if the standalone opioid dataset only shows "by Geography."
- **Alternative:** the standalone "Medicare Part D Opioid Prescribing Rates - **by Provider**" if available (NPI grain). **Do NOT** use "Opioid Prescribing Rates - **by Geography**" (state/county/ZIP, no NPI).
- **Save:** `preclean/opioid/opioid.csv`.
- **Verify columns:** `Prscrbr_NPI`, `Tot_Clms`, `Opioid_Tot_Clms`, `Opioid_LA_Tot_Clms` (the long-acting split separates a chronic-pain practice from a diversion mill).

### B5. Open Payments → `pharma_kickback` (pairs with Part D)
- **Get:** https://openpaymentsdata.cms.gov/datasets — General + Research payments, latest 3 years.
- **Save:** `preclean/open_payments/open_payments.csv`.
- **Needs:** physician NPI, manufacturer, amount, product. (Kickback co-occurrence also reads `preclean/partd/`.)

### B6. NADAC drug pricing → `drug_outlier` (drug-spread piece; needs NDC claims too, see C4)
- **Get:** https://data.medicaid.gov → "**NADAC (National Average Drug Acquisition Cost)**" weekly reference.
- **Save:** `preclean/nadac/nadac.csv`.
- **Needs:** NDC, description, per-unit cost, classification.

### B7. CMS Market Saturation → `saturation_fraud`
- **Get:** https://data.cms.gov/tools — "**Market Saturation & Utilization State-County**", CSV export.
- **Save:** `preclean/saturation/saturation.csv`.
- **Needs:** service type, FIPS, state, county, provider count, beneficiary count. (Also needs `org_nodes`, already in `graph/`.)

### B8. HRSA 340B OPAIS → `contract_pharmacy`
- **Get:** https://340bopais.hrsa.gov → **Covered Entity Daily Report** (the Excel
  download — a 3-worksheet file: Covered Entities / Shipping Addresses / Contract
  Pharmacies). The Contract Pharmacies worksheet is the entity×pharmacy grain the
  adapter needs; `state` comes from the Covered Entities worksheet.
- **Save:** drop the native file at `preclean/hrsa_340b/opais.xlsx` — the adapter
  (`hrsa_340b.load_opais`) reads the .xlsx directly, picks the Contract Pharmacies
  worksheet, and merges `State` on the 340B ID. (Or export that one worksheet to
  `opais.csv` if you prefer a flat file; both are accepted.)
- **Needs:** entity id, name, entity type, state, contract pharmacy — all present in
  the Covered Entity Daily Report.

### B9. NPPES deactivation report → `invalid_identity` (deactivation piece)
- **Get:** https://download.cms.gov/nppes — the monthly "**NPPES Deactivated NPI Report**".
- **Save:** `preclean/nppes_deactivation/deactivation.csv`.
- **Needs:** NPI, deactivation date. (Also reads `processed/spending_fact.parquet`.)

### B10. Order & Referring → `dme_ring` (ineligible-referral piece)
- **Get:** data.cms.gov → "**Order and Referring**" (NPIs eligible to order/refer).
- **Save:** `preclean/order_referring/order_referring.csv`.
- **Needs:** NPI, eligibility flags (partb, dme, hha, pmd). (Also needs `processed/referred_claims.parquet`, see C5.)

### B11. Facility files (PBJ + Care Compare) → `worthless_services` + `hospice_ineligibility`
*These are CCN-grain — they also require the CCN→NPI crosswalk from C3.*
- **PBJ staffing:** data.cms.gov → "**Payroll Based Journal Daily Nurse Staffing**" → `preclean/facility/pbj.csv`.
- **Hospice live-discharge:** https://data.cms.gov/provider-data/ (Care Compare hospice measures) → `preclean/facility/hospice.csv`.
- **Deficiencies:** Care Compare health-deficiencies → `preclean/facility/deficiencies.csv`.

### B12. HCRIS cost reports → `cost_report_fraud`
*CCN-grain — also requires the C3 crosswalk.*
- **Get:** https://www.cms.gov/data-research/statistics-trends-reports/cost-reports (SNF / Hospital / HHA flat files).
- **Save:** `preclean/hcris/hcris.csv`.
- **Needs:** CCN, total costs, total charges, admin costs, related-party costs (varies by provider type).

### B13. Provider of Services (POS) → `worthless_services` (capacity piece, partial)
*CCN-grain — also requires the C3 crosswalk.* **Note:** `worthless_services` already
scores from PBJ understaffing + deficiencies (B11). The POS `capacity_mismatch`
input additionally needs a **per-CCN billed-volume** measure (services/visits per
CCN) that the orchestrator does not assemble automatically yet — so POS is procured
and the adapter is built, but this third input is not auto-wired into the export.
- **Get:** data.cms.gov → "**Provider of Services**" (facility + clinical-lab file).
- **Save:** `preclean/pos/pos.csv`.
- **Needs:** CCN, bed count, facility type, state.

---

## C. The unlock builders (a download + one build command)

### C1. SSA Death Master File → `invalid_identity` (billing-after-death)
- **Get:**
  - **Current (paid):** https://dmf.ntis.gov — the NTIS Limited-Access DMF subscription (the live, complete file).
  - **Free (stale):** the historical **public** DMF (pre-2011 deaths) is mirrored on archive.org and genealogy hosts — fine to stand the pipeline up and test, not for production coverage.
- **Save:** `preclean/dmf/dmf.csv`.
- **Needs:** last name, first name, **date of birth**, date of death. DOB is essential — only DOB-corroborated matches score; name-only matches are routed to human review, never scored (defamation guardrail).
- **Run:** nothing extra — `make provider-features` runs the matcher automatically (DuckDB-filtered to matched NPIs, so it stays cheap).

### C2. OpenSanctions → wider exclusion set (state lists + SAM + LEIE in one feed)
- **Get (free bulk):** `https://data.opensanctions.org/datasets/latest/debarment/targets.simple.csv`
  (or `entities.ftm.json` for the richer schema). The **debarment** collection bundles federal LEIE + SAM + ~45 state Medicaid/health exclusion lists.
- **License:** free for evaluation/non-commercial; **commercial use needs an OpenSanctions license** (a Brad decision). The code runs either way.
- **Save:** `preclean/opensanctions/targets.simple.csv`.
- **Run:**
  ```bash
  make opensanctions OPENSANCTIONS_FILE=preclean/opensanctions/targets.simple.csv
  make graph          # merges processed/exclusions_*.parquet into exclusion nodes
  ```
  This widens `within_2_hops_of_exclusion` (the ownership signal) **and** the PU
  positive label set — the graph loader auto-merges any `processed/exclusions_*.parquet`.

### C3. PECOS CCN→NPI crosswalk → `worthless_services`, `hospice_ineligibility`, `cost_report_fraud`
- **Get:** data.cms.gov → the PECOS "**Medicare Fee-For-Service Public Provider Enrollment**" institutional file, **or** the Provider of Services file (B13) — anything carrying both a CCN/provider-number and an NPI.
- **Save raw:** e.g. `preclean/pecos/enrollment.csv`.
- **Run:**
  ```bash
  make ccn-crosswalk PECOS_FILE=preclean/pecos/enrollment.csv
  # -> processed/ccn_to_npi.parquet (auto-detected by the export)
  ```
- This is the single highest-value unlock: it turns the three facility/cost-report CCN-grain schemes from blocked to scoring.

### C4. NDC drug claims → `drug_outlier` (drug-spread anomaly)
- **What:** a drug-claims extract at the **NDC** level (richer than the by-HCPCS spending fact) — billing NPI, NDC, units, billed cost.
- **Run:**
  ```bash
  python -m src.ingest_cms.claim_slices --kind ndc \
      --in <your_rx_claims.csv> --out processed/ndc_claims.parquet
  ```
- The builder rejects a too-thin file and names the missing column. Pairs with NADAC (B6).

### C5. Referral claims → `dme_ring` (ineligible-referral)
- **What:** a claims extract carrying the **referring NPI** — billing NPI, referring NPI, total paid.
- **Run:**
  ```bash
  python -m src.ingest_cms.claim_slices --kind referral \
      --in <your_claims_with_referrer.csv> --out processed/referred_claims.parquet
  ```
- Pairs with Order & Referring (B10).

---

### C6. NUCC taxonomy + CMS specialty crosswalk → better peer grouping (all schemes)
*Not a scheme feature — it fixes the peer cohort every percentile is ranked in.*
- **Get:** NUCC code set — https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40 → `preclean/nucc/nucc_taxonomy.csv` (needs `Code`, `Grouping`, `Classification`, `Specialization`). Optional CMS crosswalk → `preclean/nucc/specialty_crosswalk.csv`.
- **Run:** nothing extra — `make provider-features` auto-detects it and rolls thin/mis-coded taxonomies up to their classification cohort.

### C7. Widen the label set → battle the LEIE ceiling
The graph merges any `processed/exclusions_*.parquet` into the exclusion nodes, and
the export unions them into `provider_on_exclusion` (with `exclusion_label_sources`).
- **CMS revoked providers:** data.cms.gov → search "Revoked Medicare Providers". Then:
  ```bash
  python -m src.enforcement.medicare_revocations --in <revocations.csv> \
      --out processed/exclusions_medicare_revocations.parquet
  ```
- **SAM exclusions:** `python -m src.enforcement.sam_api --out processed/`
  (writes `exclusions_sam.parquet`; needs a free SAM API key).
- **OpenSanctions:** see C2. After dropping any of these, re-run `make graph` then `make provider-features`.

### C8. DOJ / qui tam case DB → scheme-typed, time-boxed positives (the strongest label)
- **Get:** DOJ press releases (https://www.justice.gov/news, filter "False Claims Act") + OIG enforcement (https://oig.hhs.gov/fraud/enforcement/). The `enforcement/fetch.py` + `case_db.py` pipeline parses these into the case schema; a hand-curated CSV (defendant, date, scheme, amount, summary) also works.
- **Build:** `python -m src.model_a.case_labels --case-db <cases.csv> --graph-dir <graph> --out processed/case_labels.parquet`, then run the export with `--case-db <cases.csv>`. Resolved defendants become `provider_on_exclusion` positives tagged `doj_case`, with `fraud_scheme` + `conduct_start`/`conduct_end` for scheme-stratified and out-of-time training.

---

## D. Operational cadence (no download — just a monthly job)

### D1. Owner snapshots → `ownership_turnover` (change-of-ownership churn)
Churn is only visible by diffing snapshots over time, so start capturing now:
```bash
make graph            # rebuild from the latest PECOS owners file
make owner-snapshot   # archives graph/edges/owned_by_edges.parquet under this month's date
```
Run monthly. Once **two** snapshots exist, the export diffs them into
`ownership_turnover` automatically. Nothing to procure — just the cadence. Refresh
the underlying PECOS ownership ("All Owners") file quarterly per the runbook.

---

## E. Still blocked — needs data the public PUFs don't carry (flag to counsel/Brad)

| Scheme / feature | What it needs | Note |
|---|---|---|
| `impossible_day` | Claim/line-level data with **service dates** (per-day service counts; procedure-time minutes) | Line-level Medicaid (T-MSIS claim lines) carries **licensing constraints for litigation targeting** — a legal decision, not a download. See `docs/platform/02`. |
| `dme_ring` ordering-MD concentration | DMEPOS **line-level** pairing supplier ↔ ordering MD | The public by-referring-provider PUF doesn't pair them. |

These are deliberately left dormant rather than approximated — there's no code that
can synthesize a field the data doesn't contain, and acquiring line-level claims is
a licensing/legal decision under the project's guardrails.

---

## Quick reference — paths the export looks for

```
preclean/partb/                 preclean/partd/            preclean/dmepos/
preclean/opioid/                preclean/open_payments/    preclean/nadac/
preclean/saturation/            preclean/hrsa_340b/        preclean/nppes_deactivation/
preclean/order_referring/       preclean/facility/{pbj,hospice,deficiencies}.csv
preclean/hcris/                 preclean/pos/              preclean/dmf/dmf.csv
preclean/opensanctions/         preclean/pecos/

processed/spending_fact.parquet processed/provider_dim.parquet
processed/ndc_claims.parquet    processed/referred_claims.parquet
processed/ccn_to_npi.parquet    processed/exclusions_opensanctions.parquet

owner_snapshots/                (make owner-snapshot, monthly)
```

After dropping any new file, re-run `make provider-features` and check
`PROVIDER_FEATURES_EXPORT_REPORT.md` to confirm the source was picked up.
