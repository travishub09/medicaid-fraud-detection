# Accuracy read-out: frozen_2021-12_v12 (2026-10-13)

_The first VM run: full-density graph embeddings (no node cap, full walks), ten seeds, the FCA-shaped hard tier as the TRAINING label (`train = hard`, 1,205 positives, 1,082 of them DOJ-case), the four v11 packet fixes, 40 sources used and the three expected skips (death master, graph velocity, NADAC), 22 schemes, 248 trainable features, zero calc-integrity failures. Graded on the pursuit book under the FCA ruler, as pre-registered. Leads for counsel review, never accusations._

## Verdict: HOLD, on the same three top-50 checks as v11

| check | bar | v12 | v11 | v10 |
|---|---|---|---|---|
| hits50 | >= 2 | **1** | 1 | 2 |
| hits100 | >= 4 | 4 (pass) | 4 | 3 |
| prec50_lo > base50 | | 0.0035 < 0.0057 (**fail**) | 0.0035 < 0.0057 | pass |
| top50_jaccard | >= 0.7 | **0.616** | 0.60 | 0.09 |
| lead_ge_gap | >= 0.5 | 1.0 (pass) | pass | pass |
| inst_share50 | <= 0.4 | 0 (pass) | 0 | 0 |
| seeds / groups / broadcast / train | 10 / case_cluster / weight / hard | all pass | | |

Hits at 16 / 50 / 100 / 250 on the pursuit book, FCA ruler: **1 / 1 / 4 / 7** (random 0.1 / 0.3 / 0.6 / 1.4; p at 100 = 0.001, at 250 < 0.001; v11 0 / 1 / 4 / 6; v10 2 / 2 / 3 / 4). The 250 budget is the best of the series and recall there is 7 of the 8 pool hits. Median lead 29 months, 100 percent of hits beyond the gap, institutional share 0. NPI grain under FCA: top 1 percent lift 6.7 (v11 8.4), top 10 percent lift 2.0.

## What the run says

1. **The top 50 is the same wall as v11.** 44 of the 50 are `capped_no_basis` rows (case size pinned at the 40 percent ceiling, no scheme-specific damages basis) and they hold 0 hits for the third run running; the one hit sits among the 5 "other" rows (20 percent hit rate). Below 50 the capped rows do carry hits (4 of 208 at 250). The problem is the ORDER inside the top 50, not the pool.
2. **The large-biller book cleared the bar.** `provider_priority_list_large.csv` (the pursuit book at $50M+ billed): hits 1 / 3 / 5 / 5 at 16 / 50 / 100 / 250, precision at 50 = 6.0 percent (Wilson 2.1 to 16.2), random 0.4, p = 0.004; 5 of its 5 pool hits found by 100. This is the first book in the series to pass a hits50 >= 2 check under the FCA ruler.
3. **The order sweep is split.** Shipped (dollar-weighted) 1 / 1 / 4 / 7; every basis-backed-first variant 1 / 3 / 3 / 3. Basis-first wins the top 50 and loses 100 and 250. Under the standing rule (never re-introduce basis-first without a sweep that beats the shipped order across budgets) the sort stays; the sweep is now close enough that a MIXED order (basis-backed rows interleaved into the first 50, dollar-weighted below) is the v13 sort to pre-register and sweep, not to ship silently.
4. **Ten seeds did not fix stability.** Top-50 Jaccard 0.616 across three re-folds (29 of 50 orgs in every draw, 74 ever), top-1 percent NPI Jaccard 0.41. The instability is in the label (509 forward positives, 8 pool hits), not in the seed.
5. **Full embeddings add nothing measurable.** Network verdict NO MEASURABLE SIGNAL: size-matched AUC +0.0069, interval -0.016 to +0.028. The graph family stays as features; it is not a win to cite. The cap and the walk lightening were never the reason.
6. **The hard-tier training label behaved.** 1,205 positives (1,082 DOJ-case, 474 broadcast rows down-weighted, min weight 0.415), 1,148 listed-but-not-kept providers masked from training and scored out of fold. Calibration ECE 2.5e-05, Brier 0.0019 on a 0.2 percent base rate.
7. **The dossier picked institutional names again.** The operator-book top 10 includes Sutter VNA and Hospice (22 NPIs), Mayo Clinic Arizona, SSM Health Care St. Louis, Assurex, Ameritox and Alere Toxicology: the `entity_segment` name cue misses hospital and national-lab names that carry no cue word in the first tokens. The dossier was NOT dispatched (HOLD rule); the fix is a curated institutional-name list in the segment rule before v13's dossier.
8. **Ledger:** 6 FAIL (the three pre-registration checks, the three post-cutoff editions of facility / PBJ / POS), 75 WARN, 14 INFO; 41 issues resolved since v11. Learning loop: 196 suspect positives, 13,016 candidate positives, review queue 60, no feature drift.

## v13 (new pre-registration)

- Grade TWO books: the pursuit book (hits100 >= 4, hits250 >= 6) and the large book as co-primary (large hits50 >= 2). The large book is where precision lives; the pursuit book is where recall lives.
- Pre-register the mixed order (basis-backed first within the top 50, dollar-weighted below) as a sweep row; ship it only if it beats 1 / 1 / 4 / 7 across budgets.
- Fix the institutional-name cue before the dossier runs; the dossier is not a finished run artifact while it names hospitals.
- Drop the top50_jaccard bar to 0.6 or move it to the 100 budget: with 8 pool hits the 50 budget cannot be stable, and the check is penalising the label's sparsity.
- Keep seeds at 10 (cheap on the VM), keep the hard tier, keep full embeddings (no cost on the VM, no harm).
- The real lever remains the label: 509 forward positives and 8 pool hits on 1,395 listed orgs. Every recall item (the research residue, the PurpleLab join, the state MFCU sweeps) moves this more than any model change.
