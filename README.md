# Medicaid Fraud Detection

A pipeline that integrates national Medicaid + OIG data, isolates the trustworthy spending,
and hands investigators a **ranked, explainable list of provider leads** to review.

## What this is (and what it isn't)

Medicaid fraud costs the U.S. healthcare system tens of billions of dollars a year, but
investigators can only examine so many providers. This tool reads national Medicaid
provider-spending data, attributes every dollar to the *correct* billing provider, and surfaces
the providers most worth a closer look.

It produces **leads for human review, never fraud or violation determinations.** Every lead
carries the exact signals that surfaced it, and the strongest leads are expressed as factual
*dispositions* (e.g. "billed after the provider was excluded") — not accusations.

> **Note on layout.** `src/attempt_1/` is the original three-stage anomaly pipeline
> (`clean_data → build_features → analyze_anomalies`, Isolation Forest on 15 signals). It has been
> **superseded** by `src/attempt_2/`, which this README describes. attempt_1 is kept for reference.

---

## Why attempt_2 exists — the headline finding

The raw spending file totals **$21.8 trillion** — which is impossible. attempt_2's first job was to
find out why, and the answer reshaped everything downstream:

- Only **$1.10 trillion (5%)** of the dollars are attributable to a real provider (NPPES) NPI.
- **95% of the dollars sit on ~7.9 M rows with a blank billing NPI** — embedded total/summary rows
  ingested as if they were claims.
- A single corrupt code (`HCPCS '20'`) carries **$20.3 trillion across 34 rows** — fabricated
  `TOTAL_PAID` values like `$7,469,333,333,258`.

So the real, analyzable Medicaid universe is **$1.10 T across ~230 M claim rows / 617,062 providers**.
Everything is built on that clean base; the corruption is quarantined, not deleted.

---

## The pipeline

Each script is read-only on its inputs, idempotent, and uses **runtime assertions that hard-fail the
build** rather than warnings. DuckDB does the heavy joins/aggregation; Python orchestrates and writes
a Markdown report next to each stage's data outputs.

| # | Script | What it does | Key outputs |
|---|--------|--------------|-------------|
| 1 | `clean_data.py` | Cleans all five raw sources into typed Parquet (DuckDB; NPI Luhn canonicalization; CSV→Parquet conversion) | cleaned per-source Parquet |
| 2 | `integrate.py` | **Integration funnel** — attributes spending to the correct entity with **zero fan-out**, enforced by assertions (billing NPI only; every fact→dim join is many-to-one and asserts unchanged row count + dollar sum) | `provider_dim`, `npi_xwalk`, `pecos_provider`, `spending_fact`, `owner_edges`, `exclusions`, `facility_owner_exclusion_flags`, `npi_quarantine`, `QA_REPORT.md` |
| 3 | `diagnose_coverage.py` | Read-only diagnostic of the row-vs-dollar coverage gap | `COVERAGE_DIAGNOSTIC.md` |
| 4 | `audit_corruption.py` | Root-causes the fake $21.8 T and **quarantines corruption** (per-row `TOTAL_PAID > $500M` = physically impossible; justified from the legit distribution) | `spending_corruption_quarantine`, corrected `spending_aggregate_billing`, `CORRUPTION_AUDIT.md` |
| 5 | `features.py` | Materializes the **clean provider base** (`provider_matched & total_paid ≤ $500M`, reconciled to the audit) and builds **`provider_features`** — one row per billing NPI | `spending_provider_base`, `provider_features`, `provider_month`, `provider_hcpcs`, `FEATURES_REPORT.md` |
| 6 | `detect.py` | **Three-layer explainable lead prioritization** (see below) | `fraud_leads`, `layer1_rule_hits`, `layer3_ownership_leads`, `DETECTION_REPORT.md` |
| 7 | `verify_layer1.py` | Turns the billed-while-excluded leads into **candidate cases** using the raw LEIE waiver/reinstatement data + strict-month timing | `layer1_candidate_cases`, `LAYER1_VERIFICATION_REPORT.md` |
| 8 | `refine_layer2.py` (v2) | Rebuilds Layer-2 scoring: entity-type-aware peers + degeneracy-safe **percentile-rank** normalization | `fraud_leads_v2`, `LAYER2_REFINEMENT_REPORT.md` |
| 9 | `refine_layer2_v3.py` | Adds a **claim-volume reliability gate** and **de-correlated concept** scoring | `fraud_leads_v3`, `LAYER2_V3_REPORT.md` |
| 10 | `company_rollup.py` | Consolidates per-NPI leads to the **owning company** (PAC > shared-owner > exact-name linkage); surfaces companies that cross $10M only when consolidated | `company_rollup`, `company_leads_over_10m.csv`, `npi_to_company_map`, `COMPANY_ROLLUP_REPORT.md` |
| 11 | `company_lead_tracker.py` | **Real company-vs-company v3 anomaly** (reuses `rate_features` + `score_concepts` at company grain) + combined direct/ownership signals + fragmentation flag, ranked | `company_lead_tracker.csv` (≥$10M), `company_tracker_direct_under_10m.csv`, `COMPANY_TRACKER_REPORT.md` |
| 12 | `finalize_tracker.py` | **Cleanup for triage** — resolve names, add `specialty`, fix fragmentation to genuine spreads, flag under-merges, consolidate `review_flags`, readable `reasons`, `rank` + `tier_label` | `company_leads_clean.csv`, `triage_priority.csv`, `probable_owner_backlog.csv` |
| 13 | `export_csv.py` / `export_final_leads.py` | Render leads as spreadsheet-friendly CSVs (lists flattened, names joined, NPI as text) | `*.csv` |

### The three detection layers (`detect.py`, refined in 8–9)

Leads are **not** a single blended black-box score — three independent layers, each keeping its
signals visible, tiered **Layer-1 > Layer-2 > Layer-3**:

- **Layer 1 — deterministic, highest precision.**
  - *Billed-while-excluded:* the provider's NPI is on the OIG LEIE and it billed on/after its
    exclusion date. `verify_layer1.py` splits this into `billed_after_exclusion` (strictly after,
    very high precision) vs `same_month` (ambiguous) vs `excluded_after_billing` (billing predates
    exclusion), honoring waiver/reinstatement → dispositions `QUALIFIED / AMBIGUOUS / DISQUALIFIED /
    CONTEXT_ONLY`.
  - *Physically-implausible rates:* e.g. `lines_per_patient_instance > 100`, or
    `paid_per_claim_line > $50,000` (thresholds justified from the distribution).
- **Layer 2 — anomaly on size-normalized features vs taxonomy peers.** Robust, entity-type-aware
  peer comparison using **percentile ranks** (bounded, degeneracy-safe), a **volume reliability
  gate** (ratios from too few claims are not scored), and **de-correlated concepts** so one fact
  can't count twice. Raw dollars are never a primary driver. Isolation Forest is a *secondary*
  cross-check only.
- **Layer 3 — low-confidence ownership track.** Facilities whose probable owner matches an excluded
  party (name-key match). Kept **separate**, never blended into the score.

---

## Outputs you actually use

Everything lands under `~/Desktop/data/detection/` (configurable):

- **`final_leads_over_10m.csv`** — every lead (Layer 1–3) with total billing ≥ $10 M, one row per
  NPI, sorted by tier then dollars, with `provider_name`, the contributing signals, and a `reasons`
  summary. `net_paid` is labelled `total_billing_size_proxy_not_case_value` (a size proxy, *not* an
  adjudicated case value).
- **`high_precision_excluded_leads.csv`** — the billed-while-excluded leads (kept regardless of
  dollar, since they're the highest-precision and naturally small).
- `fraud_leads_v3.parquet` — the full tiered table, one row per billing NPI.

**Company grain** (one row per owning company, NPIs consolidated):
- **`company_lead_tracker.csv`** — the company tracker, companies with total billing **≥ $10 M**,
  ranked direct → company-anomaly → ownership, with a real company-vs-company anomaly score, a
  `fragmentation_signal` flag, `reasons`, and the constituent `npi_list`.
- **`company_tracker_direct_under_10m.csv`** — sub-$10 M billed-while-excluded / on-LEIE companies,
  preserved (highest precision, naturally small).
- `company_leads_over_10m.csv` — the simpler rollup leads (per-NPI signals aggregated).

**Triage-ready (cleaned, the agent starts here):**
- **`triage_priority.csv`** — the focused queue: Direct (billed-while-excluded + on-LEIE) and
  Company-anomaly tiers only, ranked, with resolved names, `specialty`, complete `reasons`, and
  `review_flags`.
- `company_leads_clean.csv` — all leads, cleaned/ranked/ordered (triage columns first).
- `probable_owner_backlog.csv` — the noisy probable-owner name-match tier, kept off the priority queue.

Work the list **top-down by tier**: Layer-1 rows first (deterministic), then Layer-2 (ranked
anomalies), then the lower-confidence Layer-3 ownership track.

---

## Design principles (enforced in code)

- **Correct attribution, zero fan-out.** Spending is attributed only via the **billing** NPI; every
  fact→dimension join is many-to-one and asserts the row count *and* `SUM(TOTAL_PAID)` are unchanged.
- **Identifiers are strings** everywhere (NPI, PAC ID, enrollment ID, ZIP) — leading zeros preserved,
  never coerced to numbers.
- **Raw dollars are never a primary anomaly driver** — features are size-normalized; magnitude is
  context only.
- **Quarantine, never delete** — corrupt rows and Luhn-failing NPIs are preserved in their own tables
  with a reason.
- **Explainability is mandatory** — every lead exposes the exact signals/concepts that surfaced it.
- **Assertions stop the build** on any fan-out, uniqueness, or reconciliation failure.

---

## Data layout

Raw inputs and all derived outputs live **outside the repo** (HIPAA; `data/` is gitignored). The
scripts default to `~/Desktop/data/`:

```
~/Desktop/data/
├── preclean/         # raw extracts: Spending.csv, NPPES.csv, PECOS.csv, Caught.csv (LEIE), owners/*.csv
├── interim/          # CSV→Parquet conversions
├── integrated/       # integrate.py outputs + QA/diagnostic/audit reports
│   └── attempt_2/    # features.py outputs (provider_features, spending_provider_base, …)
├── features/         # working copy of the feature tables (detection reads from here)
└── detection/        # fraud_leads*, layer1_candidate_cases, final CSVs + layer reports
```

Most scripts take `--in-dir` / `--processed` / `--out-dir` flags to override locations.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Running the pipeline (in order)

```bash
python -m src.attempt_2.ingest.integrate            # 2. integrate + QA (zero-fan-out attribution)
python -m src.attempt_2.audit.diagnose_coverage     # 3. coverage diagnostic
python -m src.attempt_2.audit.audit_corruption      # 4. root-cause + quarantine corruption
python -m src.attempt_2.ingest.features             # 5. clean base + provider_features
python -m src.attempt_2.leads.detect                # 6. three-layer leads
python -m src.attempt_2.leads.verify_layer1         # 7. verify billed-while-excluded → cases
python -m src.attempt_2.leads.refine_layer2         # 8. Layer-2 v2 (entity-aware percentile)
python -m src.attempt_2.leads.refine_layer2_v3      # 9. Layer-2 v3 (volume gate + concepts)
python -m src.attempt_2.leads.company_rollup        # 10. consolidate NPIs → companies
python -m src.attempt_2.leads.company_lead_tracker --min-net-paid 10000000  # 11. company-level v3 tracker
python -m src.attempt_2.leads.finalize_tracker      # 12. clean/rank for triage
python -m src.attempt_2.export.export_final_leads --min-net-paid 10000000   # 13. final CSVs
```

(`clean_data.py` is step 1; `integrate.py` re-converts the raw CSVs itself, so the integration
step can be run directly on the `preclean/` extracts.)

### Code layout (`src/attempt_2/`)

```
src/attempt_2/
├── clean_data.py     # stage 1 + shared helpers (NPI canonicalize, normalizers, readers)
├── ingest/           # integrate.py, features.py
├── audit/            # diagnose_coverage.py, audit_corruption.py
├── leads/            # detect, verify_layer1, refine_layer2, refine_layer2_v3, company_rollup, company_lead_tracker, finalize_tracker
└── export/           # export_csv.py, export_final_leads.py
```

See `DATA_DICTIONARY.md` for every table, its grain, and its columns.

## Compliance & privacy

All data here is subject to HIPAA. Raw and derived data are excluded from version control via
`.gitignore` and must never be committed. These outputs are **investigative leads for human review**,
not determinations of fraud.
