# Accuracy read-out: frozen_2021-12_v11 (read 2026-10-12)

_Leads for counsel review, never accusations. A "hit" below is an organization on the list that a later public enforcement record touched; it is not a finding about the organization, and a miss is not an exoneration._

## 1. The short story

v11 is the first complete run of the series: all 40 sources present, all 22 schemes scored, the five sources that died of memory on v10 back, and the score plateau gone. It is registered HOLD because three of the eleven pre-registered checks were not met, and all three are about the first 50 names on the operator book. Below rank 50 it is the best list yet: four cases in the first 100 and six in the first 250, where chance gives about one, and the odds of that by luck are one in a thousand at both budgets.

The reason the top 50 lags is visible in the error table: 44 of the 50 are organizations whose damages estimate sits at the 40 percent ceiling with no scheme-specific basis, and none of those 44 is a case. The one hit sits among the six rows that do have a basis. The model is finding cases; the top of the book is still crowded with rows the case-size screen cannot size.

## 2. The checks (pre-registered 2026-10-11, locked)

| check | bar | v11 | result |
|---|---|---|---|
| hits at 50, FCA ruler | at least 2 | 1 | not met |
| lower Wilson bound at 50 above random | 0.0057 | 0.0035 | not met |
| top-50 Jaccard across fold draws | at least 0.70 | 0.60 | not met |
| share of hits beyond the gap | at least 0.5 | 1.0 | met |
| institutional share of the top 50 | at most 0.4 | 0.0 | met |
| ruler, horizon, frozen substrate | fca, 36, true | fca, 36, true | met |
| seeds, groups, broadcast policy | 5, case_cluster, weight | 5, case_cluster, weight | met |

Decision: HOLD. Eight of eleven met.

## 3. v11 against v10, pursuit book, FCA ruler

| measure | v10 | v11 | random |
|---|--:|--:|--:|
| hits at 16 | 2 | 0 | 0.1 |
| hits at 50 | 2 | 1 | 0.3 |
| hits at 100 | 3 | 4 | 0.6 |
| hits at 250 | 4 | 6 | 1.4 |
| one-sided p at 100 / 250 | 0.010 / 0.022 | 0.001 / 0.001 | |
| dollar-ranked hits at 50 / 100 / 250 | | 1 / 1 / 3 | |
| top-50 Jaccard across fold draws | 0.09 | 0.60 | |
| orgs in every top 50 (of 4 draws) | | 31 | |
| ties at rank 50 | 285-way plateau | 1 | |
| NPI top 1 percent lift | 2.2 | 8.4 | 1.0 |
| median lead, months | 26 | 29 | |
| institutional share, top 50 | 0 | 0 | |
| schemes scored | 18 | 22 | |

The one-organization difference at 50 is inside the noise of an eight-hit pool; the deeper budgets are where the two runs separate, and v11 wins there with room. The plateau fix worked: the top-50 set is 60 percent stable across draws against 9 percent on v10, with 31 names present in every draw. The 0.70 bar is not reached because the remaining 19 slots still churn; more seeds is the cheap lever (the VM makes 10 seeds affordable).

## 4. The order sweep (the same book under every sort key)

| order | 16 | 50 | 100 | 250 |
|---|--:|--:|--:|--:|
| dollar-weighted (shipped) | 0 | 1 | 4 | 6 |
| dollar-weighted, basis-backed first | 1 | 2 | 3 | 3 |
| score_max | 0 | 1 | 4 | 5 |
| score_p_any | 0 | 1 | 1 | 5 |

Ranking basis-backed case sizes ahead of the capped rows buys one hit at 16 and 50 and loses three at 250. Under the v7 rule (no sort change without a sweep that beats the shipped order across budgets) the shipped order stays. The real fix is to give more of the top rows a basis, not to reorder them.

## 5. The broadcast A/B (refit on the same matrix, single seed)

| arm | 16 | 50 | 100 | 250 |
|---|--:|--:|--:|--:|
| weight (the policy) | 1 | 2 | 3 | 5 |
| mask | 0 | 1 | 1 | 4 |

Weight wins on every budget this time; v10's single-seed refit had said the opposite. Policy unchanged.

## 6. What the packet turned up, and what was done

| finding | where | what was done |
|---|---|---|
| rx_opioid_paid_share out of [0, 1] on two pharmacies (refund lines) | EXPECTATIONS FAIL | fixed: opioid dollars on positive lines, both shares clipped, NULL stays NULL |
| the near-miss gap list carried a z of minus 2.7e14 (rx_spread_coverage constant across the top set) | LEARNING_LOOP | fixed: a MAD floor at a millionth of the column scale, z clipped at 50 |
| the operator-book dossier listed a person-named org (target 9) and an unnamed org (target 10) | PURSUIT_DOSSIER | fixed: the operator book sets aside "LAST, FIRST" names and unnamed orgs |
| the large-biller book equals the pursuit book (1,397 = 1,397): the list's 10M case-size bar already implies 10M billed | PRIORITY_LIST | the book's floor is 50M from v12 (the v11 size table puts the highest shrunk lift in the over-10M band, 11.4) |
| hits at 100 and 250 were not KPIs, so no pre-registration could hold them | registry | acc_org_hits100 / hits250 (and base, p) added; aliases hits100, hits250 |
| subscore_invalid_identity is all-NaN (0 percent coverage) although the deactivation adapter produced 8,762 orgs | RUN_ISSUES FAIL, 3 runs | NOT fixed: needs the matrix. The probe below says whether the org-grain flag or the NPI-own deact_paid_after column is empty |
| medicaid_enrollment: 0 segments ended for cause out of 3.6M NPIs, so the enrollment label source is empty | SOURCES_REPORT | NOT fixed: needs the file. The probe below lists the status / reason values the file really carries; the benign rule may be swallowing a state's vocabulary |
| cost_report_fraud CHECK_DIRECTION (AUC 0.39 on 1.4 percent coverage) | SCHEME_HEALTH | known: nursing facilities are blind to the HCPCS-dollar label; build_nursing_home_dollar_basis is the fix |
| subscore_specialty_mismatch drift PSI 0.82 against v10 | LEARNING_LOOP FAIL | explained: v10 ran without billing_lm and plausibility, two of its four inputs |
| facility / PBJ / POS editions dated 2023 inside a 2021-12 freeze | RUN_ISSUES FAIL, 3 runs | recurring procurement item (earlier editions) |
| suspect positives are dominated by one settlement broadcast across a chain's state NPIs (Pediatric Services of America, 10 rows) | LEARNING_LOOP | the per-case budget already down-weights them; a chain-aware cap below 150 members is a v12 candidate, not a change made here |

## 7. The two probes (operator, minutes, no training)

The parquet probe streams the file through DuckDB and prints a table to paste back.

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.parquet_probe --file "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v11\provider_features_for_model.parquet" --pattern "deact|after_death|invalid_identity|billing_after"
python -m src.parquet_probe --file "C:\Users\treyr\OneDrive\Desktop\data\processed\medicaid_enrollment_segments.parquet" --values status reason --top 40
```

## 8. v12 (the first VM run)

Same freeze, same ruler, a new pre-registration. What changes: full graph embeddings (every connected node, not 1.5 million), 10 seeds for stability, the fixes in section 6, and whatever the two probes say. The checks carry the deeper budgets so the gain at 100 and 250 is held, not just the coin flip at 50:

```
hits50 >= 2; hits100 >= 4; prec50_lo > base50; lead_ge_gap >= 0.5; inst_share50 <= 0.4;
top50_jaccard >= 0.7; eval_policy = fca; horizon = 36; frozen = True; seeds >= 10;
groups = case_cluster; broadcast = weight
```

v11's dossier was built (operator book) but not dispatched: no research credits on a HOLD run.
