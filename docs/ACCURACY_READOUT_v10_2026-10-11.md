# Accuracy read-out: frozen_2021-12_v10 (the first valid run of the series)

Prepared by Claude for Trey Rawles, 2026-10-11, from the v10 run packet (ACCURACY_DIAGNOSTICS on the pursuit book and the large-biller book, the two broadcast arms, EXPECTATIONS, SOURCES, LEARNING_LOOP, RUN_ISSUES, CASE_VALIDATION, the feature manifest). Plain version first, then the numbers, then what to do. A hit is a listed organization that a later public enforcement record touched; it is not a finding about anyone. Leads for counsel review, never accusations.

## 0. Plain version

v10 is the run v8 and v9 were meant to be. The matrix carries 6,366 providers labelled from DOJ cases, 1,644 training positives, and the 617,062 row billing universe. For the first time in this series the operator book beats chance at every depth a person reads: 2 hits in the first 16 and the first 50 (random would give 0.1 and 0.3), 3 in the first 100, 4 in the first 250. The one-sided p-values are 0.002, 0.024, 0.010 and 0.022. Both hits in the top 50 were at least 24 months ahead of the public record. The institutional share of the top 50 is zero.

The registry says HOLD. Two locked checks read as failed, and only one of them is real:

1. **Top-50 stability (Jaccard 0.09 against a bar of 0.7) is a real failure.** Re-shuffling the training folds changes nearly the whole top 50, even though every re-shuffle still finds 2 hits in it. The cause is visible in the list: below rank 1, the dollar-weighted scores sit at 0.2000 to four decimals for about twenty organizations, so the order among them is decided by noise in the fifth decimal. The hits are robust; the names are not.
2. **The lower Wilson bound above random (0.011 against 0.005) actually passed.** The registry's check parser compared the metric to the literal word "base50" instead of the run's own random baseline and reported "needs numbers". The same false FAIL sat on v8 and v9. It is fixed in bundle 272; re-registering v10 clears it and leaves HOLD on the stability check alone.

Five sources died of memory during the export: the servicing grain, MUE, the sector schemes, the enrollment features and the billing LM. So impossible_day, nemt_fraud, behavioral_health and rendering_identity were not scored, and the run is thinner than designed. The enrollment adapter failed on a 386 MB allocation, which says the machine had almost no free memory at that moment. v11 must run with those sources back, which means a quiet machine, and the runner should check free memory before it starts.

Verdict for the release registry: HOLD, on top-50 stability. The signal is real and the list is the first one worth researching; the ordering inside the plateau is not yet trustworthy.

## 1. The numbers

Universe 617,062 billing NPIs; 1,644 training positives (exclusions plus DOJ cases); 81 case clusters merged into fold groups; 474 broadcast case positives down-weighted (minimum weight 0.415). FCA ruler positives in the universe after dropping everything known at the freeze: 508.

Pursuit book (1,397 operator organizations; 7 of them carry an FCA hit in the 36-month window):

| budget | hits | random expected | one-sided p | Wilson 95% | dollar-ranked hits |
|--:|--:|--:|--:|---|--:|
| 16 | 2 | 0.1 | 0.002 | 3.5% to 36% | 0 |
| 50 | 2 | 0.3 | 0.024 | 1.1% to 13.5% | 1 |
| 100 | 3 | 0.5 | 0.010 | 1.0% to 8.5% | 1 |
| 250 | 4 | 1.3 | 0.022 | 0.6% to 4.0% | 3 |

Lead time of the top-50 hits: median 26 months, both at least 24 months out. Settlement-dollar recall: 3 percent at 50, 26 percent at 100, 28 percent at 250. The large-biller book reads the same 2 at 16 and 50 because every pursuit organization already clears the $10 million floor.

Provider ranking (refit of the shipped scorer, out of fold, FCA ruler): top 1 percent lift 2.2 (11 hits, 5.1 expected), top 10 percent lift 3.3 (169 hits, 51 expected), no hit in the first 50 providers. Segments with signal: billers over $10 million (lift 11 shrunk), behavioral (12), crna (49 on two positives), physical and occupational therapy (50 on two positives), Texas (6), Ohio (17). Segments with none: dentists, nurse practitioners, New York, Illinois, Florida, Pennsylvania, North Carolina.

Calibration at the top: mean calibrated probability 0.030 in ranks 1 to 50 against an observed 0.0 under the ruler's event definition, so the probabilities are still about three times too confident at the head; the FDR-bounded list size is 0.

Broadcast A/B (single-seed refits, not the shipped scorer): weight 0 hits at 50, mask 1. Too noisy to decide the v11 policy; the shipped five-seed weight policy is what read 2.

Locked checks: hits50 >= 2 passed; prec50_lo > base50 passed in fact and failed by the parser bug; lead_ge_gap >= 0.5 passed (1.0); inst_share50 <= 0.4 passed (0.0); top50_jaccard >= 0.7 failed (0.09); eval_policy, horizon, frozen, seeds, groups, broadcast all passed.

Run ledger: 22 FAIL, 115 WARN, 14 INFO, 28 issues resolved since v9. Learning loop: 630 suspect positives (38 percent of positives, most of them DOJ broadcast rows at institutional billers with ordinary billing), 1,492 candidate positives, drift FAIL on 5 features against v9 (the universe change), review queue of 60 written. Case validation: worthless_services (AUC 0.96) and single_service_mill (0.63) validated against proven DOJ cases; pharma_kickback and hospice_ineligibility partial.

## 2. What was fixed in the code (bundle 272)

| defect | fix | where |
|---|---|---|
| a check whose right-hand side is another metric was judged against the literal word | the parser resolves the right-hand side through the same aliases as the left (`value_from` recorded); re-register v10 to clear it | `run_registry.eval_check` |
| the dossier's default top 10 came from the main list: five hospitals and a national DME chain | `--operator-book` picks targets from the operator book only (no hospital / system / academic names, no 25+ provider organizations, no government, no solo individuals) | `pursuit_dossier.build` |

## 3. What v11 changes

1. The memory failures. Run on a quiet machine (no browser, no other Python), and the runner gains a free-memory check before the export. Servicing, MUE, sector schemes, enrollment and the billing LM must all read "used" in the SOURCES_REPORT or the run is not v11.
2. The plateau. The dollar-weighted score below rank 1 is a 0.2000 plateau. The fix is a continuous tiebreak inside the plateau (the calibrated member probability, which the sweep already showed does not lose hits at 50), graded by the order sweep before it ships, never by argument.
3. The locked checks stay as they are, with the parser fixed. Jaccard 0.7 is the bar the plateau fix has to clear.

## 4. Operator steps

In order: re-register v10 (minutes, clears the false FAIL); merge bundle 272; rebuild the v10 dossier with the operator book and dispatch it (the first list in this series worth the research spend); poll 30 minutes later; then the fuzzy step D batch; then v11 after the memory and plateau fixes land.

_Leads for counsel review, never accusations. A hit in this document is a later public record touching a listed organization, not a finding about it._
