# Accuracy read-out: frozen_2021-12_v19 (graded 2026-10-22; the run finished 2026-10-22 03:30 UTC)

_The first run on the audited code: platform-audit batch 1, the as-of v3 scores, the pre-cutoff
edition picker reading the 2021 editions for the first time, the October LEIE and NPPES, the
name-resolved exclusions and the CIA rows in the label; ten seeds, train = hard, 41 sources used,
21 schemes scored, 0 calc FAILs. Batch 2 (bundle 350) was NOT in this run. Leads for counsel
review, never accusations._

## Verdict: HOLD on three checks, all at the shallow budgets

| check | bar | v19 | v17 |
|---|---|---|---|
| large_hits50 | >= 2 | **1** (p 0.380) | 4 |
| large_prec50_lo > large_base50 | | **fail** (0.0035 < 0.0091) | pass |
| hits100 (pursuit) | >= 4 | **2** (p 0.158) | 5 |
| hits250 (pursuit) | >= 6 | 8 (p 0.000; the best 250 of the series) | 7 |
| lead_ge_gap | >= 0.5 | 1.0 | 1.0 |
| inst_share50 | <= 0.4 | 0 | 0 |
| top50_jaccard | >= 0.6 | 0.618 | 0.575 |
| seeds / groups / broadcast / train / frozen / horizon / policy | | all pass | all pass |

Pursuit book, FCA ruler (610 in-universe positives, 10 reachable by the book): 1 / 2 / 2 / 8 at
16 / 50 / 100 / 250 (random 0.1 / 0.4 / 0.7 / 1.8). Large book: 1 / 1 / 4 / 6 (random 0.1 / 0.5 /
0.9 / 2.3). Median lead 25.5 months, every hit at least 6 months out, institutional share 0. NPI
top-1% lift 7.5 (v17 9.0). Qui tam ruler 0 / 1 / 1 / 7 (p 0.000 at 250). Three fold draws:
30 orgs in every top 50.

Read against v17: the deep budgets held or improved (250: 8 vs 7 pursuit, 6 vs 6 large; 100 on
the large book 4 vs 4) while the shallow budgets lost three hits (large 50: 4 to 1; pursuit 100:
5 to 2). The hits did not disappear from the pool: the pool is larger (610 vs 580) and the same
cases sit lower in the head of the list. That is a re-ordering of the head, not a loss of signal.

## The order sweep: pmax holds on the second matrix

| order (pursuit book, FCA) | 16 | 50 | 100 | 250 |
|---|--:|--:|--:|--:|
| score_dollar_weighted (shipped) | 1 | 2 | 2 | 8 |
| score_p_max | 0 | 2 | 2 | 8 |
| score_p_max, basis-backed first in the top 50 | **1** | **3** | **4** | **8** |
| score_p_any | 0 | 0 | 0 | 5 |

The pmax book graded as its own file (`provider_priority_list_pmax.csv`) reads 1 / 3 / 4 / 8:
at or above the shipped order at every budget, as it did on v17 (2 / 3 / 5 / 9 vs 1 / 2 / 5 / 7).
The v17 rule was "the shipped sort moves only if the pmax book holds on a second matrix". It
held. **v20 ships `pmax_basis50`** (runner env `PURSUIT_ORDER=pmax_basis50`): the dossier picks
from that order and the pursuit book is graded under it; the dollar order stays a graded book
so the comparison continues.

## Why the head moved (the three changes this run carried, in order of weight)

1. **The as-of v3 scores came back.** v17 ran on the freeze-point code without them; v19 restored
   payment_intensity / service_intensity / concentration / temporal / specialty_mismatch from the
   as-of fact. Three subscores that consume them (payment_outlier, overutilization,
   single_service_mill) went from 25% to 73% coverage (feature_drift: null rate 0.75 to 0.27).
   That is the planned v18 change landing, and it re-weights the scorer's inputs for half the
   universe. Expected to move the head; it did.
2. **The 2021 editions were read for the first time** (PBJ 2021Q3/Q4, POS Q4 2021, the nursing-
   home November 2021 tables) instead of the 2023 editions. The drift FAILs on deficiency_count,
   nh_owner_count, deficiency_severity_weighted, pbj_*, nh_fine_total are this change: a fix, not
   a defect. The ten-run vintage FAIL on facility / PBJ / POS is closed by the ledger (every pick
   pre_cutoff); run_issues still lists it because it grades the manifest's older `vintage` block
   (a run_issues fix, batch 3).
3. **The label grew** from 1,545 to 1,712 hard-tier training positives (1,563 DOJ-case, the CIA
   rows and the October LEIE), and the ruler from 580 to 610. The per-case budget held the top-5
   case share at 7% of training mass.

Two defects were in this run and are fixed in the code v20 runs on:

- **The saturation leak.** The run log reads "reference period 2021-10-01 to 2022-09-30 (capped
  at cutoff 2021-12)": nine months of post-freeze market data inside market_saturation_index on
  every frozen run v10 to v19 (bundle 354 parses the period END).
- **The deactivation edition.** The newest deactivation report (September 2026) was read under
  the 2021-12 freeze and the ledger grades it post_cutoff. The adapter filters by deactivation
  date, so the feature itself is as-of; the ledger row is right to flag it and the pick should
  be the pre-cutoff edition when one exists (batch 3: add a dated-edition rule to
  `_deactivation_file`).

## The label ablation says what the tier policy already does

Under the union label (policy `all`, which is what the ablation trains on), dropping the
medicaid_enrollment positives RAISED forward lift by 1.29 (CI clear of zero), dropping
state_termination by 0.47 and deactivation by 0.46; dropping doj_case cost 0.79. The shipped
scorer trains under `hard`, which already excludes the weak enrollment, state and deactivation
rows, so this is confirmation of the policy, not a change. The ablation should run under the
shipped tier to say anything new (batch 3: `label_ablation --label-tier`).

## A leak suspect to read before it ships: hospice_ineligibility

FCA-ruler AUC 0.839 on 40 forward positives against 0.591 on the exclusion label. The scheme
covers 0.4% of the universe (hospices only), and hospices carry a high FCA base rate, so a
selection effect is the first explanation. The hospice release used is Feb 2022 and reads "0
CCNs with measure periods ending <= 2021-12", so the panel measures came from the nursing-home
and PBJ inputs, all pre-cutoff on the ledger. Not a leak on the evidence here; the scheme stays
and the next read-out checks it again.

## The dossier named two institutions

The operator-book pick listed Inova Health Care Services (a Virginia hospital system, 14 NPIs)
and Invitae (a national genetic-testing laboratory). Neither belongs on an operator book; both
join `_NATIONAL_NAMES` (bundle 366). The rest of the ten are the owner-run shape (ambulance,
behavioral health, a molecular lab, a California services company). Not dispatched (HOLD).

## Everything else

- Source ablation on the forward ruler: the extra data wins (ROC +0.074, lift +0.82, both CIs
  clear of zero); medicaid_enrollment, saturation, hrsa_340b and medicaid_ndc carry the largest
  marginal lifts.
- Learning loop: 235 suspect positives (14%), the largest by dollars the org-billing broadcasts
  of hospital and home-nursing settlements; 964 of 1,008 forward positives outside the top 1%,
  short on tenure_months, clinical_implausibility, new_code_burst, the billing embeddings and the
  servicing spread, the same owner-run shape as v17.
- Scheme health: 5 DEGRADED on missing inputs, 1 DORMANT (cost_report_fraud: the 2021 HCRIS files
  landed after the run), 14 prune candidates (coin-flip on both labels). The prune decision waits
  for a run where the saturation leak and the HCRIS gap are gone.
- Network family: no measurable signal (+0.003 AUC), as in every run since v12.
- Sources: 41 used, 5 skipped (death master, graph velocity, HCRIS, NADAC claim slice, order and
  referring: all known).

## Next: v20

v20 = the first run on batch 2 (asserted joins, the label gates on every path, Luhn everywhere,
the assessable mask, the pre-ranked pass-through, the saturation fix, the ablation fence) plus
the inputs that landed after v19 started: the 2021 HCRIS files, the saturation archive, 17 state
lists (the no-reason lists weak only), the verify-sources re-check (78 rows restored, 123 out),
the CIA entity load (326 rows through the name gate). Pre-registered under the v19 checks with
the pursuit book graded under `pmax_basis50`; large_hits50 >= 2, hits100 >= 4, hits250 >= 6,
top50_jaccard >= 0.6, train = hard, seeds >= 10. One run, one new pre-registration.
