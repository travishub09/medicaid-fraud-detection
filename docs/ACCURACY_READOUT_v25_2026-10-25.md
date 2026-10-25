# Accuracy read-out: frozen_2021-12_v25 (graded 2026-10-25; the re-grade finished 2026-10-25 20:48 UTC)

_v21's matrix re-scored WITHOUT class priors (the v24 arms: honest priors add nothing), the
same label (1,746 hard positives), the same ruler (611 in-universe FCA positives), the same
pursuit order (pmax_basis50), ten seeds. No export. Leads for counsel review, never
accusations._

## Verdict: HOLD on one of twenty (pursuit hits@100 = 3 against 4); the Jaccard passes for the first time since v19

| check | bar | v25 | v24 | v19 |
|---|---|---|---|---|
| large_hits50 | >= 2 | 2 (p 0.070) | 3 | 1 |
| large_prec50_lo > large_base50 | | pass (0.011 > 0.009) | pass | fail |
| hits100 (pursuit) | >= 4 | **3** (p 0.030) | 4 | 2 |
| hits250 (pursuit) | >= 6 | 6 (p 0.004) | 7 | 8 |
| top50_jaccard | >= 0.6 | 0.622 (31 orgs in every draw, 72 ever) | 0.555 | 0.618 |
| npi_lift1pct | >= 3 | 8.7 | 6.7 | 7.5 |
| score_scale | >= 0.1 | 0.185 ok | 0.168 | n/a |
| lead_ge_gap / inst_share50 / priors = none / the rest | | all pass | | |

Pursuit book, FCA ruler: **2 / 2 / 3 / 6** at 16 / 50 / 100 / 250 (random 0.1 / 0.4 / 0.7 /
1.8; p 0.047 at 50, 0.030 at 100, 0.004 at 250). Large book 2 / 2 / 2 / 6. Median lead 22
months, every hit at least six months out, institutional share 0. The hits: Cleveland
Clinic (rank 3), LabCorp (11), Genomic Health (83), Providence (197, 204), BioReference
(232); Community Health Network and Medline fell outside 250.

## The series has converged

| run | priors | pursuit 16 / 50 / 100 / 250 | Jaccard | NPI lift | failing check |
|---|---|---|---|---|---|
| v17 | none | 1 / 2 / 5 / 7 | 0.575 | 9.0 | Jaccard |
| v19 | none | 1 / 2 / 2 / 8 | 0.618 | 7.5 | hits100, large50 |
| v24 | nested | 1 / 3 / 4 / 7 | 0.555 | 6.7 | Jaccard |
| v25 | none | 2 / 2 / 3 / 6 | 0.622 | 8.7 | hits100 |

Four honest runs, one model: two to three hits at 50, two to five at 100, six to eight at
250, a head that keeps 28 to 31 of its 50 across re-shuffles, NPI lift 7 to 9, lead 22 to
26 months. Each run misses a different check by one hit or a few hundredths, and the
arms inside each run (same construction) read within one hit of each other. The checks
at the head of the book are now measuring the fold draw, not the model, and another
re-grade of v21's matrix cannot change that. The hits are the same organizations every
time: the national labs and health systems that settled qui tam cases, which the trees
find from the billing features with or without a prior column.

## The arms (refits on the same matrix; main list, then the operator book)

| arm | main 16 / 50 / 100 / 250 | operator book | NPI lift | score scale |
|---|---|---|---|---|
| ab_broadcast_weight (= the shipped policy, refit) | 0 / 0 / 1 / 3 | 0 / 1 / 3 / 4 | 8.7 | 0.185 ok |
| ab_class_priors (nested priors) | 0 / 1 / 1 / 3 | 0 / 1 / 3 / 4 | 6.7 | 0.168 ok |
| ab_prior_nested_unweighted | 0 / 0 / 1 / 4 | 0 / 2 / 3 / 5 | 6.1 | 0.168 ok |
| ab_prune (14 schemes out) | 0 / 1 / 2 / 3 | 0 / 2 / 3 / 5 | 8.2 | 0.184 ok |
| ab_broadcast_mask | 1 / 1 / 1 / 3 | 1 / 1 / 1 / 4 | 6.7 | 0.093 degenerate |
| ab_prior_loo_v22 (the leak) | 1 / 2 / 4 / 4 | 1 / 2 / 2 / 3 | 19.3 | 0.014 degenerate |

Priors and no priors read the same book again (0 / 1 / 3 / 4). The two encoder arms ran
although `PRIOR_ARMS=''` was set: the runner read an empty value as unset (fixed, bundle
392); with no priors shipped they were no-op refits.

**A measurement defect found and fixed.** Every arm sat one to two hits below the shipped
list at 16 and 50 in both v24 and v25, under the same policy and seeds. The diagnostics'
refit assigned folds with plain GroupKFold and did not hold the unassessable rows out of
training, while the shipped scorer uses stratified group folds and does. The refit now
uses the scorer's own fold function (repeat 0 reproduces the shipped scores exactly; a
stability repeat re-draws the same design under a seed), so from v26 the "shipped policy"
arm IS the ship and the Jaccard measures the shipped design. Arm-against-arm readings in
v24 and v25 stand (same construction on both sides).

## Other instruments

- Order sweep (pursuit, FCA, 10 pool hits): shipped pmax_basis50 2 / 2 / 3 / 6; the
  dollar order with the basis block 2 / 3 / 4 / 7 (it would have passed hits100 on this
  draw; v17 and v19 read the other way). A sort moves only on a sweep that wins across
  budgets on two matrices; it stays.
- Dollar recall at 50: 1% of the eligible settlement dollars (two hits, $9.7M), 4% at 250.
- Schemes, label ablation, source ablation: unchanged (same matrix).
- Ledger: 5 FAIL (the hits100 check, the two lab shares at -0.0002, the two leak_suspects),
  103 WARN.

## The dossier: the operator's decision

Not dispatched. The standing rule is no Manus credits on a HOLD run, and v25 is a HOLD.
The other reading is that v25 is the first list in the series that is honest, stable and
passes nineteen of twenty, with the miss a single hit at the noise floor; on that reading
it is the operational baseline list regardless of the checks, and the dossier (ten
research prompts, Kingman Healthcare and Sonora Quest now set aside as institutional
names, the rest home-health / addiction / toxicology / infusion operators) is worth its
credits. That is a judgment about the rule, not about the model, and it belongs to the
operator. The tasks stay open until the operator says dispatch or skip.

## v26: replication on the second freeze, not another re-grade

The lever the matrix cannot supply is a second, independent ruler. The second freeze
(2023-12, horizon 33 months, built in bundle 345 and never run) grades the same policy on
cases announced 2024 to 2026 against a matrix frozen two years later, with the October
inputs (the 2021 and 2023 editions, 17 state lists, the loaded research lanes) and the
fixed refit. If the pursuit book reads two to three hits at 50 again on a freeze that
shares no forward positives with this one, the model is real in the only sense that
matters; the FREEZE_PAIR report puts the two side by side. v26 = `ops/vm/run_frozen_night.sh
v26 2023-12` (HORIZON=33, no class priors, pmax_basis50, train hard), pre-registered under
the twenty with `priors = none`, the data scan run first on the idle VM. The label lanes'
dispatch (gated) is the other lever, and it feeds both freezes.
