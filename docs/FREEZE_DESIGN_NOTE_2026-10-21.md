# The freeze and the features it cannot test (design note, 2026-10-21)

Saved from the chat at the operator's request, after the 2021 edition hunt found that
Order and Referring (versions from June 2023) and All Owners (versions from
September 2022) have no edition before the 2021-12 freeze.

**The question.** Is it a design flaw that certain features cannot light up under the
2021-12 cutoff?

**Partly, yes, and it is worth being precise about which part.**

**What the freeze is for.** The 2021-12 cutoff is not how the model runs in
production; it is how we grade it. Every feature is computed as if it were December
2021, then the model is scored on cases that resolved in the following 36 months.
Without that, the evaluation leaks: a 2023 ownership snapshot already reflects the
change of ownership that followed the fraud, and the model looks better than it is.
Eight runs had that leak until bundle 324, so excluding post-cutoff editions was the
right fix.

**The actual flaw.** The frozen test is a lower bound, not a measurement, of the
production model. Production (the current-day run) uses every source. A source with
no 2021 edition is untested, and an untested feature is the real risk, in both
directions: the grade understates what production can do, and a harmful feature in
production would never be caught by the test.

**Three ways to close the gap, in the order to do them:**

1. **A second freeze at 2023-06.** Every source lights up there: POS, PBJ and the
   nursing-home files have 2023 editions on disk, All Owners reaches back to
   September 2022, and Order and Referring starts in June 2023. The 36-month horizon
   ends June 2026, so the cases to grade on exist. Fewer forward positives than
   2021-12 (cases take three to five years from conduct to resolution), so the hit
   counts will be smaller, but a model that holds on both rulers is stronger evidence
   than one ruler with half the features. The label-expansion plan already proposed a
   second freeze; this is the reason to pick 2023-06 rather than 2020-12.
2. **Build our own archive from now on.** The ownership gap is a CMS publishing
   limit, and the fix is already built: the monthly owner-snapshot cadence
   (`make owner-snapshot`, due November 7) stores dated owner edges, so every future
   freeze has editions. Two snapshots and ownership_turnover is live on any freeze
   from here forward.
3. **Do not substitute later editions as "slow-moving."** Tempting for ownership and
   referral eligibility, and exactly the leak that inflated eight runs. If a feature
   is ever admitted that way, it needs a staleness flag and a leak audit (the
   forward-ruler AUC implausibly above the in-time AUC), and the ablation harness can
   test that per source.

**So:** keep 2021-12 as the primary ruler (longest horizon, most positives), add
2023-06 as the second, grade v19 on both, and let the snapshot cadence make the
problem disappear over time. The second freeze is queued as reminder task
`second_freeze_2023_06`, to run after v18 is read.

What a 2023-06 freeze needs that is already on disk: the as-of NPPES edition
`nppes_history/npidata_pfile_20050523-20231210_thru2023.csv` (December 2023, so the
graph rebuild passes `--asof-nppes` with it and the address layer freezes correctly),
POS Q4 2023, PBJ 2023Q4, NH Nov 2023, Order and Referring 2023-12, NADAC 2023, All
Owners 2022-09 / 2023-01 / 2023-04 / 2023-06 (SNF), 2022-11 / 2023-03 / 2023-06
(Hospital), 2023-06 (HHA, Hospice). The runner takes the cutoff as its second
argument (`run_frozen_night.sh v19b 2023-06`) and the ruler files follow the cutoff.

## Addendum, 2026-10-21 (built the same day, bundle 345)

**The second freeze is 2023-12, not 2023-06.** Making the edition picker read quarter
and month tokens (`POS_File_Q4_2023` is 2023-12, `PBJ_2023Q4` is 2023-12,
`NH_Ownership_Nov2023` is 2023-11, `Order and Referring 2023-12`, `NADAC 2023` is
2023-12) shows that every edition this note listed as "on disk for 2023-06" is dated
AFTER June 2023, so a 2023-06 freeze would skip all of them and light up nothing the
2021-12 freeze lacks. At 2023-12 every one of them is on or before the cutoff, the
December-2023 NPPES in nppes_history/ is the exact as-of edition, and All Owners has
four pre-cutoff editions. A 36-month horizon from 2023-12 ends 2026-12, two months
out; the runner's `HORIZON=33` ends it at 2026-09, fully elapsed, and the ruler files
carry the horizon in their names (`__fca33`). The earlier `frozen_2023-12_v5` / `v6`
runs used this cutoff already.

**What was built for the three gap-closers:**

1. *The second freeze as a first-class run:* `src/model_a/freeze_inputs.py` resolves
   the as-of NPPES edition for any cutoff (newest on/before when at most 12 months old,
   else the nearest later one, recorded with its staleness in `freeze_inputs.json` and
   graded by run_issues); the runner reads it (step 0), takes `HORIZON`, names the
   rulers by it; `src/model_a/freeze_pair.py` puts two frozen runs side by side
   (grade per book and budget, the checks that HOLD ON BOTH, the sources used at one
   freeze and skipped at the other, the vintage verdicts) -> FREEZE_PAIR.md.
2. *Our own archive:* `src/entity_graph/owners_archive.py` copies every All-Owners
   file in preclean/owners into owners_prior/ under a dated name (idempotent, content
   hash); the runner calls it and archives the month's owner-edge snapshot before every
   run, so no monthly reminder is needed on the VM.
3. *No substitution:* every edition picker writes a row to the VINTAGE LEDGER
   (manifest `vintage_audit`, a table in SOURCES_REPORT): pre_cutoff / undated_substituted
   / post_cutoff_reference / nothing_eligible / current_day_newest; run_issues grades a
   substitution WARN and a post-cutoff pick FAIL; scheme_health flags `leak_suspect`
   when a scheme's forward-ruler AUC sits 0.15 above its in-time AUC (and at least
   0.70), which run_issues raises as a FAIL. HCRIS now reads one cost-report year
   on/before the freeze (it pooled every file: PLATFORM_AUDIT H7).

**And the hold-folder question, answered:** a current-day run used to POOL every
edition in a multi-file folder (PBJ 2021 with 2025, POS Q4 2021 with Q1 2026) and the
single-file pickers returned the alphabetically first name. Now a current-day run reads
the newest edition (POS, HCRIS, the NH tables) or the newest four quarters (PBJ), so the
2026 editions can move out of the hold folders.
