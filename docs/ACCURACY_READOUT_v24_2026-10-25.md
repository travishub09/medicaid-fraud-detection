# Accuracy read-out: frozen_2021-12_v24 (graded 2026-10-25; the re-grade finished 2026-10-25 07:57 UTC)

_v21's matrix re-scored under the NESTED class-prior encoder (training rows encoded from an
inner five-fold; floor 300; positives by label budget), the same label (1,746 hard positives),
the same ruler (611 in-universe FCA positives), the same pursuit order (pmax_basis50), ten
seeds. The first honest class-prior run: the v21 .. v23 encoder leaked the training label.
No export. Leads for counsel review, never accusations._

## Verdict: HOLD on one of twenty (top-50 Jaccard 0.555 against 0.6); the model is sound again

| check | bar | v24 | v23 | v19 (last run without priors) |
|---|---|---|---|---|
| large_hits50 | >= 2 | 3 (p 0.007) | 3 | 1 |
| large_prec50_lo > large_base50 | | pass (0.021 > 0.009) | pass | fail |
| hits100 (pursuit) | >= 4 | 4 (p 0.004) | 5 | 2 |
| hits250 (pursuit) | >= 6 | 7 (p 0.000) | 8 | 8 |
| lead_ge_gap / inst_share50 | >= 0.5 / <= 0.4 | 1.0 / 0 | pass | pass |
| top50_jaccard | >= 0.6 | **0.555** (28 orgs in every draw, 82 ever) | 0.240 | 0.618 |
| npi_lift1pct | >= 3 | 6.7 | 0.0 | 7.5 |
| prior_inner >= 5 / prior_floor = 300 / prior_weighted = True | | 5 / 300 / True | 0 / 300 / False | n/a |
| eval / horizon / frozen / seeds / groups / broadcast / train / priors / prune | | all pass | all pass | all pass |

Pursuit book, FCA ruler: **1 / 3 / 4 / 7** at 16 / 50 / 100 / 250 (random 0.1 / 0.4 / 0.7
/ 1.8; p 0.004 at 50, 0.004 at 100, 0.0004 at 250). Large book 1 / 3 / 4 / 6. Median lead 24
months, every hit at least six months out, institutional share 0. The hits: Cleveland Clinic
(rank 6), Community Health Network (24), LabCorp (36), Genomic Health (82), BioReference
(152), Providence (207, 241); Medline fell to 337.

**The score-scale check reads `ok`**: median refit score 0.00048 against a base rate of
0.0028 (ratio 0.17; v21 and v23 read 0.0006). The NPI grain is back (lift 6.7, 63 forward
positives in the top 1%) and the head is stable again: 28 organizations in every one of
the three re-shuffled top-50 sets against 8 in v23. The Jaccard sits where the honest runs
have always sat (v17 0.575, v19 0.618), just under the bar.

## The arms (ten-seed refits on the same matrix; main list, then the operator book under pmax_basis50)

| arm | encoder | main 16 / 50 / 100 / 250 | operator book | NPI lift | score scale |
|---|---|---|---|---:|---|
| ab_prior_loo_v22 | leave-one-out (the leak) | 1 / 2 / 4 / 4 | 1 / 2 / 2 / 3 | 19.3 | 0.014 **degenerate** |
| ab_broadcast_weight (= the shipped policy, refit) | nested, weighted | 0 / 1 / 1 / 3 | 0 / 1 / 3 / 4 | 6.7 | 0.168 ok |
| ab_prior_nested_unweighted | nested, by row | 0 / 0 / 1 / 4 | 0 / 2 / 3 / 5 | 6.0 | 0.168 ok |
| ab_no_priors | none | 0 / 0 / 1 / 3 | 0 / 1 / 3 / 4 | 8.7 | 0.185 ok |
| ab_prune | nested, 14 schemes out | 0 / 1 / 2 / 3 | 0 / 2 / 2 / 5 | 8.3 | 0.168 ok |
| ab_broadcast_mask | nested, mask | 1 / 1 / 1 / 3 | 1 / 1 / 1 / 4 | 5.1 | 0.085 degenerate |

Three readings:

1. **The leak is confirmed on the real matrix.** The v22 encoder refit on the same folds
   reads a score scale of 0.014 (degenerate) and the NPI lift of 19.3 the v22 read-out
   celebrated; the new check would have caught v21 and v22 on their own packets.
2. **Honest class priors add nothing measurable.** The shipped policy refit and the
   no-priors refit read the same operator book (0 / 1 / 3 / 4), the no-priors arm has the
   better NPI grain (8.7 against 6.7), and the main list is within one hit. The nested
   unweighted arm (0 / 2 / 3 / 5) and the prune arm (0 / 2 / 2 / 5) are inside the same
   noise. The v19 enrichment lesson (labs and systems are the hot classes) is already in
   the hits without a prior column; the trees find it from the billing features.
3. **The shipped list and its own refit differ by the fold draw** (1 / 3 / 4 / 7 against
   0 / 1 / 3 / 4 under the same policy and seeds), which is what the Jaccard measures.
   Two hits at 50 and one at 100 ride on which organizations land in the top 50 under a
   given draw; the deep budget (250) is steady at 7.

The broadcast-mask arm reads degenerate at 0.085: holding the 654 broadcast positives out
of training lowers the whole score distribution. Mask stays a graded arm, never the ship.

## Other instruments

- Order sweep (pursuit, FCA, 10 pool hits): shipped pmax_basis50 1 / 3 / 4 / 7; the
  dollar and score_max orders with the same basis block read identically; plain pmax 0 / 2 /
  2 / 7. The sort stays.
- Label ablation under the hard policy: dropping the DOJ cases costs 2.4x of 4.2x lift;
  every exclusion feed within noise (unchanged: same matrix as v21).
- Schemes unchanged (same matrix): cost_report_fraud 0.90 and hospice 0.83 signal
  (leak_suspect, the institutional selection effect), upcoding inverted, fourteen coin-flips.
- Ledger: 5 FAIL (the Jaccard check, the two lab shares at -0.0002, the two leak_suspects),
  104 WARN. The `npi_lift1pct` FAIL of v23 is resolved.
- The class-prior rate table was not written this run: the diagnostics imported the old
  argument builder after the v24 refactor and the guarded block swallowed the NameError.
  Fixed (bundle 391) with a test; the registry row is unaffected.
- Registry: the encoder keys read from the shipped list's own policy record (bundle 389),
  so `prior_inner >= 5` / `prior_floor = 300` / `prior_weighted = True` passed on their
  first use.

## The dossier

Not dispatched (HOLD; no Manus credits on a HOLD run). The operator-book pick named JWCH
Institute, Ideal Option, Kingman Healthcare, Truetox, TeamSelect Home Care of Colorado,
Pathways, LabOne of Ohio, HealthTrackRx Indiana, North East Medical Services, Rediscover.
JWCH Institute and North East Medical Services are FQHCs (a `health center` cue belongs on
the institutional list before the next pick).

## v25: the same matrix without class priors

The decision rule written before v24 was: if nested priors do not beat no priors on the
operator book, v25 ships without class priors. They did not. v25 = a re-grade of v21's
matrix with `CLASS_PRIORS=''` (the regrade runner now takes the override on every ship and
arm line), ten seeds, pmax_basis50, train hard; the nested priors become the graded
counterfactual (`ab_class_priors/`), the prior-encoder arms are off (`PRIOR_ARMS=''`), the
prune and broadcast arms stay. Pre-registered under the twenty with `priors = none`. The
question v25 answers is the one v24 could not: does the simpler model hold 3 / 4 / 7 at
50 / 100 / 250 with a head stable enough to pass 0.6 (v19, the last no-prior run, read
0.618). If it does, it is the first PROMOTE candidate of the series and the dossier
dispatch becomes the operator's call.
