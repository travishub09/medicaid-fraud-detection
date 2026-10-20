# Accuracy read-out: frozen_2021-12_v17 (graded 2026-10-20; the run finished 2026-10-14 18:14)

_The retrain on the expanded label: v15's inputs plus the 284 pre-freeze CIA parties as training positives (training label 1,545, 1,396 of them DOJ-case; v15 trained on 1,258), the as-of graph reused, ten seeds, train = hard, 40 sources used, 22 schemes, 0 calc FAILs. Run on the freeze-point code (before the 2026-10-14 build-out). Leads for counsel review, never accusations._

## Verdict: HOLD on one check (top-50 stability); hits@100 passes for the first time

| check | bar | v17 | v16 |
|---|---|---|---|
| large_hits50 | >= 2 | **4** (p 0.000) | 2 |
| large_prec50_lo > large_base50 | | pass (0.032 > 0.009) | pass |
| hits100 (pursuit) | >= 4 | **5** (p 0.000) | 3 |
| hits250 (pursuit) | >= 6 | 7 | 7 |
| lead_ge_gap | >= 0.5 | 1.0 | 1.0 |
| inst_share50 | <= 0.4 | 0 | 0 |
| top50_jaccard | >= 0.6 | **0.575** | 0.64 |
| seeds / groups / broadcast / train | | all pass | all pass |

Pursuit book, FCA ruler (580 in-universe positives, 10 reachable by the book): 1 / 2 / 5 / 7 at 16 / 50 / 100 / 250 (random 0.1 / 0.4 / 0.7 / 1.8; p 0.109 / 0.047 / 0.000 / 0.000). Large book: 1 / 4 / 4 / 6 (random 0.1 / 0.5 / 0.9 / 2.3; p 0.000 at 50, the best 50 of the series). Median lead 25.5 months, every hit at least 6 months out, institutional share 0. NPI top-1% lift 9.0 (v16 6.7; the best yet). Qui tam ruler 1 / 1 / 3 / 5 (p 0.001 at 250).

The HOLD is the stability check alone: the top-50 Jaccard across three fold draws is 0.575 against a bar of 0.6 (27 orgs in every draw, 75 ever). The retrain moved the head of the list; precision at 50 did not move (SD 0.00). The three-run miss on hits@100 (v14 3, v15 3, v16 3) is over: 5 at 100 with the CIA training rows in.

## What the CIA training rows did

The label grew from 1,258 to 1,545 training positives and the DOJ-case share from 1,109 to 1,396. The list found two more cases at 100 and two more at 50 on the large book without losing any at 250. The qui tam ruler read the same as v16. The charged-tier A/B (hard+charged, 0 charged positives in this matrix) and the broadcast A/B (weight 2/2 at 50 and 100; mask 2/2) are identical to the hard arm: neither policy moves the grade, so both stay as they are.

## The order sweep: the first sweep a probability order wins

| order (pursuit book, FCA) | 16 | 50 | 100 | 250 |
|---|--:|--:|--:|--:|
| score_dollar_weighted (shipped) | 1 | 2 | 5 | 7 |
| score_p_max | 1 | 3 | 4 | 9 |
| score_p_max, basis-backed first in the top 50 | **2** | **3** | **5** | **9** |
| score_p_any | 0 | 0 | 0 | 7 |

Since v7 the rule has been: never re-introduce a probability sort without a sweep that beats the shipped order across budgets. This is the first one that does: p_max with basis-backed rows first in the top 50 reads 2 / 3 / 5 / 9, at or above the shipped 1 / 2 / 5 / 7 at every budget. One sweep is not a ship: v18 writes that order as a THIRD graded book (`provider_priority_list_pmax.csv`, the same operator pool re-sorted) beside the pursuit and large books, and v19 moves the shipped sort only if the pmax book holds on a second matrix.

## Two things the packet shows that need fixing before v18

1. **The operator-book dossier picked sub-$1M billers.** Targets 1, 2, 6, 7, 8 and 10 billed $250k to $1.2M in the window (Royal Palms Post Acute, Sharon Care Center, Heritage Manor, Norwalk Skilled Nursing, Downey Community Care, Ensign San Dimas: California nursing homes with cap-pinned case sizes of $100k to $500k a year), while the pursuit book itself is filtered at a ~$10M-a-year case-size bar. The operator-book pick in `build()` reads a wider pool than the pursuit book. Fix: the dossier's targets come from the pursuit book's rows only. The v17 dossier was NOT dispatched (HOLD policy).
2. **The vintage FAILs** (facility / PBJ / POS 2023 editions inside the 2021-12 freeze) are on the ledger for the ninth run; bundle 324's edition picker and the `pre_cutoff_editions_2021` download close them in v18.

## Everything else

- Learning loop: 223 suspect positives (14% of the training label; the largest by dollars are hospital and national home-nursing settlements broadcast to member NPIs with ordinary billing), 767 of 881 forward positives outside the top 1%, 0 feature drift vs v16.
- Scheme health: 11 DEGRADED on missing inputs (the five v3 scores among them; the as-of v3 scores in v18 restore four of them), 0 BROKEN, 0 inverted. Case validation unchanged: worthless_services and single_service_mill validate on DOJ cases.
- Network family: no measurable signal (+0.0035 AUC, CI spans 0), as in v12 to v16.

## Next: v18

v18 = the first run on the 2026-10-14 build-out (as-of v3 scores, the pre-cutoff edition picker, the measurement block, the research-lane rows in the label after the free re-load, `--rebuild-graph`), pre-registered under the v17 checks with hits100 >= 4 kept (it passed) and top50_jaccard >= 0.6 kept (the one open check), plus the pmax book graded alongside. The dossier target fix ships in the same bundle.
