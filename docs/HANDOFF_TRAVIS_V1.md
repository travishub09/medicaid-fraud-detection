# Handoff — Medicaid provider feature matrix (v1)

_First Medicaid feature export for the supervised (graph-based tree) model. One row
per NPI, full universe. Produced by `python -m src.model_a.provider_features_export`
against the national procured data._

## The two files that matter

Both land in the export output dir (e.g.
`…/data/model_a/provider_features/`):

- **`provider_features_for_model.parquet`** — the training matrix:
  **617,062 providers × 111 columns**, one row per NPI.
- **`feature_manifest.json`** — the column contract. **Read this first** — it names
  the role of every column (label / leakage / features / metadata). Everything below
  references manifest keys so the exact column names travel with the data, not this
  doc.

Also written, for humans: `PROVIDER_FEATURES_DICTIONARY.md` (per-column reference)
and `SOURCES_REPORT.md` (which sources fed the run, used vs. skipped + why).

## This is a PU (positive-unlabeled) problem, not balanced classification

- **Label: `provider_on_exclusion`** (`manifest["label"]`) — 1 = provider appears on
  an exclusion list (**LEIE + Medicare revocations**); **1,943 positives** in v1.
  Everything else is **unlabeled, not confirmed-negative.** Train PU-style; do **not**
  assume `0 = clean`.
- **`confirmed_clean = 1`** marks **243 manufactured high-confidence negatives**
  (institutional / FQHC or long-tenure + benign billing + no fraud proximity). Use
  these as trusted negative anchors for PU calibration / case-control contrasts.

## Do NOT train on these (leakage)

- **`manifest["leakage_hard"]`** — e.g. `billed_after_exclusion`,
  `excluded_after_billing`. Derived from the provider's **own** exclusion; training on
  them is circular and inflates the backtest.
- **`manifest["leakage_adjacent"]`** — e.g. `within_2_hops_of_exclusion`,
  `graph_fraud_proximity`. Predictive but correlated with the label. Use **only under
  a strict out-of-time split**, with eyes open.

## Column families (what you're splitting on)

- **Raw** provider stats + each adapter's raw metrics → `manifest["raw_feature_cols"]`.
- **`*__peerpct`** — one-sided taxonomy-peer percentile of each raw metric (the
  platform-canonical robust comparison; higher = more than peers).
- **`subscore_<scheme>`** — 0–1 rules-engine score per scheme. v1 scores **13**:
  `upcoding, drug_outlier, pharma_kickback, pill_mill, overutilization,
  single_service_mill, payment_outlier, specialty_mismatch, rapid_ramp,
  ownership_integrity, worthless_services, hospice_ineligibility, saturation_fraud`.

Trees can use raw + peerpct + subscores together — keep what carries signal.

## Two things to honor in CV

- **`NULL` means "provider absent from that source," not zero.** Let LightGBM handle
  NaN natively — **do not `fillna(0)`**, it changes the meaning.
- **`group_id`** (`manifest["group_cols"]`) — use **group-aware CV** so related NPIs
  (same org/owner) don't straddle train/test and leak.
- **`assessable`** flag (`manifest["assessability"]`) — thin-evidence providers;
  consider down-weighting or holding them out of ranking rather than forcing a score.

## What's in v1

Part B/D, opioid, Open Payments + kickback co-occurrence, market saturation,
facility/HCRIS, NUCC peer groups, address / shell-cluster flags, org-graph ownership
features, the widened exclusion label, and the 13 fraud schemes above.

## Coming in v2 (runs on a 64 GB box, not the 16 GB laptop)

Same schema — **v2 is a superset of these 111 columns**, so anything built now carries
forward.

- **Graph embeddings** (`graph_emb_*`) + structural motifs + fraud-proximity — the
  learned graph-representation family (didn't fit 16 GB; bounded sparse backend ready).
- **`--with-analytics`**: billing-language-model surprisal, growth / ramp shock,
  clinical plausibility.
- Two sources that just need `pip install openpyxl` then a re-run:
  `nppes_deactivation` (billing-after-deactivation) and `hrsa_340b`
  (contract-pharmacy concentration).
- **DMEPOS** swap: v1's file was the referring-NPI layout (`Rfrg_NPI`); the
  by-supplier-&-HCPCS file lights up the DME scheme.

## Suggested first loop

Train a first PU model on the v1 matrix, then send back **feature importance** — that
tells us which v2 additions (embeddings vs. analytics vs. the extra sources) are worth
prioritizing on the VM.
