# Wiring audit: adapter output -> training matrix -> scorer -> runners

Read-only audit of `src/model_a/provider_features_export.py` (3,204 lines) and the modules
it calls, `src/model_a/scheme_subscores.py`, `src/model_a/feature_partition.py`,
`src/model_a/company_list.py`, `ops/vm/run_frozen_night.sh`, `ops/vm/regrade_from.sh`,
`ops/vm/measure_run.sh`, `Makefile`. Code only; no data was present. Line numbers are
from the working tree on 2026-10-21. "Export" = `provider_features_export.py` unless a
file is named.

Normalization legend used below (the subscore input path, export 543-553):
- **PT** = pass-through raw into the subscore (`V3_CONCEPTS` + `GRAPH_FEATURES` +
  `ANALYTICS_FEATURES`, export 60-73, 545-547)
- **PP** = raw column -> one-sided taxonomy-peer percentile (`__peerpct`, export 538-541);
  the percentile is the subscore input (552). Applies to every `ADAPTER_FEATURE_COLS`
  member (87-92), i.e. every scheme input that is not PT.
- **RAW** = `RAW_SUBSCORE_INPUTS` (85): the clipped 0..1 raw drives the subscore (550).
- **PP(x2)** = the adapter ALREADY emitted a percentile and the export re-ranks it again.

Grain legend: NPI = per-NPI frame merged at 478-488; ORG = org-grain frame broadcast
to every member NPI (`_broadcast_org_to_npi`, 255-266, called 507-517); CCN = CCN-grain
-> `facility_peer_percentiles` (facility.py 465-497) -> `rollup_ccn_to_org` MAX per NPI
then MAX per org (facility.py 500-516, peer_percentiles.py 86-95) -> broadcast to NPI.

---

## A. Scheme wiring table (all 22 schemes in `DEFAULT_SCHEME_WEIGHTS`, scheme_subscores.py 54-197)

| scheme | input (weight) | producer (file:line) | export block | grain | norm | notes |
|---|---|---|---|---|---|---|
| single_service_mill | concentration (1.0) | leads file (refine_layer2_v3) / frozen: asof_v3.py 105-150 | base frame; `_apply_asof_freeze` 1058-1068 | NPI | PT | |
| | code_concentration_hhi (0.8) | partb.py 102 | `_try("partb")` 1575 | NPI | PP | listed in RAW_AGGREGATE_COLS 157 yet trainable (see F-16) |
| payment_outlier | payment_intensity (1.0) | leads / asof_v3 | base | NPI | PT | |
| | allowed_per_bene (0.8) | partb.py 97 | partb | NPI | PP | RAW_AGGREGATE inconsistency |
| overutilization | service_intensity (1.0) | leads / asof_v3 | base | NPI | PT | |
| | services_per_bene (0.8) | partb.py 95 | partb | NPI | PP | RAW_AGGREGATE inconsistency |
| specialty_mismatch | specialty_mismatch (0.5) | leads / asof_v3 | base | NPI | PT | institutional damping x0.5 (scheme_subscores 45-48) |
| | billing_taxonomy_mismatch (0.5) | billing_specialty.implied_specialty, via `_billing_lm_from_parquet` 1191-1195 | billing_lm 2895-2906 (`--with-analytics`) | NPI | PP | |
| | clinical_implausibility (0.3) | plausibility.plausibility_percentiles 272-281 | growth/plausibility 1944-1958 (`--with-analytics`) | ORG | PT | already a global percentile |
| | local_volume_implausibility (0.2) | plausibility.local_denominator_plausibility 242-269 | **NO CALLER** | - | - | **DEAD INPUT** (F-5) |
| rapid_ramp | temporal (0.6) | leads / asof_v3 | base | NPI | PT | churn damping x0.5 for institutional+ownership_turnover>0 (49, 276-282) |
| | growth_level_shift (0.8), new_code_burst (0.5) | growth.py 159 (percentiles) | growth 1934-1943 | ORG | PT | |
| ownership_integrity | within_2_hops_of_exclusion (0.6), shell_score (0.5), related_party_density(_norm) (0.15) | graph_features.py 374-386 | org_graph_features 455-476 | ORG | PT (rpd -> /25 norm, 218-232) | subscore is LEAKAGE_ADJACENT (export 114) and LABEL_ADJACENT_NET (network_ab 56-60): **never in the production scorer**; assessability gate 230-252 / 563-569 |
| | ownership_turnover (0.3) | owners_vintage / ownership_churn.py 74-91 | ownership_churn 2106-2174 | ORG | PT | `GRAPH_FEATURES` lists it but org_graph_features.parquet never carries it (graph_features.py 374-386), so it arrives only via the org-grain frame |
| | nh_owner_count (0.15), nh_owner_concentration (0.1) | facility.compute_nh_ownership -> facility_peer_percentiles | facility/nh_ownership 2247-2256 | CCN | PP(x2) | |
| upcoding | em_high_level_share (0.7), em_level_mean (0.3) | partb.py 105 | partb | NPI | PP | |
| impossible_day | bene_per_day_p95 (0.6) | **none in src/** | - | - | - | **DEAD INPUT** (F-5) |
| | time_minutes_per_day (0.4) | sector_schemes.py 97 (only with preclean/hcpcs_time/hcpcs_minutes.csv) | sector_schemes 2714-2729 | NPI | PP | |
| | sv_peak_lines_month (0.5) | servicing_grain.py 90-101 | servicing 2934-2965 | NPI | PP | |
| | mue_violation_share (0.8) | mue.py 163 | mue 2781-2804 | NPI | PP | |
| pharma_kickback | op_payment_utilization_corr (0.7) | openpayments.kickback_co_occurrence 205 | kickback 1724-1736 | NPI | PP | |
| | op_payment_concentration (0.3) | openpayments.py 89 | `_try("open_payments")` 1583 | NPI | PP | |
| | px_paid_mfr_share (0.5), px_post_payment_lift (0.5) | medicaid_ndc.medicaid_kickback 512-529 | medicaid_ndc 1811-1831 (needs preclean/fda_ndc + Open Payments) | NPI | PP | |
| nemt_fraud | nemt_paid_share (0.5), nemt_lines_per_patient (0.5) | sector_schemes.py | sector_schemes | NPI | PP | |
| behavioral_health | bh_paid_share (0.4), bh_lines_per_patient (0.6) | sector_schemes.py | sector_schemes | NPI | PP | |
| drug_outlier | controlled_substance_share (0.4), high_cost_drug_share (0.4) | partd.py | `_try("partd")` 1577 | NPI | PP | |
| | drug_spread_anomaly (0.3) | nadac.py 94 (share) | nadac org-grain 2018-2031 (needs processed/ndc_claims.parquet) | ORG | PP | |
| | rx_spread_proxy (0.3), rx_spread_top_share (0.2) | medicaid_ndc.py 319-321 | medicaid_ndc | NPI | PP | |
| | drug_markup_anomaly (0.4) | analytics/drug_markup.py 84 (0..1) | drug_markup 2731-2749 | NPI | PP | |
| dme_ring | dme_high_cost_item_share (0.4) | dmepos.py | `_try("dmepos")` 1579 | NPI | PP | subscore_dme_ring is LEAKAGE_ADJACENT (export 133): never in the production scorer |
| | dme_ordering_md_concentration (0.4) | **none** (dmepos.py 12 says it needs supplier x MD pairs) | - | - | - | **DEAD INPUT** (documented dormant) |
| | ineligible_referral_share (0.4) | order_referring.py 121 | order_referring org-grain 2033-2048 (needs processed/referred_claims.parquet) | ORG | PP | |
| | dme_ineligible_referred_dollars (0.4) | order_referring.referrer_ineligible_dme | order_referring_referrer 2556-2576 | NPI | PP | LEAKAGE_ADJACENT (131-132); reads a CURRENT-DAY O&R file even when frozen (F-12) |
| worthless_services | pbj_understaffing (0.7), pbj_weekend_understaffing (0.4) | facility.compute_pbj_metrics / compute_pbj_weekend_gap -> facility_peer_percentiles | facility/pbj 2196-2216 (`_asof_files`) | CCN | PP(x2) | **all inputs org/CCN broadcast** |
| | deficiency_severity_weighted (0.5), deficiency_count (0.2) | facility.compute_deficiency_counts 460 | facility/deficiencies 2269-2282 | CCN | PP(x2) | |
| | capacity_mismatch (0.6) | export `_capacity_mismatch` 216-227 (org volume / pos_bed_count) | build_provider_matrix 532-535 | ORG | PP | never appears in sources_audit (no log line; F-19) |
| | nh_fine_total (0.2), nh_penalty_count (0.15) | facility.compute_nh_penalties | facility/nh_penalties 2257-2268 | CCN | PP(x2) | |
| hospice_ineligibility | hospice_live_discharge_rate (1.0) + 6 panel measures | facility.compute_hospice_metrics / compute_hospice_panel | facility/hospice 2217-2246 | CCN | PP(x2) | **all inputs CCN broadcast** |
| saturation_fraud | market_saturation_index (1.0) | saturation.py 128-144 (already pct) | saturation 2070-2104 | ORG | PP(x2) | **all org** |
| pill_mill | opioid_claim_share (0.6), opioid_long_acting_share (0.4) | opioid.py | `_try("opioid")` 1581 | NPI | PP | |
| | px_opioid_paid_share (0.5), px_opioid_la_share (0.3), rx_opioid_paid_share (0.3) | medicaid_ndc.py 421-453 (needs NADAC reference for `opioid_ndc_flags`, 1795-1808) | medicaid_ndc | NPI | PP | |
| contract_pharmacy | contract_pharmacy_concentration (1.0) | hrsa_340b.py 187 | hrsa_340b 2050-2068 | ORG | PP | **all org**; institutional damping x0.5 |
| invalid_identity | billing_after_deactivation (0.6) | nppes_deactivation.billing_after_deactivation | nppes_deactivation 1977-1993 | ORG | RAW | both inputs LEAKAGE_HARD (106) and feed the label (576-600); subscore LEAKAGE_ADJACENT (124): **never trains**; **all org** |
| | billing_after_death (0.6) | death_master.billing_after_death | death_master 1995-2016 | ORG | RAW | |
| cost_report_fraud | hcris_cost_anomaly (1.0) | hcris.py 137 | hcris 2292-2304 | CCN | PP | **all CCN**; no as-of edition filter (F-8) |
| facility_code_billing | facility_code_share (1.0) | code_rings.py 279 | facility_code 2751-2776 | NPI (individuals) | PP | |
| rendering_identity | sv_max_orgs_same_month (0.6), sv_peak_lines_month (0.5), sv_n_billing_orgs (0.3), sv_paid_per_patient_month (0.3), sv_lines_per_active_month (0.3) | servicing_grain.py 44-49 | servicing 2934-2965 | NPI | PP | |

Schemes whose inputs are ALL org/CCN-grain broadcast (every member NPI of an org gets the
org's value, which is the label-smear shape the 2026-10 review flagged): ownership_integrity,
worthless_services, hospice_ineligibility, saturation_fraud, contract_pharmacy,
invalid_identity, cost_report_fraud (7 of 22). Mixed: specialty_mismatch (plausibility),
rapid_ramp (growth), drug_outlier (drug_spread_anomaly), dme_ring (ineligible_referral_share).

Adapter columns consumed by NO scheme are still trainable through `adapter_numeric`
(export 687-690: every numeric column a source recorded), so there are no training
orphans among adapter numerics. Columns that are produced and reach NEITHER a scheme
nor the trainable set: everything in `RAW_AGGREGATE_COLS` (154-168), string columns
(`clinical_implausibility_driver`, `mcd_primary_state`, `sv_first/last_month`),
`co_location_cluster_size` / `betweenness` are trainable raw but no scheme lists them,
the `__peer_fallback` flags from `one_sided_percentiles` (discarded at 311), and
`anomaly_score_v3` / `v3_not_scored_reason` / `v3_peer_basis` from asof_v3 (not merged,
1062).

---

## B. Export blocks: run condition and whether a skip is RECORDED in `sources_audit`

`sources_audit` = every `    [name] ...` line that goes through `audit` (`_SourceAudit`,
1375-1431): a line starting `skipped` is a skip; anything else is "used"; **used always
wins over a later skip** (1421-1422). Lines printed with `print` are console-only.

| block | export lines | runs when | `--skip-sources` honored? | skip recorded? |
|---|---|---|---|---|
| entity_graph (org_graph_features) | 2470-2473, 455-476 | always on real runs | n/a | used line; constant-column drop is `print` only (473) |
| partb / partd / dmepos / opioid / open_payments | `_try` 1522-1569, 1575-1584 | file under preclean/<name>/ within the vintage cap | yes (1523) | yes, every path (no file / post-cutoff vintages / layout / exception / empty adapter) |
| <name>_trend | 1604-1717 | adjacent prior-year file; not `--no-trends` | via parent | missing-year and layout skips yes; `--no-trends` leaves NO line (silent, benign) |
| kickback | 1724-1736 | OP + Part D raws both present | yes (1724) | `--skip-sources` yes; **either raw missing = no line at all** (1730 has no else) |
| nadac_namegrain | 1741-1762 | NADAC edition (`_asof_pick`) + Part D raw | yes (1741) but **`return frames`** (1743) | nadac missing AND pd_raw None = no line (1759 `elif`); **see F-1** |
| medicaid_ndc | 1771-1853 | processed/ndc_fact(.asof).parquet; builds the as-of fact when raw present | yes (1771) | yes |
| medicaid_enrollment | 1862-1896 | processed/medicaid_enrollment_segments.parquet | yes (1862) | yes |
| growth / plausibility | 1933-1961 | `--with-analytics` + spending (+ provider_dim for plausibility) | yes (1934, 1944) | yes |
| nppes_deactivation | 1977-1993 | deactivation file + spending | only at `_emit` (1964) after the compute | "reading X" (1985) is a USED line logged BEFORE `_emit`, so a later skip is masked (F-18) |
| death_master | 1995-2016 | DMF + provider_dim + spending | at `_emit` | used line at 2010 after `_emit` masks a skip (F-18) |
| nadac (org, drug_spread_anomaly) | 2018-2031 | NADAC edition + processed/ndc_claims.parquet | at `_emit` | used line 2027 masks a skip (F-18) |
| order_referring (org) | 2033-2048 | O&R edition (`_asof_pick`) + processed/referred_claims.parquet | at `_emit` | used line 2044 masks a skip (F-18) |
| hrsa_340b | 2050-2068 | OPAIS file + org_nodes | at `_emit` | used line 2062 masks a skip (F-18) |
| saturation | 2070-2104 | county file + org_nodes | yes (2071) | yes; "reference period" (2080) logged before `_emit` |
| ownership_churn | 2106-2174 | vintage-diff editions (as-of chain when frozen) else >=2 snapshots | at `_emit` | yes |
| facility (pbj / hospice / nh_ownership / nh_penalties / deficiencies) | 2177-2291 | ccn_to_npi + files; `_asof_files` / `_asof_newest` / `pick_edition` | yes (2177) | per-metric skips yes; "[facility/pbj] read X" (2203) marks `facility` AND `pbj` used before the roll-up `_emit` (2289) |
| hcris | 2292-2304 | any preclean/hcris/*.csv | at `_emit` | **no files = no line** (2296 has no else); **no as-of filter** (F-8) |
| pos | 2310-2335 | preclean/pos/**/*.csv via `_asof_files` | at `_emit` | **no files = no line** (2315 has no else); post-cutoff-only is logged by `_asof_files` |
| order_referring_referrer | 2556-2576 | dmepos frame + O&R file (`_first_existing`, not `_asof_pick`) | no | **no O&R file = no line** |
| graph_embeddings | 2577-2588 | graph/node_embeddings.parquet | no (cache only via `_have`) | yes; but `to_provider_grain` returning empty (2581) = no line |
| asof freeze + asof_v3 | 2592-2615 | `--asof-cutoff` + processed/spending_fact.parquet | n/a | yes (2611-2615); "[asof] skipped" (2617) is `print` only |
| tenure backfill (in-time) | 2619-2645 | not frozen; leads lack tenure cols | n/a | `print` only |
| nucc | 1072-1091 | preclean/nucc/nucc_taxonomy.csv + provider_dim | no | yes |
| widened label | 1121-1156, 2663 | graph/nodes/exclusion_nodes.parquet | n/a | "[label]" used line; missing file = None with NO line |
| smoking_gun_timeline | 2670-2709 | spending + (exclusion dates or deactivation file) | no | yes; **never builds a `death` cut** (F-13) |
| sector_schemes | 2714-2729 | spending | **no** (F-2) | yes |
| drug_markup | 2731-2749 | spending | **no** | yes |
| facility_code | 2751-2776 | spending (+ provider_dim) | **no** | yes |
| mue | 2781-2804 | preclean/reference/mue/*.csv + spending | **no** | yes |
| doj_case | 2807-2851 | `--case-db` AND org_nodes | n/a | no `--case-db` = recorded skip (2813); **`--case-db` with org_nodes None = silent** (2817) |
| address | 2855-2883 | provider_dim | yes (2855) | yes |
| graph_velocity | 2884-2894 | >=2 snapshots under `root/feature_snapshots` (ignores `--snapshot-dir`) | **no** | yes |
| billing_lm | 2895-2906 | `--with-analytics` + provider_dim + spending | **no** (help text 2416-2419 names it as an example) | yes |
| servicing | 2934-2965 | not `--no-servicing-grain`; spending + provider_dim | **no**; `_have` not checked (a cached frame is recomputed) | yes |
| capacity_mismatch | 532-535 | pos_bed_count + service_volume + org_node_id on the matrix | n/a | **never logged** (sources_used only) |
| identifiers (provider_dim) | 2989-3010 | provider_dim | n/a | `print` only |

---

## C. The trainable set and the training frame

**Export, `build_provider_matrix` (314-935):**
- `raw_feature_cols` = V3 + GRAPH + ANALYTICS + `adapter_present` + PROVIDER_STATS +
  `adapter_numeric` (691-694), + struct motifs / pillar-4 / `extra_clean` / embeddings
  (727-728), + every `_yoy` / `_slope` column (747-749), minus leakage_hard, label_metadata,
  evidence counts (750-752). `peerpct_cols` 696, `subscore_cols` 695, `embedding_cols` 704.
- `dropped_constant` (762-771): raw + peerpct columns with <=1 distinct non-null value are
  removed from the trainable lists (kept in the parquet). **Subscore columns are not
  screened** (763 iterates raw + peerpct only).
- Classes: `_classify_all_columns` (171-213) gives every column exactly one of
  trainable_raw / trainable_peerpct / trainable_subscore / leakage_hard / leakage_adjacent /
  label / label_metadata / identifier / group / assessability / evidence_count /
  diagnostic / raw_aggregate / operational / unclassified (839-853).
- Leakage: `LEAKAGE_HARD` 98-106 (+ provider_on_exclusion, 734); `LEAKAGE_ADJACENT`
  111-133 + `graph_fraud_proximity(_delta)` (706, 725-726, 837-838).
  `n_trainable_after_fence` 881.
- Vintage: `_SRC_VINTAGE` 783-799; unlisted sources (entity_graph unless the substrate is
  frozen, address, facility, pos, hcris, saturation, ownership_churn, capacity, leads,
  order_referring_referrer, graph_velocity, hrsa_340b, nadac, order_referring) default to
  current_state (812-815).
- **The gate** (905-934): `feature_partition.partition_features` (feature_partition.py
  87-160) resolves each trainable column to a source via `sources_used`, `__peerpct`
  inheritance, `_yoy/_slope/_delta` inheritance, subscore -> scheme_coverage inheritance,
  and the `_DERIVED_CORE` / prefix list (103-129); base-frame columns no block recorded
  are filed under `leads` (916-919); any residue RAISES (926-931).
- Frozen path: `_apply_asof_freeze` (1023-1069) replaces PROVIDER_STATS from
  `asof_provider_stats` (1038-1046), drops `_ASOF_UNCOMPUTABLE` (1017-1020: the five v3
  scores, anomaly_score, anomaly_pct, n_concept_signals, signals_tripped,
  log_max_single_month, provider_hhi, growth_slope) at 1047-1048, then (unless
  `--no-asof-v3`) restores the five `V3_SCORE_COLS` + log_max_single_month +
  n_concept_signals from `asof_v3_scores` (1058-1068). Still absent after the freeze:
  anomaly_score, anomaly_pct, signals_tripped, provider_hhi, growth_slope. provider_hhi and
  growth_slope are plain leads columns that an IN-TIME run files under `leads` and trains
  on (917-919), so the in-time and frozen feature sets differ by those two (low; noted
  in F-20).

**Scorer, `company_list.oof_scores` (333-442):**
- feature columns = `full_feature_list(partition_features(manifest))` minus
  `LABEL_ADJACENT_NET` (network_ab 56-60) minus manifest leakage_adjacent minus
  leakage_hard (360-364); then a single-column AUC > 0.985 leak screen (368-384).
- X = native NaN (390), no fill. y = `_training_label` (251-277): policy `all` = the
  in-matrix column; a tier policy RAISES without exclusion nodes (256-262) and with < 50
  positives (273-276); `tier_label` (207-248) re-derives the label from the exclusion
  nodes' statutes (`fca_relevance.classify_series` + `tier_mask`) dated <= `<cutoff>-01`
  (226), ORs `doj_case` always, `charged_case` under a policy keeping `charged`,
  `hcf_commercial` under one keeping `commercial` (233-247).
- ambiguous rows (in-matrix positive, policy negative) masked from training folds
  (397-402); `broadcast == mask` adds broadcast-only positives (412-417).
- sample weight = `label_weight` clipped [0,1], None when absent (280-292).
- groups = `group_id` from the matrix (403-404); `fold_assignments` = StratifiedGroupKFold
  -> GroupKFold -> KFold (295-306); seed bagging (426-429); collapse guard (434-441).
- `training_policy(manifest)` (88-94) returns `group_policy` but no caller reads it (the
  export already baked `group_id`), informational.
- The adjacent fence is NOT applied in `source_ablation` (full_feature_list only,
  source_ablation.py 183, 362) and is deliberately inverted in `network_ab._trainable`
  (74-85 includes leakage_adjacent); `label_ablation` (117-120) and `signal_ranking` (52)
  do fence. Matches the open item in docs/MODEL_REVIEW_2026-10.

---

## D. The label join

- `_widened_label_from_graph` (1121-1156): EVERY NPI-matched exclusion node (any tier)
  -> `provider_on_exclusion = 1`, `exclusion_label_sources` = `;`-joined
  `_label_source(excl_type)` (1097-1118: opensanctions / medicare_revocation / preclusion /
  sam / state_termination / state_board / medicaid_enrollment / doj_case / research_<src>
  / leie). Per-source counts + the one-source-dominates WARNING (1149-1155).
- Merge into the matrix 373-381 (fan-out asserted). The in-matrix label is therefore
  policy `all`; the FCA tier is NOT a matrix column for exclusion rows, it is re-derived
  at train time by `company_list.tier_label` from `--exclusion-nodes`. The only tier
  columns in the matrix are the case-row metadata `fca_strict_tier` / `intervened` /
  `qui_tam` (389-392).
- Case labels 386-453: `hard_npis` = rows whose basis is not `affiliated_individual` and
  whose source is not docket/charged/commercial (413-424) flip the label and get the
  `doj_case` tag (443-451); docket_case / charged_case / hcf_commercial are tagged in
  `exclusion_label_sources` WITHOUT flipping (426-442); `label_weight` / `case_fanout`
  metadata (398-404); `sources_used["doj_case"]` (452-453) is what `run_issues`
  (`label:doj_case:missing_case_labels`) looks for.
- Section-E widening (576-600): `deact_paid_after` / `death_paid_after` (own event) or the
  org flag restricted to single-NPI orgs; tags `deactivation` / `death`.
- `label` = provider_on_exclusion else provider_on_leie (732-733); `label_metadata`
  737-745; `group_id` 651-663 (case_cluster policy via `company_list.case_cluster_groups`);
  `assessable` 669-673 = any `__peerpct` non-null AND (net_paid > 0 or service_volume > 0).
- `assessable` consumers: accuracy_diagnostics 567/803, learning_loop 62, org_find 107,
  inbound_lookup 149-169, feeds/nppes_verify 72-73. **Not** `company_list.oof_scores` /
  `rollup` and not `pursuit_dossier` (grep): unassessable rows are trained on and ranked
  exactly like the rest (F-15).

---

## E. Runners

### E.1 Every flag the runners pass vs the CLI argparse

Verified present (file:line of the `add_argument`): entity_graph `--input --out --asof
--asof-nppes --max-embed-nodes --light-walks-above` (__main__.py 284-308);
provider_features_export every flag at 2341-2427; prospective_label `--exclusion-nodes
--cutoff --fca-tier --case-db --graph-dir --defendant-npis --horizon-months --fca-case-only
--qui-tam-only --affiliated --out` (245-283); network_ab `--matrix --manifest --future-label
--cutoff --out` (569-593); scheme_triage `--run-dir` (441); pursuit_dossier `--run-dir
--priority-list --order --label-tier --exclusion-nodes --operator-book --dispatch`
(975-1034); accuracy_diagnostics `--run-dir --case-db --graph-dir --defendant-npis
--label-tier --exclusion-nodes --horizon-months --repeats --qui-tam-ruler --list-name
--score-source --broadcast-rows --out` (1164-1198); results_digest `--dir --out` (182-186);
run_registry `--data-root --run-dir` (1401-1405); learning_loop `--run-dir --data-root
--label-tier --exclusion-nodes --prev-run-dir` (481-490); run_issues `--run-dir --data-root
--prev-run-dir` (420-422); label_expansion `merge-inputs --case-db --cia-cases
--defendant-npis --cia-npis --out-cases --out-npis` (474-480); vintage_inspect `--data-root`
(483); scheme_health `--matrix --manifest --future-label --baseline --out --json-out`
(316-325); signal_ranking (85-87); label_enrichment `--run-dir --future-label --spending`
(337-339); label_audit `--exclusion-nodes --matrix --case-db --cia --org-nodes --cutoff
--out` (947-955); data_refresh `--run-dir --data-root --reminders` (195-197); label_ablation
`--splits --boot` (297-302); source_ablation `--oof --per-group --splits --cutoff` (567-590);
questions_readout (253-255). Makefile targets: feature_store (129-131), ccn_npi_crosswalk
(296-302), opensanctions (126-129), ownership_snapshot (83-89), model_c (143-151),
warn_monitor (267-272), enforcement.fetch (250-256), docket_monitor (199-202), sam_api
(141-142), feeds.freshness (main at 53), scheme_discovery (230), model_a (253-278).

**Mismatches:**
1. `run_frozen_night.sh:159` passes `accuracy_diagnostics --label-tier hard+commercial`;
   `accuracy_diagnostics.py:1176` choices = `["all","hard","hard+charged","hard+weak"]`.
   argparse exits 2 -> `fail 8`. Fires whenever `grep -q "hcf_commercial" "$CASES"` is
   true (:158), i.e. as soon as the commercial lane's 76 loaded rows are merged into
   `doj_cases_v3_plus_cia.csv` (the operator loaded them on 2026-10-20). **The next full
   VM run stops at step 8.**
2. Same choice list omits `hard+commercial` / `hard+charged+commercial` (TIER_POLICIES,
   fca_relevance.py 324-325) in `pursuit_dossier.py:1012` and `learning_loop.py:488`, so
   `TRAIN_TIER=hard+commercial` can never be the runner default even though
   `company_list.set_label_policy` accepts it (51-57).

### E.2 Step order vs data dependencies (run_frozen_night.sh)

0 merge-inputs -> 1 as-of graph (reused unless `--rebuild-graph`) -> 2 export on the as-of
graph (its exclusion nodes are the training label; `--defendant-npis` from step 0) -> 3
forward rulers on the CURRENT graph `$DATA/graph` -> 4 network_ab (needs step 3) -> 5
triage -> 6 priority list (writes `provider_oof_scores.parquet` + `training_label_policy.json`,
pursuit_dossier 784-789) -> 7/8 diagnostics (read the step-6 lists; FCA + qui tam rulers
rebuilt in-process from `--case-db`, accuracy_diagnostics 1247-1261) -> 9 dossier -> 10
registry (reads `accuracy_diagnostics__pursuit/__large/__pmax.json`, run_registry
243-278, matching `output_suffix`, accuracy_diagnostics 366-368 / 1279-1290) -> 11
learning loop (reads step-6 OOF) -> 12 measure -> 13 ledger. The chain is dependency-correct.

Gaps:
- The CURRENT-DAY graph that the forward rulers read (:116-128) is never rebuilt by any
  runner; `--rebuild-graph` rebuilds only `$GRAPH_ASOF` (:93-100). New exclusion sources
  (`processed/exclusions_name_resolved.parquet`, `exclusions_medicaid_enrollment.parquet`)
  reach the TRAINING label on the next as-of rebuild but reach the FORWARD ruler only after
  a separate `make graph` (F-9).
- `future_bans_after_<cut>__aff36.csv` (:121-125) is consumed by nothing that grades
  (`_discover_rulers` 1150-1159 looks for `__hard` / `__hard_weak` only; learning_loop 448
  reads `__fca36`; only `questions_readout.py:195` lists it). Built artifact, no reader.
- `regrade_from.sh:57-61` copies the base and `__fca36` rulers only; `__aff36` /
  `__quitam36` are absent in the re-graded folder unless `REBUILD_RULERS=1`. Grading is
  unaffected (rebuilt in-process); the readout reports them missing.
- `regrade_from.sh` step labels run "0/7, 0b/7, 6/12 ... 11/13, 12/13, 13/13" (:56, 67,
  86-116): cosmetic. `ls -la "$RUN" | head -30` (:64) under `set -euo pipefail` is a
  SIGPIPE exit if the listing ever exceeds 30 lines (today it is ~20).
- `measure_run.sh:43-44`: `--baseline "$RUN/scheme_health.json" --json-out
  "$RUN/scheme_health.json"` reads the SAME run's export-time json as the baseline, so the
  "regression vs the last run" compares the run to itself. The export-side
  `write_health` (scheme_health 287-299) also defaults to `out_dir/scheme_health.json`,
  which never exists in a fresh `frozen_<cut>_<tag>` folder: the cross-run regression alarm
  never fires on the VM naming scheme.

### E.3 Stop guards and the strings they grep

| guard | greps | what the code prints | match? |
|---|---|---|---|
| run_frozen_night.sh:107 `grep -q "NO DOJ case labels" SOURCES_REPORT.md` | "NO DOJ case labels" | export 2813-2816 `[doj_case] skipped: no --case-db given, the matrix carries NO DOJ case labels ...` -> `_SourceAudit` detail -> SOURCES_REPORT row 3196-3199 | yes. Blind when `--case-db` IS given but `org_nodes is None` (2817, no line) or `build_case_labels` returns 0 rows (386: no source recorded, no string) - caught only by the step-13 ledger |
| :108 `grep -q "\[asof_v3\] restored" night_run.log` | `[asof_v3] restored` | export 1068 via `log=print` (2608) -> stdout -> `tee` (:61) | yes |
| :109-113 `grep -E "^\| \`$s\` \| .*skipped"` for servicing, mue, sector_schemes, medicaid_enrollment, billing_lm, graph_embeddings | a `| \`name\` | ... skipped` row | `_write_sources_report` 3196-3199: `| \`{source}\` | ⏭️ skipped | detail |` | yes. Blind when the source has NO row: medicaid_enrollment / medicaid_ndc after the `nadac_namegrain` early return (F-1); graph_embeddings when `to_provider_grain` returns empty (2581); and the used-wins rule (1421) |
| :69 free-RAM >= 24 GB; :70-71 case DB + resolver file exist; :97 as-of NPPES edition exists | - | - | fine |
| regrade_from.sh:42-44 matrix / exclusion nodes / folder exists | - | - | fine; no post-step guards (none needed) |

### E.4 Makefile targets vs the standing rules

- `provider-features` (78-86): the export line carries no `--case-db` / `--defendant-npis`
  (the RULE in CLAUDE.md "every training runner's export line carries --case-db +
  --defendant-npis"); the chained `pursuit-dossier` runs without `--priority-list` or
  `--label-tier` (label policy `all`). `run_issues` would file
  `label:doj_case:missing_case_labels` as a FAIL on that manifest.
- `frozen-package` (126-151): graph build has no `--asof-nppes` (export 2921-2924 prints
  the "frozen run on an UNFROZEN address substrate" WARNING and continues: the v6 lesson),
  the export has no `--case-db` / `--defendant-npis` / `--no-servicing-rows` / `--seeds`,
  `prospective_label` has no `--fca-tier` (ruler `all`), output folder `frozen_<cut>`
  without a tag. The target is the v5-era package and is stale relative to
  `ops/vm/run_frozen_night.sh`.

---

## F. Findings, ranked

Severity: **HIGH** = features or labels silently lost / a run that will stop;
**MEDIUM** = wrong or leaky input reaches the matrix, or a documented control does not
work; **LOW** = inconsistency, cosmetic, or an unread artifact.

### HIGH

**F-1. `--skip-sources nadac_namegrain` (or a resume-cache restore of it) silently drops
medicaid_ndc and medicaid_enrollment, and the runner guard cannot see it.**
`provider_features_export.py:1741-1743`: `if "nadac_namegrain" in skip: log(...); return frames`
returns from `_run_npi_adapters` before the medicaid_ndc (1764-1853) and medicaid_enrollment
(1855-1896) blocks, the same class of bug fixed for `kickback` at 1719-1736. The realistic
trigger is the resume cache: `main` unions every cached frame name into `skip` (2519), so a
run that crashed after checkpointing `nadac_namegrain` restarts with both July-2026 sources
never computed, no `[medicaid_ndc]` / `[medicaid_enrollment]` audit line (no SOURCES_REPORT
row), and `run_frozen_night.sh:109-113` passes because it greps for a `skipped` ROW, not for
a missing one. Fix: replace `return frames` with an `else:` around the nadac_namegrain body
(as 1724-1736 does), and make the runner guard fail when a required source has no row at all.

**F-2. `run_frozen_night.sh:159` will stop every run whose merged case file carries
`hcf_commercial`.** `accuracy_diagnostics --label-tier hard+commercial` is rejected by the
choice list at `accuracy_diagnostics.py:1176` (`["all","hard","hard+charged","hard+weak"]`)
-> argparse exit 2 -> `fail 8`. The commercial lane loaded 76 rows on 2026-10-20, so the
condition at :158 is true for the next merge-inputs. Fix: set `choices=list(TIER_POLICIES)`
(fca_relevance.py 324) in accuracy_diagnostics 1176, pursuit_dossier 1012 and learning_loop
488.

### MEDIUM

**F-3. `--skip-sources` is ignored by nine main()-level blocks**, including the one the
help text uses as its example. Only `address` checks `skip` in `main` (2855); sector_schemes
(2714), drug_markup (2731), facility_code (2751), mue (2781), smoking_gun_timeline (2670),
graph_velocity (2884), billing_lm (2895), servicing (2934) and graph_embeddings (2577) run
regardless, while the flag's help (2416-2419) says `'kickback,billing_lm'`. The memory
safety valve the operator was told to use for the five v10 casualties does not exist for
four of them. Fix: wrap each block in `if name in skip: audit("[name] skipped: --skip-sources")`.

**F-4. `local_volume_implausibility` is a dead scheme input and a dead ANALYTICS
pass-through.** `scheme_subscores.py:76` weights it 0.2 in specialty_mismatch,
`ANALYTICS_FEATURES` (export 72-73) and `feature_partition._DERIVED_CORE` (76) list it, but
its producer `plausibility.local_denominator_plausibility` (plausibility.py 242-269) has no
caller anywhere in `src/` (the export's plausibility block keeps only
`clinical_implausibility`, 1953) and `census_population.county_population` /
`zip_to_county` are imported only by `ingest_cms/__init__.py` and the saturation block.
Fix: either wire the org-volume-by-FIPS builder into the plausibility block (county pop +
the HUD crosswalk the saturation block already loads, 2085-2095) or remove the input so the
scheme's weight denominator stops advertising a signal that never arrives.

**F-5. Two more dead scheme inputs with no producer in `src/`:** `bene_per_day_p95`
(scheme_subscores 104, impossible_day, 0.6) and `dme_ordering_md_concentration` (138,
dme_ring, 0.4). Both are documented as "dormant by data" (99-103, 136-137) but neither has
a stub adapter or a preflight registry row, so no SOURCES_REPORT line will ever say why they
are missing; scheme_health reports them as missing_inputs every run. Fix: remove them or add
the producing adapter contract (the sector_schemes `time_minutes_per_day` path, 97, is the
model).

**F-6. The HCRIS block pools every file on disk in a frozen run.** `_run_ccn_grain`
2295: `hcris_files = sorted((pc / "hcris").glob("*.csv"))` with no `_asof_files`, unlike
pbj (2197), pos (2314) and the NH files (2248-2273) fixed in bundle 324. A 2023 HCRIS
extract sits inside a 2021-12 freeze exactly as the facility editions did for eight runs.
Fix: `hcris_files = _asof_files(sorted(...), asof_cutoff, log, "hcris")`.

**F-7. The referrer-grain O&R check reads a current-day eligibility edition under a
freeze.** 2563-2564 uses `_first_existing(preclean / "order_referring", "order_referring.csv",
"*.csv")` while the org-grain block at 2036-2037 uses `_asof_pick(..., asof_cutoff=asof_cutoff)`.
`dme_ineligible_referrer` / `dme_ineligible_referred_dollars` are already LEAKAGE_ADJACENT
(export 130-132), so the production scorer is fenced, but network_ab (74-85) and
source_ablation (183) train on them under the forward ruler with post-cutoff eligibility
bans. Fix: use `_asof_pick(preclean / "order_referring", "order_referring.csv",
asof_cutoff=args.asof_cutoff)` at 2563.

**F-8. The forward ruler's graph is never rebuilt by any runner.** `run_frozen_night.sh:116-128`
and `regrade_from.sh:70-80` read `$DATA/graph/nodes/exclusion_nodes.parquet`;
`--rebuild-graph` (:93-100) rebuilds only `graph_asof_<cut>_full`. The entity-graph build
is the only place `processed/exclusions_*.parquet` is merged (entity_graph/__main__.py
60-63), so the 843 name-resolved rows and the mcd_enroll rows enter the TRAINING label
(as-of rebuild) but not the FORWARD ruler until someone runs `make graph` by hand. The two
rulers then disagree on the label universe. Fix: a `--rebuild-current-graph` step 1b in the
runner (or document `make graph` as a pre-requisite of `--rebuild-graph`).

**F-9. Facility, saturation and NH features are percentiles of percentiles.**
`facility_peer_percentiles` (facility.py 465-497) and `saturation.py:128-144` already emit
one-sided percentiles in facility / county peer cells; `rollup_ccn_to_org` takes the MAX
per NPI and per org (facility.py 513-516, peer_percentiles.py 95); the export then
re-ranks those values across TAXONOMY peers at NPI grain (538-552) because they are in
`ADAPTER_FEATURE_COLS`. The subscore input is thus "taxonomy-percentile of the org's max
facility-percentile", undocumented, and inside ownership_integrity it is mixed with raw
pass-through graph features (0/1 flags). Fix: add these columns to a pass-through set
(like `ANALYTICS_FEATURES`) or document the double rank in the dictionary.

**F-10. Five org-grain blocks log a "used" line before or regardless of `_emit`, so a
skipped source shows as used.** `_SourceAudit` lets used supersede skipped (1421-1422):
nppes_deactivation 1985 ("reading X"), death_master 2010, nadac 2027, order_referring 2044,
hrsa_340b 2062 all log a non-`skipped:` line around an `_emit` that may have said "adapter
returned no rows" (1973-1975); facility/pbj 2203 ("read X") marks `facility` used before the
roll-up at 2289 can fail. The SOURCES_REPORT (and the runner guard) then read "used" for a
source that produced nothing. Fix: route those informational lines through `print` or
prefix them with `[name/info]`, keeping `[name]` for the `_emit` verdict only.

**F-11. Silent no-row paths in the audit.** hcris (2296, no else), pos (2315, no else),
order_referring_referrer (2563-2576, no else when the O&R file is absent), kickback (1730,
no else when either raw is None), nadac_namegrain (1759 only `elif nadac_p`),
doj_case with `--case-db` but `org_nodes is None` (2817), graph_embeddings when
`to_provider_grain` is empty (2581), widened label when exclusion_nodes.parquet is missing
(1127-1131 return None, no line), capacity_mismatch (532-535, never logged). None of these
produce a SOURCES_REPORT row. Fix: one `else: audit("[name] skipped: <reason>")` per path.

**F-12. The Makefile's two export targets violate the `--case-db` rule and the v6 lesson.**
`provider-features` (78-86) and `frozen-package` (126-151) carry no `--case-db` /
`--defendant-npis` (a manifest without `doj_case` is a ledger FAIL by the written rule),
and `frozen-package` builds the as-of graph without `--asof-nppes` (unfrozen address layer,
export 2921-2924 only WARNs) and the ruler without `--fca-tier`. Fix: mirror
`ops/vm/run_frozen_night.sh` step 1-3 flags, or mark both targets deprecated in `make help`.

**F-13. The smoking-gun timeline never builds the `death` cut.** 2673-2693 build `excl`
(graph) and `deact` (file) cuts only, although `LEAKAGE_HARD` names `death_*` (102-103) and
the section-E widening prefers `death_paid_after` (588, 591-592). With the DMF present the
death widening therefore always falls to the org-broadcast flag restricted to single-NPI
orgs (594). Fix: add a `("death", matches[match_confidence == "high"][["npi", "dod"]])` cut
from `death_master.match_deceased_providers` inside the same try block.

**F-14. `assessable` is written but not enforced by the scorer.** The export computes it
(669-673) and the manifest promises "thin-evidence providers aren't force-ranked", but
`company_list.oof_scores` / `rollup` (333-524) and `pursuit_dossier` never read it; only
diagnostics, learning_loop, org_find, inbound_lookup and nppes_verify do. Unassessable rows
(no peer percentile at all) are trained on as negatives and ranked. Fix: either mask
`assessable == 0` rows from training folds (like `ambiguous`, 397-402) or drop the claim
from the manifest's `training_policy`.

### LOW

**F-15. `RAW_AGGREGATE_COLS` contradicts the trainable lists.** 154-168 say
`code_concentration_hhi`, `services_per_bene`, `allowed_per_bene`, `dme_code_concentration`,
`brand_generic_cost_ratio` are "kept for provenance, never fed", but the first three are
scheme inputs and enter `raw_feature_cols` via `adapter_present` (692-693) plus a
`__peerpct`, and the `_yoy` of the last two enter via `yoy_cols` (747-749) while their bases
do not. `_classify_all_columns` files them trainable (typed buckets win, 182-186). Fix:
drop the three from `RAW_AGGREGATE_COLS` and decide the yoy-of-an-aggregate rule once.

**F-16. `dropped_constant` does not screen subscores.** 763 iterates raw + peerpct only;
an all-NaN or constant `subscore_*` (the v9-v11 `subscore_invalid_identity`) stays in
`subscore_cols` and in the trainable set. Fix: include `subscore_cols` in the loop.

**F-17. `_SRC_VINTAGE` gaps.** `capacity`, `ownership_churn`, `leads`,
`order_referring_referrer`, `graph_velocity`, `hrsa_340b`, `nadac`, `order_referring` are
unlisted (783-799) and default to current_state. For `leads` the base-frame billing columns
(`provider_hhi`, `growth_slope`, any extra leads column) are mis-classed; for
`ownership_churn` the frozen chain IS point-in-time (2122-2131) but is classed
current_state. Only the feature_vintage report is affected.

**F-18. In-time vs frozen trainable sets differ by `provider_hhi` / `growth_slope`.**
Both are leads columns filed under `leads` in-time (917-919) and dropped frozen (1020,
1047); `asof_v3` does not recompute them (keep_v3 1062). Fix: add them to the as-of
restore or to `DIAGNOSTIC_COLS` so neither run trains on them.

**F-19. `graph_velocity` ignores `--snapshot-dir`.** 2886 reads `root / "feature_snapshots"`
while `--snapshot` writes to `args.snapshot_dir` (2381, 3108). A non-default store never
feeds `graph_emb_drift`.

**F-20. Cache-restored frames are re-logged as skipped, and `servicing` ignores the cache.**
The `else` branches at 2586-2588, 2705-2707, 2882-2883, 2903-2904 emit "skipped" for a
frame `_have` already reported as restored (masked by used-wins, harmless); the servicing
block (2934-2965) never calls `_have("servicing")`, so a checkpointed servicing frame is
recomputed on resume.

**F-21. `scheme_health` baseline wiring.** `measure_run.sh:43-44` passes the same file as
`--baseline` and `--json-out`; `write_health` (scheme_health 294) defaults to
`out_dir/scheme_health.json`, which a fresh `frozen_<cut>_<tag>` folder never has. The
cross-run regression alarm is inert on the VM. Fix: `--baseline "$PREV/scheme_health.json"`
in measure_run.sh (PREV is already known to the night runner, :24).

**F-22. `__aff36` ruler has no reader.** Built at run_frozen_night.sh:121-125 and
regrade_from.sh:73-77, read only by questions_readout.py:195 (a presence line).
`accuracy_diagnostics` has no `--affiliated` flag and `_discover_rulers` (1150-1159) never
discovers the `__fca36` / `__aff36` / `__quitam36` names. Fix: either grade it
(`--forward-label aff=<path>`) or stop building it.

**F-23. `regrade_from.sh` copies two of the four rulers** (:57-61: base + `__fca36`; not
`__aff36` / `__quitam36`) and its step labels mix /7, /12 and /13; `ls | head -30` (:64)
under `set -euo pipefail` is a latent SIGPIPE exit.

**F-24. `training_policy()["group_policy"]` is dead.** company_list 88-94 returns it;
no caller uses it (the export bakes `group_id`, 651-663). Informational.

**F-25. Two scheme inputs reach the matrix twice-removed from their file's vintage.**
`nppes_deactivation` (1980) and `death_master` (2000) read the cumulative current-day files
on a frozen run; both compare billing months to event dates so post-cutoff events produce
zero after as-of billing, and both columns are LEAKAGE_HARD. Fine for training; the
`SOURCES_REPORT` does not say the edition is post-cutoff. Cosmetic.

---

## G. What is wired correctly (so the next reader does not re-audit it)

- Every flag in `run_frozen_night.sh`, `regrade_from.sh`, `measure_run.sh` and the Makefile
  exists in its CLI except F-2.
- The three runner stop guards match the strings the code prints (E.3), subject to the
  no-row blind spots in F-1 / F-11.
- The trainability gate (export 905-934) is enforced: every trainable column must resolve
  to a source or the export raises; `leads` catches base-frame columns.
- The frozen path restores the five v3 scores (1058-1068) and the runner refuses a run
  without the `[asof_v3] restored` line (:108).
- `company_list.oof_scores` fences leakage_hard + leakage_adjacent + LABEL_ADJACENT_NET,
  masks ambiguous and (under `mask`) broadcast-only positives, weights by `label_weight`,
  groups by `group_id`, bags seeds, and refuses a collapsed ranking.
- `_run_org_grain_adapters._emit` honors `--skip-sources` for every org/CCN block (1964-1968).
- PBJ / POS / NH editions are as-of filtered (`_asof_files` / `_asof_newest`); NADAC and
  O&R org-grain editions use `_asof_pick`; owners editions chain pre-cutoff when frozen.
- The step order in both VM runners satisfies every file dependency (E.2).
