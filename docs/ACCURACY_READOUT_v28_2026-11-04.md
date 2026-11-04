# Accuracy read-out: frozen_2023-12_v28 (graded 2026-11-04; the run finished 2026-11-04 04:55 UTC)

_The second freeze under the label levers of 2026-11-01: the page-verified, expanded label
(train = hard, 2,915 positives, 2,619 DOJ-case, 1 charged, 0 commercial), the state and the
seven scheme-typed rulers read beside the FCA ruler, embeddings off (EMBEDDINGS=0), the
analyst's arms (feature set, settlement weight) refit on the same matrix. 617,062 providers,
284 trainable features, 43 of 46 sources, 26 schemes (cost_report_fraud dormant), 0 calc
FAILs, 60 calc WARNs. The run crashed twice at step 2b (the graph rebuild: a pyarrow dtype,
then a pandas NA) and was resumed on two hand-checked files; the export itself is dated
2026-11-02, before batch 5b, so the cluster-level and as-of-graph grades of batch 5b are
NOT in this packet (v29 carries them). Graded on the 33-month FCA ruler: 603 forward
positives in the 610,811 eval providers, 9 of them inside the pursuit book's rosters
(reach 0.067). Leads for counsel review, never accusations._

## Verdict: HOLD on one of the pre-registered checks (top-50 Jaccard 0.52, bar 0.6); the other bars pass

| check | bar | v28 (2023-12) | v27 (2023-12) | v26 (2023-12) |
|---|---|---|---|---|
| hits100 (pursuit) | >= 2 | 2 (p 0.052) | 3 | 5 |
| hits250 (pursuit) | >= 4 | 4 (p 0.010) | 6 | 6 |
| large_hits50 | >= 2 | 2 (p 0.029) | 3 | 3 |
| top50_jaccard | >= 0.6 | **0.522** | 0.616 | 0.588 |
| npi_lift1pct | >= 3 | 21.2 | 23.4 | 15.0 |
| score_scale | >= 0.1 | 0.220 ok | 0.219 | 0.215 |
| inst_share50 | <= 0.2 | 0.00 | 0.00 | 0.00 |
| fca_reach | >= 0.02 | 0.067 | (new) | (new) |
| embeddings | = 0 | 0 | 1 | 1 |
| train / policy / frozen / seeds / groups / broadcast / priors / prune | as declared | hard / hard / True / 10 / case_cluster / weight / none / none | same | same |

Pursuit book, FCA ruler: **1 / 2 / 2 / 4** at 16 / 50 / 100 / 250 (random 0.1 / 0.2 / 0.4 /
0.9; p 0.059 / 0.014 / 0.052 / 0.010). Large book **2 / 2 / 3 / 5** (p 0.029 / 0.013 / 0.004
at 50 / 100 / 250: the best 16 of the large book so far). The pmax book 0 / 1 / 2 / 4. Median
lead 6.5 months, half the hits beyond the gap, institutional share 0, score scale 0.22. The
ledger raises ONE FAIL (the Jaccard bar); the run_issues suggestion to carry `hits50 >= 2`
is noted, not adopted (a bar moves only through a new pre-registration with a reason).

**Read against v27.** Same ruler, same universe, the same policy, and the head of the
pursuit book lost one hit at 100 and two at 250 while the large book gained one at 16. The
training label grew (2,915 vs 2,470 hard positives; the state-side and page-verified rows)
and the top 50 re-shuffles more between fold draws (Jaccard 0.52 vs 0.62). v26 / v27 / v28
are one model at the deep budgets (4 to 6 at 250 on a 7-to-9-hit pool); the head checks keep
measuring the fold draw. Not a regression in the scorer: a wider label with the same signal.

## The other rulers

| ruler | in-universe positives (reach) | pursuit 16 / 50 / 100 / 250 | note |
|---|---|---|---|
| FCA (headline) | 603 (0.067; 9 in rosters) | 1 / 2 / 2 / 4 | above |
| affiliated (+employers) | reach 0.034 (12 in rosters) | 1 / 2 / 2 / 4 (p 0.025 / 0.030 at 50 / 250) | the employer rows add 3 pool hits and no head hits |
| qui tam only | reach 0.056 (4 in rosters) | 0 / 0 / 0 / 2 | NPI top-1% lift 37.4: the qui tam cases are found at the NPI grain, not in the operator book |
| state (terminations, enrollment ends, MFCU, employers) | reach 0.017 (1 in rosters) | 0 / 0 / 0 / 0 | NPI lift 3.4: the state-side positives are tiny billers whose orgs never rank; a reach fact, as the excl58 read |

## The arms (refits on the same matrix; operator book under the shipped order / main list)

| arm | pursuit 16 / 50 / 100 / 250 | main list | NPI lift | scale | read |
|---|---|---|---|---|---|
| shipped policy refit (weight) | 0 / 0 / 2 / 4 | 2 / 3 / 3 / 4 | 21.2 | 0.220 | the shipped list (1 / 2 / 2 / 4) vs its own refit = the fold draw |
| broadcast mask | 1 / 1 / 2 / 3 | 1 / 1 / 4 / 4 | 9.5 | 0.109 | degenerate scale again: mask stays an arm |
| class priors (nested) | 0 / 2 / 2 / 4 | 2 / 3 / 3 / 4 | 20.1 | 0.204 | +2 at 50 over the refit, as on v27; priors stay none, the arm keeps running |
| prior encoder loo_v22 / nested_unweighted | 0 / 0 / 2 / 4 | 2 / 3 / 3 / 4 | 21.2 | 0.220 | identical to the ship (no priors shipped, so the encoder knobs are inert) |
| charged tier (hard+charged) | 0 / 2 / 2 / 5 | 1 / 3 / 3 / 4 | 20.1 | 0.220 | +2 at 50, +1 at 250 over the refit on ONE charged positive: the difference is the draw, not the tier; policy stays hard |
| commercial tier | 1 / 1 / 2 / 3 | 1 / 3 / 3 / 3 | 16.6 | 0.220 | 0 commercial positives trained (the page rule removed them): inert |
| feature set = subscores only | 1 / 1 / 1 / 2 | 0 / 1 / 1 / 3 | 6.4 | 0.399 | the 26 subscores alone lose most of the NPI grain (lift 21 to 6) |
| feature set = other (no subscores) | 0 / 0 / 2 / 4 | 2 / 2 / 3 / 3 | 17.7 | 0.219 | equals the full refit on the operator book: the subscores add nothing the raw features do not carry at the head |
| settlement weight 0.5 | 0 / 0 / 2 / 4 | 2 / 3 / 3 / 4 | 21.2 | 0.220 | identical to the ship: halving civil-settlement weight moves nothing |
| embeddings | (no separate folder: the runner has no embeddings refit arm) | | | | the comparison is v28 (embeddings 0) against v27 (embeddings 1) on the same freeze: 1 / 2 / 2 / 4 vs 1 / 2 / 3 / 6 with the label changed too, so it is not a clean A/B; the v12 reading (no measurable signal) stands |

Order sweep (pursuit, FCA): no order beats the shipped pmax_basis50 at every budget; the
dollar order reads 1 / 2 / 2 / 4 too. Sort stays.

## Schemes on the rulers (SCHEME_HEALTH, the forward FCA ruler unless stated)

- **Signal:** impossible_day 0.607 (542 positives; 0.633 on the state ruler, 110), specialty_mismatch 0.626 (561), lab_testing 0.589 (137; 0.721 on the medical-necessity typed ruler, 5). New this run: **contract_pharmacy 0.705 on the kickback typed ruler (64 positives)** while coin-flip on the FCA ruler (0.530): its own ruler sees it. rendering_identity 0.614 on the state ruler (102) while 0.576 on FCA.
- **Inverted:** ownership_integrity 0.419 (476; 0.414 on organizations): the within-2-hops and related-party inputs rank the enforced BELOW the clean on organizations. Missing inputs (excluded_within_2_hops_norm, related_party_density_norm) are part of it; the subscore needs the batch-5b inputs before it is judged.
- **Prune candidates** (coin-flip on both labels, 20+ positives each): drug_outlier, behavioral_health, contract_pharmacy (on FCA only: its typed ruler says keep), facility_code_billing, lab_unbundling, overutilization, payment_outlier, pharma_kickback, pill_mill, rapid_ramp, rendering_identity (state ruler says keep), saturation_fraud, session_inflation, single_service_mill, upcoding. A prune is a pre-registration, never the ledger; the typed and state rulers now veto three of them.
- Thin (under 20 forward positives): dme_ring, hospice_ineligibility, invalid_identity, evv_personal_care, home_health_intensity, nemt_fraud, worthless_services. The state ruler reaches 3 personal-care and 1 home-health positives: the owner-run Medicaid schemes still have no ruler that can see them in this universe.

## Label and sources

- **Label ablation (policy hard):** doj_case carries the forward signal (lift 4.54 to 2.72 without it, CI clear of zero); medicare_revocation contributes (-0.31, CI clear); leie, medicaid_enrollment and state_termination show no measurable effect; leie-only 2.11 (the widening earned its keep).
- **Source ablation (out-of-fold, FCA):** the extra files win on ROC-AUC (+0.024) and top-decile lift (+0.79, CI clear); full-only catches 67 forward positives the core buries, core-only 19. Marginal groups: medicaid_enrollment +0.31, open_payments +0.31, medicare_puf +0.29 (CIs touch zero).
- **Learning loop:** 688 suspect positives (24 percent) score below the mean negative; the top by dollars are org_billing broadcasts of settlements onto large operators (All American Homecare $122.8M; Pediatric Services of America, seven states; three San Mateo County numbers, a government entity carrying a settlement label) and two resolved defendants (El Paraiso, Maryland Treatment Centers). The per-case budget caps their weight; batch 5b's cluster counting addresses the grade. 535 of 603 forward positives sit outside the top 6,171 NPIs and fall short most on clinical_implausibility, new_code_burst and billing_emb_7.
- **Label audit (5 high):** the identifier gap is unchanged (LEIE 10 percent valid NPI, SAM 5, state lists 16, OpenSanctions 0); 3 percent of usable DOJ cases resolve to a scored NPI; the employer and names lanes are the lever and loaded 0 rows this cycle (their gates: no Luhn NPI, employment window).
- **Editions:** 5 post-cutoff picks inside the 2023-12 freeze (deactivation 2026-06 feeding three blocks, HCRIS 2024-12, OPAIS 2026-08) and 7 MIXED; the pre-cutoff downloads are the due reminders (pos, pbj, care_compare, nadac, hrsa_340b, mue, clia).
- Skipped: death_master (no DMF), graph_velocity (one snapshot), nadac (no NDC claims). Network family: NO MEASURABLE SIGNAL (+0.002 AUC), the 17th run.

## The dossier (built, not dispatched)

The operator-book pick named ten: EPIC HEALTH SERVICES (PA) and PREMIER HEALTHCARE SERVICES
(both Aveanna subsidiaries under their pre-acquisition names), LOVING CARE AGENCY (its own
2011-2018 D.N.J. qui tam: a public-disclosure read before anything), CARIS MPI, MYRIAD
GENETIC LABORATORIES and AMBRY GENETICS (national genetics laboratories), and four
operator-run candidates: NSTX INC (TX, $32.3M, impossible day + rendering identity),
ANDVENTURE LLC (AZ/PA, $121.3M, 5 numbers), ADVANCED PAIN MANAGEMENT SPECIALISTS (MD,
$35.7M, 6 numbers) and PORT CITY OPERATING COMPANY (CA, $106.6M, 2 numbers). The five
chain names join `_NATIONAL_NAMES` with this read-out; 428 orgs were set aside as public
since the freeze and 51 by standing verdicts.

RULE (no credits on a HOLD run): not dispatched. The four operator-run names get the free
code-mix line first (`org_find --codes` and pi_facts top codes on the cd1 fact) and a
court-record pass; a kill prompt only for a survivor of those two, and only on the
operator's word.

## What v29 adds and what stays

v29 is the first run on batch 5b (case-cluster hits, the ruler on the as-of graph, per-CCN
facility inputs, honest patient-month bases, the policy-aware features, the solo book, the
field ruler), pre-registered under v28's bars plus `ruler_graph = asof`, `facility_rollup =
own_ccn`, `segment = all`, `case_budget = npi100`. The Jaccard bar stays 0.6 (three runs
read 0.52 to 0.62 on the same model: v29's cluster-level grade is the better instrument for
the head, and a bar moves only with a reason written before the run). Before v29's read: add the five national names to the dossier pick
(done in this bundle); carry the typed-ruler vetoes into any prune list.
