# Accuracy read-out: frozen_2021-12_v15 (2026-10-14)

_The first run on the expanded label: the DOJ case DB plus the CIA parties (185 resolved, 2,497 NPIs), the resolver's NPIs plus the CIA party NPIs, 843 name-resolved exclusions in a REBUILT as-of graph with full embeddings, affiliated convictions and the qui tam ruler as a sweep, the charged tier as a refit A/B. Same recipe otherwise (ten seeds, train = hard, 40 sources, 22 schemes, 0 calc FAILs, 617,062 rows, 248 trainable features). Leads for counsel review, never accusations._

## Verdict: HOLD on the same single check, seven of eight pass, and one pipeline finding

| check | bar | v15 | v14 |
|---|---|---|---|
| large_hits50 | >= 2 | 2 (p 0.049) | 2 |
| large_prec50_lo > large_base50 | | pass | pass |
| hits100 (pursuit) | >= 4 | **3** | 3 |
| hits250 (pursuit) | >= 6 | 6 | 7 |
| lead_ge_gap | >= 0.5 | 1.0 | 1.0 |
| inst_share50 | <= 0.4 | 0 | 0 |
| top50_jaccard | >= 0.6 | 0.64 | 0.61 |
| seeds / groups / broadcast / train | | all pass | all pass |

Pursuit book, FCA ruler: 1 / 2 / 3 / 6 at 16 / 50 / 100 / 250 (random 0.1 / 0.3 / 0.6 / 1.4; p 0.031 at 50). The pursuit book passes the 50 budget for the first time since v10. Large book: 1 / 2 / 4 / 5 (p 0.049 / 0.002 / 0.008). Median lead 25.5 months, institutional share 0. Jaccard 0.64, the most stable top 50 of the series. Ledger: 4 FAIL (the three stale editions, hits100), 75 WARN, 0 drift.

## The pipeline finding: the CIA parties never reached the label

The ruler held 509 positives in the universe, the same number as v14, and the training label grew by only 53 (1,258 against 1,205; 27 of them DOJ-case). The merged inputs were used (the sources report names doj_cases_v3_plus_cia.csv and 4,066 resolved defendant NPIs, up from 123), so the CIA rows were present. They were dropped by the OUTCOME RULE: the case_labels log reads "2,633 case rows without a resolved outcome" against 325 in v14, and the difference is the CIA rows. Their summaries said only "OIG Corporate Integrity Agreement (active)", which the rule reads as no outcome. Bundle 318 rewrote that summary to state the settlement, but the operator's cia_cases.csv on the VM was written by bundle 315, before the fix, and the runner's merge step took it as given.

Consequences, and the fix:

- v15 is therefore v14's label plus the 843 name-resolved exclusions (26 of them landed in the universe as pre-cutoff positives) and the org-resolution change (27 more DOJ-case positives from conviction releases without a dollar amount). It is not the CIA run. Its grade is a fair grade of that label.
- `merge_label_inputs` now counts the CIA rows that pass the outcome rule and prints a WARNING when the count is zero, so this cannot pass silently again.
- v16 = re-run `label_expansion cia` (the summaries now state the settlement), then `REBUILD_RULERS=1 ops/vm/regrade_from.sh v16 v15 2021-12`: the merged inputs and the three rulers are rebuilt and v15's matrix is re-graded (no retrain). The CIA parties with agreements effective after the freeze land on the FORWARD ruler, so v16 asks whether the shipped list already finds them. The parties effective before the freeze are training positives and need a retrain (v17).

## The sweeps

- **Charged tier A/B** (refit, same matrix, same seeds): hard+charged = 1 / 1 / 1 / 3, hard = 1 / 1 / 1 / 3, identical at every budget. The 68 charged-only positives (168 case rows) do not move the org grade. Policy stays hard; the tier stays available for a later run with the outcome-refresh rows folded in.
- **Qui tam ruler** (351 positives in the universe): pursuit 0 / 1 / 2 / 5 (random 0.1 / 0.2 / 0.4 / 1.1; p 0.001 at 250). Narrower than the FCA ruler by construction; the 250 budget holds under it.
- **Order sweep**: basis-backed-first-in-the-top-50 on the dollar score = 1 / 2 / 3 / 6, identical to the shipped order. Third reading: one win, one split, one tie. The sort stays dollar.
- **Broadcast A/B**: weight 1 / 1 / 1 / 3, mask 0 / 0 / 3 / 3. Weight stays.
- **Affiliated ruler**: 3,106 forward positives on paper against 3,051 (the 55 billers); in-universe effect inside the exclusion ruler only.

## The dossier

Operator-book targets: a Pennsylvania home-health agency, a toxicology lab, an ambulance company, a Florida pediatric home-care operator, two pediatric home-care companies, a sun-care DME supplier, a transplant-testing lab, a Wisconsin hospital system (Aurora Health Care Metro, which the national-name list should have set aside: add it), and a regional health company. Not dispatched (HOLD).

## Next

1. v16: the CIA re-run and the ruler-rebuild re-grade (task v16_regrade). Pre-registered under the v15 checks.
2. Add Aurora Health Care to `_NATIONAL_NAMES`.
3. v17 (retrain) only if v16 shows the CIA rows change the ruler materially; otherwise the next retrain waits for the Manus residue lanes (CIA no-match parties, the 610 ambiguous names, the outcome refresh).
