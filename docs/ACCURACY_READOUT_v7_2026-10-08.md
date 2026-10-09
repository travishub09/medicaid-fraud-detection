# Accuracy read-out: frozen_2021-12_v7 (the first business-outcome grading)

Prepared by Claude for Trey Rawles, 2026-10-08, from ACCURACY_DIAGNOSTICS.md and accuracy_diagnostics.json in the v7 run folder (the shipped priority list, FCA ruler with a 36-month horizon, refit NPI tables, no repeats). Plain version first, then the numbers. A hit is a listed organization or provider that a later public enforcement record touched; it is not a finding about anyone. Leads for counsel review, never accusations.

## 0. Plain version

The v7 list, graded on what the business actually wants (an FCA case or criminal health-care-fraud conviction becoming public within three years of the freeze), is close to chance at every depth a person reads. The first 50 organizations held 1 hit where a random draw from the same pool would hold about 0.2. The first 250 held 3 where random would hold 1. Those are better than chance but not by enough to call: the one-sided p-values are 0.18 and 0.07.

The reason is not that the model has no signal. It does: among individual providers, the top 1 percent by refit score holds 11 future FCA providers where 2.5 were expected, and among the largest billers (over $10 million a year) the top 1 percent hits at 4.6 percent, sixteen times the base rate. The reason is what the list does with that signal:

1. The very top of the provider ranking is a tie. The first 285 providers share one score, and the first 1,000 contain zero future cases. The useful part of the ranking sits between ranks 1,000 and 6,000.
2. The top 50 organizations are made of the two classes least likely to produce a case: 24 are hospitals and health systems (the institutional book) and 24 are organizations whose case size sits at the cap or under $250,000 in billing. Zero are operator-run organizations of the kind the whistleblower strategy targets. The institutional 24 produced no hits; the one hit came from the tiny-dollars 24.
3. Under the exclusion rulers (the ones every earlier run was graded on), the organization list cannot be graded at all: the $10 million damages bar removes nearly every provider who later gets excluded, so the pool of 2,741 listed organizations holds at most one with an exclusion hit. The forward lifts reported for v6 and v7 described providers who never reached the list.
4. The label still misses most of the money. Of the settlement dollars announced after the freeze, $1.4 billion attaches to a scored provider and $199.6 billion does not. Of the gradable $1.4 billion, the top 100 organizations touched 32 percent.
5. The calibrated probabilities at the top are about three times too confident, and no organization reaches a 20 percent false-discovery bound, so a probability-based cut would return an empty list.

Verdict for the release registry: HOLD. v7 is not a promotable lead list on the FCA ruler. The pre-registered v8 checks (two hits in the top 50, lower interval above the random baseline, institutional share at most 40 percent) are each failed by v7 as it stands, which is the right starting line for v8.

## 1. The numbers

Universe (617,062 matrix rows; everything known at the freeze dropped first):

| ruler | positives in universe | still billing within 12 months of the freeze | ambiguous negatives |
|---|--:|--:|--:|
| all exclusions | 302 | 41% | 886 |
| hard plus weak exclusions | 817 | 49% | 238 |
| FCA cases (headline) | 254 | 72% | 1,055 |

The FCA positives are the most visible at the freeze (72 percent still billing), which means the FCA ruler is gradable from frozen features; the exclusion rulers are less so.

Organization list (2,741 organizations after the government and $10 million filters), FCA ruler:

| budget | hits | random expected | one-sided p | Wilson 95% | dollar-ranked hits |
|--:|--:|--:|--:|---|--:|
| 16 | 0 | 0.1 | 1.00 | 0% to 19% | 0 |
| 50 | 1 | 0.2 | 0.18 | 0.4% to 10.5% | 0 |
| 100 | 2 | 0.4 | 0.06 | 0.6% to 7.0% | 1 |
| 250 | 3 | 1.0 | 0.07 | 0.4% to 3.5% | 1 |

Only 11 of the 2,741 listed organizations have any FCA hit in the 36-month window (recall at 250 is 3 of 11). Under the two exclusion rulers the list has 0 hits at every budget and the pool holds at most 1 organization with a hit.

What the top of the list is made of (same for all three rulers, since the list is the same):

| depth | institutional | tiny dollars | other | operator-class |
|--:|--:|--:|--:|--:|
| 50 | 24 (0 hits) | 24 (1 hit) | 2 | 0 |
| 250 | 147 (1 hit) | 100 (2 hits) | 3 | 0 |

Provider ranking (refit of the shipped scorer, out of fold), FCA ruler:

| budget | hits | random expected | lift | note |
|---|--:|--:|--:|---|
| top 16 / 50 / 250 | 0 | 0.0 to 0.1 | 0 | 285 providers tied at the boundary |
| top 1,000 | 0 | 0.4 | 0 | |
| top 1% (6,119) | 11 | 2.5 | 4.3 | Wilson 0.10% to 0.32% precision |
| top 10% (61,188) | 91 | 25.4 | 3.6 | |

Segments with real signal on the FCA ruler (top 1 percent within the segment): billers over $10 million a year, 4 hits in 86 (precision 4.6 percent, lift 16); New York, 4 of its 7 positives in the top 1 percent; Illinois and California, lift 7 to 10; organizations and facilities (the "other" family), 8 hits, lift 4. Segments with none: dentists, nurse practitioners, therapists, optometrists, Texas, Florida, Ohio.

Lead time of the one top-50 hit: 34 months. Calibration at the top: mean calibrated probability 0.0032 in ranks 1 to 1,000 against an observed rate of 0; expected 1.8 true discoveries in the top 50 organizations against 1 observed; FDR-bounded list size 0. Fold mixing: fold 1 supplies 33 percent of the top 1 percent against 20 percent expected, so one fold's model runs hotter than the others.

Settlement dollars: 89 post-cutoff cases ($1.4 billion) resolve to a scored provider; $199.6 billion more does not. Dollar recall: 8 percent at 50 organizations, 32 percent at 100, 33 percent at 250.

## 3. The v6 run says the same thing (2023-12 freeze, 24-month horizon, graded 2026-10-08)

The v6 list (4,834 organizations after the filters) was graded with the same module and a shorter horizon, because its freeze is two years later and the public record has had less time to catch up.

| ruler | positives in universe | hits at 50 | hits at 250 | random at 250 | one-sided p at 250 |
|---|--:|--:|--:|--:|--:|
| all exclusions | 967 | 0 | 1 | 0.5 | 0.41 |
| hard exclusions | 233 | 0 | 0 | 0.1 | 1.00 |
| hard plus weak | 544 | 0 | 0 | 0.2 | 1.00 |
| FCA cases (headline) | 133 | 0 | 2 | 0.4 | 0.06 |

The top 50 is 19 institutional, 30 tiny-dollars and 1 intermediary-shaped; zero operator-class, zero hits. The provider ranking has the same shape as v7: a 219-way tie at the top, no future case in the first 1,000, then real signal at the top 1 percent (lift 3.0 on the FCA ruler, 3.7 to 4.3 on the exclusion rulers) and the top 10 percent (lift 4.8 on FCA, with 48 percent of all FCA positives inside it). Fold 1 again supplies 33 percent of the top 1 percent. FCA positives are the most visible at the freeze (70 percent still billing in the last 12 months; the hard exclusion tier only 39 percent). Dollars: 65 post-cutoff cases ($954 million) resolve to a scored provider; $521 billion do not; the top 250 touches 3 cases, 41 percent of the gradable dollars.

One segment stands out in v6: Illinois, where 17 of the all-ruler positives sit in the state's top 1 percent (shrunk lift 23). That is most likely a batch of state terminations arriving as one event, which is a label artifact to check before it is read as model skill.

The two freezes agree on the diagnosis. The shipped list is built from the two classes that do not produce cases; the model's signal lives in the top 1 to 10 percent of providers and in large billers; the head of the ranking is a tie block; the exclusion rulers cannot grade the organization list; most settlement dollars are unresolved. Verdict for v6 on the FCA ruler: HOLD, same as v7.

## 4. What this changes for v8

Ranked by how much of the gap above each item explains:

1. **The head of the ranking.** Untie the top: native missing values and the trainability gate (already in v8), a calibrated probability as the sort key instead of a dollar-weighted mean of raw scores, and an organization score of P(any member) rather than the member mean. The 285-way tie and the empty first 1,000 are the single largest loss.
2. **The composition of the top 50.** The institutional book and the tiny-dollars class should not occupy the first 50 rows of a whistleblower list. Keep both as separate books (the segment flag exists); the pursuit list should be built from operator-class organizations with case size above the bar, and the v8 check `inst_share50 <= 0.4` holds that line.
3. **Grade on the FCA ruler only.** The exclusion rulers cannot see the list. Every registry number for v8 comes from `acc_*` under `eval_policy = fca`.
4. **Label recall.** $199.6 billion of post-cutoff settlements attach to no scored provider. The defendant resolver and the research residue are the levers; each resolved large case adds gradable dollars and positives in the band where the model already works.
5. **Train and rank within the band that works.** The over-$10-million band shows 4.6 percent precision at the top; a within-band ranking (or a size-aware objective) would surface it instead of burying it under hospitals and small agencies.
6. **Fold balance.** Use a positive-stratified grouped split so one fold cannot supply a third of the top.

## 5. The pursuit book, graded on v7 (2026-10-09)

Items 1, 2 and 6 above were built the same day and the operator book was re-graded on the v7 run before anything shipped. The companion file `provider_priority_list_pursuit.csv` holds the operator segment only: 1,855 organizations, 8 of them with a future FCA case in the pool. The diagnostics' order sweep (section 2b of the report) ranks the same 1,855 rows under every candidate sort key and counts hits at the four budgets a human reads.

| sort key (operator book) | hits at 16 | at 50 | at 100 | at 250 |
|---|--:|--:|--:|--:|
| score_dollar_weighted (the main list's key) | 1 | 2 | 3 | 4 |
| score_p_max (max member probability) | 0 | 2 | 2 | 3 |
| score_max (max member raw score) | 0 | 2 | 2 | 3 |
| score_p_any (calibrated P(any member)) | 0 | 0 | 0 | 2 |
| basis-backed first, then any key above | 0 | 0 | 0 | 0 to 3 |
| the full main list, for reference | 0 | 1 | 2 | 3 |
| random draw from the pool | 0.1 | 0.2 | 0.4 | 1.0 |

Plain version: cutting the hospitals and the solo individuals out of the book doubled the hits at every budget. Changing the sort to the calibrated probability, or putting the basis-backed case sizes first, threw those hits away again. So the pursuit file keeps the dollar-weighted order (score_max breaks ties); the calibrated columns ride along for the reader and nothing sorts on them. The first shipped version of the pursuit file sorted on P(any) with basis-backed first and graded at 0 hits at 50, which is why the sort was changed before v8. The first re-grade of the re-sorted file also read 0, because the diagnostics loader assumed any pursuit book was sorted on P(any) and re-sorted it before counting; the loader now infers the sort key from the file's own order (bundle 241), and the budgets grade the order the operator reads.

Why P(any) lost: the cross-fitted isotonic map is flat across the head of the book (section 7 of the pursuit report: mean calibrated probability 0.0032 in ranks 1 to 50, 0.0032 in 51 to 250, 0.0027 in 251 to 1,000 on the FCA ruler), so sorting on it reshuffles the first few hundred rows by noise. The dollar-weighted score carries the same signal with more resolution. Item 1 above stays open as a v8 question (untie the head with native NaN and the trainability gate), but the sort-key half of it is settled: not P(any), not on this scorer.

Confirmed on the rebuilt file (2026-10-09, 18:29): the diagnostics read the pursuit book as 1,855 rows in the dollar-weighted order and scored 2 hits at 50 on the FCA ruler against 0.2 by chance, with a median lead of 29 months. That is the number v8 must not fall below.

What this means for v8: grade `provider_priority_list_pursuit.csv` with `--list-name`, under the FCA ruler, with `hits50 >= 2` as the enforced bar (the v7 operator book at the dollar-weighted order already reads 2; v8 must not read below it).

_Leads for counsel review, never accusations._
