# Model and label review, October 2026

Prepared 2026-10-08 by Claude for Trey Rawles. Scope: every data source and scheme currently wired into the per-NPI feature export, the label assembly, the training and evaluation path, and the lead ranking. Method: two independent code audits of the repository (one over the adapters and schemes, one over the model path from the feature matrix onward), each finding verified by reading the cited lines before anything was changed. Nothing here was run against real data; the run-level checks are listed at the end as the first thing to do after the next build.

Each section has a plain version first and the technical detail second.

## 0. The one-page summary

**Plain version.** The platform's data plumbing is in good shape: 21 schemes, about 30 adapters, every source logged as used or skipped, every join asserted. The label is wide and well provenanced. What is not in good shape is the layer between the feature matrix and the number we tell Brad: the model that produces the pursuit list is not the model whose lift gates releases; several of the statistical safeguards described in the docs (calibration, false-discovery control, conformal bands, the PU prior correction) are written and tested but never called by any pipeline; and a handful of bugs were quietly corrupting the label or hiding negatives on frozen runs. Seven of those bugs are fixed in this bundle with regression tests. The rest of this document is the prioritized plan for the remaining items, with the ones that change metric semantics flagged so they are re-registered rather than silently applied.

**What was fixed today (bundle 224).**

| # | Defect | Effect before the fix | Fix |
|---|---|---|---|
| 1 | Label smear: one deactivated or deceased member marked every NPI in its org as a positive | Hundreds of innocent NPIs per event became training positives | Widening now uses the NPI's own post-event dollars from the smoking-gun timeline; the org flag counts only for single-NPI orgs |
| 2 | Frozen runs manufactured zero clean negatives | Matched sets and weak-supervision anchors were empty on every as-of run | The fallback compared a 0-1 percentile to 0; it now tests at or below the median (0.5) |
| 3 | Docket-only case labels crashed the export | Any case DB with a docket row raised NameError | Logger scope fixed |
| 4 | Label provenance tagged state terminations, state board actions, SAM rows and research rows as `leie` | Label ablation could not separate those sources; SAM rows fell into the wrong FCA tier | Mapper extended; SAM rows now carry a `sam:` prefix |
| 5 | Every no-taxonomy provider was pooled into one fake "unknown" NUCC peer cell and ranked inside it | Confident percentiles for providers with no cohort | No taxonomy now means no cohort |
| 6 | In-matrix tier label froze at the cutoff month's end while the graph and the forward label freeze at its start | Late-month bans were both a training positive and a forward positive | Same instant everywhere |
| 7 | Frozen-run DOJ labels were boxed by when conduct began, not when the case was announced | A settlement announced after the freeze became a training positive on a frozen run | Cases must be announced on or before the cutoff |
| 8 | `--skip-sources` was ignored by every org and CCN-grain block | The flag lied, and SOURCES_REPORT with it | Honored at emit time |

Plus the two July 2026 CMS files are wired (section 6) and the vintage inspector's orphan list was corrected.

## 1. Data and scheme wiring: what is fed, what is not

**Plain version.** Of the 21 schemes, 14 are fully fed when their files are present. Four have an input that nothing produces, so part of their weight is dead. Three depend on files you have not downloaded or on a crosswalk that must be built first. The source audit in every run already reports used-versus-skipped per source, so the standing check after each build is simply to read SOURCES_REPORT.md against this table.

**Scheme status** (the engine takes a weighted mean over the inputs that are present, so an unproduced input lowers the subscore's evidence count but does not zero it):

| Scheme | Inputs that are produced | Inputs nothing produces | Depends on |
|---|---|---|---|
| single_service_mill, payment_outlier, overutilization | v3 concept + Part B ratio | none | v3 concepts are dropped on frozen runs, so only the Part B ratio remains there |
| specialty_mismatch | v3, billing-implied taxonomy, clinical plausibility | `local_volume_implausibility` (producer exists, never called) | `--with-analytics` |
| rapid_ramp | v3, growth level shift, new-code burst | none | `--with-analytics` |
| ownership_integrity | graph features, ownership turnover, NH owner stakes | none | owner snapshots or All-Owners editions |
| upcoding | Part B E/M levels | none | Part B |
| impossible_day | minutes per day, MUE violation share | `bene_per_day_p95` | hcpcs_time, MUE |
| pharma_kickback | Open Payments concentration and co-occurrence | none | Open Payments + Part D |
| nemt_fraud, behavioral_health, facility_code_billing | sector metrics from the spending fact | none | none |
| drug_outlier | Part D shares, drug markup | `drug_spread_anomaly` until the NDC file lands (now wired) | NADAC + NDC claim slice |
| dme_ring | DMEPOS shares, ineligible referral share | `dme_ordering_md_concentration` | order_referring + referred claims |
| worthless_services | PBJ, deficiencies, capacity, penalties | none | ccn_to_npi crosswalk |
| hospice_ineligibility | Care Compare hospice measures | none | ccn_to_npi; the long-format pivot fix for six measures |
| saturation_fraud, pill_mill, contract_pharmacy, cost_report_fraud, invalid_identity | fed | none | respective files |

**Technical detail.** Definitions live in `src/model_a/scheme_subscores.py` (weights, sigmoid, institutional damping). Peer normalization for adapter columns is the mid-rank one-sided percentile on the ladder taxonomy by entity type by state, then taxonomy by entity type, then taxonomy, then the NUCC cohort, with a minimum cell of 30 (`src/analytics/peers.py`). Hard rule 8 in CLAUDE.md says robust z; the export's path is percentile rank with no MAD step and no clipping, while five other modules each use a different convention (robust z clipped at 6 or 50, `r/n` rank, global rank). This is not a bug, but it means "percentile" does not mean the same thing across columns, and the subscore engine clips every input to 0-1 as if it did. See plan item T2-4.

Other wiring facts worth knowing: when two sources emit the same column name the first one silently wins (`total_services` from Part B over DMEPOS, `total_claims` from Part D over opioid, `total_paid` from deactivation over death master); the referrer-grain order-and-referring fallback picks the alphabetically first file and ignores the vintage cutoff; HCRIS reads only top-level CSVs while POS recurses; the NUCC loader requires the exact filename `nucc_taxonomy.csv`; 340B fills non-participants with 0 rather than missing. Each is listed with its line in the appendix.

## 2. The label

**Plain version.** The training label is a union of six kinds of evidence: the OIG exclusion list, Medicare revocations, SAM debarments, OpenSanctions, state Medicaid terminations, and resolved DOJ cases, plus two "smoking gun" flags (billing after NPI deactivation or death). Each positive carries a provenance string naming its sources, and a separate forward label marks providers excluded after a freeze date for out-of-time testing. The FCA tiering (hard, weak, excluded) exists and works, but it is applied only to the forward label and to the tier-policy switch of the production scorer; the matrix's own `provider_on_exclusion` column has never been tiered, so a loan default or a paperwork revocation is a positive in the default label. Fix 4 above repaired the provenance strings; the tiering gap is plan item T1-3.

**Technical detail.** Assembly order: `integrate.build_exclusions` writes `processed/exclusions.parquet`; each enforcement CLI writes `processed/exclusions_<source>.parquet`; the graph loader concatenates them into exclusion nodes (without de-duplication, so running `medicare_revocations` after `integrate` already merged revocations creates duplicate nodes); `_widened_label_from_graph` turns NPI-matched nodes into `provider_on_exclusion` with provenance; `case_labels.build_case_labels` adds DOJ positives by named NPI, resolved defendant, org billing and affiliated individual (the last never flips the label); the smoking-gun widening follows; `clean_anchors` and `weak_supervision` produce negatives and a soft label. The weak-supervision model is computed on every run and written as label metadata, but no trainer consumes it, and two of its labeling functions vote on the very flags that were folded into the label (circular, not in the fixed-weight list).

## 3. Training, leakage, evaluation and ranking

This is where the review spends its weight.

### 3a. Three models, three different questions

**Plain version.** There are three supervised models and they do not measure the same thing. The pursuit dossier and priority list come from `company_list.oof_scores`, trained on the in-time label with the leakage-adjacent columns banned. The number that gates a release in the run registry comes from `source_ablation --oof`, trained on the forward label with the adjacent columns allowed, and only the difference between the full and core feature sets is gated, never the absolute lift. Travis's `src/model` LightGBM picks its negatives as the providers with zero anomaly signal and then reports that it ranks high-anomaly providers at the top, which is the definition it was given, not a validation.

**Technical detail.**

| Track | Label | Features | Split | Output |
|---|---|---|---|---|
| `company_list.oof_scores` (operative) | in-time `provider_on_exclusion`, optional tier policy | bans hard + adjacent + AUC > 0.985 | 5-fold GroupKFold on org | pursuit dossier, priority list |
| `source_ablation --oof` (gating) | forward label | bans hard only; adjacent allowed | 5-fold GroupKFold on org | SOURCE_ABLATION_OOF, registry delta |
| `src/model` (Travis) | `provider_on_leie` all-time | bans detector columns | random stratified 80/20; early stopping on the reporting set | score ≥ 0.90 lists, no drivers |

The registry glossary says the forward-lift model "never saw these outcomes." That is not true of the OOF design: it learns from other providers' post-cutoff bans inside the same window. The only path that trains on the past and tests on the future is `label_ablation`, and its "all sources" row is the closest thing we have to the shipped scorer's real forward performance. Bootstrap intervals in the ablations resample evaluation clusters without refitting, so they are narrower than the truth; `network_ab` does pool over splits and is the better-built of the two.

### 3b. Leakage controls, and the holes that remain

**Plain version.** The platform has the right ideas: a billing cutoff, a point-in-time graph, a fence list of columns that may not be trained on, grouped cross-validation, a forward label. Each is enforced somewhere. The holes are that the fence is applied inconsistently across the three tracks, that frozen runs still read several current-day files as if they were frozen, and that the Makefile's frozen package does not freeze the address layer unless a flag is set by hand.

**Technical detail, in order of consequence.**

1. The adjacent fence is not applied in `feature_partition._trainable_set` or `network_ab._trainable`, so every ablation trains on `within_2_hops_of_exclusion`, `shell_score`, `dme_ineligible_referrer` and the `billing_after_deactivation__peerpct` transform of a hard-leak flag. On an in-time ablation that is direct label leakage.
2. `shell_score` is treated as "structural" in the network A/B, but without `--asof-nppes` its co-location substrate is today's NPPES. `FROZEN_GRAPH_FLAGS` defaults to empty in the Makefile. `recommend()` only checks `substrate_frozen` if someone pre-registered `--check "frozen = True"`.
3. Frozen runs still read current-day inputs as clean features: `graph_velocity` diffs the two newest snapshots regardless of the cutoff; address grounding reads the current provider_dim; facility, POS, ownership and HCRIS are labelled current-state in the manifest but remain trainable.
4. The in-time default build uses the full billing history, so a positive's own post-exclusion collapse is inside its feature window (tenure, active months, growth).
5. `confirmed_clean` requires zero exclusion proximity, so on in-time runs the negatives are selected on the label's own neighborhood; the matched sets saturate, which is why a ceiling AUC constant exists.
6. The single-column AUC leak screen runs on all rows before the folds (mild, conservative in direction).

### 3c. Evaluation

**Plain version.** The numbers that exist are top-decile lift, PR and ROC AUC, catches at review budgets, and per-scheme AUC. The numbers that the documentation promises but no pipeline computes are calibration (Brier, ECE, reliability), false-discovery control on the lead list, conformal bands, and the PU-corrected lift. Those modules are written and tested and have no caller. RESULTS_DIGEST contains no forward-lift figure and hard-codes a sentence claiming the model "reliably re-finds providers already known to be excluded," which is the in-time result, not the forward one.

**Technical detail.** `calibration.py`, `conformal.py`, `fdr.py`, `pu_prior.py`, `feature_store.asof_join` and `temporal_split`, and `validation.temporal_holdout_precision_at_k` have no in-repo caller outside tests. `operating_points` fits its isotonic map on the same OOF rows it reports precision on. `supervised.train_pu_classifier` fits its isotonic map on all rows including training rows and against the labelled indicator, which undoes the PU correction; its output becomes `adjusted_prob` in the ERV composite. The registry's PROMOTE rule gates the full-minus-core delta, the expectation FAIL count and the network verdict; nothing gates absolute forward lift, and a pre-registration can be written after seeing results because `declared_at` is never compared with the matrix timestamp.

### 3d. Ranking and explainability

**Plain version.** Five rankings exist and only one of them reaches Brad. The pursuit list sorts on the dollar-weighted supervised score with dollars as a filter, which is the right rule. The Model A ERV composite sorts on dollars first, which contradicts that rule, and Travis's lists carry no drivers at all, which violates hard rule 5. The "top schemes" shown as drivers on a pursuit card are org-mean subscores, not model attributions, so a card can name a scheme the model did not actually use.

**Technical detail.** `pursuit_dossier` keeps candidates with `score_dollar_weighted ≥ 0.5` on raw LightGBM probabilities at a base rate near one percent; that threshold is uncalibrated and will drift with every refit. `company_list.rollup` ranks orgs on the max member score, which rises with org size. `lead_export` is the only output that asserts dollars out of its sort key.

## 4. The optimization plan

Ordered by expected effect on forward precision per unit of work. Items marked (re-register) change metric semantics and must be a new pre-registered run, not a silent swap.

### Tier 1: before the next frozen run

- **T1-1 One fence for all three tracks (re-register).** Make `feature_partition._trainable_set` and `network_ab._trainable` subtract `leakage_adjacent` by default, with an explicit `--allow-adjacent` for the diagnostic that wants it. The gating metric and the shipped scorer then describe the same model. Expect the full-minus-core delta to shrink; that is the honest number.
- **T1-2 Freeze the substrate by default.** Set `FROZEN_GRAPH_FLAGS ?= --asof-nppes` in the Makefile and make `recommend()` refuse PROMOTE when `substrate_frozen` is false, unconditionally. Make `graph_velocity` pick snapshots dated at or before the cutoff and address grounding read the as-of provider_dim.
- **T1-3 Tier the matrix label.** Apply `fca_relevance` to `provider_on_exclusion` at assembly time and ship `fca_tier` as label metadata, so the default label excludes loan defaults and paperwork revocations the way the forward label already does. Record `label_policy` in the manifest.
- **T1-4 Calibrate the operative score (re-register).** In `company_list.oof_scores`, cross-fit an isotonic map (fit on four folds' OOF scores, apply to the fifth) and emit `score_calibrated`. Replace the raw `≥ 0.5` hot threshold in the pursuit dossier with a calibrated probability or a fixed review budget from `operating_points`. Write Brier and ECE to RESULTS_DIGEST.
- **T1-5 Forward lift of the shipped model.** Add `label_ablation`'s all-sources forward row to RESULTS_DIGEST and the registry as `forward_lift_shipped`, and gate on it with a pre-registered floor. Remove the hard-coded "re-finds excluded providers" sentence.
- **T1-6 Run the July files** (section 6) and rebuild the graph so the enrollment terminations enter the label.

### Tier 2: the next two runs

- **T2-1 Group by ring, not org.** Build `group_id` from connected components over owner, address and reassignment edges (capped at a size), so a ring cannot straddle folds.
- **T2-2 Honest intervals.** Refit inside the bootstrap for the ablation deltas, or report the across-fold standard error alongside the cluster bootstrap. Pool over splits as `network_ab` does.
- **T2-3 Feed the dead inputs or drop their weight.** Produce `local_volume_implausibility` (the census denominator function exists), `bene_per_day_p95` (from the spending fact per NPI per month), and `dme_ordering_md_concentration` (from the DMEPOS by-referring file). Until then set their weights to zero so the evidence count is honest.
- **T2-4 One normalization contract.** Decide whether adapter columns are percentiles or robust z and make every producer conform before the subscore engine; stop re-ranking columns that already arrive as percentiles (facility, HCRIS, saturation, 340B, drug markup). Re-rank the HCRIS max-of-percentiles the way v3 re-ranks concept maxima.
- **T2-5 Negatives without the label's neighborhood.** On in-time runs, drop the proximity conditions from `confirmed_clean` or draw controls by propensity on billing covariates only, and move the two circular labeling functions into the fixed-weight list.
- **T2-6 Registry integrity.** Reject a pre-registration whose `declared_at` is after the matrix timestamp; refuse `--replace` on a registered run; make sign-off a gate.
- **T2-7 Drivers from the model, not from the subscores.** Attach per-row SHAP (or LightGBM contributions) to the pursuit card's technical picture; keep the subscore drivers as the plain story. Give Travis's exports the same.
- **T2-8 De-duplicate exclusion nodes** in the graph loader on (npi, excl_type, excl_date).

### Tier 3: research

- **T3-1 Replace the max rollup** with a size-adjusted org score (mean of top-k member scores or a Bayesian shrinkage toward the org mean).
- **T3-2 Survival framing.** Model time-to-exclusion from the first billing month with the feature snapshots as time-varying covariates; it uses the conduct windows and removes the arbitrary cutoff.
- **T3-3 Scheme-specific models** where the DOJ scheme labels reach 50 positives (upcoding, kickback, home health), evaluated with the fair-cohort restriction already in `scheme_eval`.
- **T3-4 The `src/model` track.** Retire the zero-anomaly negative rule; train on the same matrix, label and fence as `company_list`, with GroupKFold and a held-out time slice, and add drivers. Until then its lists should not reach counsel.

## 5. What to verify on the next build

Read these in the run packet; they are the run-level counterparts of the findings above.

1. SOURCES_REPORT: every scheme in section 1 shows its inputs as used; `medicaid_ndc` and `medicaid_enrollment` appear once their files exist.
2. The manifest's `label_audit`: positives by source now include `state_termination`, `sam`, `research_*` and `medicaid_enrollment` as separate names.
3. `confirmed_clean` count on the frozen run is no longer zero.
4. The number of positives from `deactivation` and `death` sources falls to roughly the number of deactivated or deceased NPIs with post-event dollars, not the number of their org members.
5. `case_labels` prints the count of rows dropped for post-cutoff announcement on a frozen run.
6. The NUCC cohort column has blanks where taxonomy is blank.

## 6. The two July 2026 files

**Medicaid Provider Spending by NDC** (`src/ingest_cms/medicaid_ndc.py`). Streams the raw CSV into `processed/ndc_fact.parquet` with row and dollar conservation asserted, writes the NDC claim slice that un-gates `drug_spread_anomaly` when the file carries units, and emits per-NPI features for both roles: pharmacy (`rx_*`: prescriber concentration, top-prescriber share, distinct NDCs, refills per patient, the share of dollars on fills dated after the prescriber's exclusion or NPI deactivation) and prescriber (`px_*`: pharmacy concentration, dollars per patient-month, brand share against NADAC). A frozen run derives an as-of fact under its cutoff. Column names are resolved from candidate lists; the load report prints the resolution.

**Medicaid Provider Enrollment Segments** (`src/ingest_cms/medicaid_enrollment.py`). Streams the segments into Parquet; per-NPI features as of the cutoff (`mcd_*`: states enrolled, primary state, segments, tenure months, enrolled months, gap share, open segments, ended for cause); termination events with a non-benign status or reason become label rows `mcd_enroll:<ST>:<reason>` in `processed/exclusions_medicaid_enrollment.parquet`, tiered by the state-termination ruler (benign ends are never positives); the paid-after-enrollment-end per-se check; and a state fill for provider_dim. Rebuild the graph after the file exists so the events become exclusion nodes.

Both are in the preflight registry, the export's SOURCES_REPORT, the operator reminders with the exact download and build commands, and the test suite.

## Appendix: open items with locations

Unfixed, listed for the next bundle. Lines refer to the state of the code before this bundle's edits where an edit moved them.

- Adjacent columns trainable in ablations: `src/model_a/feature_partition.py` `_trainable_set`; `src/model_a/network_ab.py` `_trainable`.
- Substrate not frozen by default: `Makefile` `FROZEN_GRAPH_FLAGS`; `src/model_a/run_registry.py` `recommend()`.
- `graph_velocity` ignores the cutoff and `--snapshot-dir`: export around the velocity block; `src/entity_graph/graph_velocity.py`.
- Referrer-grain order-and-referring fallback ignores vintage: export `_first_existing(..., "*.csv")` in the referrer block.
- Same-name column collisions resolved by first adapter: export `build_provider_matrix` merge loop.
- `supervised.train_pu_classifier` isotonic fit on training rows against the labelled indicator: `src/model_a/supervised.py`.
- `operating_points` in-sample isotonic: `src/model_a/operating_points.py`.
- Weak-supervision circular functions outside the fixed-weight list: `src/model_a/weak_supervision.py` `CIRCULAR_WITH_ANCHORS`.
- Exclusion node duplication: `src/entity_graph/__main__.py` loader.
- Unproduced scheme inputs: `local_volume_implausibility`, `bene_per_day_p95`, `dme_ordering_md_concentration`.
- Downstream scheme-name lists out of sync: `dossier.SCHEME_NARRATIVES`, `model_c/priors.SCHEME_PRIORITY_MULTIPLIER`, `sector_priors.SCHEME_EXPOSURE_BASIS`.
- `--fixture` path NameErrors in the export (`g`, `processed` undefined).
- RESULTS_DIGEST hard-coded claim sentence: `src/model_a/results_digest.py`.
- `src/model` design: `src/model/data.py` negative rule, `train.py` early stopping on the reporting set, `export_leads.py` no drivers.
