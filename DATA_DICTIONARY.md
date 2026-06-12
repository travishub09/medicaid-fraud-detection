# Data Dictionary (attempt_2)

Every dataset the attempt_2 pipeline reads and produces, in flow order. **Grain** = what one row
represents. All identifier columns (NPI, PAC ID, enrollment ID, ZIP) are kept as **strings**.

> The original attempt_1 tables (`provider_monthly`, `fraud_features`, `provider_rankings`, …) are
> superseded; see git history if needed.

---

## 1. Raw inputs (`~/Desktop/data/preclean/`)

### `Spending.csv` — Medicaid provider spending (the fact table)
**Grain:** one row per billing NPI × servicing NPI × HCPCS × month.

| Field | Description |
|-------|-------------|
| `BILLING_PROVIDER_NPI_NUM` | NPI that was paid — **the only key used to attribute dollars** |
| `SERVICING_PROVIDER_NPI_NUM` | NPI that performed the service — attribute only, never used for attribution |
| `HCPCS_CODE` | Procedure code billed |
| `CLAIM_FROM_MONTH` | Month of service (`YYYY-MM`) |
| `TOTAL_PATIENTS` | Distinct patients **within that row** (never summed as distinct patients) |
| `TOTAL_CLAIM_LINES` | Claim lines |
| `TOTAL_PAID` | Dollars paid |

### `NPPES.csv` — national provider registry
**Grain:** one row per NPI (330 columns; only ~12 read): NPI, entity type, legal/last/first name,
primary taxonomy, practice address, deactivation/reactivation dates.

### `PECOS.csv` — Medicare enrollment base
**Grain:** one row per NPI × enrollment. Carries `NPI`, `PECOS_ASCT_CNTL_ID` (PAC ID),
`ENRLMT_ID`, provider type, state, names — the **crosswalk** that ties owners/facilities to NPIs.

### `Caught.csv` — OIG LEIE exclusion list
**Grain:** one row per exclusion record (a provider may have several). Columns include `NPI`,
`EXCLTYPE`, `EXCLDATE`, `REINDATE`, `WAIVERDATE`, `WVRSTATE`, and name fields.

### `owners/*.csv` — CMS "All-Owners" (FQHC / HHA / Hospice / Hospital / Nursing)
**Grain:** one row per facility × owner. Facilities keyed by `ENROLLMENT ID` / `ASSOCIATE ID`;
owners by `ASSOCIATE ID - OWNER`, with owner type/role, name, address, and ownership-type flags.

---

## 2. Integration outputs (`integrate.py` → `~/Desktop/data/integrated/`)

### `provider_dim.parquet` — the provider dimension
**Grain:** exactly one row per canonical NPI (asserted unique).

| Field | Description |
|-------|-------------|
| `npi` | Canonical 10-digit NPI (Luhn-validated) |
| `entity_type` | 1 = individual, 2 = organization |
| `org_legal_name`, `provider_name`, `name_key` | Legal/display name + normalized key |
| `taxonomy_code` | Primary taxonomy (peer-group key) |
| `addr_line1/city/state/zip`, `addr_key` | Practice address + normalized key |
| `deactivation_date`, `reactivation_date`, `is_active` | NPI lifecycle |
| `provider_type_desc`, `pecos_state` | Enriched many-to-one from PECOS |

### `spending_fact.parquet` — cleaned, attributed spending
**Grain:** one row per spending record (row count and `SUM(total_paid)` identical to raw — proof of
zero fan-out / zero dropped dollars). Adds `billing_npi` (canonical), `provider_matched`, the
`provider_dim` attributes, and `active_at_claim`.

### Other integration tables
| Table | Grain / purpose |
|-------|-----------------|
| `npi_xwalk.parquet` | NPI ↔ PAC ↔ enrollment (deduped; never joined to spending) |
| `pecos_provider.parquet` | one row per NPI (deterministic enrollment collapse) |
| `owner_edges.parquet` | facility → owner edges; facility NPI resolved via enrollment id |
| `exclusions.parquet` | cleaned LEIE: `npi`, `excl_date`, `reinstate_date`, `excl_type`, `name_key` |
| `facility_owner_exclusion_flags.parquet` | per facility NPI: excluded-owner counts by tier (high/probable) |
| `npi_quarantine.parquet` | identifiers that failed Luhn/format (source + raw value + reason) |

---

## 3. Corruption audit (`audit_corruption.py`)

| Table | Grain / purpose |
|-------|-----------------|
| `spending_corruption_quarantine.parquet` | rows with `TOTAL_PAID > $500M` (physically impossible) + `reason`, `hcpcs_malformed` |
| `spending_aggregate_billing.parquet` | legitimate non-provider/aggregate billing (blank-NPI, plausible) |

Rule: `$500M`/row cleanly separates the legitimate distribution (matched max ≈ $119M, largest
aggregate ≈ $470M) from corruption (smallest corrupt ≈ $505M). Real total after quarantine ≈ $1.42 T.

---

## 4. Feature base (`features.py` → `~/Desktop/data/features/`)

### `spending_provider_base.parquet`
**Grain:** the clean base — `spending_fact` where `provider_matched & total_paid ≤ $500M`
(≈230 M rows / $1.10 T; reconciled to the audit). All features read **only** this.

### `provider_features.parquet` — the feature table
**Grain:** exactly one row per billing NPI (617,062). Selected columns:

| Group | Fields |
|-------|--------|
| Volume / payment | `gross_paid`, `net_paid`, `reversal_amount`, `reversal_ratio`, `total_claim_lines`, `service_volume` (= Σ`TOTAL_PATIENTS`, a volume **proxy** — not distinct patients), `n_distinct_hcpcs`, `n_active_months`, `tenure_months` |
| Ratios | `paid_per_claim_line`, `paid_per_patient_instance`, `lines_per_patient_instance` |
| Concentration | `top_hcpcs_paid_share`, `hcpcs_hhi` |
| Peer-relative | robust z + percentile for net_paid / paid-per-claim / lines-per-patient / paid-per-patient, vs taxonomy and taxonomy×state, + `peer_group_size_*`, `peer_group_too_small_*` |
| Temporal (mature months only) | `yoy_growth_net_paid`, `month_to_month_volatility`, `max_single_month_net_paid`, `new_biller_surge_onset` |
| Specialty proxy | `rare_for_taxonomy_paid_share` |
| Linkage | `provider_on_leie`, `facility_has_excluded_owner_high/_probable`, `excluded_owner_role` |
| Carried dims | `entity_type`, `primary_taxonomy`, `practice_state`, `org_legal_name` |

`provider_month.parquet` / `provider_hcpcs.parquet` are the supporting intermediates.

---

## 5. Detection outputs (`detect.py`, `refine_layer2*.py` → `~/Desktop/data/detection/`)

### `fraud_leads.parquet` / `fraud_leads_v2.parquet` / `fraud_leads_v3.parquet`
**Grain:** one row per billing NPI. `v3` is the latest. Columns:

| Field | Description |
|-------|-------------|
| `priority_tier` / `priority_rank` | `1_L1_billed_after_exclusion` > `2_L1_implausible_rate` > `3_L1_excluded_after_billing` > `4_L2_anomaly` > `5_L3_probable_owner` > `6_none` |
| `provider_on_leie`, `billed_after_exclusion`, `excluded_after_billing`, `rule_reasons` | Layer-1 flags + reasons |
| `anomaly_score_v3`, `n_concept_signals`, `anomaly_contributing_concepts` | Layer-2 (v3): mean concept percentile; count of concepts ≥ in-group P99; which concepts fired |
| `iforest_score_secondary` | Isolation Forest cross-check (secondary) |
| `peer_basis`, `not_scored`, `not_scored_reason` | how/whether scored (`low_volume_unreliable`, `degenerate_zero_gross_paid`, `missing_taxonomy`, `peer_group_too_small`) |
| `layer3_probable_owner`, `facility_excluded_owner_n_probable`, `excluded_owner_role` | Layer-3 ownership track |
| `entity_type`, `primary_taxonomy`, `practice_state`, `net_paid`, `gross_paid` | context |

Layer-2 concepts (v3): `concentration`, `payment_intensity`, `service_intensity`,
`specialty_mismatch`, `temporal` — correlated features collapse to one so a single fact can't
double-count.

### `layer1_candidate_cases.parquet` (`verify_layer1.py`)
**Grain:** one row per LEIE-matched billing NPI (578). `disposition` (`QUALIFIED / AMBIGUOUS /
DISQUALIFIED / CONTEXT_ONLY`), `paid_after`, `n_clean_after_months`, `claim_lines_after`,
`top_hcpcs_after`, `excl_months`, `reindates`, `waiver_states`, `excltype` + `fraud_related_excltype`,
`name_match`, NPPES + LEIE names.

---

## 6. Final CSVs (`export_final_leads.py`)

### `final_leads_over_10m.csv`
**Grain:** one row per NPI; every lead (tiers 1–5) with billing ≥ threshold, sorted tier then dollars.

| Field | Notes |
|-------|-------|
| `npi` | written as TEXT (quoted) |
| `provider_name` | display name (orgs + individuals) |
| `entity_type`, `primary_taxonomy`, `taxonomy_description`, `state`, `priority_tier` | |
| `total_billing_size_proxy_not_case_value` | = `net_paid` — a **size proxy, not a case value** |
| Layer-1 | `provider_on_leie`, `billed_after_exclusion`, `layer1_disposition`, `paid_after`, `excl_date`, `excl_type` |
| Layer-2 | `anomaly_score_v3`, `n_concept_signals`, `contributing_concepts` |
| Layer-3 | `probable_excluded_owner` |
| `reasons` | one-line summary of why the lead surfaced |

### `high_precision_excluded_leads.csv`
Same columns; the billed-while-excluded leads (tier 1 + any `QUALIFIED` disposition), kept regardless
of dollar.

---

## 7. Company grain (`company_rollup.py`, `company_lead_tracker.py`)

### `npi_to_company_map.parquet` (`company_rollup.py`)
**Grain:** one row per billing NPI → `company_id` (+ per-NPI `net_paid`, `merge_basis_raw`). The audit
trail that makes the rollup droppable back to claim level.

### `company_rollup.parquet` / `company_leads_over_10m.csv` (`company_rollup.py`)
**Grain:** one row per company (all 538,232). NPIs linked by PAC > shared-owner > exact-name
(`merge_basis` + `merge_confidence`). Aggregates: `company_net_paid`, `company_gross_paid`,
`npi_count`, `states`, `primary_taxonomies`, `entity_types`, `npi_list`, and aggregated signals
(`any_provider_on_leie`, `any_billed_after_exclusion`, `any_probable_excluded_owner`,
`max_anomaly_score_v3`, `best_priority_tier`, `flagged`). The CSV is the ≥$10M flagged subset (+ a
`reasons` column).

### `company_lead_tracker.csv` / `.parquet` (`company_lead_tracker.py`)
**Grain:** one row per company lead, **companies with `company_net_paid` ≥ $10 M** (all signal tiers),
ranked direct → company-anomaly → ownership. Unlike the rollup's aggregated "max anomaly across NPIs",
this carries a **real company-vs-company v3 anomaly** (`rate_features` + `score_concepts` applied at
company grain).

| Field | Notes |
|-------|-------|
| `company_id`, `company_name`, `npi_count`, `states` | `company_id`/`npi_list` written as TEXT |
| `company_total_billing_size_proxy_not_case_value` | = `company_net_paid` — **size proxy, not case value** |
| `priority_tier` | `1_billed_after_exclusion` > `2_on_leie` > `3_company_anomaly` > `4_probable_owner` |
| direct | `any_provider_on_leie`, `any_billed_after_exclusion`, `paid_after` |
| company anomaly | `company_anomaly_score`, `n_concept_signals`, `contributing_concepts` |
| `fragmentation_signal` | company is a Layer-2 lead but NO constituent NPI was (≥2 NPIs) — possible split-billing |
| ownership | `any_probable_excluded_owner` |
| `merge_basis`, `merge_confidence`, `reasons`, `npi_list` | linkage provenance + why-flagged + constituents |

### `company_tracker_direct_under_10m.csv` (`company_lead_tracker.py`)
Same columns; the sub-$10 M billed-while-excluded / on-LEIE direct leads, preserved (highest precision,
naturally below the threshold).

### `company_leads_clean.csv` / `triage_priority.csv` / `probable_owner_backlog.csv` (`finalize_tracker.py`)
Cleanup of the tracker for agent triage (no score changes). **Grain:** one row per company lead,
triage columns first: `rank`, `company_name` (resolved — never a raw `npi:` id), `tier_label`,
`specialty` (dominant-taxonomy description), `states`, `company_total_billing_size_proxy_not_case_value`,
`reasons` (complete sentence), `review_flags` (`low_merge_confidence` / `possible_same_operator` /
`genuine_fragmentation` / `name_unresolved`), `npi_count`, `company_anomaly_score`, `n_concept_signals`,
`fragmentation_signal` (corrected: genuine distributed billing only — max single NPI < 50% of company),
the `any_*` direct/ownership flags, `paid_after`, `related_entities` (likely same-operator companies),
`merge_basis`, `merge_confidence`, `npi_list`.
- `company_leads_clean.csv` — all leads.
- `triage_priority.csv` — Direct + Company-anomaly tiers only (the queue agents start on).
- `probable_owner_backlog.csv` — the probable-owner tier alone (noisy name-match backlog).

## 8. LEIE backtest (`src/backtest/`)

Held-out validation of the Layer-2 company anomaly score against the OIG LEIE. Outputs are written
inside `src/backtest/`. The large intermediate `company_scores_full.parquet` (exact company-grain
score for every company; produced by `score_universe.py`) is gitignored — regenerate it before
running `backtest_leie.py`.

### Fraud-relevant LEIE exclusion types (the label definition)
A LEIE row is a **fraud-relevant positive** only for conviction / fraud / kickback exclusion types:
`1128a1`, `1128a2`, `1128a3`, `1128b1`, `1128b2`, `1128b3`, `1128b7`. **Dropped** (not fraud
convictions): license-revocation `1128b4`; program/loan/derivative `1128b5`,`1128b6`,`1128b8`,
`1128b14`,…; civil monetary penalty `1128Aa`; peer-review/quality `1156`,`1160`; agreement breaches
`BRCH SA`/`BRCH CIA`. Only ~10% of LEIE rows carry a usable `NPI` (matched high-confidence); rows
without an NPI are matched by business name + state (lower confidence). `EXCLDATE` is `YYYYMMDD`.

### `backtest_results.csv` (`backtest_leie.py`)
**Grain:** one row per anomaly lead = company with `company_anomaly_score ≥ 0.70` (on-LEIE
companies INCLUDED — this is the score backtest, not the tier).
- `company_name`, `states` — company identity (strings).
- `company_anomaly_score` — exact Layer-2 company-grain score (LEIE-independent).
- `npi_count` — distinct NPIs parsed from the company's `npi_list`.
- `hit` — 1 if the company carries a fraud-relevant LEIE exclusion, else 0.
- `matched_npi` — the constituent NPI(s) that matched a fraud-relevant LEIE NPI (`;`-joined).
- `exclusion_type` — matched LEIE `EXCLTYPE`(s) (`;`-joined).
- `exclusion_date` — earliest matched `EXCLDATE` (`YYYYMMDD`).
- `match_confidence` — `npi` (high) or `name` (name+state, noisier) or empty (no hit).
- `timing_bucket` — `before_2018` / `during_2018_2024` / `after_2024` vs the billing window.
- `score_decile` — 1–10 anomaly-score decile across the **full** scored universe.
- `size_band` — billing quartile `Q1`–`Q4` across the universe.

### `backtest_report.json` (`backtest_leie.py`)
Prose narrative + a `metrics` object. Sections: `methodology`, `data_sources`, `label_definition`,
`matching_approach`, `results`, `size_baseline_finding`, `timing_finding`, `disjointness_finding`,
`limitations`, `conclusion`. Key metrics: `base_rate`, `anomaly_top_decile_lift`,
`billing_top_decile_lift`, `anomaly_beats_size`, `within_size_top_decile_lift` (per billing
quartile), `after_2024_top_decile_lift`, `permutation_p_value`, `bootstrap_top_decile_lift_ci95`.

## 9. Entity-resolution graph (`src/entity_graph/` → `~/Desktop/data/graph/`)

The canonical graph built from the integration outputs. See `docs/platform/03-entity-resolution.md`.
Run: `python -m src.entity_graph --input ~/Desktop/data/processed --out ~/Desktop/data/graph`.

### Node tables (`nodes/`)
- **`provider_nodes.parquet`** — one row per NPI. `node_id` (`provider:<npi>`), `npi`,
  `entity_type`, `provider_name`, `org_legal_name`, `name_key`, `taxonomy_code`, `addr_key`,
  `addr_state`, `is_active`, `pac_id`, `enrollment_id`, `node_type`.
- **`org_nodes.parquet`** — one row per canonical organization. `org_node_id` (`org:<company_id>`),
  `company_id`, `n_constituent_npis`, `member_npis` (`;`-joined), `org_name`, `org_legal_name`,
  `aliases`, `addr_key`, `addr_state`, `n_states`, `primary_taxonomy`, `merge_basis`
  (`pac_id`/`shared_owner`/`name`/`single`), `merge_confidence` (`high`/`medium`/`low`/`single`).
- **`owner_nodes.parquet`** — one row per distinct owner. `node_id` (`owner:<key>`), `owner_key`,
  `owner_type` (I/O), `owner_display_name`, `owner_npi`, `is_private_equity`.
- **`exclusion_nodes.parquet`** — one row per LEIE record. `node_id` (`exclusion:<row>`), `npi`,
  `entity_name`, `name_key`, `excl_type`, `excl_date`, `reinstate_date`, `currently_active`.

### Edge tables (`edges/`) — `src_id`, `dst_id`, `edge_type` + attributes
- **`member_edges.parquet`** — `provider → org` (`member_of`), with `basis`.
- **`owned_by_edges.parquet`** — `org → owner` (`owned_by`): `pct_ownership`, `owner_role`,
  `association_date`, `is_private_equity`.
- **`excluded_in_edges.parquet`** — `provider/owner → exclusion` (`excluded_in`): `match_tier`
  (`exact`/`probable`), `excl_date`, `currently_active`.
- **`co_located_edges.parquet`** — `org ↔ org` (`co_located_with`): `addr_key`, `cluster_size`.

### `npi_to_org.parquet`
**Grain:** one row per NPI (the resolution crosswalk). `npi`, `org_company_id`, `org_node_id`,
`merge_basis_raw`. The audit trail proving every NPI resolves to exactly one organization.

### `org_graph_features.parquet`
**Grain:** one row per canonical organization. Feeds Model A.
- `excluded_party_distance` — graph hops to the nearest exclusion (`-1` = unreached).
- `within_2_hops_of_exclusion` — 1/0 integrity proximity flag.
- `related_party_density` — count of organizations sharing this org's owner(s).
- `co_location_cluster_size` — organizations sharing this org's address.
- `shell_score` — 0–1 heuristic (shared address + thin/name-only + exclusion proximity).
- `community_id` — Louvain (fallback greedy-modularity) community membership.
- `betweenness` — betweenness centrality (the orchestrator of a suspected ring).

### Ring detection (`rings/`)
- **`shared_address_shells.parquet`** — address clusters of ≥3 orgs: `addr_key`, `n_orgs`,
  `n_thin`, `org_node_ids`, `org_names`.
- **`common_owner_clusters.parquet`** — owners controlling ≥4 orgs: `owner_node_id`, `owner_name`,
  `n_orgs`, `excluded_in_network` (1 if an exclusion sits in the owner's network), `org_node_ids`.
- **`excluded_party_proximity.parquet`** — orgs within 2 hops of an exclusion: `org_node_id`,
  `org_name`, `hops_to_exclusion`.
- **`referral_rings.parquet`** — gated/empty until referral-pair data is ingested.

### `GRAPH_REPORT.md`
Table sizes + graph-feature highlights (orgs near exclusions, max related-party density, max
co-location cluster, count of high shell-score orgs).
