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

---

## 1. The core pipeline inputs (required)

These produce the backbone the whole platform builds on. The raw Medicaid spending +
NPPES + PECOS + LEIE you already have feed the 13-stage pipeline:

| Raw file | Save as | Source |
|---|---|---|
| Medicaid provider spending by HCPCS | `preclean/Spending.csv` | T-MSIS-derived HHS open-data extract (see `docs/platform/09`) |
| NPPES provider registry | `preclean/NPPES.csv` | https://download.cms.gov/nppes/NPI_Files.html |
| PECOS enrollment / ownership | `preclean/PECOS.csv` | https://data.cms.gov/provider-characteristics/medicare-provider-supplier-enrollment |
| LEIE exclusions | `preclean/leie.csv` | https://oig.hhs.gov/exclusions/exclusions_list.asp |

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
- `feature_manifest.json` — column roles (label / leakage / features / metadata)
- `PROVIDER_FEATURES_DICTIONARY.md` and `PROVIDER_FEATURES_EXPORT_REPORT.md`

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
| 340B OPAIS | Daily Report (covered entities + contract pharmacies) | https://340bopais.hrsa.gov | `preclean/hrsa_340b/opais.csv` | `contract_pharmacy` |
| NPPES deactivation | monthly "NPPES Deactivated NPI Report" | https://download.cms.gov/nppes | `preclean/nppes_deactivation/deactivation.csv` | `invalid_identity` (deactivation) |
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

**PECOS CCN-to-NPI crosswalk** → unlocks the facility / hospice / cost-report schemes,
writing `processed/ccn_to_npi.parquet`:

```bash
make ccn-crosswalk PECOS_FILE=preclean/pecos/enrollment.csv
```

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
