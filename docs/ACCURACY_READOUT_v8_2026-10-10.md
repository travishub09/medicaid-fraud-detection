# Accuracy read-out: frozen_2021-12_v8 (HOLD, and why the run is not a model result)

Prepared by Claude for Trey Rawles, 2026-10-10, from the v8 run packet (ACCURACY_DIAGNOSTICS on the pursuit book and the large-biller book, EXPECTATIONS_REPORT, SOURCES_REPORT, the export report, LEARNING_LOOP, RUN_ISSUES, the feature manifest). Plain version first, then the numbers, then the fixes that are now in the code. A hit is a listed organization or provider that a later public enforcement record touched; it is not a finding about anyone. Leads for counsel review, never accusations.

## 0. Plain version

v8 reads no better than chance at every depth a person reads. The first 50 organizations on the pursuit book held 0 hits where a random draw would hold about 0.3. The first 250 held 1 where random would hold 1.4. The provider ranking is worse than random at the top 1 percent on the FCA ruler (lift 0.76), the first 2,015 providers share one score, and the top 50 changed completely between fold draws (Jaccard 0.00). The release registry records HOLD on 4 of the 8 locked checks, and the v8 pursuit dossier was NOT sent to Manus (nothing on a chance-level list deserves research spend).

v8 is not a model result. It is a pipeline error, and the packet shows where:

1. **The model never saw an FCA case.** The night-runner's export line did not carry `--case-db` or `--defendant-npis`. The manifest lists 34 sources and no `doj_case`. The training label was 920 exclusion positives, and then the forward FCA ruler (529 positives in the universe) graded a model that had never been shown the thing it was graded on. The documented command in the runbook carries `--case-db`; the runner dropped it. v7 trained with the case labels, which is why v7 read 1 hit at 50 and v8 reads 0.
2. **The universe doubled.** The servicing grain added 843,551 rendering-only rows (329 carrying an exclusion) to the 617,062 billing rows. Every peer percentile, every subscore distribution and the ranking itself were computed over a universe that is 58 percent clinicians with no billing row. The provider-level signal that v7 showed in the top 1 percent (lift 3 to 4) was diluted to below chance.
3. **Two servicing shares were computed wrong.** Refund rows (negative dollars) pushed `sv_top_billing_share` and `sv_share_under_others` outside 0 to 1 for 26 providers (two of the eleven EXPECTATIONS FAILs).
4. **Five features were dead on arrival.** `graph_degree` / `graph_triangles` / `graph_clustering` were constant (the embedding cap zeroed the motifs for every node in a small component); `px_brand_paid_share` was all NaN (the NADAC reference was not passed to the NDC block before bundle 265); `mcd_ended_for_cause` was all zero as of 2021-12 (the enrollment file's for-cause ends begin later); `hospice_live_discharge_rate` and `subscore_invalid_identity` were all NaN (the 2023 facility editions inside a 2021 freeze; the deactivation file after the cutoff).

What this means: v8's numbers must not be read as "the levers failed" or as a regression from v7. Nothing about the case-cluster folds, seed bagging or the per-case budget was exercised, because those are v9 and v8 did not train on the labels those levers act on.

Verdict for the release registry: HOLD. Cause: pipeline error (missing case labels, doubled universe), not model performance.

## 1. The numbers

Universe: 1,460,613 matrix rows (617,062 billing plus 843,551 servicing-only); 920 training positives, all exclusion-sourced; 529 FCA-ruler positives in the universe after dropping everything known at the freeze.

Pursuit book (1,459 operator organizations after the government and $10 million filters; 8 of them carry an FCA hit in the 36-month window), FCA ruler:

| budget | hits | random expected | one-sided p | Wilson 95% | dollar-ranked hits |
|--:|--:|--:|--:|---|--:|
| 16 | 0 | 0.1 | 1.00 | 0% to 19% | 0 |
| 50 | 0 | 0.3 | 1.00 | 0% to 7% | 1 |
| 100 | 0 | 0.5 | 1.00 | 0% to 4% | 1 |
| 250 | 1 | 1.4 | 0.78 | 0.1% to 2.2% | 3 |

The dollar-ranked baseline beat the model at 50, 100 and 250. The large-biller book (over $10 million billed) reads the same 0 at 16 and 50.

Provider ranking (refit of the shipped scorer, out of fold):

| ruler | top 1% lift | note |
|---|--:|---|
| FCA cases (headline) | 0.76 | 2,015 providers tied at the top; no case in the first 1,000 |
| all exclusions | 1.71 | v7 read 3 to 4 on the same cut |

Stability: top-50 Jaccard across re-shuffled folds 0.00; fold 4 supplied 45 percent of the top set against 20 percent expected. Lead time: no hits, so no lead time. Settlement-dollar recall at 50: 0 of $1.5 billion gradable.

Locked v8 checks: `hits50 >= 2` failed (0); `prec50_lo > base50` failed (0 vs 0.005); `lead_ge_gap >= 0.5` unmeasured (no hits); `top50_jaccard >= 0.6` failed (0.00); `inst_share50 <= 0.4` passed (0.0, the operator filter did its job); `eval_policy = fca`, `horizon = 36`, `frozen = True` passed.

Run ledger: 19 FAIL, 106 WARN, 16 INFO. Learning loop: 566 suspect positives (labelled positives scoring below the mean negative, most of them exclusion rows with no billing signal on the diluted universe), 974 of 983 forward positives are near misses, feature drift FAIL on 9 columns against v7 (the universe change, not a file change), network family NO MEASURABLE SIGNAL.

## 2. What was fixed in the code (bundle 268)

| defect | fix | where |
|---|---|---|
| runner dropped the case labels | the export now records `doj_case` as skipped in the SOURCES_REPORT, with the reason, when no `--case-db` is given; the issue ledger raises `label:doj_case:missing_case_labels` as a FAIL whenever a manifest's sources carry no `doj_case`; the v9 runner carries `--case-db` and `--defendant-npis` on the export line | `provider_features_export.py`, `run_issues.py`, `run_v9_night.bat` |
| universe doubled | new flag `--no-servicing-rows`: the sv_* features are still computed for every rendering NPI (they feed rendering_identity and impossible_day) but no rendering-only rows enter the universe; the v9 runner uses it | `provider_features_export.py` |
| shares outside 0 to 1 | `sv_top_billing_share` and `sv_share_under_others` are built on positive dollars only and clipped; the net paid totals are unchanged | `servicing_grain.py` |
| `px_brand_paid_share` all NaN | fixed earlier (bundle 265 passes the NADAC reference into the NDC block); v9 will carry it | `provider_features_export.py` |

Still open, carried on the ledger for v9: the three constant graph motifs (the embedding cap zeroes motifs for solo-component nodes, a feature with no variance should be dropped rather than shipped), the post-cutoff facility / PBJ / POS editions inside the 2021-12 freeze (procure pre-cutoff editions or exclude the sources from frozen builds), `mcd_ended_for_cause` constant as of 2021-12 (expected; the feature earns its keep on a later freeze), and the deactivation file after the cutoff.

## 3. How v9 is graded

v9 is a new pre-registration on the same freeze with the case labels back, the billing-NPI universe, case-cluster folds, five-seed bagging and the per-case label budget. The hits bar stays at `hits50 >= 2` (v8 did not read higher). Grade it on hits at 50 on the pursuit book against random under the FCA ruler, never on decile lift. The weight-vs-mask broadcast A/B in the v9 runner decides the v10 broadcast policy.

## 4. Operator steps

In order: merge bundle 268; copy the refreshed run_v9_night.bat into the repo folder; pre-register v9 with the command in the reminders; start the runner; say "v9 finished" when it prints DONE. The fuzzy-tier verification batch may be dispatched now, since the v8 dossier was not sent and the Manus slot is free.

_Leads for counsel review, never accusations. A hit in this document is a later public record touching a listed organization, not a finding about it._
