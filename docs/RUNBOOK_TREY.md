# Operator Runbook — running the model end to end (Trey)

This is the start-to-finish guide to produce the per-provider feature export that
feeds Travis's model: what to install, where to get every data file (with URLs,
filenames, and save paths), and the exact order to run things. Pair it with
`RUNBOOK_TRAVIS.md` (what the outputs mean) and `DATA_ACQUISITION_GUIDE.md` (the
canonical source list this section condenses).

---

## 0. One-time setup

```bash
git clone <repo> && cd medicaid-fraud-detection
pip install -r requirements.txt
make test            # 319 tests, no data needed — confirms the install is sane
make demo            # end-to-end on synthetic data → /tmp/demo (sanity check)
```

Set your data root once (everything reads/writes under it; default `~/Desktop/data`):

```bash
export MEDICAID_DATA_ROOT=~/Desktop/data       # or pass DATA_ROOT=… to each make target
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
make pipeline            # 13 stages → processed/spending_fact.parquet, provider_dim.parquet, detection/fraud_leads_v3.parquet
make graph               # entity graph → graph/ (nodes, edges, npi_to_org, node_embeddings)
make provider-features   # rebuilds graph, then writes the per-NPI export for Travis
```

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

| # | Dataset (search title on the host) | URL | Save as | Unlocks |
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

```bash
# SSA Death Master File → invalid_identity (billing-after-death)
#   Get: https://dmf.ntis.gov (paid limited-access) or the free pre-2011 public DMF mirror.
#   Needs: last/first name, DOB, date of death. Save → preclean/dmf/dmf.csv  (no build step)

# OpenSanctions → widen exclusions + the label (LEIE + SAM + ~45 state lists)
#   Free bulk: https://data.opensanctions.org/datasets/latest/debarment/targets.simple.csv
make opensanctions OPENSANCTIONS_FILE=preclean/opensanctions/targets.simple.csv   # → processed/exclusions_opensanctions.parquet

# CMS revoked providers → widen the label
python -m src.enforcement.medicare_revocations --in preclean/revocations/revocations.csv \
    --out processed/exclusions_medicare_revocations.parquet

# SAM exclusions → widen the label (needs a free SAM API key in env)
python -m src.enforcement.sam_api --out processed/

# PECOS CCN↔NPI crosswalk → facility / hospice / cost-report schemes
make ccn-crosswalk PECOS_FILE=preclean/pecos/enrollment.csv     # → processed/ccn_to_npi.parquet

# NDC drug claims & referral claims (richer extracts than the by-HCPCS spending file)
python -m src.ingest_cms.claim_slices --kind ndc --in <rx_claims.csv> --out processed/ndc_claims.parquet
python -m src.ingest_cms.claim_slices --kind referral --in <claims_with_referrer.csv> --out processed/referred_claims.parquet

# DOJ / qui tam case DB → scheme-typed, time-boxed positives (the strongest label)
#   DOJ press releases (justice.gov/news, filter "False Claims Act") + OIG enforcement.
```

After any of these, **re-run `make graph` then `make provider-features`** (the graph
must rebuild so OpenSanctions/revocations/CCN flow through).

---

## 3. The full run, with all the bells

```bash
# 1. backbone
make pipeline
# 2. graph (point-in-time optional — see §5)
make graph
# 3. the export with every optional capability
python -m src.model_a.provider_features_export \
    --graph-dir   $MEDICAID_DATA_ROOT/graph \
    --leads       $MEDICAID_DATA_ROOT/detection/fraud_leads_v3.parquet \
    --preclean    $MEDICAID_DATA_ROOT/preclean \
    --processed   $MEDICAID_DATA_ROOT/processed \
    --case-db     $MEDICAID_DATA_ROOT/preclean/enforcement/doj_cases.csv \
    --with-analytics \      # growth + plausibility + billing-LM (DuckDB-streamed; use a filtered/by-state spending file if RAM-bound)
    --case-control \        # also writes provider_features_matched.parquet
    --geocode \             # live Census geocoding of addresses (network)
    --snapshot --asof 2026-06-01 \   # archive a valid-time snapshot
    --out         $MEDICAID_DATA_ROOT/model_a/provider_features
```

Hand Travis `provider_features_for_model.parquet` + `feature_manifest.json` (and
`provider_features_matched.parquet` if you ran `--case-control`).

---

## 4. The monthly cadence (build history for the time-based signals)

Three signals need accumulated history; start the clock now by running these monthly:

```bash
make graph && make owner-snapshot                              # ownership_turnover (CHOW churn)
make provider-features && make feature-snapshot ASOF=$(date +%F)  # graph_velocity + point-in-time store
```

`ownership_turnover` activates after 2 owner snapshots; `graph_velocity` after 2
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
