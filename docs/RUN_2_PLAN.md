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
3. **Add a recovery-potential FILTER on the OUTPUT at ~$5M — SCHEME-AWARE, never a blanket
   own-billing gate** (docs/OUTPUT_METHODOLOGY.md; `SCHEME_EXPOSURE_BASIS`). The gate's
   dollar basis depends on where the scheme's dollars live: own billing for the volume/price
   schemes; **INFLUENCED dollars for ordering/referring + kickback schemes** (the $136M
   telemedicine nurse billed almost nothing herself — gating her on own billing deletes the
   case); ring-aggregate for ownership/ring schemes (members inherit the ring's pass);
   facility program payments for hospice/SNF/cost-report. A lead passes if ANY applicable
   basis clears the threshold. Estimate expected recovery per basis (exposure × plausible
   overpayment share × FCA treble+penalties × P(intervention)) — **Model C / ERV** — applied
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
- **OpenSanctions — DATA IN HAND (bulk CSV is a free download; only the API costs).**
  Adapter wired (`enforcement.opensanctions`); it also carries ~45 STATE Medicaid
  exclusion lists, substantially covering §I6 without scraping 50 state sites. Run it
  now; the **commercial-USE sign-off** (Brad/counsel) proceeds in parallel and gates
  production reliance, not testing.
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
5. **Phase-2 additions (I):** slot the CMS-revalidation-wave items into 2a (the cheap,
   public-data ones) and gate the rest.

---

## I. PHASE-2 ADDITIONS — ride the CMS "swift revalidation" wave (new, July 2026)
_Context: on 2026-04-23 CMS ordered every state to **swiftly revalidate high-risk Medicaid
provider organizations** (10-day first pass, 2-year strategy in 30 days). CMS's named
high-risk categories are **home health, hospice, DME, skilled nursing, and providers without
NPIs**. That is the government publishing its own high-risk Medicaid target list — the exact
sectors we already score — so these additions both sharpen the model and ride a live
enforcement tailwind (higher P(intervene) for Model C, plus a dual-use sell: the same engine
IS the revalidation-triage tool states now need on a clock). Guiding rule below: every **base**
version runs on PUBLIC data we already hold (the Medicaid PUF-grain fact + public registries);
the **deep** versions that need beneficiary-level, day-level, or GPS/EVV data are T-MSIS-RIF /
EVV / state-DUA restricted and barred for litigation-targeting (docs/platform/02) — keep them
gated behind counsel._

**I1. No-NPI / weak-identity flag (CMS's own #1 high-risk marker).**
- _Detects:_ billing/enrolling under a missing, invalid, deactivated, or mismatched identifier.
- _Data strategy:_ **no new procurement.** Validate every billing + servicing NPI in the
  Medicaid fact against **NPPES bulk** (held) and **PECOS enrollment** (held); light up the
  built-but-gated **`nppes_deactivation`** adapter (one `openpyxl` install) and DuckDB-filter
  the spending fact for billing after the deactivation month (same pattern as the deceased-NPI
  check). Flags: NPI absent from NPPES; deactivated-then-billing; thin-history NPI (recently
  issued) with immediate high volume; servicing-NPI missing while billing at scale; PECOS
  Medicaid enrollment with no NPI link. New `billed_without_valid_npi` → folds into
  `consistency_flags`. _Cost: free. Slot: **Run 2a.**_

**I2. CMS-revalidation sector overlay + Model C signal.**
- _Detects:_ nothing new in the data — it re-weights ranking and underwriting toward the
  sectors CMS is actively hunting.
- _Data strategy:_ a **curated priors table** (`cms_revalidation_2026`: sector → boost weight,
  effective date, source cite), mapped to providers by **NUCC taxonomy / PECOS enrollment
  type**. Feed the existing **`model_a/government_interest`** overlay (refresh quarterly, same
  cadence as the OIG Work Plan table) and add a **Model C** feature
  `sector_under_active_revalidation` (raises P(intervene)). As states publish **revalidation /
  termination lists**, scrape them into the exclusions schema + label store (fresh positives).
  _Cost: free (curation + light scraping). Slot: **2a** (overlay) + **ongoing** (state lists)._

**I3. Impossible-day metric (definitional, smoking-gun class).**
- _Detects:_ time billed per provider per day beyond physical limits (the 500-hrs/day archetype).
- _Data strategy:_ our fact is NPI×HCPCS×**month**, so exact per-day needs day-level claims
  (T-MSIS/state — gated). **Public approximation:** build an `hcpcs_time_map` from public CMS
  PFS time files (time-based codes — psychotherapy, ABA, anesthesia — define their own minutes);
  implied-minutes = Σ(claim_lines × code_minutes) per NPI per month → implied hours/day =
  minutes / (working-days × 60) → `implied_hours_per_day`, `impossible_day_flag` at a
  conservative cut. _Cost: free (public map). Slot: **2a** (monthly approximation); true
  per-day **gated** on lawful day-level data._

**I4. NEMT (non-emergency medical transport) as a first-class scheme.**
- _Detects:_ phantom trips, mileage inflation — Medicaid-specific; two of our own
  billed-after-ban smoking guns are transport companies.
- _Data strategy:_ **already in the fact.** NEMT bills under transport HCPCS (A0080–A0999
  ambulance/mileage; T2001–T2005 non-emergency transport; S0209/S0215; taxi/livery) and
  transport NUCC taxonomies. Identify NEMT providers, then peer-relative signals: mileage-units
  per trip, trips per patient, base-rate-to-mileage ratio, one-way/round-trip anomalies → new
  `subscore_nemt_fraud`. Deep "rides with no destination service that day" needs
  beneficiary-day linkage (T-MSIS / state EVV — gated). _Cost: free base. Slot: **2a** (base
  subscore); deep version **gated**._

**I5. Behavioral-health / SUD / ABA sector overlay.**
- _Detects:_ patient brokering, group-billed-as-individual, ABA unit inflation, impossible
  counseling hours (our impossible-volume DOJ example IS behavioral health).
- _Data strategy:_ **already in the fact** — BH taxonomies + BH HCPCS (H0001–H2037, 90791/90837,
  ABA 97151–97158). BH-stratified peer cells; signals: units/day (ties to I3), ABA-units-per-
  child, group-vs-individual code mix, rapid SUD panel growth → `subscore_behavioral_health`.
  _Cost: free. Slot: **2a.**_

**I6. State Medicaid exclusion lists (not just federal LEIE).**
- _Shortcut: the OpenSanctions bulk file (already downloaded, adapter wired) aggregates
  ~45 of these lists — run it first; per-state scraping then only fills the gaps._
- _Detects:_ more banned actors + more banned-adjacency; state MFCU exclusions often precede
  federal and are Medicaid-specific.
- _Data strategy:_ new `enforcement/state_exclusions.py` that scrapes/downloads each state
  Medicaid-agency / MFCU exclusion list (free, fragmented; fields: name, NPI-where-present,
  license, date, cause) and normalizes into the **existing `exclusions` schema** via the shared
  name normalizer, then merges into graph exclusion nodes + widens the PU label with source tag
  `state_medicaid`. Reuse the generalized exclusion loader already built for OpenSanctions/LEIE.
  Prioritize the highest-row states (CA, NY, OH, TX, FL). _Cost: free; per-state scraping
  friction. Slot: **2a** (top-5 states) → **ongoing.**_

**I7. Phantom-network / provider-directory mismatch.**
- _Detects:_ providers listed in Medicaid MCO networks with little/no real billing (phantom
  network); identity fields that disagree across directories (shell).
- _Data strategy:_ near-term proxy — scrape **state Medicaid MCO provider directories** and
  cross listed NPIs against billing presence in the fact → `network_listed_no_billing`. Future
  taps — the **National Directory of Healthcare Providers (NDH)** + the interop-mandated payer
  **Provider Directory / Provider Access APIs** (the article's subject) → `directory_identity_
  mismatch` across NDH/NPPES/PECOS. _Cost: scraping now; APIs when live. Slot: **2b** (state-
  directory proxy) / **gated** (national APIs not fully shipped)._

**I8. CLIA lab-capacity mismatch (bonus, lab sector).**
- _Detects:_ labs billing test complexity/volume beyond their CLIA certificate.
- _Data strategy:_ new `ingest_cms/clia.py` off the public **CDC CLIA Laboratory Registry**
  (CLIA #, certificate type, location); join to lab NPIs (name/address fuzzy) → flag billed
  high-complexity tests under a waiver/PPM cert, or volume implausible for the certificate.
  _Cost: free; CLIA↔NPI join friction. Slot: **2b.**_

_Fast wins to fold into Run 2a: I1 (no-NPI), I2 (revalidation overlay), I3 (impossible-day
approximation), I4 (NEMT base), I5 (behavioral-health), and I6 top-5 states — all run on
public data already in hand. I7/I8 and every "deep" variant stay gated on lawful data +
counsel._

---

## J. DATA-SOURCE EXPANSION — prioritized + phased (from the federal + state research files)
_The research catalogs ~80 federal datasets and a 50-state resource map. Most of the adapters
already exist in `ingest_cms/` and `enforcement/` (built, dormant, waiting on the file). So this
is mainly a **procurement + activation order**, not a build list. Phased by value ÷ friction ÷
gating. Guardrail unchanged: DUA/permitted-use-restricted sources (T-MSIS, APCD, PDMP, MA
encounter) are barred for litigation-targeting — counsel-gated (docs/platform/02)._

### Standout free win: the State False Claims Act overlay (Model C, zero procurement)
The federal FCA covers Medicaid (the federal share) in every state. But **35 states + DC have
their own FCA** (adds the state share, and often a better relator deal); **16 do not** — AL,
AK, AZ, AR, ID, KY, MO, NE, ND, **OH**, OR, PA, SD, UT, WV, WI. That matters here because **Ohio
is one of our largest data volumes (13.2M rows) and has no state FCA**, while CA / NY / TX / FL
do. Action: a curated `state_fca` table (state → has-FCA, covers-managed-care, relator-share
band) becomes a **Model C case-value multiplier** — all else equal, rank/underwrite leads in
state-FCA states higher, without dropping federal-only states. Free; the CSV is the data.

### Already in hand — EXCLUDED from the phases below
Per the last run's `SOURCES_REPORT` (and the source registry), **~20 of the 21 sources from the
last run are already held** — Part B/D, opioid, Open Payments, saturation, facility, NPPES/
address, NUCC, kickback, LEIE, and the rest. Those are **not re-listed here.** The few that only
need *switching on* (no new data) are a one-liner, not a phase: `--with-analytics` (growth /
plausibility / billing-LM), `pip install openpyxl` (340B + deactivation), two dated snapshots
(graph-velocity + ownership-churn), and building `ccn_to_npi` (unlocks HCRIS/POS). **Everything
below is net-new or still-missing data only.**

### Phase N1 — new, cheap, public, highest value (acquire now)
- **Physician Shared Patient Patterns** (free NBER/CMS file: NPI↔NPI shared-patient + same-day
  counts) → activates the built DocGraph referral edges + **referral-ring detection**. Single
  biggest add — referral rings are the kickback signature and the network layer is our strongest.
- **CMS Provider Enrollment Moratoria** (nationwide HHA + Hospice, May 2026) — CMS's own
  highest-risk determination; tiny curated file → government-interest / revalidation overlay.
- **Revalidation Due Date List** (NPI + due date) + **Special Focus Facility list** — overdue /
  SFF providers as risk flags for the same overlay. Small.
- **State FCA + MFCU-activity overlays** (curated tables) — the case-value win above + MFCU
  recovery volume as a Model C receptiveness prior. Zero bulk data.
- **The genuinely-missing files from the last run** (the "+1" that was NOT in hand): **SSA Death
  Master File** (billing-after-death), **NADAC** + the NDC claim slice (drug-spread), the
  **order/referring eligibility** file (dme_ring), and a re-pull of **DMEPOS in the
  by-supplier-&-HCPCS layout** (last run had the referring layout, so it produced nothing).

### Phase N2 — new, medium build, public
- **State medical/nursing/pharmacy licensing** (URLs in the state CSV) → procure for the built
  state-licensing adapter: license status + discipline = identity corroboration + soft
  exclusions. Top-volume states first (CA, NY, OH, TX, FL).
- **National Provider Directory** (`directory.cms.gov`, FHIR NDJSON) + **state MCO directories**
  → phantom-provider detection: billing NPIs absent from every directory.
- **DEA ARCOS** (opioid distribution by pharmacy/county; free WaPo mirror) → pill-mill corroboration.
- **SNF All Owners** + **Nursing-Home Penalties** → related-party / shell ownership + facility
  risk (extends the facility adapter).
- **CLIA lab registry** (CDC) → lab-capacity mismatch (labs billing beyond their certificate).

### Phase N3 — gated on DUA / license / counsel (biggest coverage, real friction)
- **State APCDs** (all-payer claims, not just Medicaid FFS — the single largest coverage
  expansion; the CSV flags which states have one). Per-state DUA + permitted-use review, counsel.
- **T-MSIS TAF via ResDAC DUA** — claims-level Medicaid gold standard (diagnoses + bene linkage;
  unlocks the *deep* NEMT / impossible-day / rides-to-nowhere variants). DUA bars
  litigation-targeting → Brad + counsel decision.
- **State PDMP** (controlled-substance scripts) — law-enforcement-restricted → gated.
- **MA risk-adjustment / encounter data** — Medicare Advantage diagnosis upcoding (MedPAC
  estimates ~8% coding inflation; a huge theater) — restricted access.
- **Price-transparency MRFs** (hospital + payer negotiated vs billed rates) — public but heavy
  and messy; low near-term ROI.
- **Defacto payer-directory APIs** (127+ payers, commercial) — phantom-network at scale; Brad
  license decision.
- **State Secretary-of-State business search** (ownership / shell entity resolution) — heavy
  per-state scraping.

_Sequencing: N1 is the free public new sources + curated overlays + the 3-4 still-missing files
(days). N2 adds licensing, directories, ARCOS, owners, CLIA. N3 holds the licensing/DUA calls.
None of it blocks a rerun — the ~20 sources you already hold are enough to rerun today, and the
"switch-on" one-liner above lights up several more with no procurement at all._

---

## K. SCHEME OPTIMIZATION — double down on the winners (no new data needed)
_These are the schemes already carrying signal. The single highest-ROI change here needs zero
new data — it's fixing how we score what we already compute._

### The headline fix: the 0–1 subscores are throwing away their own raw signal
`signal_ranking.csv` shows the raw features (and their peer-percentiles) beat the rules-engine
`subscore_*` versions — at the top of the list, which is exactly the slice that becomes leads:

| scheme | raw feature (AUC / top-decile lift) | subscore (AUC / top-decile lift) |
|---|---|---|
| saturation | `market_saturation_index` 0.534 / **7.22×** | `subscore_saturation_fraud` 0.549 / **1.00×** |
| pharma kickback | `op_payment_concentration__peerpct` **0.599** / 0.64 | `subscore_pharma_kickback` **0.423** / 0.28 |
| specialty mismatch | `specialty_mismatch` 0.545 / **1.96×** | `subscore_specialty_mismatch` 0.529 / 1.86× |
| overutilization | `service_intensity` 0.550 / **1.71×** | `subscore_overutilization` 0.533 / 1.68× |

The saturation subscore has **no** top-decile concentration (1.0×) while its raw feature has
**7.2×**; the kickback subscore is literally **below random** (0.42) while its raw peer-percentile
is our **best single separator** (0.599). The 0–1 transforms (hard thresholds + squashing) are
flattening the peer-relative signal. Two-part fix:
1. **Train on raw + `__peerpct`, not the subscores** (the handoff already says this). Keep the
   subscores for *explainability only* — they name the driver; they must never be the ranking
   input. Add an audit that fails if any `subscore_*` outranks its own raw feature in the model.
2. **Rebuild the subscore transform to preserve top-decile concentration** — score directly off
   the one-sided robust peer-percentile (median/MAD, clipped), not a threshold, so the subscore's
   top decile matches the raw 7.2×, not 1.0×.

### Per-scheme tuning
- **Saturation (strongest — 10.3× in the top 1%).** Compute supply-vs-need at **county ×
  provider-type** off the CMS Market Saturation file (not a coarse cell); blend with the growth
  signal (markets that *recently* over-saturated = fresh ring spin-up); auto-promote when it
  co-occurs with banned-adjacency (the 555).
- **Overutilization / service intensity (3.4×).** Residualize harder inside tighter NUCC peer
  cells so legitimately-large practices don't dilute the tail; corroborate with the
  **impossible-day** metric (§I3) — intensity + impossible-day is near-certain; separate
  intensity-per-patient from patients-per-provider (distinct fraud shapes).
- **Specialty mismatch (2.2×).** Replace the self-reported taxonomy (gameable) with the **built
  billing-implied taxonomy classifier** (`model_a/billing_specialty.py` →
  `billing_taxonomy_mismatch`); weight by *how far* outside the specialty the codes sit
  (magnitude, not binary).
- **Pharma kickback (best new separator, 0.599, but ~17% coverage).** Load **multiple Open
  Payments vintages** → a true longitudinal payment↔utilization correlation (v1 used one year);
  add the Insys shape as one feature (money concentrated in one manufacturer AND that
  manufacturer's product a disproportionate share of the provider's billing); weight
  **ownership-stake** payments highest.
- **Rapid ramp (temporal, Travis's #3).** Decompose the single temporal feature into ramp-slope +
  level-shift + volatility (the raw columns already exist); add the "spike-then-vanish"
  fly-by-night shape distinct from steady growth; measure it point-in-time via the built
  `asof_billing` so it stays leakage-correct.
- **Network / ownership (7.8× operational — our strongest).** The 64 GB **graph embeddings** are
  the optimization (crude 2-hop → full neighborhood); densify the graph with the **shared-patient
  referral edges** (N1) before the walks; add tight-shell-cluster motif detection.

### Cross-cutting (multiplies every scheme)
- **Peer-grouping quality** is the lever under all of it: the NUCC coherent-cohort fallback is
  built — audit that the ladder resolves to `taxonomy × entity × state` where it can, and flag
  cells that fall back to national (those are weak comparisons diluting the scheme).
- **Per-scheme calibration** (`model_a/calibration.py`, built) so each scheme's score is a
  comparable probability before they stack.
- **Feed the model the signal-count** (§I3 clustering fix) so the *combination* of these tuned
  schemes is rewarded, not each in isolation.

_Highest ROI, in order: (1) stop ranking on the lossy subscores — free, immediate; (2) rebuild
the subscore transform off the robust peer-percentile; (3) swap specialty-mismatch to the
billing-implied taxonomy (module already built); (4) tighten saturation's geography. None need
new data._
