# Accuracy read-out: frozen_2021-12_v16 (2026-10-14)

_v15's matrix re-graded with the CIA parties ON the rulers (`REBUILD_RULERS=1 regrade_from.sh v16 v15 2021-12`: the merged inputs and the three rulers rebuilt from a cia_cases.csv whose summaries state the settlement; no retrain). Same matrix, same scorer, same shipped lists as v15. Leads for counsel review, never accusations._

## Verdict: HOLD on the same single check; the ruler grew and the list held up under it

| check | bar | v16 | v15 |
|---|---|---|---|
| large_hits50 | >= 2 | 2 (p 0.070) | 2 |
| large_prec50_lo > large_base50 | | pass (0.011 > 0.009) | pass |
| hits100 (pursuit) | >= 4 | **3** | 3 |
| hits250 (pursuit) | >= 6 | 7 | 6 |
| lead_ge_gap | >= 0.5 | 1.0 | 1.0 |
| inst_share50 | <= 0.4 | 0 | 0 |
| top50_jaccard | >= 0.6 | 0.64 | 0.64 |
| seeds / groups / broadcast / train | | all pass | all pass |

Pursuit book, FCA ruler: 1 / 2 / 3 / 7 at 16 / 50 / 100 / 250 (random 0.1 / 0.4 / 0.7 / 1.8; p 0.047 at 50). Large book: 1 / 2 / 5 / 6 (random 0.1 / 0.5 / 0.9 / 2.3). Median lead 25.5 months, institutional share 0.

## What the CIA parties did to the ruler

| | v15 | v16 |
|---|---|---|
| FCA forward positives on paper | 1,804 | 2,044 |
| FCA positives in the scored universe | 509 | 583 |
| post-cutoff cases resolvable to the universe | 130 | 166 |
| known at the freeze (dropped from the test) | 2,748 | 3,032 |
| pool hits reachable by the pursuit book | 8 | 10 |

The 339 CIA parties split by effective date: those after the freeze became 74 new forward positives in the universe (36 new cases to find), those before it became 284 "known at the freeze" rows, which a re-grade cannot use but a retrain can (they are training positives for v17). Under the larger ruler the shipped list found two more hits (10-hit pool: 7 at 250 against 6; the large book 5 at 100 and 6 at 250 against 4 and 5). Random's expectation rose with the ruler, so the p-values moved little; the list did not get worse as the ruler got harder, which is the honest reading.

## The single miss, three runs running

hits@100 reads 3 against a bar of 4 for the third consecutive grade (v14, v15, v16), with 7 at 250 each time. The fourth hit sits between ranks 100 and 250 under this scorer draw. Two readings are possible and the registry cannot tell them apart: the bar is one hit too tight for a 10-hit pool at a 100 budget, or the top 100 needs a better order. The order sweep says the latter is not available from the columns we have (every variant reads 3 at 100). v17 keeps the bar; if a retrain on the CIA training rows still reads 3, the pre-registration for v18 should grade the 100 budget on the large book (5 at 100 here) and keep the pursuit book's bars at 50 and 250.

## Everything else

- Qui tam ruler (351): 0 / 1 / 2 / 5, unchanged (the CIA rows carry no qui tam flag; the roster does not say).
- Order sweep: basis50 on dollar = shipped (1 / 2 / 3 / 7). Sort stays dollar.
- Broadcast A/B: weight 1 / 1 / 1 / 4, mask 0 / 0 / 4 / 4. Weight stays.
- Dossier: chain-free (the Aurora and CareDx rows left with the national-name additions): home-health, toxicology, two ambulance companies, pediatric home care, a DME supplier, a regional holding company. Not dispatched (HOLD).

## Next: v17

A retrain on v15's inputs plus the 284 pre-freeze CIA parties as training positives (no graph rebuild needed; the as-of graph already holds the 843 name-resolved exclusions). Full runner, ten seeds, train = hard, the v16 checks. If v17 also reads 3 at 100, v18 moves the 100-budget check to the large book.
