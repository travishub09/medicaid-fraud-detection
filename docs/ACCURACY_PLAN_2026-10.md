# Accuracy plan, October 2026

Prepared by Claude for Trey Rawles and Travis. This is the deep audit the operator asked for on 2026-10-08: not "does the code run" (that was the verification campaign) but "what limits the accuracy of the model, the label and the data, and what would move the number". Three independent audits were run against the code and the v6 and v7 run packets: one on the label versus the target, one on the features and data versus the fraud typologies, one on the evaluation versus the business outcome. This document is the synthesis, the evidence, what was built today in response, and the ranked list of what to do next.

Plain version first, then the technical record. No part of this document is a finding about any provider; the run packets rank organizations for counsel review, never accusations.

## 0. The one-page version

**What we learned.** The model was being graded on the wrong thing, trained on the wrong thing, and shown only part of what the pipeline builds.

1. **Wrong target.** The forward label is a list of exclusions. The business wants False Claims Act cases. On the v7 freeze (December 2021) the exclusion ruler gave a top-decile lift of 4.40 under the all-tier policy and 2.43 under the hard-tier policy, but no number anywhere said how many later FCA cases the first 50 organizations held. Nothing measured lead time or dollars either.
2. **Wrong universe.** Only 324 of the 1,610 hard-tier forward positives had a row in the matrix (20 percent). The convicted physician usually renders under a group's billing number; the matrix had rows for billing numbers only. The model could not rank what it never saw.
3. **Built features never reached the model.** The training harness only trains on columns that trace to a recorded source, and several never did. On in-time runs the five Medicaid billing-pattern scores (concentration, payment intensity, service intensity, specialty mismatch, temporal) traced to nothing and were dropped silently. On v6 and v7 the capacity signal was dropped the same way, and every column from the two July 2026 Medicaid files and the servicing grain would have followed. The frozen runs have a second, older gap: the five billing-pattern scores are removed before the matrix is built because they cannot yet be recomputed as of the freeze, so v6 and v7 never saw them at all.
4. **Missing meant innocent.** Every harness filled a missing value with zero. A missing peer percentile is "no adequate peer group", but zero reads as "more benign than every real provider".
5. **Some rules measured size, not impossibility.** The impossible-units rule compared claim lines with the daily maximum times the days in the month and left the patient count out, so a clinic seeing 121 patients a month on an office-visit code tripped it. The frozen-run billing statistics counted rows instead of patients, so frozen and in-time runs were not like for like.
6. **Negatives were contaminated.** A provider the exclusion list names but the tier policy does not keep (a license action, a loan default, a derivative exclusion) trained as a clean negative. Those are the most contaminated negatives available.

**What was built today, with tests.** The servicing grain (rendering clinicians get rows and signals; bundle 225). A forward FCA ruler built from DOJ and OIG cases announced after the freeze, mergeable with the exclusion ruler. The trainability gate (every produced numeric feature now reaches the model; the export refuses to write a manifest with an unmapped column). Native missing values in all four training harnesses. The per-patient MUE rule. Frozen-run statistic parity and the analytics pass in the frozen target. The peer engine's minimum enforced on the metric's non-null count with per-metric fallback. Masked, not negated, ambiguous rows. The outcome rule on every case-label tier. The evidence ladder counting business names. And the accuracy diagnostics module, which answers the business question directly: hits in the first 16 and 50 organizations with exact intervals against random, dollar-ranked and size-matched baselines; lead time; what the top of the list is made of; calibration at the top; stability under re-shuffled folds; settlement-dollar recall; all read into the release registry as enforceable checks.

**What this means for the next run.** v8 is a new pre-registration, not a rerun. The feature set, the missing-value policy, the MUE rule, the peer rule, the label rule and the ruler all changed, so v8's numbers are not comparable with v7's and must not be read as a regression or an improvement until the diagnostics say so on the FCA ruler at the budgets a human reads.

## 1. Evidence from the real runs (v6, v7)

These are the facts the audits started from, read from the run packets in the operator's data folder.

| Fact | v6 (2023-12 freeze) | v7 (2021-12 freeze) | Source |
|---|---|---|---|
| Providers in the matrix | 617,062 | 617,062 | manifest |
| In-time positives | 4,560 | 4,560 | manifest |
| Forward positives in the universe, hard tier | not reported | 324 of 1,610 (20%) | LABEL_LAG |
| Hard-tier positives still billing in the last 6 months before the freeze | 27% | 34% | LABEL_LAG |
| Median months from last bill to exclusion, hard tier | | 44 | LABEL_LAG |
| Top-decile forward lift, all-tier ruler | | 4.40 (ROC 0.725) | SOURCE_ABLATION__all |
| Top-decile forward lift, hard-tier ruler | | 2.43 (ROC 0.535) | SOURCE_ABLATION |
| Overlap of the top 100 organizations between v6 and v7 | 0 of 100 (Spearman -0.04) | | RANK_STABILITY |
| Top 10 of the pursuit dossier | | all New York home-care agencies tied at score 1.0000 | PURSUIT_DOSSIER |
| Schemes flagged CHECK_DIRECTION | | 5 | SCHEME_HEALTH |
| Trainable columns | | 161 | manifest |

Reading the table: the ruler that looks good (all tiers, lift 4.40) is mostly administrative events. The ruler that looks like the target (hard tier, lift 2.43, ROC barely above chance) is the one that matters, and only a fifth of its positives were in the universe at all. The two freezes disagree completely about who belongs at the top, and the v7 top ten is a tie block, which is a ranking that carries no information inside the block.

## 2. The three audits, condensed

### 2a. Label versus target

- The widened in-matrix label (LEIE, revocations, SAM, OpenSanctions, state terminations, Medicaid enrollment ends, DOJ cases) mixes fraud with paperwork. The tier map handles this for the forward label but the training label used the whole union, and the rows the policy did not keep became negatives. **Fixed:** masked from training, scored out of fold (`company_list.oof_scores`).
- Revocation grounds (a)(3) felony and (a)(12) program termination were tiered hard; the tier-transition data showed only 20 and 17 percent of them convert to a fraud-tier event. **Fixed:** weak.
- The DOJ case label accepted indictments and complaints on the org and affiliation tiers; only the named-NPI and resolved-defendant tiers applied the outcome rule. **Fixed:** every tier requires a resolved outcome (settlement, judgment, conviction, plea); docket rows stay allegations.
- The forward label had no FCA ruler and no horizon, so v6 (33 months of events) and v7 (57 months) were graded with different rulers. **Fixed:** `prospective_label.build_forward_case_label` plus `--horizon-months` on both rulers.
- The evidence ladder graded every resolved business as weak because national name hits were counted for people only. **Fixed:** counted for both kinds.
- Still open: hospitals were removed from the clean anchors (done), but the clean-anchor rule for frozen runs needs a check on real data that it now manufactures negatives.

### 2b. Features and data versus typologies

Ranked by how much accuracy each costs, from the audit's own table:

1. The partition dropped the v3 concepts on in-time runs, `capacity_mismatch` on v6 and v7, and would have dropped every `rx_`, `px_`, `mcd_` and `sv_` column (**fixed**, with a hard gate). The frozen runs also drop the v3 concepts as as-of-uncomputable before the matrix exists (open: recompute them point-in-time from the as-of spending fact; `features.rate_features` is grain-agnostic).
2. The MUE rule ignored patients (**fixed**; per patient, per rendering NPI).
3. Frozen-run statistics changed meaning: `service_volume` was a row count, gross equalled net, and the frozen target never ran the analytics pass (**fixed**).
4. No servicing-grain rows or features (**built**, bundle 225).
5. Missing values filled with zero in every harness (**fixed**).
6. The peer engine's minimum counted rows, not non-null metric values, and the NUCC rung sat after national (**fixed** for the minimum; NUCC rung order open).
7. Run-out and suppression flags computed but never used; growth fills absent months with zero dollars (open).
8. DocGraph and reassignment edges produce no per-NPI features (open).
9. Org resolution: owner-tier merging rarely fires, roles unfiltered (open).
10. Missing shapes that can be built from data in hand: Medicaid-side upcoding (E/M mix), NPI-level step changes and dormant-then-burst, code-mix drift, copy-paste billing, phoenix entities, clone clusters, patient-sharing concentration, Medicare-versus-Medicaid divergence (open; the ranked experiments in section 4).

### 2c. Evaluation versus the business outcome

- Every harness scored NPIs at the top decile (60,000 rows) and compared feature sets. None scored the shipped organization list at depth 16 or 50. A 3x decile lift is consistent with zero future cases in the first 50.
- No artifact recorded lead time, settlement dollars, why non-hits failed, or what happened to a card.
- The CIs covered evaluation-row resampling only, holding the fitted models, the fold assignment and the column screen fixed; LightGBM with default settings and GroupKFold are deterministic, so every out-of-fold number in the repository was one realization with zero measured fold variance.
- **Built:** `model_a/accuracy_diagnostics.py` (section 3).

## 3. What was built today

| Component | File | What it does | Test |
|---|---|---|---|
| Forward FCA ruler | `src/model_a/prospective_label.py` `build_forward_case_label`, `merge_forward_labels`, CLI `--case-db --graph-dir --horizon-months --fca-case-only` | DOJ / OIG cases with a resolved outcome announced after the cutoff, conduct begun on or before it, same columns as the exclusion ruler; union with the exclusion ruler drops anything known at the freeze under either | `tests/test_prospective_label.py` (2 new) |
| Outcome rule on every case tier | `src/model_a/case_labels.py` | org-billing and affiliation rows now require a resolved outcome and carry the strict tier, intervened and qui tam flags | existing case-label tests |
| Evidence ladder for businesses | `src/enforcement/defendant_resolver.py` | national name hits counted for org names too, so a unique business match is moderate, a chain name weak | `tests/test_defendant_resolver.py` |
| Trainability gate | `src/model_a/provider_features_export.py`, `src/model_a/feature_partition.py` | base-frame sources recorded (`v3`, `provider_stats`, `capacity`); every numeric adapter column is a candidate feature; trend suffixes inherit their base; new groups `medicaid_ndc`, `medicaid_enrollment`, `capacity`, `servicing`; the export raises when any trainable column maps to no source and writes `feature_partition` plus `training_policy` to the manifest | fixture export (core 55, extra 12, unmapped 0); `tests/test_feature_partition.py` |
| Native missing values | `company_list.py`, `network_ab.py`, `source_ablation.py` | no zero fill in the training frames (the single-column leak screen keeps its fill) | existing harness tests |
| Per-patient MUE | `src/ingest_cms/mue.py` | lines over MUE times days times patients, at billing by servicing by code by month; one-patient fallback stated in the log | `tests/test_mue.py` (1 new) |
| Frozen-run parity | `src/model_a/asof_billing.py`, `Makefile` | `service_volume` is the patient sum, gross is positive-only; `FROZEN_EXPORT_FLAGS ?= --with-analytics` | existing as-of tests |
| Peer minimum on non-null | `src/analytics/peers.py` `one_sided_percentiles(min_peer=)` | per-metric fallback to the next ladder level when the assigned cell has fewer than 30 non-null values; `<metric>__peer_fallback` flags the rows; the export logs the count | existing peer tests |
| Accuracy diagnostics | `src/model_a/accuracy_diagnostics.py` | section 3a | `tests/test_accuracy_diagnostics.py` (5) |
| Registry KPIs | `src/model_a/run_registry.py` | reads `accuracy_diagnostics.json`; aliases `hits50`, `prec50_lo`, `base50`, `lead_median`, `inst_share50`, `top50_jaccard`, `eval_policy`, `horizon`, `dollar_recall50` for `--check` | `tests/test_accuracy_diagnostics.py` |

### 3a. The accuracy diagnostics, in plain words

The module takes a run folder and one or more rulers and answers, in this order: how many of the first 16 and first 50 organizations on the shipped list had a member provider later named in a public enforcement record, with the exact interval and what random, dollar-only and size-matched picks would have found; how many months ahead of the record the list was and what share of hits were far enough ahead to matter to a relator; what the top of the list is made of (government, already known, institutional, intermediary-shaped, tiny dollars, data artifacts); how the model does by state, specialty family and size with shrunk rates; how the numbers move when ambiguous negatives are removed; whether the calibrated probabilities at the top match what happened; how much the top 50 changes when the folds are re-shuffled; and what share of post-cutoff settlement dollars the top 50 touched. Everything known at the freeze is dropped before any number is computed. The headline is the FCA ruler when a case database is supplied.

Two readings to carry into every discussion of the results. A single PI round is underpowered by construction: 2 hits in 16 has a 95 percent interval from 3.5 to 36 percent, so rounds are pooled and the deeper budgets gate the release. And organization hit rates rise with organization size, which is why the size-matched baseline is reported next to the random one.

## 4. What would move the number: ranked experiments

Every experiment is measured on the FCA ruler with a fixed horizon, at organization budgets 50 and 250 and NPI top 1 percent, with the diagnostics module, and pre-registered before the run. Items that change trained columns or metric meaning are new runs, never silent swaps.

| Rank | Experiment | Why it should move the number | Effort | Status |
|---|---|---|---|---|
| 1 | Run v8 with the trainability gate, native missing values, the servicing grain, the per-patient MUE, the peer fallback and the masked ambiguous rows, graded on the FCA ruler with a 36-month horizon | The model finally sees the capacity signal, the July columns and the rendering clinicians; the ruler finally is the target | one night | code built; run pending |
| 1b | Point-in-time v3 concepts: recompute concentration, payment intensity, service intensity, specialty mismatch and temporal from the as-of spending fact instead of dropping them on frozen runs | The frozen runs that gate releases have never seen the five Medicaid billing-pattern scores | small to medium | open |
| 2 | Run the diagnostics on v7 and v6 as they are (priority-list mode, no training) | Establishes the baseline the sprint is measured against | minutes | reminder queued |
| 3 | Suppression and run-out hygiene: exclude the last six immature months in the export adapters, treat absent months as missing for low-volume providers in growth, add a suppression-exposure covariate, apply the v3 volume gate to rate adapters | Removes false ramps and mill hits on small sporadic providers (the v7 top ten is tie-flooded small agencies) | small | open |
| 4 | Medicaid-side upcoding and a state-by-code price index (E/M, ED, 90837, ABA, T1019 mixes; expected paid from state-code medians) | Upcoding is Part B only today; Medicaid-only billers have no upcoding signal | small to medium | open |
| 5 | NPI-level monthly series pack: step change on paid, lines and patients; dormant-then-burst; abrupt stop while enrolled (payment-suspension signature); months to half of peak; code-mix drift; copy-paste repeats | Rapid ramp is org-level, paid-only and global-ranked today; these are the shapes investigators describe | medium | open |
| 6 | Peer ladder: NUCC classification by entity by state rungs before national; a size tercile rung; Medicaid program state; billing-implied classification when the margin is high; export peer level and n as features | Wrong peers are the first failure mode the engine was built to prevent; the NUCC rung almost never fires where it sits | medium | open |
| 7 | Wire the dead producers: local-volume implausibility (census), reassignment features, capacity ratios for all NPIs, NPPES enumeration date, taxonomy and address changes across editions | Built signal that reaches no row | small to medium | open |
| 8 | Clone clusters and phoenix entities from embeddings, address, owner and servicing overlap | The successor-entity shape is encoded nowhere | medium | open |
| 9 | DocGraph per-NPI referral features with referral and reassignment edges in the embedding adjacency | Patient-sharing concentration and steering are invisible | medium | open |
| 10 | Org resolution: role- and share-aware owner merges as capped components; fold groups by component | Chains under-merged, rings straddle folds; lift should drop (more honest) | medium | open |
| 11 | Repeated cross-fitting intervals (5 to 10 re-shuffled fold assignments, Nadeau-Bengio correction) alongside the cluster bootstrap; later, the cluster bootstrap with refit | Every OOF number was one realization | overnight | `--repeats` built; the corrected interval open |
| 12 | Card-level outcomes: log the 50-card packets (which 16, why, what happened) so the diagnostics' `--cards` input exists | The business outcome is otherwise unmeasurable | operator | open |

## 4b. Built after the v6 and v7 read-outs (2026-10-09)

Three of the six items the read-out called for are now code: the scorer's folds are stratified on positives (so one fold cannot supply a third of the top), every out-of-fold score gets a cross-fitted isotonic probability, and the organization rollup carries P(any member positive) and the maximum member probability. The dossier writes a companion `provider_priority_list_pursuit.csv`: the operator book only (no hospital, system, 25-plus-provider or solo-individual rows). The main list is unchanged so the registry can compare runs.

The sort key of that file was settled by grading it, not by argument. The first version sorted on P(any member) with basis-backed case sizes first and read 0 hits at 50 on v7. The order sweep in the diagnostics (section 2b) then ranked the same 1,855 operator rows under every key: dollar-weighted 1 / 2 / 3 / 4 hits at 16 / 50 / 100 / 250, max member probability 0 / 2 / 2 / 3, P(any) 0 / 0 / 0 / 2, and every basis-backed-first variant 0 at 16, 50 and 100. The operator filter is what doubled the hits (the full main list reads 0 / 1 / 2 / 3); the probability sort and the basis-first block both gave them back. The pursuit file therefore keeps the dollar-weighted order with score_max as the tiebreak, and the calibrated columns ship for the reader only. Read-out section 5 has the table. Item 1 of section 4 (untie the head) stays open for v8; its sort-key half is closed. The diagnostics grade either file (`--list-name`) and write the top tie block to `accuracy_tie_block.csv` so the tied head can be read instead of guessed at. The cap-pinned rows now have their own cause in the error taxonomy (`capped_no_basis`), separate from under-$250k billers.

## 4c. The four levers the audit left, built while v8 ran (2026-10-10)

None of these change v8. They land in the code for v9 and for the current-day label work, and each one is graded before it is trusted.

1. **Duplicate settlements.** The case database marks a row that is the same settlement harvested twice (same rounded amount, same year, a shared distinctive defendant token: the national DOJ release and the local office's release, or a sentencing re-announcement). The row stays, with `duplicate_of` pointing at the earliest copy, and every dollar figure the diagnostics and the audit print now skips the copies. On the 2026-10-09 audit that was 2,929 pairs and $594 billion of double counting in the unresolved-dollars line.

2. **Label recall.** The defendant resolver gained a fuzzy business tier: an unmatched business mention whose distinctive name tokens sit inside one enrolled organization's name in the same state (city narrows) resolves as `resolved_fuzzy`. Those rows are held out of training and out of the strict label file until a verification batch confirms them, because a fuzzy name match is the weakest rung of the evidence ladder. The operator steps are in the reminder queue: rerun the resolver, write the review file with `--include-fuzzy`, verify, then `--verification` promotes the confirmed rows.

3. **The broadcast label.** Five cases supplied 64 percent of v7's DOJ positives because one settlement can name twenty enrolled organizations and fan out across all of them, under the per-organization guard. The case labels now carry a per-case budget: every broadcast row (org billing or affiliated clinician) gets `label_weight = min(1, budget / case_fanout)`, where the fan-out is the number of distinct providers the case reaches, and the budget defaults to 100. Direct rows (a named NPI, a resolved defendant) always weigh 1.0, and no row is dropped, so stratified evaluation keeps its metadata. The export carries the weight as label metadata and the production scorer passes it to the model as a sample weight. A matrix without the column trains exactly as before, so v8 is untouched; a matrix with it is a new model and needs a new pre-registration (v9).

4. **The large-biller book.** The v7 segment table found the model's precision highest among organizations billed over $10 million in the matrix window (top 1 percent hit at 4.6 percent, sixteen times the base rate), and the main list buried them. The priority-list builder now writes `provider_priority_list_large.csv`: the operator book restricted to that band, in the same order as the pursuit book. It is a filter, never a sort key, and it grades on its own through the diagnostics' `--list-name`, with its own suffixed report. The first grade is on v7, where the pursuit book reads 2 hits at 50; the large book has to beat that per row read to earn a place in v9's checks.

## 4d. The v9 design (2026-10-10): structural fixes, not a bigger model

The operator asked whether the per-case weight was a bandage and whether a larger model would help. The weight is a mitigation; the label grain and the fold leak are the structural problems, and with 254 FCA positives a larger learner would memorize five chains with more confidence. v9 therefore keeps LightGBM and changes three things, all written into the matrix manifest under `training_policy` so every scorer call reads the same contract:

1. **Case-cluster folds** (`group_policy = case_cluster`). Every organization that shares a DOJ case is merged into one fold group. A chain settling under twenty enrolled names used to sit fifteen in training and five in test, and the scorer found the five. The manifest counts the clusters.
2. **Seed bagging** (`n_seeds = 5`). Five fits per fold are averaged. It unties the head of the ranking and raises the top-50 stability check; the cost is minutes.
3. **The broadcast-row policy** (`broadcast_rows`). `weight` trains the org-billing and affiliated rows at their per-case weight, the v9 ship. `mask` is the bag-label reading: a row whose only evidence is a broadcast tier leaves every training fold and is scored out of fold. The night runner grades both on the same matrix (`ab_broadcast_weight/` and `ab_broadcast_mask/`), so the v10 question is answered by hits at 50, not by argument.

The registry reads the three keys (`--check "seeds >= 5"`, `"groups = case_cluster"`, `"broadcast = weight"`), and a CLI override on the dossier or the diagnostics is an A/B on the same matrix, never the shipped scorer. The pre-registration for v9, locked before the run starts:

```
hits50 >= 2
prec50_lo > base50
lead_ge_gap >= 0.5
inst_share50 <= 0.4
top50_jaccard >= 0.7
eval_policy = fca
horizon = 36
frozen = True
seeds >= 5
groups = case_cluster
broadcast = weight
```

The hits bar is the v7 operator book's 2; raise it to v8's number if v8 reads higher. v9 reuses v8's point-in-time graph (same freeze, same inputs) and rebuilds only the matrix, so the run is shorter than v8.

When to revisit the learner: once the FCA ruler holds a few thousand positives across hundreds of cases, or once case-cluster folds show the tree model has plateaued. Then a graph neural network over the entity graph is the candidate, because the fraud is relational. Before that it would be a more expensive way to learn five chains.

## 4e. The learning loop and the issue ledger (2026-10-10)

Two post-run modules now close the loop on every run without changing a label or a model by themselves.

The issue ledger reads every report a run writes and produces one ranked list, worst first, with a named fix per row, tracked across runs so a recurring defect escalates and a fixed one is recorded. It also suggests the checks the next pre-registration should carry. On the v7 packet it ranks the 2023-dated facility, PBJ and POS editions inside the 2021-12 freeze as the top FAILs.

The learning loop runs five analyses on the same out-of-fold scoring the shipped list used. Label noise by confident learning: a labelled positive scoring below the mean negative is a suspect label (identity collision, administrative ban, or a scheme the features cannot see), and a labelled negative scoring above the mean positive is a candidate. The active-learning queue sends the rows where one public-records verdict moves the most through the existing Manus plumbing; verdicts feed the label store as evidence. Feature drift against the previous run by population stability index flags a feature that moved without a code change. Near misses on the forward ruler name the five features each missed case sits furthest below the top set on, and the recurring ones are the feature requests. The calibration monitor reports ECE and Brier and writes a conformal p-value per provider. All five feed the ledger.

What this is not: an automatic retrain. Model behaviour changes only through a new pre-registered run graded on hits at 50.

## 5. How to read v8 against v7

Do not compare lifts. Compare, on the FCA ruler with the same horizon: organization hits at 50 against the random baseline from the same pool; the lead-time share beyond the gap; the institutional and already-known shares of the top 50; the Jaccard of the top 50 under re-shuffled folds; dollar recall at 50. A run that raises hits at 50 while the institutional share and the tie count fall is an improvement. A run that raises decile lift while hits at 50 stay flat is not.

The pre-registration checks for v8, enforceable through the registry:

```
hits50 >= 2
prec50_lo > base50
lead_ge_gap >= 0.5
inst_share50 <= 0.4
top50_jaccard >= 0.6
eval_policy = fca
horizon = 36
frozen = True
```

## 6. Operator steps

The reminder queue carries these with the exact commands. In order: run the diagnostics on the v7 packet (priority-list mode, no training, minutes); download the two July files and build them; run v8 as a new pre-registration with the checks above; run the diagnostics on v8 with the case database and the full graph; read the FCA-ruler verdict box first.

_Leads for counsel review, never accusations. A hit in this document is a later public record touching a listed organization, not a finding about it._
