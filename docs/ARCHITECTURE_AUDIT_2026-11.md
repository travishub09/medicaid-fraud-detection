# Architecture audit, 2026-11-02

The operator asked for a full audit of the model (label, features, schemes, evaluation,
graph) with the defects ranked and the fixes named, to answer one question: why does a
model that finds institutional cases at ranks 100 to 250 sit at chance inside the
owner-run class, and what would move it. Four independent reads of the code were run in
parallel (the scorer and label chain; the feature factory and peer engine; every scheme
input's calculation against the catalog; the evaluation and the graph rollup). Every
finding below was confirmed in the code by file and line; the ones marked VERIFIED were
re-read by hand after the reports came in. The read-outs of v7 to v28 and
docs/SCHEME_DEEP_DIVE_2026-10-22.md are the empirical side of the same picture.

## 1. The verdict in one paragraph

The system is not broken in one place; it is biased toward institutions in five places
that compound. The label's training mass is proportional to the size of the settled
organization (a hundred-member lab settlement is a hundred positives, a convicted solo
operator is one). The ruler that grades the list broadcasts a settlement to every member
of an organization on the current-day graph and counts each member's org as an independent
hit. Three levels of max-over-members rollups (CCN to NPI, NPI to org, and the dossier's
score_max) hand a chain the expected value of the largest of its percentiles. The rollup
to the operator book is sorted by statistics that grow with the member count. And every
billing feature is a within-taxonomy percentile, so the class base rate the cases
actually concentrate in (clinical laboratories, lift 26) is invisible to the trees. On
the other side, the owner-run class is diluted twice (its reassigning clinicians count
against its fan-out budget but never train), its most likely forward cases are removed
from both the list and the grade because the 'all' label decides who is "known", its
calibration is learned with weak-tier events as negatives, and several of its scheme
inputs are either zero-where-null (scoring "nothing happened" at the median) or wrong in
grain or denominator. Fifteen versions produced the same result because the head checks
measure fold noise on one to three hits while these mechanics held constant.

## 2. The structural read (the four facts that explain fifteen versions)

1. **The label is institutional by construction.** 82 percent of forward positives are
   organizations; 959 of 1,082 DOJ training positives come by org-billing broadcast
   (hospital and lab settlements); 83 percent of convicted individuals never bill under
   their own NPI and so never join. Training mass follows organization size (defect A1).
2. **The grain hides the operator.** Features live at billing-NPI grain on aggregate
   provider x code x month spending; the owner-run shapes (a clinic billing under one
   number for many renderers, an aide agency, a day program) are visible only through the
   servicing summary and the capacity cards, and the operator book excludes solo
   individual NPIs by rule.
3. **Normalization erases the base rate and the size dimension.** Every adapter metric is
   ranked inside taxonomy x entity x state; the peer cell has no size axis, so a one-NPI
   agency and a five-thousand-NPI system are peers on dollar intensities, while the class
   rate itself cannot be expressed by any percentile.
4. **The ruler cannot measure the owner-run class.** One to eight hits per budget, each
   clustered by case and counted as independent, graded on a book that removes most
   settling organizations and on checks that sit inside one hit of their bar. Only the
   250 budget carries information, and the case-clustering makes even that optimistic.

## 3. Defects, ranked (severity, effect on the owner-run class, status)

Status: FIXED = in the batch committed today (v29 semantics, new pre-registration);
NEXT = batch 5, built this week; EXPERIMENT = graded as an arm before any default moves.

| # | where | the defect | effect on owner-run | status |
|---|---|---|---|---|
| A1 VERIFIED | case_labels 505-512 | label_weight = min(1, 100 / fan-out): a settled org's training mass grows with its size up to 100 | the solo conviction is a rounding error in the loss | NEXT: weight per CASE (1 / fan-out, or a cap of 3 to 5), graded as an arm |
| A2 VERIFIED | case_labels 505 + export 468 | fan-out counts affiliated_individual rows that never flip the label | a small practice with many reassigning clinicians is diluted twice | NEXT: fan-out over the bases that train |
| A3 VERIFIED | prospective_label 206, 227 | the forward ruler keeps affiliated_individual rows and fills a missing conduct start with the cutoff; training does neither | phantom positives on clinicians' own solo orgs; the two sides disagree on what a positive is | NEXT: one rule for both |
| D1e VERIFIED | accuracy_diagnostics 773-830 | the top-50 Jaccard is computed over ALL orgs by dollar-weighted mean, not the shipped book | v17, v21, v24, v26 held on a number orthogonal to the graded object | NEXT: Jaccard of the operator book's top 50 per repeat |
| D2e VERIFIED | run_registry 515-518 | string '=' in a check was a prefix test: 'train = hard' passed on hard+charged | a wrong policy could pass pre-registration | FIXED |
| D3e | accuracy_diagnostics 418-443 | hits are case-clustered (one release, twenty labs) but graded as independent draws | p-values at 100 / 250 anti-conservative | NEXT: cases-touched@k as the headline count, hypergeometric over case clusters |
| D6e | run_frozen_night.sh 285 | the FCA ruler is resolved on the CURRENT-DAY graph: a settlement lands on today's roster of a merged or renamed org | hits widen on large merged orgs; a mild forward leak | NEXT: resolve on the as-of graph's npi_to_org |
| F1 | facility 536, peer_percentiles 95 | pre-ranked facility percentiles are rolled up CCN to NPI to org by MAX; the max of k uniforms has expectation k/(k+1) | a fifty-home chain reads 0.98 on every facility scheme by construction; a single home keeps its true rank | NEXT: map each NPI to its own CCN's percentile |
| F2 / C2 VERIFIED | export 223-234; pos.capacity_billing_mismatch uncalled | capacity_mismatch = the whole org's patient-instances over the LARGEST facility's beds, then percentiled by each member's taxonomy | worthless_services (weight 0.6) scores system size | NEXT: per CCN, facility peers; call the existing function |
| A4 VERIFIED | company_list 495-507 | calibrate_oof treats weak-tier and unassessable rows as negatives, with no weights; score_p_max (the pmax book, every refit arm's book) is built on it | the P scale flattens exactly where small Medicaid providers sit (the 296-way plateau) | NEXT: same masks and weights as training; Platt or rank-average at 0.3 percent prevalence |
| A5 VERIFIED | export 105-131 | mcd_ended_for_cause / mcd_paid_after_end trainable while the same statuses feed the label; rx_excluded_prescriber_share likewise | "state termination flag = fraud" learned on administrative events | FIXED (fenced) |
| A6 VERIFIED | case_labels 167 | a difflib 0.92 fuzzy defendant-to-org pass survived at both call sites after the resolver's fuzzy tier was retired on verification | generic small-operator names collide into phantom positives | FIXED (off) |
| A7 VERIFIED | case_labels 40-52 | any four-digit year in a release became the conduct start, so the frozen conduct gate was near-vacuous | un-findable positives in the pool deflate every budget | FIXED (stated spans only) |
| A8 | company_list 676-682; diagnostics 214-218 | rollup drop_excluded and eval_frame 'known' use the 'all' label while training uses 'hard' | a provider with a 2019 reprimand who settles in 2024 is removed from the list AND the grade | NEXT: both read the tier label; 'known' = a public outcome, not an allegation |
| C1 VERIFIED | hrsa_340b 189-191 | contract_pharmacy_concentration was 0, not NULL, for every org absent from OPAIS; raw-driven | the whole universe carried one constant subscore | FIXED (NULL) |
| C3 VERIFIED | sector_schemes 253-254 | em_prolonged numerator held inpatient and non-face-to-face codes over an office denominator (469x, capped); 90785 measured against a base missing the codes it attaches to | session_inflation measured a code-set mismatch | FIXED |
| F5 / C8 | mue 162, order_referring 82, deactivation 217, billing_specialty 106 | zero-where-no-event shares and a 0/1 flag percentiled: every zero reads about 0.49, the subscore 0.48 for "nothing happened" | impossible_day (weight 0.8 on mue) is a tie block | FIXED (raw-driven) |
| F6 | export 588-591, 746 | the percentile of a pre-ranked percentile shipped as a trainable column, re-ranked once per member | a 500-NPI chain defines its taxonomy cell's median | FIXED (pre-ranked and raw inputs no longer re-ranked) |
| C4 | sector_schemes 165-190, servicing_grain 94 | "patient-month" denominators are SUM(patients) over code x month rows (a patient counted once per code) | lines-per-patient under-reads; lab_lines_per_patient cannot measure breadth | NEXT: NPI x month patients in the SQL base |
| C5 | nppes_deactivation 184-219, export 1289 | billing_after_deactivation is the deactivated NPI's own post/pre share, documented as the org's, broadcast to every member | every member of a system with one retiree still billing lights up | NEXT: per-NPI only (deact_paid_after exists); org flag for single-NPI orgs |
| C6 | medicaid_ndc 305-323, drug_markup 52-88 | drug spread compares paid per LINE to a NATIONAL median; state fee schedules and fill size are the variance | drug_outlier measures geography | NEXT: within state, by days_supply |
| C7 | facility 101-116, 303-324 | hospice panel measures are regex-matched and averaged across whichever rows match | hospice_ineligibility reads a mixture | NEXT: lock each to its measure code |
| C9 | partb, partd, dmepos 64, openpayments 206 | Medicare individual-grain files score schemes graded on an organization ruler; DME supplier scheme fed by a referrer-grain file; no-payment NPIs 0 not NULL | the class-C depletion the deep dive found | NEXT: weights to 0 on org-ruled runs; supplier PUF |
| C10 | hcris 126-144 | a HIGH cost-to-charge ratio read as fraud for every facility type | sign depends on cost-based vs PPS payment | NEXT: sign per payment basis |
| F4 | consistency 53-69 | flags fire on taxonomy base rate (pooled entity types, no minimum cell) and one fact counted twice; tenure is billing-derived on every path | 10 percent of every individual taxonomy flagged by construction | NEXT: rank within taxonomy x entity with min_peer; enrollment tenure |
| F7 | expected_billing 44, 123 | the "digital twin" controls (tenure, active months) are from the same fact as the target; rank(pct) not mid-rank | the residual is partly the target | NEXT: enrollment tenure as the control |
| F8 | peers 45-49 | the peer ladder has no size dimension; complexity_adjust exists and is never called | a one-NPI agency and a system are peers on dollar intensities | NEXT: size band in the ladder for intensity metrics |
| G1 | resolve_entities 83, 103-105, 147-150 | one legal entity with several PECOS enrollments is several orgs; the shared-owner merge depends on the lexicographic minimum owner key; a PE holding owner merges whole stacks; aliases carry every member's name so a settlement lands on the stack | institutions merge up and clear the $10M bar; small operators split | NEXT: owner-component merge with a stable key; case size per facility |
| G2 | company_list 699-716 | score_max and score_p_max are order statistics that grow with member count; dollar-weighted is the largest biller | the pmax order re-introduces a size statistic | EXPERIMENT: exceedance over the order-statistic null at the roster size |
| D5e | accuracy_diagnostics 131-152, 502 | hits are positional, not tie-aware; basis50 books hide ties (score = minus rank) | a plateau at ranks 2 to 50 read as a fact | NEXT: gate on tie-aware hits when ties cross the boundary |
| W1-W11 | scheme_subscores | one input in two schemes with one sign; opposite constructs pointed the same way (nh_owner_count vs concentration); a $20 lunch = 1.0 (op_payment_concentration); pharmacy and prescriber populations mixed under one scheme name; the "never fed" list fed | the weight table disagrees with its own contract | NEXT: a weight-table lint test and the listed corrections |

## 4. What was correct (so nobody re-audits it)

The freeze instant is month-start on all four sides (graph, training label, forward ruler,
case label). The diagnostics' refit reproduces the shipped scorer's folds, weights, masks
and column screen (repeat 0 is pinned by test). The nested class-prior encoder no longer
leaks a row's own label. The exclusion-proximity features (fraud proximity, two-hop
counts, ownership_integrity, excluded owners) are fenced from the shipped scorer. The v3
concept scores, clinical_implausibility, growth and burst, ownership_turnover, the lab mix
shares, the facility penalty and deficiency inputs, the hospice live-discharge rate, the
servicing identity statistics and the Medicaid kickback features compute what the catalog
says they compute.

## 5. The experiments that settle the open questions (each one arm on one matrix)

1. **Per-case label mass.** Train with label_weight = 1 / fan-out (one outcome, one unit)
   against the current min(1, 100 / fan-out); grade the operator book. If hits at 100 and
   250 hold, the institutional lift was label geometry. (A1, S5)
2. **Cases as the unit of the grade.** Re-grade v25 and v27 counting cases touched at
   each budget, with the hypergeometric over case clusters and the size-matched baseline
   as the bar. This is the honest version of every number the series has reported. (D3e)
3. **Stability of the shipped book.** Run the Jaccard through the operator book per
   repeat. Expect it to move materially; v17 and v24 may have been passes, v21 a fail for
   the right reason. (D1e)
4. **Per-CCN facility inputs.** Replace the three max rollups with each NPI's own CCN
   percentile and the per-CCN capacity ratio; grade worthless_services and
   hospice_ineligibility on their typed rulers. (F1, F2)
5. **Segment models.** One scorer trained on operator-class rows only (the entity_segments
   rule) beside the global scorer, graded on the pursuit book; and a fourth book of solo
   individual NPIs above the case-size bar graded on the state ruler. (S8, S2)
6. **A held-out field-outcome ruler.** Field outcomes recorded after a list was built,
   never trained on, as the one ruler that sees the owner-run class at the rate the
   business cares about; it needs a date-of-record gate and the training exclusion that
   already exists. (the evaluation read, section 2)
7. **Employer resolution for every state and MFCU positive**, not only hard-tier
   exclusions, dated by the individual's event, so the state ruler reaches the billing
   organizations. (reach)
8. **Rollup by mean, dollar-weighted mean and top-2 mean** on v27's OOF scores, graded on
   the pursuit book, against score_max and score_p_max. (G2)

## 6. The order of work

- **Batch 4 (today, committed with tests, v29 semantics):** exact string checks in the
  registry; the case-label fuzzy pass off; conduct windows from stated periods only; the
  enrollment-end and exclusion-derived prescriber columns fenced; contract-pharmacy NULL
  for non-covered entities; office-only prolonged codes and the full interactive base;
  mue / ineligible-referral / taxonomy-mismatch raw-driven; pre-ranked inputs no longer
  re-ranked.
- **Batch 5 (this week, before v29 is pre-registered):** per-case label mass as an arm,
  fan-out over training bases, one conduct rule on both sides, the ruler on the as-of
  graph, Jaccard on the book, cases-touched@k, tie-aware hits, calibrate_oof with the
  training masks and weights, the tier label in drop_excluded and 'known', per-CCN
  facility inputs and capacity, the patient-month base, billing_after_deactivation per
  NPI, the weight-table lint.
- **Batch 6 (structural, each an arm):** segment models, the solo book, the held-out field
  ruler, the owner-component org key, drug spread within state, the hospice measure
  codes, the Medicare individual-grain weights.
- **The operator decides:** whether v29 waits for batch 5 (my recommendation: yes; a run
  on batch 4 alone measures the same geometry), and whether the field-outcome ruler is
  worth the records discipline it needs (one row per visited card, dated).

## 7. What this does not fix

Aggregate data cannot see a kickback, a medical-necessity theory or unbundling on one
claim. The screens work because they encode a theory and a count a visit can decide; the
model's demonstrated value is institutional case prediction at ranks 100 to 250 under a
ruler that will stay thin until the state, MFCU, docket and field labels reach the
billing organizations. The claims-level join (PurpleLab) is the only change that moves the
grain; everything above moves the geometry.
