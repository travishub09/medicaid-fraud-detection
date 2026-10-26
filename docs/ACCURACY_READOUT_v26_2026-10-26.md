# Accuracy read-out: frozen_2023-12_v26 (graded 2026-10-26; the run finished 2026-10-26 17:07 UTC after a resume)

_The SECOND FREEZE: a full export on a matrix frozen at December 2023 (two years after the
first freeze), graded on the 33-month FCA ruler (cases and hard-tier exclusions announced
January 2024 to September 2026; 273 positives in the universe), under v25's policy: no
class priors, nested encoder knobs recorded, pmax_basis50, train hard (2,470 positives,
2,170 DOJ-case), ten seeds, 44 sources. Leads for counsel review, never accusations._

## Verdict: HOLD on one of seventeen (top-50 Jaccard 0.588 against 0.6); the method replicates

| check | bar | v26 (2023-12) | v25 (2021-12) |
|---|---|---|---|
| hits100 (pursuit) | >= 2 | 5 (p 0.000) | 3 |
| hits250 (pursuit) | >= 4 | 6 (p 0.000) | 6 |
| large_hits50 | (not a v26 bar) | 3 (p 0.002) | 2 |
| top50_jaccard | >= 0.6 | **0.588** (26 orgs in every draw, 77 ever) | 0.622 |
| npi_lift1pct | >= 3 | 15.0 (the best of the series) | 8.7 |
| score_scale | >= 0.1 | 0.215 ok | 0.185 |
| lead_ge_gap | >= 0.5 | 0.50 (exactly at the bar) | 1.0 |
| inst_share50 / horizon = 33 / frozen / seeds / groups / broadcast / train / priors = none / prune / prior_inner | | all pass | |

Pursuit book, FCA ruler: **1 / 2 / 5 / 6** at 16 / 50 / 100 / 250 (random 0.1 / 0.2 / 0.4
/ 0.9; p 0.014 at 50, under 0.001 at 100 and 250). Large book 1 / 3 / 5 / 6 (p 0.002 at
50). Institutional share 0. The hits: Precision Toxicology (rank 1, announced 8 months after
the freeze), Community Health Network (24, announced the month of the freeze), Cleveland
Clinic (51, 5 months), Tactile Systems Technology (87, 31 months), Dana-Farber Cancer
Institute (100, 24 months), Savida Health (168, 12 months).

**The freeze pair** (`model_a.freeze_pair` on the two packets, FREEZE_PAIR_v25_v26.md): eight
of eight comparable checks hold on both freezes (hits above random at 50, 100 and 250 on
the large and pmax books, institutional share 0 on both). The second freeze lights two
sources the first could not (Home Health Compare, Order and Referring); every other
source is used at both.

## How independent the second ruler is

The two matrices are independent (billing through 2021 against billing through 2023, two
years of features apart) but the two rulers overlap: a case announced in 2024 falls inside
the first freeze's 36-month window and after the second freeze's cutoff. Of v26's 2,598
ruler positives, 1,266 are also on v25's ruler; 1,264 (announced 2025 and 2026) are not.
Among the six hits, three were announced within eight months of the freeze and are on
both rulers (Community Health Network, Cleveland Clinic, Precision Toxicology); Tactile
Systems and Dana-Farber were announced 31 and 24 months out, beyond anything the first
ruler could see; Savida sits at the edge. The replication is real for the matrix and
partial for the ruler; the next freeze pair with a wider gap (2020-12 against 2023-12)
would be the clean one.

The median lead is 4 months (v25: 22). The second freeze is graded on cases that resolved
soon after it, because the 33-month window has only just elapsed; the lead-time check sits
exactly at its bar (half the hits six or more months out). That is a property of the ruler's
age, not of the model, and it will lengthen as 2025 and 2026 cases accumulate against the
same freeze.

## The arms (refits on the same matrix, now the same fold design as the ship; main list, operator book)

| arm | main 16 / 50 / 100 / 250 | operator book | NPI lift | score scale |
|---|---|---|---|---|
| ab_broadcast_weight (= the shipped policy, refit) | 2 / 2 / 3 / 6 | 2 / 2 / 5 / 6 | 15.0 | 0.215 ok |
| ab_class_priors (nested priors) | 2 / 2 / 2 / 7 | 2 / 2 / 5 / 6 | 15.4 | 0.195 ok |
| ab_tier_charged | 2 / 3 / 3 / 4 | 1 / 2 / 3 / 5 | 19.4 | 0.215 ok |
| ab_tier_commercial (the first run with commercial rows) | 1 / 1 / 2 / 5 | 1 / 1 / 4 / 6 | 18.3 | 0.216 ok |
| ab_broadcast_mask | 1 / 1 / 2 / 3 | 1 / 2 / 2 / 4 | 8.1 | 0.109 ok |

The shipped-policy refit now reads the shipped operator book to the hit (2 / 2 / 5 / 6
against the shipped 1 / 2 / 5 / 6): the fold-design fix of bundle 392 confirmed. Class
priors add nothing on this freeze either (the same book). The charged and commercial
tiers trade the deep budgets for the NPI grain; neither moves the policy. Mask stays an arm.

## What the second freeze exposed (fixed in bundle 395)

- **The hospice edition picker** chose the May-2026 release for the 2023-12 freeze (the
  rule: the earliest edition after the cutoff, since a release carries the periods that
  ended before it) and found zero measure periods ending by December 2023, while the
  November-2023 edition sat beside it: `subscore_hospice_ineligibility` is all-NaN in v26.
  `pick_edition_candidates` now returns the order to try and the export falls back to the
  next edition when one filters to nothing. The subscore returns on the next export.
- **Three shares left [0, 1] on the 2023 data**: prolonged-visit add-ons per E/M line up to
  469, interactive-complexity add-ons per psychotherapy line up to 199 (add-on codes billed
  without the base line), and the lab component share at 1.47 (refund months). The add-on
  shares are capped at 1 (the maximum a base line can carry), the lab share is computed on
  positive-only dollars. v27 semantics for `session_inflation` and `lab_unbundling`.
- **The dossier named a settled case first.** Precision Toxicology, rank 1 of the operator
  book, settled in 2024. A frozen run's dossier is an operational pick made today, so
  `pursuit_dossier` now sets aside every organization with a member on the run's forward
  ruler (public since the freeze), lists them in `dossier_public_since_freeze.csv`, and
  never uses the ruler to rank. `--keep-public-cases` restores the old behaviour. Sanford,
  Stanford Health and Eurofins joined the national-name list (the v26 pick named them).
- The second freeze has no All-Owners edition on or before December 2023 (ownership
  turnover absent; reminder `owners_2023_editions`) and the HHA cost reports lack the
  admin-cost column. Both are data, not code.
- Ledger: 5 FAIL (the Jaccard, the three shares, the hospice subscore), 106 WARN.

## The dossier: rebuilt, then the operator's decision

Not dispatched. The v26 pick as written (Precision Toxicology, Sanford Bismarck, Sunrise
Medical Laboratories, Stanford Health Care, LabOne, HealthTrackRx Indiana, ACM Medical
Laboratory, Stavros Center, Ideal Option, Eurofins Diatherix) carries a settled case and
two hospital systems; the rebuilt pick under bundle 395 drops them. The operator's
standing decision ("dispatch tomorrow") now has both freezes in hand: v26's matrix is two
years more current than v25's, its NPI grain is the strongest of the series, and its
method holds on both rulers. The recommendation is to dispatch the REBUILT v26 operator
book, not v25's.

## Next

The series has its replication. The levers that remain are the ones the matrix cannot
supply: the label (the gated Manus lanes; the research residue), the two data gaps above,
and a current-day export for the operational list once the dispatch returns. v27 = the
2023-12 export again when the owners editions and the hospice fix are in (ownership
turnover and hospice back), pre-registered under the v26 seventeen.
