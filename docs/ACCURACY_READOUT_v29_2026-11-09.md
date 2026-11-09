# Accuracy read-out: v29 (frozen 2023-12, horizon 33 months)

_Read 2026-11-09 from the run packet (run_packet_v29.tar.gz, pulled by the analyst). Every number here describes how a ranked list compared with later public enforcement records; a hit is a listed organization a later record touched, not a finding, and a miss is not an exoneration. Leads for counsel review, never accusations._

## 1. What v29 is

The first run on batch 5b (docs/ARCHITECTURE_AUDIT_2026-11.md): the case ruler resolved on the as-of graph, per-CCN facility inputs, the per-case label budget (`npi100`), honest patient-month bases, the policy-aware features, the solo book and the held-out field ruler. Same freeze and policy as v28: 2023-12, horizon 33, ten seeds, train = hard, no class priors, no prune, embeddings off, pursuit order dollar-weighted. Pre-registered 2026-11-04 17:25 UTC under 21 checks (the v28 seventeen plus `ruler_graph = asof`, `facility_rollup = own_ccn`, `segment = all`, `case_budget = npi100`).

The run stopped twice and resumed in the same folder: once on the runner's skip guard (it read the word "skipped" inside a USED source's detail; behind it the data-root defect that had hidden the policy library from every frozen run since v26, both fixed, resumed at step 2 with a full re-export), and once at step 7 on the held-out field ruler (no visit inside the horizon, an empty ruler file, a pandas 3 cast error; fixed so an empty ruler is set aside by name, resumed at step 7). DONE 2026-11-06 02:25 UTC. The matrix is 617,062 providers by 401 columns, 277 trainable; calc-integrity 0 FAIL, 63 WARN; 44 sources used, 4 skipped (CLIA held back on purpose for v30, the death master file, graph velocity, NADAC).

## 2. The verdict

**Registered: HOLD on one check of 21.** Top-50 Jaccard 0.599 against the 0.6 bar (RUN_ISSUES: FAIL 1, WARN 123). The other twenty pass.

**The failing number was the wrong measure.** The check was written for the stability of the pursuit BOOK's top 50, and the diagnostics report prints `acc_top50_jaccard_source = all_orgs`: the national top-50 by dollar-weighted score. The book measure was `None` on this run and on v28. The analyst found the cause in `accuracy_diagnostics.stability()`: the rank array was named `order`, overwriting the book's sort-key argument, so every repeat's book failed on an array's `startswith` and the check fell back to the national measure. He re-measured v29 on the book with the fix: **0.749** (PR #3, merged 2026-11-07 as part of c9d7c34). On the measure the check was written for, v29 passes 21 of 21.

**What to do with that.** Nothing to v29's registry row: a re-registration needs the diagnostics re-run on the fixed code, and the first run that will do so cleanly is the analyst's v30 (same freeze, the book measure, bar 0.7). v29 stands as HOLD on paper with the cause recorded; v27 stays the champion; the baseline pin does not move.

## 3. The grades

Pursuit book (2,403 organizations listed), FCA ruler (326 positives in the universe, 21 inside listed rosters, reach 6.4 percent):

| budget | hits | random | p | dollar-ranked |
|--:|--:|--:|--:|--:|
| 16 | 1 | 0.1 | 0.065 | 0 |
| 50 | 2 | 0.2 | 0.017 | 1 |
| 100 | 2 | 0.4 | 0.062 | 1 |
| 250 | 4 | 1.0 | 0.014 | 3 |

Large book (1,269 organizations, the $50M floor): 1 / 2 / 4 / 6 at 16 / 50 / 100 / 250 (random 0.1 / 0.3 / 0.6 / 1.6; p 0.037 at 50, 0.002 at 100, 0.001 at 250). The 100 and 250 budgets are the best the large book has read in the 2023-12 series (v27 1 / 3 / 3 / 6, v28 2 / 2 / 3 / 5).

Probability-ordered book (pmax): 1 / 2 / 3 / 4. Solo book: 19 individuals, zero reach on every ruler, nothing to grade.

Other KPIs: NPI top-1 percent lift 17.8 (v28 21.2, v27 23.4); score scale 0.217 (healthy); institutional share of the top 50 zero; case-cluster hits at 50 = 2 (p 0.053 on the pursuit book, 0.069 on the large); median lead 6 months (the 33-month ruler is young); state ruler 1 hit at 50 (random 0.1, p 0.08); affiliated ruler 1 / 2 / 2 / 4; qui tam ruler 118 positives in the universe. Settlement dollars: the top 50 touched 2 of 188 resolvable post-cutoff cases.

The top 50 is still 39 cap-pinned rows without a case-size basis (1 hit) and 11 others (1 hit): the plateau class the sweep has described since v11.

## 4. The order sweep and the arms

Order sweep on the pursuit book, FCA ruler: the shipped dollar-weighted order 1 / 2 / 2 / 4; `score_p_max` 2 / 2 / 2 / 4 (wins the 16); every "basis-backed first in the top 50" variant 1 / 2 / 3 / 4 (wins the 100 by one). No order wins across budgets, so the sort stays dollar.

Refit arms, operator book at 16 / 50 / 100 / 250, with the NPI top-1 percent lift:

| arm | hits | NPI lift | read |
|---|---|--:|---|
| weight (the ship) | 1 / 2 / 2 / 4 | 17.8 | baseline |
| mask | 2 / 2 / 3 / 5 | 10.7 | score scale 0.117: the degraded calibration seen in v24 and v28; stays an arm |
| class priors (nested) | 2 / 3 / 3 / 4 | 13.2 | +1 at 16 and 50 on this draw, the NPI grain weaker; third draw, same pattern as v27 |
| hard+charged | 2 / 3 / 3 / 4 | 11.3 | +1 at 16 / 50 / 100 (v28 +2 at 50): the recurring small win; policy stays hard, the arm continues |
| feature set: other (no subscores) | 1 / 1 / 3 / 4 | 15.6 | about the ship |
| feature set: subscores only | 0 / 2 / 2 / 3 | 7.4 | loses the NPI grain again (v28 6.4) |
| segment = operator | 1 / 2 / 3 / 5 | 8.6 | +1 at 100 and 250, NPI grain halved |
| settlement weight 0.5 | 1 / 2 / 2 / 4 | 17.8 | identical to the ship |
| hard+commercial | 1 / 2 / 2 / 4 | 9.8 | identical on the book, weaker NPI grain |

Nothing beats the ship across budgets while keeping a healthy NPI grain. Policy for v30 unchanged.

## 5. The sources and the label

The policy library reached the matrix for the first time (179,018 NPIs with 26 policy columns; 423,196 provider-topic pairs under a stated state rule): the data-root fix did its job. Source ablation on the forward label (633 positives): the extra files beat the five core files (ROC-AUC +0.031, top-decile lift +0.54, both intervals clear of zero); by group, saturation +0.42, Medicaid enrollment +0.31 and 340B +0.29 carry the lift, the policy features +0.18 with an interval spanning zero (one run; the first), facility quality -0.03.

Training label under policy hard: 2,778 positives. Label ablation: removing the DOJ-case rows costs 1.36x of forward lift (the one source with a measurable forward signal); the exclusion-only label reads 2.12x against the union's 4.09x. Label audit: 6,281 matrix positives, the join clean both ways, 23,936 distinct valid NPIs across the exclusion-style feeds of which 15 percent ever billed in the universe; five high-severity findings, none new.

Learning loop: 566 suspect positives (20 percent of the training label), led by org-billing broadcast rows from DOJ settlements onto counties, a university and a large home-care agency. A county government and a university are never the operator class a relator comes from; `is_government_name` exists and does not gate the org-billing tier. A v31 label rule.

## 6. Schemes

On the FCA ruler: impossible_day 0.624 (578 positives), behavioral_health 0.597, lab_testing 0.595, specialty_mismatch 0.591 read as signal; ownership_integrity 0.416 on 501 positives reads INVERTED for the second run; drug_outlier, pill_mill, single_service_mill, facility_code_billing, lab_unbundling, overutilization, payment_outlier, pharma_kickback, rapid_ramp, rendering_identity, session_inflation and upcoding read coin-flip; rendering_identity 0.611 on the state ruler.

**A measurement defect found in this packet, fixed 2026-11-09.** SCHEME_HEALTH marked eight schemes DEGRADED for "missing inputs" that the manifest's own scheme_coverage lists as used: the raw-driven inputs (code_concentration_hhi, billing_taxonomy_mismatch, mue_violation_share, px_opioid_la_share, rx_spread_top_share, contract_pharmacy_concentration) and the two graph counts normalised inside the subscore engine. The input check looked only for a `__peerpct` twin, which those inputs do not have by design. The statuses, the "weight present" column and the prune-candidate list on v28 and v29 are wrong for those schemes; the AUCs are right. `_input_reaches` now reads the manifest's coverage first and knows the raw-driven and pre-ranked names; v30's measurement step prints the corrected table.

Scheme alternatives: for single_service_mill the screens-style `top_code_share` reads 0.623 on the medical-necessity typed ruler (68 positives) against the current subscore's 0.388, above the +0.05 bar: a candidate replacement for a pre-registered run, never a silent swap.

## 7. The dossier

The default pick named Cochlear Americas, Sequenom Center for Molecular Medicine, Lester E Cox Medical Centers and La Clinica de la Raza (a device maker, a LabCorp genetics arm, a hospital system under one NPI, an FQHC): all four join the do-not-send list today. Loving Care Agency carries its own 2011 to 2018 qui tam (a standing-verdict row). The operator-run candidates are Centrus Premier Home Care, NSTX (a v28 repeat), Healthcare Consultants, Universal Nursing Services and Beechtree Diagnostics. Not dispatched (the HOLD rule); a dispatch, if the operator wants one, re-renders the pick with the pushed code first.

## 8. What follows

- v30 (the analyst's run, from the integration branch at or after c9d7c34): the same setup plus servicing_mix and CLIA (the 2026 edition, post-cutoff by design, read separately), the book-Jaccard measure at a 0.7 bar, the runner stop list. The corrected scheme-health table ships with it.
- v31 candidates, each a pre-registration item: the single_service_mill formula swap; government and university names out of the org-billing label tier; the ownership_integrity direction; the as-of provider_dim; a pre-cutoff CLIA edition.
- The charged tier keeps winning by one at the head on single refits; it moves to the ship only when it holds on two runs of ten seeds.
