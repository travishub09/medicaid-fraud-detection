# Medicare "Phase 2" plan (saved — build AFTER the current Medicaid run completes)

**Trigger:** start this once the current Medicaid model run is finished and Travis has
his Medicaid `provider_features_for_model.parquet` in hand. Phase 2 does **not** touch
or rerun the Medicaid pipeline.

**Goal:** build **everything we have for Medicaid, for Medicare** — full feature
parity — plus a Medicare-native **year-over-year growth** signal off the multi-year
PUFs Trey already holds (2016–2024 for Part B / Part D, etc.). Produce a second,
Medicare-native per-NPI file (`provider_features_medicare.parquet`), parallel to the
Medicaid one and same schema, so Travis trains it the same way and we merge into one
cross-payer model later if it wins.

## Why it works with no rerun
Everything already built is keyed on **NPI**, not payer — the entity graph + graph
features, the exclusion label (LEIE/revocations/OpenSanctions/preclusion), the NPPES
registry, taxonomy/peer groups, ownership, address grounding, calibration/FDR/
conformal. The **only** Medicaid-specific artifact is the billing fact. So Medicare =
swap in a Medicare billing fact + add the Medicare-specific builders below, and
**reuse everything else**.

## A. Multi-year YoY growth (NEW — the priority add)
Trey holds **all years 2016–2024** of Part B and Part D (and DMEPOS, opioid). The
current export uses only the **latest** year per source (cross-sectional ratios) to
fit 16 GB. Phase 2 adds a Medicare **year-over-year growth** builder:

- **`src/ingest_cms/medicare_growth.py`** — read each year file reading ONLY a few
  columns (`npi` + total paid + total claims/services), so all 9 years fit in memory
  (this is the memory-safe trick — few columns × N years, never the full files).
- Per NPI, compute across years: **spend YoY growth**, **claims YoY growth**, a
  **level-shift / ramp** score (sudden jump vs. the provider's own history), and a
  **new-code / breadth-expansion** signal — the Medicare analogue of the Medicaid
  `growth_level_shift` / `new_code_burst` / `rapid_ramp` features.
- Emit per-NPI `medicare_partb_spend_growth`, `medicare_partd_spend_growth`,
  `medicare_ramp`, etc., one-sided peer-normalized like every other signal.
- Feeds a `rapid_ramp`-style scheme on the Medicare side.

This is exactly the YoY-spend-growth signal the Medicaid fact already provides from its
2018–2024 monthly series; Phase 2 gives Medicare the same, at **annual** grain.

## B. Full feature parity — port the whole Medicaid stack to Medicare
Build, on the Medicare billing fact, the Medicare-native versions of everything the
Medicaid pipeline produces:
1. **Billing fact converter** (`medicare_fact.py`): Part B "by Provider & Service" →
   `(billing_npi, hcpcs_code, total_paid)`; Part D "by Provider & Drug" → drug as code,
   `Tot_Drug_Cst`. (Multi-year stacked where memory allows; latest-year otherwise.)
2. **`--spending-fact` / `--out` override** on `provider_features_export` so the SAME
   export runs against the Medicare fact → `provider_features_medicare.parquet`.
3. **Medicare-native base concepts** (Design A): recompute concentration / payment
   intensity / service intensity / specialty-mismatch from the Part B fact (don't reuse
   the Medicaid v3 concepts — no mislabeled columns).
4. **Billing language model** (`billing_emb_*`, `billing_surprisal`), **expected-billing
   residual**, **clinical plausibility**, **consistency flags**, **billing-implied
   specialty** — all recomputed on the Medicare fact.
5. **Cross-sectional scheme adapters** already wired: upcoding (Part B), drug_outlier /
   pharma_kickback (Part D + Open Payments), pill_mill (opioid), dme_ring (DMEPOS).
6. **Exposure / ERV:** fold Medicare allowed/paid dollars into the dollars-at-risk so
   Medicare-heavy providers aren't undersized. Keep Medicare and Medicaid exposure as
   **separate** components (combinable later).

## What ports vs. what stays Medicaid-only (data-grain)
- **Ports** (annual / provider×code grain is enough): **YoY growth + ramp (NEW, via the
  multi-year files)**, billing-LM, concentration, payment/service intensity,
  specialty-mismatch, residual, plausibility, the upcoding/drug/pill-mill/DME adapters,
  + all reused graph + label + calibration features.
- **Stays Medicaid-only** (PUFs have no claim-level dates): **within-year monthly**
  temporal features, `sequence_surprisal` (code-adoption order), and the as-of monthly
  cutoff. Annual YoY *is* now covered (A); only sub-annual monthly detail is not. The
  manifest marks the Medicaid-only columns absent rather than faking them.

## Workflow once built
```
python -m src.ingest_cms.medicare_fact   --partb-dir preclean/partb --partd-dir preclean/partd --out processed/medicare_fact.parquet
python -m src.ingest_cms.medicare_growth --partb-dir preclean/partb --partd-dir preclean/partd --out processed/medicare_growth.parquet
python -m src.model_a.provider_features_export --spending-fact processed/medicare_fact.parquet \
    --out .../provider_features_medicare --with-analytics
```
Hand Travis both files (identical schema); he trains in parallel, merges later if it wins.

**Legal note:** the False Claims Act covers Medicare AND Medicaid equally — Medicare is
the larger qui tam theater. Medicaid-first is a data-availability ordering, not a scope
decision. Phase 2 brings Medicare to full parity.
