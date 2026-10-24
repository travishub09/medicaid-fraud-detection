# Accuracy read-out: frozen_2021-12_v22 (graded 2026-10-24; the re-grade finished 2026-10-24 06:07 UTC)

_v21's matrix re-scored under the corrected class priors (positives counted by the per-case
label budget; a class under 300 training rows takes the global rate), the same label (1,746
hard positives), the same ruler (611 in-universe FCA positives), the same pursuit order
(pmax_basis50); no export. Leads for counsel review, never accusations._

## Verdict: HOLD on five of seventeen checks, and the lesson is bigger than the hold

| check | bar | v22 | v21 |
|---|---|---|---|
| large_hits50 | >= 2 | **1** | 3 |
| large_prec50_lo > large_base50 | | **fail** | pass |
| hits100 (pursuit) | >= 4 | **1** | 4 |
| hits250 (pursuit) | >= 6 | **1** | 8 |
| top50_jaccard | >= 0.6 | **0.009** (0 orgs in every draw, 195 ever) | 0.247 |
| npi_lift1pct | >= 3 | 19.3 (the best of the series by a factor of two) | 0.0 |
| lead_ge_gap / inst_share50 / eval / horizon / frozen / seeds / groups / broadcast / train / priors / prune | | all pass | all pass |

Pursuit book, FCA ruler: **0 / 1 / 1 / 1** at 16 / 50 / 100 / 250 (random 0.1 / 0.4 / 0.7
/ 1.8). Large book 0 / 1 / 1 / 4. NPI top-1% lift 19.3 (precision 1.9%, 41 of the 132
physician forward positives inside the top 1%, lift 31 within that family; behavioral
lift 33). Main list (the hard arm, same policy, single seed): 1 / 2 / 4 / 4, better than
v21's main list (0 / 1 / 2 / 4) with institutional share 0.76.

## What happened: the fix moved the signal from organizations to individuals

The v21 read-out diagnosed the class priors twice: a 199-provider hematology class at a
24.6% training rate (one broadcast settlement) emptied the NPI-grain head, and per-fold
encoding of thin classes re-shuffled the top 50. The v22 encoder cured both: the NPI
grain is the best ever and the main list improved. But the pursuit book, the graded
deliverable, collapsed, for two reasons the packet makes plain:

1. **The head of the raw score is now home-care and HCBS agencies.** The pursuit book's
   first thirty are personal-care, home-health, group-home and behavioral operators with
   raw member scores of 0.57 to 0.69, no FCA case among them. v21's head was national
   labs and health systems enrolled under one or two NPIs (LabCorp at rank 2, Cleveland
   Clinic 15, Community Health Network 16, Genomic Health 87, Providence 107 and 131,
   Medline 118, BioReference 194: eight hits). In v22 those same organizations rank 45,
   1,035, 324, 710, 455, 523 and 1,136. The FCA ruler is settlements, and settlements
   are labs and systems; v21's prior-dominated scorer ranked that class up, v22's
   feature-dominated scorer ranks owner-run agencies up. The ruler did not move; the
   model's idea of what fraud looks like did.
2. **The calibrated probability has a 296-way plateau.** `score_p_max` reads 0.010853
   for 296 of 1,390 pursuit-book orgs (the top 20% of the book), because the cross-fit
   isotonic calibration found no monotone gain in training-label rate across the top
   of the raw score and pooled it into one step. The pmax order is then decided by the
   raw score inside the plateau, which differs between fold draws: Jaccard 0.009, zero
   organizations common to the three draws.

The priors themselves barely moved for the classes that matter: clinical laboratories
1.16% to 1.09%, hospitals 2.15% to 1.97%. What moved was the rest of the table (590 of
775 classes fell under the 300-row floor and took the global rate), which took away
the handful of extreme-rate thin classes v21's trees keyed on, and the model re-learned
the label from billing features instead.

## The arms (single refits, main list)

| arm | 16 / 50 / 100 / 250 | NPI lift | read |
|---|---|---:|---|
| weight (= shipped policy) | 1 / 2 / 4 / 4 | 19.3 | main list better than v21 (0 / 1 / 2 / 4) |
| no priors | 0 / 0 / 1 / 3 | 8.7 | identical to v21's arm (same matrix, as expected) |
| prune | 1 / 2 / 3 / 3 | 8.3 | no |
| broadcast mask | 0 / 2 / 2 / 2 | 6.9 | no |

No arm graded the pursuit book, which is where the ship is decided. That is the
instrumentation gap this read-out closes.

## Built from this read-out (v23 semantics)

- **The prior encoder is training policy.** `prior_min_n` (the class floor; 0 = the v21
  encoder) and `prior_weighted` (positives by label budget; off = the v21 encoder) in the
  manifest, the export (`--prior-min-n`, `--prior-weighted`), the scorer, the dossier and
  the diagnostics CLIs (`company_list.prior_encoder_args` is the one place all three read
  it), the registry (`prior_floor`, `prior_weighted`). The three encoders (v21 = 0 /
  off, floor = 300 / off, weighted = 300 / on) are refit arms in both runners
  (`ab_prior_<name>/`), the shipped one skipped.
- **Every refit arm grades the operator book.** `accuracy_diagnostics.operator_book_from_refit`
  takes the refit's rollup (now carrying calibrated probabilities), applies the shared
  `pursuit_dossier.entity_segments` rule and the shipped pursuit order, and writes
  `acc_pursuit_hits16/50/100/250` beside the main-list KPIs (registry aliases
  `refit_pursuit_hits*`). An arm that cannot grade the pursuit book cannot pick a policy.
- `entity_segments` is one function shared by the list builder and the diagnostics.

## v23

A re-grade of v21's matrix with the floor-only encoder shipped (PRIOR_MIN_N=300,
PRIOR_WEIGHTED=0: the guess is that the floor cured the thin-class blow-up while the
weights are what re-learned the label away from the settlement classes), with the v21
and weighted encoders as arms graded on both the main list and the operator book.
Pre-registered under the seventeen checks. If floor-only restores the pursuit head
while keeping the NPI grain, it ships; if the arms say otherwise, v24 ships the arm.
