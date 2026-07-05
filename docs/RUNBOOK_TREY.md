# Operator Runbook — running the model end to end (Trey)

This is the start-to-finish guide to produce the per-provider feature export that
feeds Travis's model: what to install, where to get every data file (with URLs,
filenames, and save paths), and the exact order to run things. Pair it with
`RUNBOOK_TRAVIS.md` (what the outputs mean, including the per-scheme catalog in its
§5.4 — what each fraud scheme detects and which files feed it) and
`DATA_ACQUISITION_GUIDE.md` (the canonical source list this section condenses).

---

## 0. One-time setup

```bash
git clone <repo> && cd medicaid-fraud-detection
pip install -r requirements.txt
make test
make demo
```

`make test` runs the 319-test suite (no data needed) to confirm the install is sane;
`make demo` runs end-to-end on synthetic data into `/tmp/demo` as a sanity check.

Set your data root once — everything reads and writes under it (default
`~/Desktop/data`; or pass `DATA_ROOT=/path` to each make target):

```bash
export MEDICAID_DATA_ROOT=~/Desktop/data
```

**Golden rules for every file below**
- **Never open a CSV in Excel** — it strips leading zeros off NPIs/ZIPs/CCNs/NDCs/FIPS and silently corrupts the join keys. Save the raw download as-is.
- Save under `MEDICAID_DATA_ROOT`; `preclean/` (raw inputs) and `processed/` (pipeline outputs) live inside it.
- IDs are strings. The loaders read everything as text first; don't pre-convert.

### Confirm your files are in place — run preflight first

After downloading, check that everything is present and named correctly **before**
running anything else:

```bash
python -m src.preflight --data-root "C:\Users\treyr\OneDrive\Desktop\data"
```

It scans `preclean/`, then prints `[ok]` / `[MISSING]` / `[rename?]` for every
expected file — grouped into CORE (required), UNLOCKS (each adds schemes), and
OPTIONAL/GATED — plus what each missing item would enable and the next command. The
`[rename?]` flag means a folder has files but none match an accepted name (the most
common foot-gun). `python -m src.pipeline_status` then shows which pipeline OUTPUTS
you've already built. Read-only and instant.

---

## 1. The core pipeline inputs (required)

These produce the backbone the whole platform builds on. The raw Medicaid spending +
NPPES + PECOS + LEIE you already have feed the 13-stage pipeline:

| Raw file | Save as | Source |
|---|---|---|
| Medicaid provider spending by HCPCS | `preclean/Spending.csv` | T-MSIS-derived HHS open-data extract (see `docs/platform/09`) |
| NPPES provider registry | `preclean/NPPES.csv` | https://download.cms.gov/nppes/NPI_Files.html |
| PECOS enrollment / ownership | `preclean/PECOS.csv` | https://data.cms.gov/provider-characteristics/medicare-provider-supplier-enrollment |
| LEIE exclusions | `preclean/Caught.csv` (the integrate default; `leie.csv` also accepted) | https://oig.hhs.gov/exclusions/exclusions_list.asp |

Then build the processed backbone, the entity graph, and the feature export:

```bash
make pipeline
make graph
make provider-features
```

`make pipeline` runs the 13 stages that produce `processed/spending_fact.parquet`,
`processed/provider_dim.parquet`, and `detection/fraud_leads_v3.parquet`. `make graph`
builds the entity graph into `graph/` (nodes, edges, `npi_to_org`, node embeddings).
`make provider-features` rebuilds the graph and then writes the per-NPI export for Travis.

`make provider-features` writes to `model_a/provider_features/`:
- `provider_features_for_model.parquet` — the matrix (one row per NPI)
- `feature_manifest.json` — column roles (label / leakage / features / metadata),
  plus a `sources_audit` block: every source as used/skipped with reason + file
- `PROVIDER_FEATURES_DICTIONARY.md` and `PROVIDER_FEATURES_EXPORT_REPORT.md`
- `SOURCES_REPORT.md` — **read this first**: one table of which sources contributed
  and which were skipped (and why / which file), so a silently-missed file is
  impossible to overlook. The console also prints `sources: N used / M skipped`.

That's the minimum viable run. Everything in §2 is **additive and optional** — each
file you add lights up more columns; the export report tells you what it found.

---

## 2. Optional source files — where to get them, what they unlock

Drop each at the exact path shown; re-run `make provider-features`. The export
skip-loads anything absent and logs why.

### 2.1 Quick-win adapter files (one CSV each)

| Source | Dataset (search title on the host) | URL | Save as | Unlocks |
|---|---|---|---|---|
| Part B | "Medicare Physician & Other Practitioners – by Provider and Service" | data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners | `preclean/partb/partb.csv` | `upcoding` |
| Part D | "Medicare Part D Prescribers – **by Provider and Drug**" (Download, not API) | data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers | `preclean/partd/partd.csv` | `drug_outlier`, `pharma_kickback` |
| Opioid | "Medicare Part D Prescribers – **by Provider**" *summary* (carries `Opioid_Tot_Clms`, `Opioid_LA_Tot_Clms`; NOT "by Geography") | same hub as Part D | `preclean/opioid/opioid.csv` | `pill_mill` |
| DMEPOS | "Medicare DMEPOS – **by Referring Provider and Service**" (NOT "by Supplier", NOT plain "by Referring Provider") | data.cms.gov/provider-summary-by-type-of-service/medicare-durable-medical-equipment-devices-supplies | `preclean/dmepos/dmepos.csv` | `dme_ring` |
| Open Payments | General + Research payments, latest 3 yrs | https://openpaymentsdata.cms.gov/datasets | `preclean/open_payments/open_payments.csv` | `pharma_kickback` |
| NADAC | "NADAC (National Average Drug Acquisition Cost)" | https://data.medicaid.gov | `preclean/nadac/nadac.csv` | `drug_outlier` (spread) |
| Market Saturation | "Market Saturation & Utilization State-County" | https://data.cms.gov/tools | `preclean/saturation/saturation.csv` | `saturation_fraud` |
| 340B OPAIS | **Covered Entity Daily Report** (Excel — the 3-worksheet file) | https://340bopais.hrsa.gov | `preclean/hrsa_340b/opais.xlsx` (drop the native .xlsx as-is; or export the Contract Pharmacies worksheet to `opais.csv`) | `contract_pharmacy` |
| NPPES deactivation | monthly "NPPES Deactivated NPI Report" (a .zip of an Excel file) | https://download.cms.gov/nppes/NPI_Files.html | `preclean/nppes_deactivation/deactivation.zip` (drop the native zip/.xlsx as-is; the adapter unzips + reads it) | `invalid_identity` (deactivation) |
| Order & Referring | "Order and Referring" | data.cms.gov | `preclean/order_referring/order_referring.csv` | `dme_ring` (ineligible-referral) |
| PBJ staffing | "Payroll Based Journal Daily Nurse Staffing" | data.cms.gov | `preclean/facility/pbj.csv` | `worthless_services` |
| Care Compare hospice | hospice live-discharge measure | https://data.cms.gov/provider-data | `preclean/facility/hospice.csv` | `hospice_ineligibility` |
| Care Compare deficiencies | health-deficiency citations | https://data.cms.gov/provider-data | `preclean/facility/deficiencies.csv` | `worthless_services` |
| HCRIS cost reports | SNF/Hospital/HHA cost reports | https://www.cms.gov/data-research/statistics-trends-reports/cost-reports | `preclean/hcris/hcris.csv` | `cost_report_fraud` |
| POS | "Provider of Services" file | data.cms.gov | `preclean/pos/pos.csv` | `worthless_services` (capacity; partial) |
| NUCC taxonomy | provider-taxonomy code set | https://www.nucc.org → Code Sets | `preclean/nucc/nucc_taxonomy.csv` | better peer grouping (all schemes) |

### 2.2 The unlock builders (download + one build command)

Each is a download plus one command. After running any of them, **re-run `make graph`
then `make provider-features`** so the graph rebuilds and OpenSanctions / revocations /
CCN flow through.

**SSA Death Master File** → unlocks `invalid_identity` (billing-after-death). Get it
from https://dmf.ntis.gov (paid limited-access) or the free pre-2011 public DMF
mirror; it needs last/first name, DOB, and date of death. Save to `preclean/dmf/dmf.csv`
— there is no build step.

**OpenSanctions** → widens both the exclusion graph and the label (LEIE + SAM + ~45
state lists). Free bulk download: https://data.opensanctions.org/datasets/latest/debarment/targets.simple.csv.
Then build `processed/exclusions_opensanctions.parquet`:

```bash
make opensanctions OPENSANCTIONS_FILE=preclean/opensanctions/targets.simple.csv
```

**CMS revoked providers** → widens the label, writing `processed/exclusions_medicare_revocations.parquet`:

```bash
python -m src.enforcement.medicare_revocations \
    --in preclean/revocations/revocations.csv \
    --out processed/exclusions_medicare_revocations.parquet
```

**SAM exclusions** → widens the label (needs a free SAM API key in your environment):

```bash
python -m src.enforcement.sam_api --out processed/
```

**CMS Preclusion List** → widens the label with a `preclusion` source tag, writing
`processed/exclusions_preclusion.parquet`. Unlike the lists above, this one is **not
a public download** — CMS distributes it to MA / Part D plan sponsors through HPMS,
so you need a sponsor-channel copy of the file. Once you have it:

```bash
python -m src.enforcement.preclusion \
    --in preclean/preclusion/preclusion_list.csv \
    --out processed/exclusions_preclusion.parquet
```

**CCN-to-NPI crosswalk** → unlocks the facility / hospice / cost-report schemes,
writing `processed/ccn_to_npi.parquet`. Needs a file with BOTH a CCN and an NPI.
The PECOS *enrollment* extract has NPI-but-no-CCN and the standard POS file has
CCN-but-no-NPI, so point it at the **raw NPPES file** — it bridges via the NPPES
"Other Provider Identifier" type-06 (Medicare CCN) fields:

```bash
python -m src.ingest_cms.ccn_npi_crosswalk --in preclean/NPPES.csv --out processed/ccn_to_npi.parquet
```

If your NPPES vintage uses only type-code 01/05 (no type-06 Medicare CCN — common),
the line above returns 0 pairs. In that case bridge through the **POS file** instead:
pass POS as `--in` and NPPES as `--nppes`, and it joins them two ways (POS Medicaid
vendor number ↔ NPPES type-05 IDs, plus normalized facility name + ZIP):

```bash
python -m src.ingest_cms.ccn_npi_crosswalk --in preclean/pos/pos.csv --nppes preclean/NPPES.csv --out processed/ccn_to_npi.parquet
```

(If you ever have a single file that carries both columns — e.g. an institutional
PECOS file or a POS vintage with NPI — point `--in` there alone; it auto-detects.)

**NDC drug claims & referral claims** → richer extracts than the by-HCPCS spending
file, for the drug-spread and ineligible-referral signals:

```bash
python -m src.ingest_cms.claim_slices --kind ndc --in <rx_claims.csv> --out processed/ndc_claims.parquet
python -m src.ingest_cms.claim_slices --kind referral --in <claims_with_referrer.csv> --out processed/referred_claims.parquet
```

**DOJ / qui tam case DB** → scheme-typed, time-boxed positives (the strongest label).
Source: DOJ press releases (justice.gov/news, filtered to "False Claims Act") plus OIG
enforcement, saved as the case CSV that the export reads via `--case-db`.

---

## 3. The full run, with all the bells

Three steps — build the backbone, build the graph (point-in-time optional, see
section 5), then run the export with every optional capability turned on:

```bash
make pipeline
make graph
python -m src.model_a.provider_features_export \
    --graph-dir   $MEDICAID_DATA_ROOT/graph \
    --leads       $MEDICAID_DATA_ROOT/detection/fraud_leads_v3.parquet \
    --preclean    $MEDICAID_DATA_ROOT/preclean \
    --processed   $MEDICAID_DATA_ROOT/processed \
    --case-db     $MEDICAID_DATA_ROOT/preclean/enforcement/doj_cases.csv \
    --with-analytics \
    --case-control \
    --geocode \
    --snapshot --asof 2026-06-01 \
    --out         $MEDICAID_DATA_ROOT/model_a/provider_features
```

What the optional flags do:
- `--with-analytics` — adds growth, clinical plausibility, and the billing-LM features (DuckDB-streamed; use a filtered/by-state spending file if RAM-bound).
- `--case-control` — also writes `provider_features_matched.parquet` (matched positives + clean controls).
- `--geocode` — live Census geocoding of billing addresses (network).
- `--snapshot --asof DATE` — archives a valid-time snapshot for point-in-time training.
- `--asof-cutoff DATE` — a feature-freeze date: every billing feature is computed only on service months *before* it, so the matrix is leakage-correct for out-of-time training (pair it with labeling only providers whose conduct began at/after the date). Writes a filtered spending file to `interim/` first.

Hand Travis `provider_features_for_model.parquet` + `feature_manifest.json` (and
`provider_features_matched.parquet` if you ran `--case-control`).

---

## 4. The monthly cadence (build history for the time-based signals)

Three signals need accumulated history; start the clock now by running these monthly:

```bash
make graph && make owner-snapshot
make provider-features && make feature-snapshot ASOF=$(date +%F)
```

The first line archives an owner snapshot (for `ownership_turnover` / CHOW churn);
the second archives a feature snapshot (for `graph_velocity` + the point-in-time
store). `ownership_turnover` activates after 2 owner snapshots; `graph_velocity` after 2
feature snapshots. Nothing to procure — just the cadence. Refresh the underlying
sources on the calendar in `docs/platform/12-data-runbook.md`.

---

## 5. Point-in-time (leakage-correct) builds for training

To train Travis's model honestly — "would we have flagged them *before* they were
caught" — build the graph as-of a past date so it uses only exclusions known then:

```bash
python -m src.entity_graph --input $MEDICAID_DATA_ROOT/processed \
    --out $MEDICAID_DATA_ROOT/graph_2020 --asof 2020-01-01
```

Then point the export's `--graph-dir` at `graph_2020`. See `RUNBOOK_TRAVIS.md` §
"out-of-time validation" for how he uses `conduct_start` + the snapshot store.

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| A scheme is all-null | its source file isn't in `preclean/` — check the export report's skip reasons |
| `[graph_velocity] skipped: needs ≥2 feature snapshots` | expected until you've run `make feature-snapshot` twice |
| facility/HCRIS schemes blocked | missing `processed/ccn_to_npi.parquet` → run `make ccn-crosswalk` |
| `--with-analytics` slow / RAM-bound | use a by-state-filtered spending file; it's DuckDB-streamed but plausibility holds a per-(npi,code) frame |
| stale-graph WARN | the graph predates the leads — re-run `make graph` (or just `make provider-features`, which rebuilds it) |
| leading zeros gone from NPIs | a CSV was opened/saved in Excel — re-download the raw file |

`python -m src.pipeline_status` prints "where did I leave off" (read-only).

## 7. Run 2 additions — the new commands, in the order you run them

Everything below landed after the first national run. Same rules as always: read-only
on inputs, every step writes a report, nothing here needs code changes.

**Before the rerun (one-time):**

```bash
pip install openpyxl                       # unlocks 340B + deactivation (files already downloaded)

# OpenSanctions (already downloaded; free bulk CSV) → exclusions + ~45 state Medicaid lists
python -m src.enforcement.opensanctions --in preclean/opensanctions/targets.simple.csv \
    --out processed/exclusions_opensanctions.parquet

# what do I have, and is it named right? (also regenerates the source-registry doc)
python -m src.preflight --data-root ~/Desktop/data
```

**The rerun (Medicaid):** run the pipeline + graph + export as in §3. The export now
adds automatically: the volume/price digital twins, smoking-gun timelines, the
deactivation/death label widening, NEMT + behavioral-health schemes, native
name/city/zip, and it ends with `EXPECTATIONS_REPORT.md` — read that first; every
FAIL row is a calculation that ran but didn't behave, with "go look at" attached.

**After the export:**

```bash
# per-feature signal trend (replaces the old scratchpad script)
python -m src.model_a.signal_ranking --matrix provider_features_for_model.parquet \
    --manifest feature_manifest.json --out signal_ranking.csv

# the ranked, gated lead lists (no size cut; scheme-aware $5M gate)
python -m src.model_a.lead_export --matrix provider_features_for_model.parquet \
    --out-dir detection/leads

# ghost NPIs — billing numbers absent from the registry (CMS's #1 high-risk marker)
python -m src.model_a.identity_flags --spending processed/spending_fact.parquet \
    --provider-dim processed/provider_dim.parquet --out ghost_npis.csv
```

**When Travis sends artifacts back:**

```bash
# audit his training against the contract (leakage, subscore-vs-raw, group split)
python -m src.model_a.verify_training --importance feature_importance.csv \
    --manifest feature_manifest.json --assignments train_test.csv \
    --matrix provider_features_for_model.parquet

# settle the negatives/split question with the ablation grid (the A/B answer, with receipts)
python -m src.model_a.retrospective --matrix provider_features_for_model.parquet \
    --manifest feature_manifest.json
```

**Medicare phase (once partb_<year>.csv / partd_<year>.csv vintages are staged):**

```bash
python -m src.ingest_cms.medicare_fact --partb-dir preclean/partb --partd-dir preclean/partd \
    --out-dir processed/medicare
python -m src.ingest_cms.medicare_growth --fact processed/medicare/medicare_fact.parquet \
    --out processed/medicare/medicare_growth.parquet
python -m src.model_a.medicare_export --medicare-dir processed/medicare --preclean preclean \
    --provider-dim processed/provider_dim.parquet --graph-dir <graph-dir> --out-dir <out>
```

**Small optional files that light up more schemes** (drop in place, rerun the export):
`preclean/hud/zip_county.csv` (county-grain saturation — the "local area" upgrade),
`preclean/hcpcs_time/hcpcs_minutes.csv` (impossible-day), `preclean/cms_priority/`
(moratoria.csv / revalidation_due.csv / sff.csv → the CMS-wave overlay flags),
`preclean/dmf/dmf.csv` (billing-after-death), the DMEPOS by-Referring-AND-Service
re-pull, and `preclean/docgraph/` shared-patient (referral rings).
