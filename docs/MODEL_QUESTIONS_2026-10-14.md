# The operator's questions before any bar moves (2026-10-14)

_Asked after the v16 read-out: are the schemes noise, are they wired right, are they the right schemes, can the label tell us what schemes we are missing, is the data good, is the label complete, is commercial fraud a label, is Manus maximized. Answered from the v15 / v16 packets (SCHEME_HEALTH, SCHEME_TRIAGE, CASE_VALIDATION, LEARNING_LOOP, RUN_ISSUES). Each answer ends with what is built, what is not, and what to run._

## 1. Are some schemes just noise?

Yes, most of them, measured against the label we grade on. SCHEME_HEALTH grades every scheme subscore by how well it separates known positives from everyone else (AUC; 0.5 is a coin flip):

| band | schemes |
|---|---|
| real signal (AUC 0.60 to 0.89) | invalid_identity 0.89, ownership_integrity 0.66, specialty_mismatch 0.62, dme_ring 0.60 |
| coin-flip (0.50 to 0.57) | 16 of the 22: pill_mill, pharma_kickback, upcoding, behavioral_health, worthless_services, facility_code_billing, nemt, rendering_identity, rapid_ramp, impossible_day, overutilization, payment_outlier, single_service_mill, saturation, contract_pharmacy, hospice |
| inverted (0.39) | cost_report_fraud fires on the clean side |

Against the DOJ cases of each scheme's own type (CASE_VALIDATION, the ruler that fits) only two validate: worthless_services (AUC 0.96 on 6 cases) and single_service_mill (0.63 on 29). The rest are PARTIAL, WEAK or THIN.

Two readings, both true. The label is blind to most of these schemes: an exclusion list records convictions, not billing patterns, and the DOJ case DB has 6 hospice cases and 0 upcoding cases in coverage. And a coin-flip subscore still enters the production scorer as a feature, where a tree model can at best ignore it and at worst split on its noise. The scorer's shipped ranking is dominated by a few features; 16 subscores add parameters without adding hits.

**How we handle it today:** the per-scheme AUC and the DOJ validation are computed every run and read in the triage, but nothing acts on them: every subscore stays in the trainable set. **What is built but not in the runner:** `source_ablation` (retrain with each source family removed, measure the forward-lift delta with a bootstrap CI) and `signal_ranking` (per-feature separation on covered rows). Neither has run on a VM run. **The action:** add both to the runner (they are the measurement that says which scheme families to drop), then pre-register v18 on the surviving set. Dropping a family is a new pre-registration, never a silent swap.

## 2. Are the failing schemes wired right, with the right files?

Checked every run, three ways: SOURCES_REPORT (every source used or skipped, with the file it read), the vintage audit (file dates against the freeze), and SCHEME_TRIAGE (the six-question diagnosis per flagged scheme). What they say about the 11 DEGRADED schemes: every one is degraded for the same reason, missing INPUTS, not broken code. The missing inputs are named per scheme (dme_ordering_md_concentration, drug_spread_anomaly, hospice nurse minutes, billing_after_death, ownership_turnover, bene_per_day_p95, service_intensity, concentration, temporal, local_volume_implausibility). They fall into three classes:

- data not procured: the SSA death master file (billing_after_death), NADAC with NDC-level claims (drug_spread), a second owner snapshot (ownership_turnover), the feature snapshots (graph_velocity);
- a frozen run cannot compute them: the v3 billing scores (concentration, temporal, service_intensity, payment_intensity) are current-day stage-one outputs with no as-of version, so every frozen run trains without them. This is the single largest wiring gap and it is OUR gap, not a procurement one;
- an adapter present but its column dead: bene_per_day_p95, local_volume_implausibility.

**What is built:** the gate that raises when a trainable column maps to no source, the SOURCES_REPORT, the triage. **What is missing:** an as-of version of the v3 billing scores (compute the five scores from the as-of spending fact inside the export). **Also a FAIL on the ledger for eight runs:** the facility, PBJ and POS files are 2023 editions inside a 2021-12 freeze. That is information from after the freeze reaching the features. It has not been fixed because the pre-cutoff editions were never downloaded. Until they are, the honest move is to exclude those three sources from frozen runs.

## 3. Are these the right schemes? Can the model find schemes we do not know?

Partly, and partly already does. The supervised scorer trains on all 248 features, so any pattern the label rewards is used whether or not a scheme names it. What it cannot do is tell us, in words, what that pattern is, and a scheme we never wrote has no subscore and no dossier driver.

Two reports already answer "what does the label light up":

- The learning loop's near-miss table (v16): 881 forward positives were not known at the freeze; 804 of them rank outside the top 1 percent; the median miss sits at rank 76,643. The features on which the misses most often fall short of the top set: clinical_implausibility (282 misses), new_code_burst (245), a billing-embedding dimension (222), servicing tenure (217), active months (194), the servicing-org counts (165, 158). Read plainly: the real cases we miss are younger, less implausible on paper, and spread across billing orgs, which is the owner-run-operator shape the model is near chance on.
- The weak-supervision label model (12 labeling functions, their learned accuracies) and the clean-anchor machinery.

**What is missing:** a label-conditioned enrichment report. For the forward positives versus the universe: which HCPCS codes, taxonomies, states, code mixes and growth shapes are over-represented, with lift and counts, so a new scheme can be designed from the cases themselves instead of from a typology list. The near-miss table is the first half of it; the code-level half is a DuckDB pass over the spending fact keyed by the forward label. Built next.

## 4. Is the data good?

The calc-integrity report reads 0 FAIL, 53 WARN every run; the warnings are the same near-constant flags (addr_is_mailbox, dme_ineligible_referrer, incons_* and others that fire on under 4 percent of rows). Row and dollar conservation are asserted at every join. The dirt is elsewhere:

- the three post-cutoff editions above (leakage, recurring);
- 302 suspect positives (24 percent of the training label: excluded providers whose billing looks ordinary; administrative bans, identity collisions, or schemes the features cannot see);
- 24,774 undated exclusion rows that no ruler can use;
- the annual-PUF cap and the 2021-or-older rule are applied, but the vintage inspector has not been run on the VM's data root since the July files landed.

**Action:** run `vintage_inspect` on the VM once; exclude facility / PBJ / POS from frozen runs until their pre-cutoff editions exist; send the 302 suspect positives through the label-review lane (built, not dispatched).

## 5. Is the label the best we can build from the sources we have?

No, and the audit says where the slack is. Not 100 percent on any source:

| source | what we use | what we leave |
|---|---|---|
| LEIE | NPI rows (8 percent) + 843 name-resolved + UPIN join | 610 ambiguous names (Manus lane written), 24,774 undated rows, the (a)(4) controlled-substance convictions (weak tier, ablation pending) |
| DOJ cases | 1,109 training positives, 583 forward | 126 CIA parties unmatched, the resolver residue (research lane written), 452 charged-only rows (now `charged`, A/B showed no org-level effect), 2,681 duplicate pairs tagged |
| state lists | 7 states | the ten largest Medicaid states are not loaded; license-number and Medicaid-id joins not built |
| SAM | corroboration only | nothing more to take |
| OpenSanctions | nothing (tiered excluded) | the identifier crosswalk, gated on the license |
| dockets | allegations recorded, never positives | unsealed qui tam dockets with outcome entries (the harvest set exists, not run) |
| OIG CIA | 185 of 339 parties | 126 for research |
| commercial fraud | federal convictions already in (18 USC 1347 covers private plans) | state insurance-fraud convictions: not harvested |

**Commercial-plan fraud as signal:** yes. A provider convicted of billing a commercial plan falsely has the same hands, codes and staff on its Medicaid claims. It is a TRAINING source (`hcf_commercial`), admitted on a conviction or judgment, never a forward positive (the forward ruler must stay on the business outcome) and never in the strict PurpleLab label. The harvest is a Manus prompt set in the same CSV schema as the MFCU sweep (state AG and insurance-department releases, state dockets, board orders citing commercial billing fraud); the loader exists (mfcu-cases); the source tag and the ablation switch are an afternoon of code. Not built yet because no Manus batch has gone out.

## 6. Have we maximized Manus?

No. Every research lane is written and none has been dispatched since the fuzzy batches:

- CIA parties missing from the case DB plus the 126 unmatched (set `cia`);
- the 610 ambiguous excluded names (set `exclusion_names`);
- the outcome refresh for the charged-only cases (set `outcome_refresh`);
- the label-review queue of suspect positives (60 rows per run, built by the learning loop);
- the defendant-resolver residue (defendant_research, tiered);
- the state MFCU sweeps for the Southeast (set `mfcu`) and, once written, the commercial-fraud sweep;
- the 30-row label verification sample (label_verification).

The HOLD rule (no credits on a HOLD run) was about the pursuit dossier, where research on a list we do not trust is wasted. The label lanes are different: they improve every future run regardless of the grade. They should go out now, CIA first.

## 7. The order, before any bar moves

1. Measurement in the runner: `source_ablation` + `signal_ranking` + per-scheme AUC on the FCA ruler (not only the exclusion label). One VM run (v17's matrix, no retrain).
2. The as-of v3 billing scores (the biggest wiring gap) and the exclusion of the three post-cutoff sources from frozen runs.
3. The label-conditioned enrichment report (what the forward cases light up) and the near-miss feature requests turned into features (code adoption age, servicing spread).
4. The Manus label lanes dispatched, CIA and names first; `hcf_commercial` harvested and ablated.
5. Then v18, pre-registered on the surviving scheme set, the label the lanes return, and the same bars. The bar moves only if that run reads 3 at 100 again with 7 at 250.

## 8. Built the same day (2026-10-14, bundle 323)

The operator asked for all of it in one day and a version to go back to. The freeze point is the tag `freeze-v16-2026-10-14` (branch `freeze/v16-2026-10-14`) at the commit of this document's first version; everything below is after it.

| item | what exists now | where it runs |
|---|---|---|
| §1 measurement | `scheme_health --future-label` grades every scheme on the FCA ruler (`fca_auc`, `fca_verdict`); `signal_ranking`; `label_ablation` and `source_ablation --oof --per-group` on the forward ruler; `run_issues` raises `prune_candidate` for a scheme that is coin-flip on both labels | `ops/vm/measure_run.sh`, step 12 of the night runner and the re-grade script |
| §2 as-of v3 scores | `model_a/asof_v3.py`: the five concept percentiles recomputed from the as-of fact with the exact v3 formulas; the export restores them in the frozen base frame; the runner refuses a run without the restore line | export step 2 (`--no-asof-v3` = the old behaviour) |
| §2 post-cutoff editions | frozen runs skip `facility` (Care Compare + PBJ) and `pos` until the pre-cutoff editions exist | `FROZEN_SKIP` in the runner |
| §3 enrichment | `model_a/label_enrichment.py`: codes, dominant code, taxonomy, state, size, tenure, scheme top-deciles among the forward positives vs the universe, with lift and a tail probability | measure step 3 |
| §5 commercial fraud | source `hcf_commercial`: harvest set `commercial`, loader `research_evidence commercial-cases`, policy `hard+commercial`, never a forward positive, never the strict label; an A/B refit in the runner once rows exist | reminders task `commercial_harvest` |
| §6 Manus | the dispatch lines for every lane are in the reminders (`label_manus_lanes`, `commercial_harvest`) | the operator's laptop (Manus key) |

v18 = the first run on this code: v17's inputs, the as-of scores in, facility / POS out, the measurement block, under the v17 checks (the 100-budget rule from v16 applies if v17 reads 3). The scheme set changes only after the measurement block has run once.
