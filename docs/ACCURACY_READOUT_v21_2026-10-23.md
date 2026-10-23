# Accuracy read-out: frozen_2021-12_v21 (graded 2026-10-23; the run finished 2026-10-23 00:51 UTC)

_The first run with class priors shipped (taxonomy + entity base rates, target-encoded
inside each fold), the lab_testing scheme, batch 2 + batch 3 of the platform audit, the
saturation-leak fix, the post-v19 inputs (2021 HCRIS, the saturation archive, 17 state
lists, the CIA and outcome-refresh loads), ten seeds, train = hard (1,746 positives,
1,596 DOJ-case), 42 sources used, 23 schemes scored, 2 calc FAILs (two lab shares at
-0.0002). The pursuit book shipped under `pmax_basis50`. Leads for counsel review, never
accusations._

## Verdict: HOLD on one check (15 of 16 pass), with two defects the check caught

| check | bar | v21 | v19 |
|---|---|---|---|
| large_hits50 | >= 2 | 3 (p 0.007) | 1 |
| large_prec50_lo > large_base50 | | pass (0.0206 > 0.0091) | fail |
| hits100 (pursuit) | >= 4 | 4 (p 0.004) | 2 |
| hits250 (pursuit) | >= 6 | 8 (p 0.000) | 8 |
| lead_ge_gap | >= 0.5 | 1.0 | 1.0 |
| inst_share50 | <= 0.4 | 0 | 0 |
| top50_jaccard | >= 0.6 | **0.247** (9 orgs in every top 50, 121 ever) | 0.618 |
| priors = taxonomy,entity / prune = none | | pass | n/a |
| seeds / groups / broadcast / train / frozen / horizon / policy | | all pass | all pass |

Pursuit book, FCA ruler (611 in-universe positives, 10 reachable by the book): **3 / 3 / 4 / 8**
at 16 / 50 / 100 / 250 (random 0.1 / 0.4 / 0.7 / 1.8; p 0.000 / 0.004 / 0.004 / 0.000). The
best 16 and 50 of the series (v10 had 2 / 2; v19 under the same pmax_basis50 order read
1 / 3 / 4 / 8). Large book **3 / 3 / 5 / 6** (random 0.2 / 0.5 / 0.9 / 2.3). Qui tam ruler
2 / 2 / 3 / 7; affiliated ruler 3 / 3 / 4 / 9. Median lead 24 months, every hit at least
six months out, institutional share 0, zero `capped_no_basis` rows in the top 50 (the
basis-first block doing its job). Dollar recall at 50: 21% of the eligible settlement
dollars. The hits: LabCorp (rank 2), Cleveland Clinic (15), Community Health Network (16),
Genomic Health (87), Providence (107, 131), Medline (118), BioReference (194): the chain
and large-biller class where the model's precision has always lived.

## The two defects the run exposed (both from the class priors)

**1. The NPI-grain ranking collapsed to zero.** NPI top-1% lift 0.0 (v19 7.5, v17 9.0):
the first 6,000 providers by score hold none of the 611 forward positives, where chance
alone gives six. The no-priors arm on the same matrix reads 8.7. The cause is in
`class_prior_rates__pursuit.csv`: the taxonomy prior gave a 199-NPI hematology class a
24.6% training rate (49 positives, one broadcast settlement; lift 70), DME customised
equipment 3.7% on 1,256, skilled nursing facilities 3.1% on 4,552. The scorer took the
prior as its strongest feature and filled the head of the NPI list with those classes,
which carry training positives (broadcast rows) and no forward FCA cases. Within a
family the ranking still works (physicians: 36 of 132 forward positives in the top 1%,
lift 27); across families the prior decided the order. The org books survived because
the pursuit order is `pmax` with the operator filter, and the labs and systems the
prior also lifted are where the FCA cases are.

**2. The head is unstable.** Jaccard 0.247 against the 0.6 bar (v19 0.618). The prior
is encoded per fold, so a thin class with a huge rate swings between fold draws and
the top 50 re-shuffles with it (9 orgs in every draw, 121 ever); precision at 50 was
3 in every draw, so the hits are robust while the names around them are not.

Both have one fix, built the same day (v22 semantics): the prior counts positives
by the per-case label budget (`label_weight`, so forty-nine NPIs of one settlement
count as that case's budget) and a class with fewer than 300 training rows takes
the global rate (`class_priors.MIN_CLASS_N`); the rate table records
`weighted_positives` and `below_min_n`. v22 adds the check that would have caught
this on its own: `npi_lift1pct >= 3`.

## The arms (single-seed refits on the MAIN list, 1,957 orgs, institutional share 0.5 to 0.6)

| arm | 16 / 50 / 100 / 250 | NPI lift | read |
|---|---|---:|---|
| hard, priors, weight (= shipped policy) | 0 / 1 / 2 / 4 | 0.0 | the baseline |
| no priors | 0 / 0 / 1 / 3 | 8.7 | priors +1 / +1 / +1 on orgs, and the NPI grain intact |
| prune (the 14 coin-flip schemes held out) | 0 / 1 / 1 / 7 | 0.2 | mixed: -1 at 100, +3 at 250; prune stays none |
| hard + charged | 0 / 1 / 3 / 5 | 4.3 | +1 / +1 over hard (v15 read identical); re-run on v22 |
| hard + commercial | 0 / 1 / 2 / 4 | 0.0 | = hard |
| broadcast mask | 0 / 1 / 3 / 7 | 0.0 | +1 / +3 over weight this run (v19: weight won); noise floor |

Single refits move by one or two hits between draws; none of these sets a policy.

## The order sweep (pursuit book, FCA)

| order | 16 | 50 | 100 | 250 |
|---|--:|--:|--:|--:|
| score_p_max, basis-backed first in the top 50 (shipped) | 3 | 3 | 4 | 8 |
| score_p_max | 1 | 3 | 6 | 8 |
| score_dollar_weighted, basis-backed first in the top 50 | 1 | 3 | 5 | 8 |
| score_dollar_weighted | 0 | 2 | 6 | 8 |
| score_p_any | 0 | 0 | 0 | 8 |

The shipped order wins the 16 and 50 budgets and loses 100 to the plain pmax (4 vs 6):
the basis-first block promotes the dossier's named-damages rows at the cost of two hits
between 50 and 100. The sort stays `pmax_basis50`; the 100 bar passed.

## Schemes (SCHEME_HEALTH, the FCA ruler)

Signal: cost_report_fraud 0.90 (51 positives), hospice_ineligibility 0.83 (49),
specialty_mismatch 0.65 (901), worthless_services 0.65 (49), contract_pharmacy 0.61 (715).
Coin-flip on both labels (the prune candidates): dme_ring, drug_outlier, behavioral_health,
facility_code_billing, impossible_day, lab_testing (0.52 on 314 in its first run),
overutilization, payment_outlier, pharma_kickback, pill_mill, rapid_ramp,
rendering_identity, saturation_fraud, single_service_mill; upcoding inverted (0.41 on 53).
Two `leak_suspect` FAILs: cost_report_fraud (0.90 forward vs 0.43 on the exclusion label)
and hospice_ineligibility (0.83 vs 0.65). Both read as the hospice selection effect of
v19 (only facilities with a cost report or a hospice file score; the FCA ruler is 82%
organizations and the settlements are institutional) rather than a vintage leak: the
vintage ledger shows every HCRIS, hospice, PBJ, POS and nursing-home pick `pre_cutoff`,
and the source ablation's per-group delta for hcris is +0.10 [-0.20, +0.42]. The
feature-drift FAILs on the saturation / hospice / worthless subscores are the archive
file and the 2021 editions landing (a code change, not a data defect).

## Label and sources

Label ablation under the hard policy: dropping the DOJ cases costs 2.4x of 4.2x lift
(the DOJ label is the signal); LEIE, enrollment, revocations and state terminations
each within noise. Source ablation: the extra files win on ROC-AUC (+0.09) and
top-decile lift (+1.1); hrsa_340b, saturation and the Medicare PUFs pull the most.
Network family: no measurable signal (+0.01 AUC, CI spans 0), as in every run since v12.
Four sources skipped as before (death master, graph velocity, NADAC, order-and-referring).

## The dossier

Not dispatched (HOLD, no Manus credits). The operator-book pick named Kingman
Healthcare, LabOne of Ohio, Ideal Option, Sonora Quest, SVMC Holdings, Owensboro
Health, Truetox, Group Health Plan, Park Nicollet, D&S Residential: hospitals and
systems enrolled under one billing NPI with no cue word. Fixed: an organization with a
hospital-licensed member (NUCC 27xxxx / 28xxxx) is institutional for the pick and a
`hospital_licensed` flag on the list, never a filter on the graded book; "HEALTH PLAN"
joined the cue words.

## v22

A re-grade of v21's matrix (`ops/vm/regrade_from.sh v22 v21 2021-12`, PURSUIT_ORDER
pmax_basis50): the re-score runs the weighted, size-floored prior encoder on the same
policy, the no-priors counterfactual is graded beside it, the charged arm re-runs.
Pre-registered under the v21 sixteen plus `npi_lift1pct >= 3`. If the NPI grain and
the Jaccard come back with the org books holding at 3 / 3 / 4 / 8, v22 is the first
PROMOTE of the series.
