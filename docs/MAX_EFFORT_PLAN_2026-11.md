# The maximum-effort plan, 2026-11-02

The operator's standing instruction: every question gets a detailed plan, pursued with
everything we have, in parallel wherever the work does not collide. This document is that
plan. Each section states the question, the outcome that would count as solved (a number
we can read off a run, never an impression), the diagnosis from docs/ARCHITECTURE_AUDIT_2026-11.md
and the read-outs, the full work list with its state (DONE / RUNNING / BUILDING = a
worker is on it now / NEXT / NEEDS = data, credits or a decision), the experiment that
proves it, and the dependency on other sections. Parallel execution rules are at the end.

## 0. How the parallel work is organised

Seven builders run at once, each in its own git worktree, each owning a disjoint set of
files, each required to commit with tests. The merge is the one serial step; a builder
that needs a change in a file it does not own reports the change instead of making it, so
no two workers edit the same line. The VM runs v28 (the first run on the label levers),
lane cycle 3 (the retries, the proof-sentence prompts, the uncapped defendant research)
and the browser-proof lane beside it; nothing else shares the VM until v28 is done.
Research credits are uncapped where the work feeds the label. The result of every
builder lands as v29 semantics behind one pre-registration, never as a silent change.

| builder | slice | files owned |
|---|---|---|
| A | per-case label mass, fan-out over training bases, one conduct rule on both sides | case_labels, prospective_label |
| B | ruler on the as-of graph, case-cluster grade with its baseline, tie-aware hits | accuracy_diagnostics |
| C | per-CCN facility percentiles (no max rollups), capacity per CCN, hospice measure codes | facility, peer_percentiles, pos, the two export blocks |
| D | honest patient-month bases, deactivation per NPI, drug spread within state | sector_schemes, servicing_grain, medicaid_ndc, drug_markup, nppes_deactivation |
| E | operator-segment scorer, the solo book, size-neutral rollup statistics, weight-table lint | company_list, pursuit_dossier, scheme_subscores |
| F | policy-aware features from the state rule library, the held-out field-outcome ruler | new modules |
| G | employer reach for every state and MFCU positive, docket outcome entries onto the ruler | label_expansion, docket_backfill |

## 1. Improve the label (recall and integrity)

**Outcome that counts:** forward positives in the universe on the FCA ruler above 583 at
the 2021-12 freeze and 273 at 2023-12, with every row carrying an identifier, a date, a
tier and a page that names the defendant; a state ruler whose positives reach the listed
organizations (ruler reach above 2 percent); zero research rows training on an unverified
page.

**Diagnosis.** The label is 82 percent institutional because the DOJ feed is; 83 percent
of convicted individuals never bill under their own NPI; the research lanes attach wrong
defendants to real pages one time in two; the state, MFCU and enrollment labels are
individuals whose organizations never rank.

**Work.**
- DONE: the page rule (a research row trains only when its cited page names the
  defendant), the proof-sentence rule on every harvest prompt, the categorical outcome
  vocabulary, the URL and state-name gates, the retry of service-failed tasks, the resolver
  re-run (2,955 NPIs across 2,760 cases), field outcomes by command (8 rows).
- RUNNING: lane cycle 3 (retries, proof prompts, 150 per lane), the uncapped defendant
  research (about 1,650 tasks), the browser-proof lane (the blocked MFCU, AG, DOJ and
  commercial pages), the laptop page cache for the hosts that allow a plain download.
- BUILDING (G): employers for every state and MFCU positive, carried with the
  individual's tier and date; docket entries with a settlement or judgment as case rows.
- BUILDING (F): the held-out field-outcome ruler with a date gate and a trained-on refusal.
- NEXT: the Manus lanes for the OIG CIA residue and the name-only exclusions keep running
  each cycle; the strict PurpleLab label re-exported after the proof lane loads; the
  claims-level join (PurpleLab) as the one change that moves the grain.
- NEEDS: field outcomes for every visited card (one command per card); the Morrison
  visit when Sage schedules it; Dashel's CourtListener token and PACER login for the
  docket entries and the Sims complaint.

**Experiment.** Re-grade v28's matrix under three rulers (FCA, state, field) with the
reach table; the lever that moves reach the most gets the next credits.

## 2. The analyst's four questions

**Outcome that counts:** each question answered by a graded arm on one matrix, written up
in the run's read-out, with the policy that wins pre-registered for the next run.

- Ablation: DONE (source and label ablations run in every measure step, fenced to the
  shipped scorer's ban list); NEXT: the per-source table is folded into RUN_ISSUES with
  the bootstrap CI printed beside every forward lift.
- Settlement weighting: DONE as an arm (ab_settlement_05 in v28); the per-case label mass
  (builder A) is the stronger version of the same question and ships as an arm in v29.
- Run time and the quick check: DONE (the resume cache, the measurement step, the
  free re-grade path regrade_from.sh); NEXT: a `--quick` profile on the runner (one seed,
  no arms, the pursuit book only) for a thirty-minute sanity run.
- The market-data leak: DONE (the saturation period fix, the vintage ledger, the data
  scanner); the audit added three fences today (enrollment ends, exclusion-derived
  prescriber shares) and one more class is BUILDING (C: no post-cutoff facility edition
  through a max rollup).

## 3. The owner-run class is at chance

**Outcome that counts:** hits inside the operator book at 100 and 250 above random on the
FCA ruler AND the state ruler, with the solo book graded on its own; a segment model that
beats the global model on the operator book, or a documented reason it cannot.

**Diagnosis (the audit, section 1).** Five compounding institutional biases (label mass by
org size; the ruler on the current-day graph with clustered hits; three max-over-members
rollups; size-growing rollup statistics; within-taxonomy percentiles that hide the class
base rate) and four owner-run dilutions (fan-out counts rows that never train; the 'all'
label removes their likeliest forward cases from the list and the grade; calibration
learned with weak-tier events as negatives; zero-where-null inputs scored at the median).

**Work.**
- DONE: the 'all'-label drop rule and the known rule replaced by the tier label and
  outcome sources; the calibrator fit on the training rows; the Jaccard on the shipped
  book; cases touched per budget; the zero-where-null inputs raw-driven; the re-ranked
  percentiles stopped.
- BUILDING (A): per-case label mass as a policy; fan-out over training bases.
- BUILDING (B): the ruler on the as-of graph; the cluster-aware grade and baseline.
- BUILDING (C): per-CCN facility inputs and capacity.
- BUILDING (E): the operator-segment scorer; the solo book; mean / top-2 / exceedance
  rollups for the order sweep.
- NEXT: v29 pre-registered with every arm above graded on the operator book and the solo
  book under the FCA and state rulers; the size dimension in the peer ladder (the audit's
  F8) and the owner-component org key (G1) as v30 arms.

**Experiment.** The audit's experiments 1, 2, 5 and 8 on v28's matrix, no retrain except
the segment model; the winner is the v29 policy.

## 4. Scheme integrity: wrong calculation, wrong data, or not an indicator

**Outcome that counts:** every scheme's inputs computed as the catalog states them, each
scheme graded on its own typed ruler with a verdict (signal / coin flip / inverted /
thin), the inverted and coin-flip schemes either repaired or pruned by a pre-registration,
and the alternative formulas graded beside them.

**Work.**
- DONE: the third audit read graded every input (the table in the agent report; the ten
  worst in the audit section 3); six repaired today (contract-pharmacy null, prolonged
  and interactive bases, the raw-driven flags, the re-ranked percentiles, the fenced
  enrollment and prescriber columns).
- RUNNING: v28 measures every scheme on the FCA, state and typed rulers and grades the
  alternative formulas (SCHEME_ALTERNATIVES.md).
- BUILDING (C, D): the capacity grain, the facility rollups, the hospice measure codes,
  the patient-month bases, the deactivation grain, the drug spread reference.
- NEXT: the remaining table rows (hcris sign by payment basis, the Medicare
  individual-grain weights on org-ruled runs, the DME supplier file, time_minutes_per_day
  at the servicing grain, the consistency flags within entity type); the lint test (E)
  keeps the weight table honest from here.

**Experiment.** The v28 scheme table read against the audit's verdict column: a scheme
that is coin-flip on its own ruler after its calculation is fixed is pruned in v29.

## 5. Payer policies in the model

**Outcome that counts:** every theory screen reads the state's rule where the library
has one (bar, text, citation), and the model carries a policy-aware feature per topic
(observed intensity over the state's parameter) that grades above coin flip on its typed
ruler.

**Work.**
- DONE: the library (1,157 state rules, 216 plan rules, 6 states on the first cycle),
  the screens' policy bars and rule text, the records lanes (rosters, fee schedules,
  audits, plan policies), the loaders' gates.
- RUNNING: the remaining policy prompts (uncapped) and the plan prompts.
- BUILDING (F): policy-aware features per NPI from the library and the code-family
  intensities, with the export integration written out.
- NEXT: wire the features into the export (partition "policy"), grade them in v29; the
  twelve topics for the ten largest states plus the Southeast on the next cycle; a
  per-state read of the refused rows (269 short quotes) with the analyst.

## 6. The architecture audit

**Outcome that counts:** docs/ARCHITECTURE_AUDIT_2026-11.md (DONE) with every defect
either fixed, built as an arm, or scheduled with its experiment; the ones fixed today in
batch 4 and 5a, the ones building now as batch 5b, batch 6 scheduled (segment models,
the solo book, the field ruler, the org key, drug spread, hospice codes, the Medicare
weights). The audit is re-run after v29 on the same four slices to confirm the fixes
and find what they exposed.

## 7. Reliable lead lists for investigators

**Outcome that counts:** picks to survivors per round above one in two, with every
packet carrying the theory in one sentence, the WHEN sheet, the roles, the court-record
answer and the release scope of any prior settlement; a field outcome recorded for every
card worked.

**Work.**
- DONE: seven theory screens, three rounds (labs 20 to 9, day cares 4 of 4, transport 3
  of 3), nineteen handoffs, eight field outcomes recorded.
- NEXT: the next two screens (worthless services, hospice ineligibility) once builder C's
  per-CCN inputs land; the state rosters for the per-diem card one state at a time; the
  solo book (E) as a fourth source of picks; every returned card into field_outcomes.csv
  the same week.
- NEEDS: Bergen's weekday count at the door; the NJ DOH licence files; the transport
  yard days; the Morrison visit.

## 8. Decisions the operator owns

1. v29 waits for batch 5b (recommended) or runs on batch 4 and 5a alone.
2. The per-case label mass ships in v29 only if its arm beats the current weighting on
   the operator book at 100 and 250; otherwise it stays an arm.
3. The field-outcome ruler needs one row per visited card, dated, the same week.
4. The OMIG audit pages are left out of the browser-proof lane (findings never train);
   say so if they should be read anyway.
