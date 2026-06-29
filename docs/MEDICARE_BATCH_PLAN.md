# Medicare "Batch 2" plan (saved — not yet built)

Produce a **second, Medicare-native** per-NPI feature file for Travis
(`provider_features_medicare.parquet`), parallel to the Medicaid one, **without
rerunning or redesigning the Medicaid pipeline**. Merge into a single cross-payer
model later if it proves worthwhile.

## Why it works with no rerun
Everything already built is keyed on **NPI**, not payer — the entity graph + graph
features, the exclusion label (LEIE/revocations/OpenSanctions), the NPPES registry,
taxonomy/peer groups, ownership, address grounding. The **only** Medicaid-specific
artifact is the billing fact (`spending_fact`). So a Medicare batch = swap in a
Medicare billing fact and **reuse everything else**.

## What to build (additive, separate modules)
1. **Part B/D → billing-fact converter** (`src/ingest_cms/medicare_fact.py`):
   - Part B "by Provider and Service" PUF → `(billing_npi, hcpcs_code, total_paid)`
     at provider×HCPCS grain; `total_paid = Tot_Srvcs × Avg_Mdcr_Pymt_Amt` (or a total
     payment column). Part D "by Provider and Drug" → drug as the code, `Tot_Drug_Cst`.
   - Annual PUF → set a single `service_month` bucket (the data year).
   - Write `processed/medicare_fact.parquet`.
2. **`--spending-fact` / `--out` override on `provider_features_export`** so the SAME
   export runs a second time against the Medicare fact, reusing the existing graph +
   labels + registry → `provider_features_medicare.parquet`, same column schema.
3. **Design (A): Medicare-native base concepts** — recompute concentration / payment
   intensity / service intensity / specialty-mismatch from the Part B fact (don't
   reuse the Medicaid v3 concepts), so the file genuinely stands alone with no
   mislabeled columns. (Design B — reuse Medicaid leads as scaffold — is faster but
   leaves the v3 concepts Medicaid-derived; rejected.)
4. **Exposure/ERV:** bring Medicare allowed/paid dollars into the dollars-at-risk so
   Medicare-heavy providers aren't undersized. Keep Medicare and Medicaid exposure as
   **separate** components (combinable later).

## What ports vs. what doesn't (data-grain, not design)
- **Ports** (provider×code grain is enough): billing-LM embeddings + `billing_surprisal`,
  code concentration, payment/service intensity, specialty-mismatch, expected-billing
  residual, the upcoding/drug-scheme adapters, + all reused graph + label features.
- **Does NOT port** (PUFs have no claim dates): monthly-temporal features,
  `sequence_surprisal`, the as-of monthly cutoff. Manifest marks them absent, not faked.

## Workflow once built
- Medicaid: the current run, finished as-is.
- Medicare: `python -m src.ingest_cms.medicare_fact --partb … --partd … --out processed/medicare_fact.parquet`
  then `python -m src.model_a.provider_features_export --spending-fact processed/medicare_fact.parquet --out …/provider_features_medicare --with-analytics`.
- Hand Travis both files (identical schema); he trains in parallel, merges later if it wins.

**Legal note:** the False Claims Act covers Medicare AND Medicaid equally — Medicare is
the larger qui tam theater. The Medicaid-first ordering is a data-availability artifact
(claim-level Medicaid on hand vs. aggregated Medicare PUFs), NOT a scope decision.
