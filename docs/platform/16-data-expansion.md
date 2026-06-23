# 16 — Data Expansion Sprint (new sources, download → model)

This doc turns the data-source research sweep into a build queue. It has three
jobs, in plain language:

1. **What to download and how to name it** — click-by-click, like the
   [Data Runbook](12-data-runbook.md), for each new source.
2. **How each one plugs into the models** — step-by-step, from raw file to a
   feature that changes an org's ERV / a label that trains Model C.
3. **The full catalog** — every candidate the sweep surfaced, sorted
   free / licensed (flag to Brad) / barred, so nothing gets lost.

Each new source already has an **adapter stub** in the codebase with its input
and output contract written into the docstring (the function raises
`NotImplementedError` citing the section here). Building one = replacing the
raise, keeping the contract. The stubs:

| # | Source | Stub module | Feeds |
|---|---|---|---|
| 1 | Inpatient/Outpatient Hospital PUFs | `ingest_cms/hospital_puf.py` | Model A — `upcoding` (facility) |
| 2 | Medicare Geographic Variation | `ingest_cms/geographic_variation.py` | Model A — regional denominator |
| 3 | NUCC taxonomy + CMS crosswalk | `ingest_cms/nucc_taxonomy.py` | Model A — peer grouping (all schemes) |
| 4 | Medicaid State Drug Utilization (SDUD) | `ingest_cms/sdud.py` | Model A — drug signals |
| 5 | CMS Change-of-Ownership (CHOW) | `ingest_cms/chow.py` | Model A — `ownership_integrity` (A7) |
| 6 | OIG Corporate Integrity Agreements | `enforcement/cia.py` | Model A risk prior + Model C labels |

**Recommended build order** (value per effort, lowest-friction first):
**3 (NUCC)** → **1 (Hospital PUFs)** + **2 (Geo Variation)** → **5 (CHOW)** →
**4 (SDUD)** → **6 (CIA)**. NUCC first because it improves *every* other signal
by fixing peer grouping.

All paths below sit under your data root (the folder `MEDICAID_DATA_ROOT` points
at; `preclean/` is inside it). **Never open these in Excel** — it destroys the
leading zeros on CCNs/NPIs/NDCs/FIPS.

---

## Part 1 — Download & naming (click-by-click)

### 1. Medicare Inpatient & Outpatient Hospital PUFs → `hospital_puf/`
1. Go to https://data.cms.gov/provider-summary-by-type-of-service/medicare-inpatient-hospitals
2. Open **"Medicare Inpatient Hospitals - by Provider and Service"**, download the
   latest year CSV → save as `preclean/hospital_puf/inpatient_YYYY.csv`.
3. Open the **Outpatient** sibling on the same collection page → save as
   `preclean/hospital_puf/outpatient_YYYY.csv`.
- **Verify:** header has `Rndrng_Prvdr_CCN`, a DRG/APC code column, `Tot_Dschrgs`
  (or `Tot_Srvcs`), `Avg_Submtd_Cvrd_Chrg`, `Avg_Mdcr_Pymt_Amt`.

### 2. Medicare Geographic Variation → `geo_variation/geo_variation.csv`
1. Go to https://data.cms.gov/summary-statistics-on-use-and-payments/medicare-geographic-comparisons/medicare-geographic-variation-by-national-state-county
2. Download the latest **National/State/County** CSV → `preclean/geo_variation/geo_variation.csv`.
- **Verify:** header has a geography code/level column and a standardized
  per-capita payment column (e.g. `Tot_Mdcr_Stdzd_Pymt_PC`).

### 3. NUCC taxonomy + CMS specialty crosswalk → `nucc/`
1. NUCC code set: https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40 →
   download the current CSV → `preclean/nucc/nucc_taxonomy.csv`.
2. CMS crosswalk: https://data.cms.gov/provider-characteristics/medicare-provider-supplier-enrollment/medicare-provider-and-supplier-taxonomy-crosswalk →
   download CSV → `preclean/nucc/specialty_crosswalk.csv`.
- **Verify:** taxonomy file has `Code`, `Grouping`, `Classification`,
  `Specialization`; crosswalk has a Medicare specialty code column + a provider
  taxonomy code column.

### 4. Medicaid State Drug Utilization (SDUD) → `sdud/sdud_YYYY.csv`
1. Go to https://www.medicaid.gov/medicaid/prescription-drugs/state-drug-utilization-data
   (bulk CSV + API at data.medicaid.gov).
2. Download the latest annual file → `preclean/sdud/sdud_YYYY.csv`.
- **Verify:** header has `NDC`, `State`, `Units Reimbursed`,
  `Number of Prescriptions`, `Total Amount Reimbursed`, `Utilization Type`.
- **Note:** this is the PUBLIC SDUD aggregate — **not** the DUA-gated "Medicaid
  Drug Utilization LDS" (same-sounding name, different file; the LDS is barred —
  see Part 3).

### 5. CMS Change-of-Ownership (CHOW) → `chow/`
1. data.cms.gov → search **"Change of Ownership"**; download the SNF, Hospital,
   HHA, and Hospice CHOW CSVs → `preclean/chow/chow_<type>.csv`
   (e.g. `chow_snf.csv`, `chow_hospital.csv`).
- **Verify:** header has buyer & seller name + PAC/Enrollment ID columns, a
  transaction type, and an effective date.
- **Snapshot rule still applies:** keep dated copies; do not overwrite.

### 6. OIG Corporate Integrity Agreements → `cia/`
1. Structured list: https://oig.hhs.gov/compliance/corporate-integrity-agreements/browse-cias/
   (also the data.gov "Corporate Integrity Agreement (CIA) Documents" dataset).
2. Export/scrape the active + closed lists → `preclean/cia/cia_list.csv` with at
   least: party name, state, effective date, status, document URL.
- **Verify:** one row per agreement with a party (org) name and an effective date.

---

## Part 2 — Wiring each source into the models (step-by-step)

The general flow for a **Model A feature** (sources 1, 2, 5) is always the same:

> raw file → adapter (per-NPI/CCN metric) → `peer_percentiles` (0–1) →
> `rollup_to_org` → merge into the company-features parquet → register the
> column in `model_a/scheme_subscores.py` → it flows into the subscore, the
> adjusted probability, and the ERV ranking automatically.

### Source 3 — NUCC + crosswalk (do this first)
This one is not a scheme feature; it improves the *grouping* every signal uses.
1. Implement `load_taxonomy_hierarchy`, `load_specialty_crosswalk`,
   `canonical_peer_group` in `ingest_cms/nucc_taxonomy.py`.
2. In the peer engine (`analytics/peers`), use `canonical_peer_group`'s
   `peer_group_key` as the cohort key instead of the raw taxonomy string.
3. In `model_a/sector_priors.py`, replace the hand-coded
   `TAXONOMY_SECTOR_PREFIXES` lookups with the authoritative hierarchy (keep the
   `sector_for_taxonomy` signature so callers don't change).
4. Re-run Model A — false positives from mis-grouped specialties drop; no new
   column, every existing scheme gets sharper.

### Source 1 — Hospital PUFs → facility `upcoding`
1. Implement `compute_hospital_drg_metrics` + `hospital_upcoding_anomaly`
   (mirror `hcris.py`: `_resolve_columns`, per-CCN aggregate, then
   `facility.facility_peer_percentiles`).
2. Roll the per-CCN `hospital_upcoding_anomaly` up to org with
   `facility.rollup_ccn_to_org`, producing an org-grain column.
3. In `model_a/scheme_subscores.py`, add `"hospital_upcoding_anomaly"` to the
   `upcoding` scheme's feature set.
4. Merge the column into the features parquet you pass to `--features`. Re-run
   Model A; hospitals with outlier DRG mix / charge-to-payment now score on it.

### Source 2 — Geographic Variation → regional denominator
1. Implement `compute_geo_baseline` (per-FIPS standardized per-capita cost) and
   `attach_geo_expectation` (join to orgs via
   `census_population.zip_to_county`, state-grain fallback).
2. The output `regional_cost_index` is a driver for `specialty_mismatch` /
   overutilization — add it to that scheme in `scheme_subscores.py`.
3. Re-run; an org billing far above its county's standardized norm is now
   flagged relative to region, not just raw dollars.

### Source 5 — CHOW → `ownership_integrity` (A7)
1. Implement `normalize_chow_events` in `ingest_cms/chow.py` — it outputs the
   **same event schema** `entity_graph/ownership_churn.py` already consumes
   (`org_node_id, owner_key, event_type, event_date`).
2. Feed those events straight into
   `ownership_churn.ownership_turnover_features` (skip the snapshot diff, or run
   both and union). You get the real `ownership_turnover` 0–1 feature plus the
   `is_pe_reit` flag.
3. `ownership_turnover` already feeds the `ownership_integrity` scheme — no
   registry change needed; it just becomes real instead of inferred.

### Source 4 — SDUD → sharper drug signals
1. Implement `compute_sdud_reference` (per-NDC reimbursed-per-Rx, high-volume
   flag).
2. Pass it alongside NADAC into the drug-reference step so
   `nadac.compute_nadac_reference`'s "high cost / high volume" determination uses
   actual Medicaid utilization, not just acquisition cost.
3. **Honest limit:** SDUD has no billing NPI, so it does **not** produce the
   per-org `drug_spread_anomaly` numerator by itself — that still needs an
   NDC-level claims source keyed by NPI. SDUD is the denominator/expected-mix
   upgrade, not the org attribution.

### Source 6 — OIG CIA → Model A prior + Model C label
1. Implement `normalize_cia` (uses the shared `norm_org_name` — do not write a
   second normalizer) and `cia_case_rows`.
2. **Model A:** join `cia_status` to org nodes on `name_key`; an active CIA / a
   Heightened-Scrutiny listing is a risk-multiplier and a dossier corroboration
   line (surface as *context*, never an accusation — guardrail #5).
3. **Model C:** append `cia_case_rows` to the case DB
   (`enforcement/case_db.py`). CIA effective date ≈ settlement date; these are
   real settled-case labels that help retire the cold-start rules and re-derive
   sector priors (`enforcement/derive_priors.py`).

After any new label lands, re-run `derive_sector_priors` so the placeholder
multipliers in `sector_priors.py` move toward enforcement-weighted base rates.

---

### Calibration — graduate Model A from heuristic to enforcement-trained

Once you have a case DB (DOJ/OIG settlements, CASE_COLUMNS), one command turns it
into Model A calibration and breaks the `adjusted_prob ≈ 1.0` ceiling of the
label-free v1:

```bash
python -m src.model_a.calibrate --case-db <cases.csv> \
    --graph-dir $ROOT/graph --features $ROOT/features/company_features.parquet
# writes model_a/sector_priors.json (+ model_a/pu_model.pkl if ≥5 defendants matched)

python -m src.model_a --graph-dir $ROOT/graph \
    --features $ROOT/features/company_features.parquet \
    --spending $ROOT/processed/spending_fact.parquet --out $ROOT/model_a \
    --priors model_a/sector_priors.json --pu-model model_a/pu_model.pkl
```

`calibrate` (1) derives enforcement-weighted sector multipliers
(`enforcement/derive_priors`), and (2) matches the case defendants to org nodes
(`outcomes_from_case_db`) and trains a PU classifier on org features → a
**calibrated P(fraud)** that replaces the saturated heuristic and re-ranks ERV.
The more matched defendants, the sharper the calibration — this is exactly why
the OIG enforcement feed / CIA list / DOJ backfill (sources above) are the
priority. Without a case DB, Model A stays on the documented placeholder priors.

## Part 3 — Full catalog (everything the sweep found)

### Free / public — clear legal footing
- **Model A signal:** Inpatient/Outpatient Hospital PUFs (#1); Geographic
  Variation (#2); NUCC + crosswalk (#3); SDUD (#4); CHOW (#5); OIG Enrollment
  Moratoria (county×specialty fraud prior, curate like the Work Plan table);
  CMS QPP/MIPS Cost scores (high cost + weak quality = upcoding tell);
  HRSA AHRF + HPSA shortage areas (provider-density denominator);
  Care Compare hospital outcomes (HAC/readmissions/mortality — quality-vs-billing
  mismatch); Hospital Price-Transparency CMP enforcement list (governance flag);
  IRS EO Business Master File (nonprofit entity-resolution spine);
  AHRQ CHSP system-parent crosswalk; CMS Doctors & Clinicians National
  Downloadable File; Medicaid Managed Care Enrollment; CMS Opt-Out list
  (false-positive suppressor).
- **Model C labels:** OIG Enforcement Actions feed; OIG CIA (#6); OIG CMP +
  Provider Self-Disclosure settlements; DOJ annual FCA statistics (base rates);
  NAMFCU / MFCU annual reports (state-Medicaid base rates); state AG settlement
  feeds (CA/NY/TX first); RECAP/CourtListener docket parsing (the `intervened`
  boolean + unsealing dates).
- **Model B audiences (org/role grain only):** DOL OFLC LCA + PERM (role × employer
  × worksite — the standout); BLS OEWS (audience-sizing denominator); NLRB
  filings + OSHA inspections (org-level workforce-grievance prioritization);
  EEOC aggregate stats (calibration); DOL OLMS LM-2 (union channel mapping, mild
  counsel review on named officers).

### Licensed / paid — flag to Brad
- **OpenSanctions commercial license** — *biggest gated win; the adapter
  (`enforcement/opensanctions.py`) already exists.* ~45 state Medicaid exclusion
  lists in one feed (the ones LEIE misses). Free for non-commercial; commercial
  license priced on application. Keep internal (adjudicates people). Free DIY
  fallback: scrape TX/NY/CA exclusion files individually.
- **Violation Tracker (Good Jobs First)** — ~$250–450/yr (far cheaper than a bulk
  license); FCA settlements with parent rollups → Model C labels. Counsel read on
  redistribution terms only.
- **Free Law Project** — quote-based bulk PACER backfill for NOS-376 dockets.
- **Revelio Labs / Lightcast** — per-employer role headcount/turnover (Revelio) and
  live job postings (Lightcast); **buy only the aggregate/postings tiers** — the
  individual-profile tiers fall under the same FCRA gate as the people-data
  vendors. Counsel-gated.
- **State APCD claim-level extracts (CO/MA/MD)** — public pricing dashboards are
  free and usable now; claim-level extracts are DUA + release-committee gated and
  border on barred for litigation-sourcing.

Do-not-touch on terms-of-use grounds: **Nursys, Glassdoor, Indeed** (anti-scraping
terms; substitute state license rosters + NLRB/OSHA).

### Barred — do not acquire (legal guardrail, see [02-data-sources.md](02-data-sources.md))
- **CMS Preclusion List** — access restricted to MA/Part-D plans; we are not a plan.
- **Medicaid Drug Utilization LDS** — DUA-gated; use the public SDUD aggregate (#4).
- **T-MSIS Analytic Files (TAF) RIF** — beneficiary-level, DUA, barred for
  litigation targeting.
- **IQVIA Xponent / Optum / Komodo commercial claims**, **CMS LDS/RIF**, **MA
  RADV / encounter research files** — barred per the standing guardrail.
- **Treasury Do Not Pay** — federal agencies only.
- **MCSIS** (cross-state Medicaid terminated-provider share) — government-internal;
  OpenSanctions is the public substitute.

---

## Legal notes (carry into every build)
- Sources that adjudicate people (OpenSanctions, CIA parties, any people-data
  vendor) stay **internal corroboration**, never an exported audience, never a
  named-individual call list (guardrails in [01-legal-compliance.md](01-legal-compliance.md)).
- Model B additions stay at **role × employer × channel** grain; individual-level
  fields are dropped or kept internal pending FCRA/counsel review.
- Every dossier line sourced from enforcement data is **context for human review,
  not an accusation** (hard rule #5).
- Record in [02-data-sources.md](02-data-sources.md) that public **SDUD** ≠ the
  barred **Medicaid Drug Utilization LDS**, so they are never conflated.
