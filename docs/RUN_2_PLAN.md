# RUN 2 — Consolidated Plan (do NOT execute yet)
_The full to-do for the next model run, gathered from the whole build session.
Ordered as: (A) the design correction, then themed workstreams, then a suggested
run order. "Run 2a" = the next Medicaid re-run (mostly laptop-feasible quick wins);
"Run 2b" = the bigger build that needs a 64 GB box._

---

## A. DESIGN CORRECTION — size is a FILTER on output, never a ranking variable
**Goal:** find qui tam cases worth **>$5M in recovery**. Run 1 wrongly *excluded* big /
complex providers to dodge the "big = looks intense" trap. That threw out the high-value
targets. Fix:

1. **Remove the size-exclusion from lead generation.** Big/complex providers go back in
   the pool. Stop dropping the top-$ / "modest-scale-only" shortlist.
2. **Rank on size-ADJUSTED anomaly only.** Route the lead score through the existing
   `complexity_adjust` (residualize each metric against size/volume/breadth *within* the
   peer cell) + peer percentiles + the expected-billing residual ("digital twin").
   **Audit that no raw-dollar column feeds the score or the model** — dollars may be a
   *display* field, never a *ranking* variable. A big provider that bills normally *for a
   big provider* must rank LOW; a big provider abnormal *for its kind* ranks high.
3. **Add a recovery-potential FILTER on the OUTPUT at ~$5M.** Estimate dollars-at-risk →
   expected recovery (billing exposure × plausible overpayment share × the FCA
   treble-damages+penalties math × P(intervention)). This is **Model C / ERV** — apply it
   *after* ranking, as a gate. Size decides what surfaces; it never inflates a rank.
4. **Big-entity-appropriate signals.** Institutional fraud often hides *within* the entity
   — lean on the billing residual, scheme-specific institutional signals (hospice
   live-discharge, cost-report inflation), and within-entity/unit anomalies, not
   provider-level intensity alone.
5. **Keep the "not just big" guardrails the RIGHT way** — peer/complexity adjustment,
   consistency + plausibility checks, network/exclusion-proximity corroboration, counsel
   review. Then **validate the fix**: confirm large, genuinely-anomalous, banned-linked
   providers *rise* to the top (not that we just re-added all the big hospitals).
6. **Deliverable wording:** reframe §2.4 ("what we didn't flag") from "we exclude big
   institutions" (virtue) to "we separate size from suspicion; ranking is driven by
   anomaly, and the recovery-size filter is a final step."

---

## B. MEDICARE PHASE 2 (Part B + Part D + multi-year growth)  [Run 2b]
_(from docs/MEDICARE_BATCH_PLAN.md — build AFTER the current Medicaid run + Travis v1.)_
- **`medicare_growth.py`** — multi-year (2016–2024) year-over-year spend/claims growth,
  ramp/level-shift, new-code breadth (few columns × many years = memory-safe). The Medicare
  analogue of the Medicaid growth features.
- **`medicare_fact.py`** — convert Part B "by Provider & Service" + Part D "by Provider &
  Drug" into a billing fact; run the SAME export against it → `provider_features_medicare.parquet`
  (identical schema, so Travis trains it in parallel and merges later if it wins).
- **Physician-fraud signals lead here** (upcoding, kickbacks, pill-mill) — they under-cover
  the org-heavy Medicaid label but fit the Medicare population. Fold Medicare
  allowed/paid dollars into the exposure/ERV (kept separate from Medicaid, combinable later).

---

## C. ENTITY GRAPH — node embeddings memory  [Run 2b]
- The graph build + entity resolution itself is fine; **the node-embeddings step OOM'd**
  on 16 GB (≈13.6M connected nodes → the co-occurrence matrix + SVD exceed RAM even with
  the sparse backend).
- **Run embeddings on a 64 GB box at full fidelity** (`n_walks=10, walk_len=20`, no
  auto-lightening). This lights up the `graph_emb_*` + structural motifs + fraud-proximity
  columns that are ABSENT from Run 1 (we shipped `--no-embeddings`).
- Keep the SciPy-sparse backend; consider further chunked-SVD / connected-core-only
  refinements if we ever need to fit a smaller box.

---

## D. SOURCES WE SKIPPED OR MISSED (data already present / nearly free)  [Run 2a]
- **Order & Referring — the data is already there; the adapter just didn't fire.** Build
  the `referred_claims.parquet` claim-slice from the DMEPOS referring file (`Rfrg_NPI`)
  that was present, so `order_referring.ineligible_referral_share` **and** the
  referral-ring detection light up. (Root cause in Run 1: `referred_claims.parquet` wasn't
  built, so the block skipped.)
- **`pip install openpyxl`** → turns on **nppes_deactivation** (billing-after-deactivation)
  + **hrsa_340b** (340B contract-pharmacy concentration). The deactivation file is already
  downloaded.
- **SSA Death Master File** → billing-after-death smoking gun (procure the file).
- **DMEPOS proper "by Supplier & HCPCS" file** → the DME scheme (Run 1's file was
  referring-grain, missing `hcpcs`).
- **`--with-analytics`** → growth/ramp shock, clinical plausibility, billing-language-model
  surprisal (DuckDB-streamed; skipped in Run 1).

---

## E. SMOKING-GUN + FEATURE UPGRADES  [Run 2a]
- **Time attributes on billing-after-deactivation** (and billing-after-exclusion /
  after-death): not just a 0/1 flag — add *when* it happened, *how long after* the
  ban/deactivation/death, and *how much $* billed after. Turns each smoking gun into a
  dated timeline for a case file.
- **Bake state + city + zip + names into the export natively** (stop the post-hoc
  `make_scored_parquet` join). Also fold the composite `anomaly_score` / `anomaly_pct` /
  `signals_tripped` into the export.
- **Widen the training label** with the new near-certain sources as positives
  (deactivation, death, preclusion) — more positives = stronger model.

---

## F. VALIDATION & LABELS  [Run 2a/2b]
- **DOJ case DB as a training label (not lead-validation):** add fuzzy matching + a
  Medicaid-only case filter, then fold matched, prosecuted outcomes into the label.
- **Prospective validation (gold standard):** start archiving **point-in-time feature
  snapshots** now (`make feature-snapshot` on a cadence) + monthly **owner snapshots**
  (`make owner-snapshot`) so next cycle we can (a) rank as-of a past date and check who got
  banned *later*, and (b) light up `graph_velocity` (needs ≥2 snapshots) and
  `ownership_turnover` / CHOW (needs ≥2 owner snapshots).
- **Model-level rigor once trained (Travis):** calibration (isotonic/Platt), PU
  class-prior-corrected lift, FDR control, conformal intervals — all already built in the
  platform.

---

## G. GATED / DECISIONS (money or licensing — Brad)
- **Managed-care / NDC / diagnosis data (IQVIA or PurpleLab)** — fills the payer/drug/
  diagnosis gaps; clears many schemes. Licensing + permitted-use review with counsel.
- **OpenSanctions commercial license** (widens exclusion label).
- **People-data vendor + FCRA review** to activate Model B (whistleblower audiences).
- **DOJ 10-year backfill** already pulled (1,522 cases) — reuse as label source.

---

## H. SUGGESTED RUN ORDER
1. **Travis trains v1** on the Run-1 file; returns feature importance → tells us which
   signals to lean into (may reprioritize D/E).
2. **Run 2a (next Medicaid re-run, laptop-feasible):** size-correction (A), order/referring
   (D), openpyxl sources (D), time-attributes (E), state+score in export (E), start
   snapshots (F). Re-export → refreshed leads with the $5M filter.
3. **Run 2b (64 GB box):** graph embeddings at full fidelity (C), `--with-analytics` (D),
   then **Medicare Phase 2** (B).
4. **Ongoing:** accumulate snapshots for the prospective test (F); decide the gated data
   licenses (G).
