# Verification campaign, 2026-10-08

Prepared by Claude for Trey Rawles. The question: does every component of the model do what it is documented to do, and does the right data reach the right part of it? Method: four layers of checks, each run in this session and recorded below with its result. Nothing here used real data; every run is on the synthetic fixtures the repository ships, which carry planted truths that the outputs must recover. Where a check found a defect, the fix and its regression test are listed. The companion document `docs/MODEL_REVIEW_2026-10.md` holds the design-level review; this one is about execution.

Plain version first, then the technical record.

## 0. Verdict in one page

**Plain version.** The engine computes what it says it computes. Every statistical component (peer percentiles, the scheme engine, the out-of-fold scorer, calibration, conformal bands, false-discovery control, the positive-unlabeled prior, the point-in-time filters, the expected-billing twin, case-control matching) passed a planted-answer test written for it today. The full pipeline runs end to end on synthetic data from raw CSV to the final triage list and recovers the planted frauds in the right tiers. The export, the Model A demo, Model C, the pursuit dossier, scheme triage, the results digest, the expectations report and the label audit all run on the fixture output.

What the campaign found is that the stage-one pipeline could not run on any data except the one file it was built on. Three modules asserted the spending base against hard-coded totals from the 2024 national file, so a refreshed download, a state cut or the synthetic fixture would fail the chain's own assertions. Four more stages crashed on a small book. Two paths in the export used variables that were never set. Those are all fixed with tests. Travis's track has the same hard-coded-count pattern and could not be exercised; it is reported, not changed.

| Layer | What was run | Result |
|---|---|---|
| Static analysis | pyflakes over every module in src | 0 undefined names after the bundle-224 fix; 1 duplicate dictionary key (fixed) |
| Unit suite | pytest, 982 tests | all pass |
| Coverage | pytest under coverage.py | 68 percent of statements; stage-one chain and src/model were 0 percent before today |
| Component invariants | 14 new planted-answer tests | all pass on first run |
| End-to-end, export side | graph, export, Model A, Model C, dossier, triage, digest, expectations, label audit on the fixture | all run; 2 export defects fixed |
| End-to-end, stage-one side | 12 stage CLIs on the raw fixture | 8 defects fixed; planted truths recovered; now a test |

## 1. Static analysis

pyflakes over `src/` reports no undefined names (the docket-branch NameError fixed in bundle 224 was the only one) and one real defect: the HCPCS description table defined `G0480` twice with different text. Fixed. The remaining pyflakes output is unused imports and f-strings without placeholders, cosmetic.

## 2. Coverage, and what it says about risk

Overall 68 percent of statements. The distribution matters more than the number.

- Well covered: the export orchestrator's library functions, the scheme engine, peer normalization, every enforcement loader, the label machinery, the graph build.
- Zero before today: the stage-one chain (`integrate` CLI path, `audit_corruption`, `features`, `detect`, `verify_layer1`, `refine_layer2`, `company_rollup`, `company_lead_tracker`, `finalize_tracker`, `export_final_leads`), the backtest, and all of `src/model`. The end-to-end chain test added today exercises the first group. The backtest and `src/model` remain untested because both hard-code the real file's paths and row counts (section 6).
- Thin: `provider_features_export.main()` itself (48 percent; the fixture path now runs clean, see section 4), `prospective_label` (52), `nucc_taxonomy` (54), `rank_lookup` (28), `feeds/client` (29).

## 3. Component invariants (new tests, `tests/test_model_invariants_2026_10.py`)

Each test plants an answer and checks the component returns it.

| Component | Property checked | Result |
|---|---|---|
| Peer percentiles | within-cell, one-sided, monotone in the metric, unchanged when the label is shuffled (label-agnostic), the ladder falls back when a cell is below 30, a constant cell gives NaN and a degenerate flag | pass |
| Scheme subscores | bounded in 0 to 1, monotone in every input, a missing input is not a zero (mean over present inputs), evidence counts, institutional damping halves exactly the damped schemes and leaves the others untouched | pass |
| Leakage fence | no hard-leak column and no label-metadata column is in any trainable family of the fixture manifest; the group column is declared | pass |
| OOF scorer | planted signal recovered (AUC above 0.8 on a 1,200-row matrix), the near-copy-of-label column is screened out and named, no org straddles a fold, the label column is taken from the manifest when the default name is absent | pass |
| Isotonic calibration | fit on one half, evaluated on the other: output monotone and bounded, Brier and ECE both improve on an over-confident score | pass |
| Conformal p-values | uniform under exchangeability (KS test), small for a planted off-distribution set | pass |
| Benjamini-Hochberg on empirical p-values | under 2 percent rejections with no signal, above 95 percent of 300 planted positives found with under 3 percent false | pass |
| Elkan-Noto label frequency | recovers a planted c = 0.4 within 0.03 | pass |
| Point-in-time | exclusions after the freeze vanish from the graph tables, the forward label and the pre-cutoff set are disjoint, the in-matrix tier label agrees with the graph on the same instant, billing months at or after the cutoff are dropped | pass |
| Expected-billing twin | a provider paid exactly at expectation sits near the median; a planted four-times over-biller ranks above 0.95 | pass |
| Case-control matching | controls are never positives, come only from the clean pool, and share the case's taxonomy | pass |

One observation from the fixture matrix rather than a test: a percentile is NaN for two individual providers whose peer cell has identical values. That is the documented degenerate-cell rule working, not a defect.

## 4. End to end, export side

Commands run on the fixture, in order, with the result:

- `python -m src.model_a.provider_features_export --fixture`: ran, but two later blocks printed misleading "cannot access local variable" messages because the fixture branch never bound the graph directory and processed path. Fixed. The fixture also used the production minimum peer size of 30 on a 14-provider matrix, so every percentile and subscore came back NaN and the expectations report listed 11 all-NaN failures that were fixture artifacts. The fixture now uses a minimum of 2; the remaining 4 failures are the expected-billing twin, which needs 20 providers per taxonomy to fit and correctly declines.
- `python -m src.model_a --fixture` (Model A ERV demo): ran, scored 12 orgs, wrote 3 dossiers.
- `python -m src.model_c --fixture`: ran, underwrote 12 cases.
- `python -m src.model_a.pursuit_dossier --run-dir`: crashed with KeyError on `provider_on_exclusion`, because the OOF scorer hard-coded the label column instead of reading the manifest. Any matrix built without the widened label (the fixture, or a real run where the graph's exclusion nodes failed to load) would crash the standing end-of-run artifact. Fixed: the scorer reads the manifest's label with a fallback to `provider_on_leie`. Dossier then ran: 10 targets, prompts written.
- `scheme_triage`, `results_digest`, `expectations` standalone, `label_audit`: all ran on the fixture output.

Matrix checks on the fixture export: the single positive is exactly the exclusion-node NPI; every percentile and subscore lies in 0 to 1; no hard-leak or label-metadata column sits in a trainable family; the group column and the assessable flag are present; the manifest carries the as-of cutoff, the sources audit, the vintage classes and the weak-supervision audit.

## 5. End to end, stage-one side

The Makefile's `pipeline` target, run as the operator runs it, against a throwaway data root holding the raw-shaped fixture (five CSVs with the real CMS headers and planted truths). Each defect below stopped the chain until fixed.

| Stage | Defect | Fix |
|---|---|---|
| integrate | with no `preclean/owners/` folder the facility-flags file is never written, and `features` requires it, so the chain cannot start on a data root without owners | writes the empty flags table with the contract columns and says so |
| audit_corruption | formats MAX/MIN results that are NULL when a bucket is empty: TypeError on a clean or small file | NULL formatted as 0 |
| features | asserts the provider base equals 230,133,234 rows and 1,100,631,960,143 dollars, hard-coded from the 2024 national file | reconciles against `processed/corruption_audit.json`, which the audit now writes; the constants remain only as the fallback for a folder audited before the sidecar existed, and the report names which basis was used |
| verify_layer1 | KeyError on the LEIE waiver column when the raw file lacks it | missing waiver and reinstatement columns treated as blank, logged |
| refine_layer2 | division by zero in the report on a book with no scored rows | guarded |
| company_rollup | assigning an empty apply result to one column raises on a book with no leads | guarded |
| company_lead_tracker | the same hard-coded dollar total as features; and the composition report crashed on an empty company-anomaly tier | shared `audit_basis` helper; report guarded |
| backtest score_universe | the same hard-coded dollar total | shared helper |

After the fixes the chain runs to the end and recovers every planted truth: the excluded NPI billing in 2022-09 and 2023-03 is Layer 1, QUALIFIED by the verifier with 5,000 dollars after exclusion over 2 clean months, and rank 1 of the final triage list as a direct billed-while-excluded lead; the facility owned by the LEIE-named company (Tier B name match) and by the excluded owner (Tier A NPI match) carries both ownership flags and its company reaches the tracker as a probable-owner lead; company dollars equal the matched spending base to the cent. This is now `tests/test_stage_chain_end_to_end.py` (13 seconds).

**Operator note.** On the laptop, the next `features` run will reconcile against the legacy constants until `audit_corruption` is rerun once, because the sidecar does not exist there yet. Rerun the audit before the next features build, or when the spending file is refreshed. The reminder queue carries this.

## 6. What could not be verified, and why

- **`src/model` (Travis's LightGBM track).** `config.py` points at `~/Desktop/Data` and asserts the PU frame has exactly 308,038 rows and 578 positives and the universe 617,062 rows. It cannot run on the fixture or on any refreshed data without editing the module. The same class of defect as the stage-one constants; left to Travis per the coordination rule, with the recommendation to read expected counts from a sidecar the way the stage chain now does.
- **`src/backtest/backtest_leie.py`** has no argument parser and executes the full backtest at import of `main`, so even `--help` runs it against the real paths. Not run.
- **Real-data behavior** of the eight audit fixes in bundle 224 and the stage fixes here: the next build's run packet is the test. The checklist is in the review document, section 5.
- **Downstream consumers of the fixture outputs** that need many positives (network A/B, source ablation, label ablation, operating points) cannot produce meaningful numbers on 14 rows and 1 positive; their logic is covered by their existing unit tests and by the OOF invariant test.

## 7. Files changed in this campaign

Code: `src/attempt_2/audit_basis.py` (new), `src/attempt_2/ingest/integrate.py`, `src/attempt_2/audit/audit_corruption.py`, `src/attempt_2/ingest/features.py`, `src/attempt_2/leads/verify_layer1.py`, `src/attempt_2/leads/refine_layer2.py`, `src/attempt_2/leads/company_rollup.py`, `src/attempt_2/leads/company_lead_tracker.py`, `src/backtest/score_universe.py`, `src/model_a/provider_features_export.py` (fixture path), `src/model_a/company_list.py` (label from manifest), `src/model_a/hcpcs_descriptions.py`.

Tests: `tests/test_model_invariants_2026_10.py` (14), `tests/test_stage_chain_end_to_end.py` (2).
