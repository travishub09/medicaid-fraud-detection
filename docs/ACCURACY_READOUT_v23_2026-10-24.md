# Accuracy read-out: frozen_2021-12_v23 (graded 2026-10-24; the re-grade finished 2026-10-24 20:10 UTC)

_v21's matrix re-scored under the floor-only prior encoder (300-row class floor, positives
counted by row), the same label (1,746 hard positives), the same ruler (611 in-universe FCA
positives), the same pursuit order (pmax_basis50); the v21 and weighted encoders as refit
arms graded on the main list and the operator book. No export. Leads for counsel review,
never accusations._

## Verdict: HOLD on two of seventeen, and the cause is a defect in every prior run

| check | bar | v23 | v22 | v21 |
|---|---|---|---|---|
| large_hits50 | >= 2 | 3 (p 0.007) | 1 | 3 |
| large_prec50_lo > large_base50 | | pass (0.021 > 0.009) | fail | pass |
| hits100 (pursuit) | >= 4 | 5 (p 0.000) | 1 | 4 |
| hits250 (pursuit) | >= 6 | 8 (p 0.000) | 1 | 8 |
| lead_ge_gap / inst_share50 | >= 0.5 / <= 0.4 | 1.0 / 0 | pass | pass |
| top50_jaccard | >= 0.6 | **0.240** (8 orgs in every draw, 121 ever) | 0.009 | 0.247 |
| npi_lift1pct | >= 3 | **0.0** | 19.3 | 0.0 |
| eval / horizon / frozen / seeds / groups / broadcast / train / priors / prune | | all pass | all pass | all pass |

Pursuit book, FCA ruler: **1 / 3 / 5 / 8** at 16 / 50 / 100 / 250 (random 0.1 / 0.4 / 0.7
/ 1.8; p 0.110 / 0.004 / 0.000 / 0.000). Large book 2 / 3 / 6 / 6 (p 0.008 / 0.007 /
0.000 / 0.003). Median lead 24 months, every hit at least six months out, institutional
share 0. The hits are v21's eight: LabCorp (rank 5), Cleveland Clinic (22), Community Health
Network (23), Providence (88, 103), Medline (93), Genomic Health (115), BioReference (174).
The NPI grain is empty again: the first 6,137 providers by score hold none of the 611
forward positives (the top 10% holds 52% of them, lift 5.2), exactly v21's shape.

The three encoders therefore sort into two behaviours, not three: floor-only = v21 (good
org books, dead NPI grain, unstable head) and weighted = v22 (NPI lift 19, collapsed org
books). The floor changed nothing measurable. That was the clue.

## The defect: the class-prior encoder leaks the training label

`class_priors.encode_fold` encoded a TRAINING row leave-one-out (its own label subtracted
from its class's count). Inside a class that gives every positive a value exactly one count
below every negative. Gradient-boosted trees read that two-valued signature as the label:
on a synthetic twin (60,000 rows, 30 classes, a weak real signal) the scorer reaches a
training AUC of 1.000 with the leave-one-out prior, and out of fold it does WORSE than no
priors (0.589 against 0.664), its median score falls twenty-fold and its top-1% lift from
5.0 to 1.9. At test time every row carries the fold's full rate, a third value that sits on
the negatives' side of the split, so the model predicts "negative" for nearly everyone and
ranks inside the noise. The shipped lists show it directly: the median raw score of the
v21 and v23 books is 0.0000016 against a label base rate of 0.0028 (v22's median is 0.0026,
a sane classifier's). The weighted encoder leaked too (training AUC 1.000 on the twin) but
expressed it differently, which is why v22 looked like a different model rather than a fix.

Every class-prior result in the series is contaminated: v21, v22, v23, every
`ab_class_priors` / `ab_no_priors` / `ab_prior_*` arm, and the v21 and v22 read-outs'
explanations (the hematology class, "the fix moved the signal to individuals") described
the leak's expression, not the model. The org hits were real only in the sense that the
test-time prior still orders classes by base rate and the settlement classes are hot.

The fix is the textbook one, built the same day (v24 semantics, new pre-registration):
training rows are encoded NESTED, from a deterministic inner five-fold over the training
rows (each slice from the other slices' statistics), so no row's own label reaches its
feature and positives and negatives in a class draw from the same value set. On the twin
the nested prior beats no priors (OOF AUC 0.698 against 0.664, top-1% lift 6.1 against
5.0): the class rate IS signal once encoded honestly. `prior_inner_folds` is training
policy (manifest, export `--prior-inner-folds`, the dossier and diagnostics CLIs, registry
alias `prior_inner`; 0 = the leave-one-out path, kept only as a gradable arm); both
runners ship `PRIOR_INNER=5` and grade `ab_prior_loo_v22/` (the v22 encoder, so the fix is
measured on the same matrix) and `ab_prior_nested_unweighted/`.
`tests/test_prior_encoder_leak_2026_10.py` reproduces the signature and proves it gone.

## The arms (single refits; main list, then the operator book under pmax_basis50)

| arm | encoder refit under | main 16 / 50 / 100 / 250 | NPI lift | operator book 16 / 50 / 100 / 250 |
|---|---|---|---:|---|
| ab_prior_v21 | 0 / unweighted / LOO | 0 / 1 / 2 / 4 | 0.0 | 0 / 2 / 4 / 7 |
| ab_prior_weighted | 300 / weighted / LOO | 1 / 2 / 4 / 4 | 19.3 | 1 / 2 / 2 / 3 |
| ab_no_priors | none | 0 / 0 / 1 / 3 | 8.7 | 0 / 1 / 3 / 4 |
| ab_broadcast_weight | 300 / weighted / LOO (manifest default) | 1 / 2 / 4 / 4 | 19.3 | 1 / 2 / 2 / 3 |
| ab_broadcast_mask | same, mask | 0 / 2 / 2 / 2 | 6.9 | 1 / 2 / 2 / 2 |
| ab_prune | same, 14 schemes out | 1 / 2 / 3 / 3 | 8.3 | 2 / 2 / 2 / 3 |

The broadcast and prune arms refit under the weighted encoder, not the floor-only ship
(bundle 389 closed that gap: every arm now inherits `$PRIOR_FLAGS`). All of them carry the
leak, so none sets a policy. The one clean reading is that the no-priors arm (0 / 1 / 3 / 4
on the operator book) is the honest baseline v24 must beat.

## Other instruments

- Order sweep (pursuit, FCA, 10 pool hits): shipped pmax_basis50 1 / 3 / 5 / 8; plain pmax
  1 / 1 / 7 / 8; dollar 0 / 2 / 6 / 8. The sort stays; re-read on v24.
- Schemes: unchanged from v21 (same matrix): cost_report_fraud 0.90 and hospice 0.83 signal
  (leak_suspect, the institutional selection effect), upcoding inverted 0.41, fourteen
  coin-flips.
- Label ablation under the hard policy: dropping the DOJ cases costs 2.4x of 4.2x lift;
  every exclusion feed within noise; the LEIE alone reads 1.0x.
- Source ablation on the forward ruler: +0.09 ROC, +1.1 lift for the full set.
- Ledger: 6 FAIL (the two checks, the two lab shares at -0.0002, the two leak_suspects),
  104 WARN.
- Registry: v23's row read the encoder as 0 / off because a re-grade copies the source
  manifest; bundle 389 records the effective policy beside the shipped list.

## The dossier

Not dispatched (HOLD; no Manus credits on a HOLD run). The operator-book pick named
Kingman Healthcare, LabOne of Ohio, Ideal Option, Park Nicollet, Sonora Quest, Truetox,
DEGC Enterprises, Spectrum Health Primary Care Partners, Michigan Healthcare Professionals,
HealthTrackRx Indiana: a leak-ranked list, not a target list.

## v24

A re-grade of v21's matrix under the nested encoder (`PRIOR_INNER=5 PRIOR_MIN_N=300
PRIOR_WEIGHTED=1`, order pmax_basis50, train hard), pre-registered under the v23 seventeen
plus `prior_inner >= 5`, `prior_floor = 300`, `prior_weighted = True`. The two prior arms
are the v22 encoder (the leak, same matrix) and nested-unweighted. The grade that matters:
the pursuit book against the no-priors arm's 0 / 1 / 3 / 4, with the NPI grain above 3 and
a Jaccard that is no longer fold noise. If nested priors do not beat no priors on the
operator book, v25 ships without class priors and the enrichment lesson goes back to being
a scheme-design brief.
