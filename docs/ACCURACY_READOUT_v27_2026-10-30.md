# Accuracy read-out: frozen_2023-12_v27 (graded 2026-10-30; the run finished 2026-10-30 01:06 UTC)

_The second freeze again: the December-2023 export rebuilt with the hospice-edition fix
(bundle 395) and, in intent, the December-2023 All-Owners editions on the as-of graph, under
v26's policy (no class priors, nested encoder knobs recorded, pmax_basis50, train hard: 2,470
positives, 2,170 DOJ-case; ten seeds; 44 sources, 26 schemes, 0 calc FAILs), graded on the
33-month FCA ruler (273 positives in the universe). Leads for counsel review, never
accusations._

## Verdict: all seventeen pre-registered checks PASS; the registry still prints HOLD on two standing mechanical gates

| check | bar | v27 (2023-12) | v26 (2023-12) | v25 (2021-12) |
|---|---|---|---|---|
| hits100 (pursuit) | >= 2 | 3 (p 0.005) | 5 | 3 |
| hits250 (pursuit) | >= 4 | 6 (p 0.000) | 6 | 6 |
| top50_jaccard | >= 0.6 | **0.616** (28 orgs in every draw, 74 ever) | 0.588 | 0.622 |
| npi_lift1pct | >= 3 | 23.4 (the best of the series) | 15.0 | 8.7 |
| score_scale | >= 0.1 | 0.219 ok | 0.215 | 0.185 |
| lead_ge_gap | >= 0.5 | 0.50 (at the bar, as v26) | 0.50 | 1.0 |
| inst_share50 | <= 0.4 | 0.00 | 0.00 | 0.00 |
| eval_policy = fca / horizon = 33 / frozen / seeds >= 10 / groups = case_cluster / broadcast = weight / train = hard / priors = none / prune = none / prior_inner >= 5 | | all pass (manifest: seeds 10, case_cluster, weight, priors [], prune [], prior_inner_folds 5) | | |

Pursuit book, FCA ruler: **1 / 2 / 3 / 6** at 16 / 50 / 100 / 250 (random 0.1 / 0.2 / 0.4 /
0.9; p 0.059 / 0.014 / 0.005 / 0.000). Large book 1 / 3 / 3 / 6 (p 0.002 at 50). The pmax
book reads the pursuit book exactly (the shipped order IS pmax_basis50). Precision at 50 lower
bound 0.011 against a base of 0.0038. The hits, in pursuit order: Precision Toxicology (rank 1),
Community Health Network (21), Cleveland Clinic (51), Dana-Farber (148), Tactile Systems (153),
Savida Health (200). The same six as v26; two of them moved from inside 100 to 148 and 153,
which is the fold draw the Jaccard describes (v26 had them at 87 and 100).

**The registry's HOLD.** `run_registry.recommend` applies four mechanical gates before the
pre-registered checks: zero expectation FAILs (pass), a KEEP network verdict (v27 reads NO
MEASURABLE SIGNAL, +0.0012 AUC with a CI spanning zero, as every run since the embeddings
were measured null in v12), an ablation lift CI clear of zero (the top-decile lift delta is
+0.29 [-0.14, +0.69], and the registration step runs BEFORE the measurement step that writes
it), and the lift-delta drop against a champion (none in the series). The seventeen checks
the pre-registration locked are the enforced bar the operator set; the two mechanical gates
are the registry's own 2026-09 design and no run of the series can pass them. The operator's
call, written as a rule: either the next pre-registration carries `network = KEEP` explicitly
(and the series stays HOLD until the graph family earns it) or the two gates become advisory
rows on the report (a code change registered as such, never a silent swap). Until then the
read-out counts the pre-registered checks, as every read-out since v10 has.

**The freeze pair** (FREEZE_PAIR_v25_v27.md, FREEZE_PAIR_v26_v27.md): eight of eight
comparable checks hold on both pairs; every source has the same status on v26 and v27.

## What v27 was for, and what it did not do

- **The hospice edition fix worked.** `subscore_hospice_ineligibility` is back: coverage
  0.5% of the universe (81% of hospices), label AUC 0.691, FCA AUC 0.57 on 16 forward
  positives (thin). v26 had it all-NaN.
- **The owner editions did NOT land.** The run's stated purpose, ownership_turnover at the
  second freeze, failed on one line in the log: `[ownership_churn] vintage diff failed (No
  columns to parse from file); trying snapshots`. That is pandas' error for an EMPTY csv:
  one of the files under preclean/owners_prior/ (the December-2023 editions Josh pulled, or
  an archived copy) is 0 bytes or header-less, the exception was caught around the whole
  block, and the chain fell to the snapshot path with one snapshot. `ownership_integrity`
  still lists ownership_turnover as missing (RUN_ISSUES row 32, eighteen runs). FIXED in
  code (bundle after v27): `owners_vintage.is_empty_edition` skips a 0-byte or header-less
  edition BY NAME, `load_editions(skipped=)` collects the names, and the export logs
  "[ownership_churn] N edition file(s) set aside: <names>" before the diff, so the chain
  continues on the editions that exist. The operator lists owners_prior/ on the VM to name
  the file and re-downloads it; v28 is the first run that can read the owner layer at
  2023-12.
- **The two add-on shares and the lab component share** stayed inside [0, 1] (0 calc FAILs;
  v26 had 5).
- The ledger: 0 FAIL, 103 WARN, 26 INFO. The WARNs are the standing tie-flooded flags and
  the three skipped sources (death master, graph velocity, NADAC claim slice).

## The arms (refits on the same matrix; main list, then the operator book)

| arm | main 16 / 50 / 100 / 250 | operator book | NPI lift | score scale |
|---|---|---|---|---|
| ab_broadcast_weight (= the shipped policy, refit) | 2 / 2 / 2 / 6 | 2 / 2 / 3 / 6 | 23.4 | 0.219 ok |
| ab_class_priors (nested priors) | 2 / 2 / 2 / 8 | 2 / 3 / 5 / 6 | 19.8 | 0.196 ok |
| ab_tier_charged | 2 / 2 / 3 / 4 | 1 / 1 / 2 / 6 | 21.2 | 0.218 ok |
| ab_tier_commercial | 1 / 1 / 1 / 5 | 1 / 1 / 4 / 6 | 16.5 | 0.219 ok |
| ab_broadcast_mask | 1 / 1 / 1 / 4 | 1 / 2 / 3 / 4 | 8.1 | 0.114 ok |

The shipped-policy refit reads the shipped book (2 / 2 / 3 / 6 against 1 / 2 / 3 / 6). The
nested class priors beat it on this draw at 50 and 100 (3 and 5 against 2 and 3) and tie at
250, with a weaker NPI grain; on v26's draw the same arm tied the book, and on 2021-12 (v24)
it added nothing. One freeze's single refit does not move the policy: priors stay none, and
the priors arm keeps running. Charged and commercial trade the deep budgets for the NPI
grain, as before. Mask stays an arm.

## Scheme health on the FCA ruler (26 schemes)

Signal 3 (impossible_day 0.605 on 530 positives, specialty_mismatch 0.623 on 548,
lab_testing 0.588 on 134), coin-flip 16, thin 7. lab_testing is the first of the 2026-10-22
schemes to grade with enough positives; lab_unbundling is coin-flip on the same labs (0.477
on 132: the component share is not the theory, which the kill round also said). The 2023-12
freeze has 530+ forward positives on the broad schemes, so the class-C question (individuals
against organizations) reads: impossible_day 0.73 on 137 individuals against 0.565 on 393
organizations.

## The dossier: the pick needs the list refresh before anyone reads it

Not dispatched (gated). The v27 operator-book pick as written: Integrated Home Care Services
(a Florida MCO home-health benefit manager), Venice Family Clinic (an FQHC), Habit OPCO (an
Acadia Healthcare OTP brand), Simple Laboratories (IL), Empire City Laboratories (NY), Tandem
Diabetes Care (national; on the list since the 2026-10-29 edit, which the VM pulled AFTER the
run), Mini Pharmacy Enterprises (CA), Family Counseling Center (MO, nine NPIs), Michigan
Healthcare Professionals PC ($85M under one NPI: the group-practice billing convention
before it is a card), Spero Health of Kentucky (a multi-state addiction chain). Venice Family
Clinic, Habit OPCO, Spero Health and Integrated Home Care Services join `_NATIONAL_NAMES`;
414 organizations public since the freeze and 47 with a standing verdict were set aside
correctly. The two labs (Simple, Empire City) are the only rows a research credit should see,
and they go through the definitive-reflex code-mix line (org_find --codes) first, by the
2026-10-29 rule. Re-render the dossier on the VM with the pulled code (free) before any
dispatch decision.

## Next

- Name and re-download the empty owner edition; v28 = the 2023-12 export with the owner
  layer (the same seventeen; the registry gates decided by the operator first).
- The registry-gate rule above is the one decision this read-out asks for.
- The 2020-12 freeze (48-month horizon) stays the clean replication pair.
