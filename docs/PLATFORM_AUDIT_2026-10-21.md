# PLATFORM AUDIT — 2026-10-21

The operator stopped v18 and asked for a full deep scan before anything moves forward:
the data files (right files, right dates, right fields), every intended feature wired
end to end, and an overall review of the codebase and architecture.

This document is the verdict and the fix plan. Four reviews feed it, each written
against the CODE with file:line citations and filed in full under
`docs/audit_2026-10-21/`:

| appendix | scope | length |
|---|---|---|
| `A_adapter_contracts.md` | every raw-file adapter: accepted headers, what happens when a field is missing, how the export finds the file, cutoff behaviour | 616 lines |
| `B_feature_wiring.md` | adapter output -> scheme subscore -> trainable set -> scorer; every export block's run / skip condition; the runners and their guards | 543 |
| `C_label_chain.md` | every path from a public record to a training positive, a forward-ruler positive or a strict-export row; temporal correctness; the four label gates | 208 |
| `D_architecture.md` | package map, hard-rule compliance, duplication, error handling, tests, configuration, guardrails, the two model tracks | 119 |

The data half (the files themselves) cannot be audited from the repository: no data
exists here (rule 7). It is audited by the new scanner `src/data_audit.py`, which
grades every file on disk against the adapter's OWN column map and the export's OWN
vintage picker, run on the VM by `ops/vm/data_audit.sh` (section 2). The code half is
complete below; the verdicts in section 1 that depend on the scan are marked PENDING.

Every finding in this document was re-checked in the source by the author before it
was written down; the appendix line numbers are from the working tree of 2026-10-21
(commit 3cdf3a0 plus this bundle).

## 0. The verdict in one paragraph

The platform's spine is sound: identifiers stay strings, the stage-one conservation
asserts hold, the trainability gate raises on an unmapped column, the frozen path
restores the as-of v3 scores and refuses a run without them, the scorer fences hard
and adjacent leakage, masks ambiguous rows, weights broadcast rows and bags seeds, and
the runner step order satisfies every file dependency. What is NOT sound is the edge:
the places where a missing or renamed column, a post-cutoff edition, or a skipped
block is turned into a default value or a silent gap instead of a stop. Twelve findings
are rated HIGH. Two of them would have ended v18 on their own (a CLI choice list the
runner no longer satisfies; a month-end freeze on the case label that lets one case be
both a training and a forward positive). Three are silent-blank fields that feed a
scheme with a default of zero when a column is absent, which the SOURCES_REPORT then
records as "used". None of them is hard to fix; all of them change what a run means,
so the fixes land as v19 semantics under a new pre-registration, never as a silent
swap.

## 1. HIGH findings (the ones that change what a run means)

Severity: HIGH = wrong numbers or a dead run with no message; MEDIUM = a gap that the
reports hide or a rule the code does not keep; LOW = hygiene. The letter.number keys
point into the appendices.

| # | finding | where | consequence | fix |
|---|---|---|---|---|
| H1 | The VM runner passes `--label-tier hard+commercial` to `accuracy_diagnostics`; the CLI's choice list rejects it. The condition that enables that step (hcf_commercial rows in the case file) became true on 2026-10-20 when the lanes loaded. | C.F3; `ops/vm/run_frozen_night.sh:159`, `accuracy_diagnostics.py:1176`, `pursuit_dossier.py:1012`, `learning_loop.py:488`, `consensus.py:498` | v18 would have failed at step 8 after the full retrain. | `choices=list(fca_relevance.TIER_POLICIES)` in the four CLIs. |
| H2 | The frozen DOJ case label is boxed at month END (`+ MonthEnd(0)`); every other freeze (as-of graph, tier label, exclusion ruler, case ruler, billing) is month START. | C.F1; `case_labels.py:482` | A case announced on day 2..31 of the cutoff month is a TRAINING positive and a FORWARD positive at once. The headline hits@k drops it (the in-matrix `case_ids` guard), but network_ab, source_ablation, label_ablation, scheme_health (`fca_auc` -> prune_candidate) and learning_loop read only the ruler flag. | `cut_ts = Timestamp(cutoff[:7] + "-01")`, planted test with a mid-month announcement; re-register. |
| H3 | `--skip-sources nadac_namegrain` (or a resume-cache restore of that frame after a crash) `return`s from the per-NPI adapter loop before medicaid_ndc and medicaid_enrollment run. | B.F-1; `provider_features_export.py:1741-1743` | Both July 2026 sources silently absent: no audit line, no SOURCES_REPORT row, and the runner guard greps for a `skipped` row, not a missing one. The same bug class fixed for `kickback` on 2026-10-10. | `else:` block; the guard fails on a MISSING row too. |
| H4 | Order and Referring: when the `DME` column is absent the adapter fills 0 for every NPI, so every referrer is "ineligible" and `ineligible_referral_share` = 1.0 for every org. No log line. | A.F-03; `order_referring.py:50-53, 69-79, 110-113` | dme_ring scored on a constant. | Raise on a missing eligibility column; `dropped_constant` catches the rest. |
| H5 | `paid_after_enrollment_end` runs on the FULL spending fact, never the as-of fact, and its `mcd_paid_after_end*` columns are trainable and filed as point-in-time. | A.F-05; `provider_features_export.py:1873-1875` | Post-cutoff paid months inside a frozen feature. | Pass `asof_spend_p` when frozen; add to the as-of planted test. |
| H6 | `load_state_exclusions` writes `state_term_<st>:<action>`; the ruler's `state_term` prefix branch catches it first and reads an empty body, so every research-harvested state row tiers WEAK "no reason recorded". The intended `mfcu_<st>` form is never produced (the CLI passes no `--source`). | C.F2; `research_evidence.py:197-224, 557-562`, `fca_relevance.py:250-252` | The state-case research rows (the Manus state lanes) never train under `hard`. | Write `state_term:<ST>:<action>` (one convention) and pin a tier test on the loader's output. |
| H7 | HCRIS pools every file on disk in a frozen run (`glob("*.csv")`, no `_asof_files`), and `readiness_audit` claims a fiscal-year as-of that does not exist. | B.F-6, A.F-19; `provider_features_export.py:2295` | cost_report_fraud reads 2023-2025 cost reports inside the 2021-12 freeze (the same class as the facility / POS leak fixed in bundle 324). | `_asof_files(..., "hcris")`. |
| H8 | Three scheme inputs have NO producer anywhere in `src/`: `bene_per_day_p95` (impossible_day 0.6), `dme_ordering_md_concentration` (dme_ring 0.4), `local_volume_implausibility` (specialty_mismatch 0.2; the function exists, nothing calls it). | B.F-4, B.F-5; `scheme_subscores.py:76, 104, 138` | Dead weight in three schemes; the scheme's effective weights are not the documented ones. | Remove the inputs or wire the producers; the catalog in RUNBOOK_TRAVIS must match. |
| H9 | Nine joins in the export enforce many-to-one by silently de-duplicating the right side instead of asserting; `_broadcast_org_to_npi` promises "never fans out" with no uniqueness check; 5 of 119 merges carry `validate=`. | D.F2; `provider_features_export.py:263, 349, 378, 393, 458, 486, 515, 525, 1064` | Rule 3 ("joins are many-to-one by construction and asserted") is kept by dedup, not by assertion: a duplicated adapter row is dropped, not quarantined, not counted. | One `join_m1()` that asserts, quarantines duplicates, merges with `validate="m:1"`. |
| H10 | Stage one reads the raw spending file with `ignore_errors=true`, and the first conservation count starts AFTER the loss. | D.F4; `clean_data.py:221-222, 271-272, 508-514`; `docgraph.py:92` | Malformed raw lines vanish with no count and no quarantine (rule 4). | `store_rejects=true`, assert `parquet + rejected == raw`, write rejects to quarantine. |
| H11 | The Makefile's export lines carry no `--case-db` / `--defendant-npis` (the v8/v9 HOLD cause) and `frozen-package` builds the as-of graph without `--asof-nppes` (the v6 lesson). | D.F5, B.F-12; `Makefile:79-84, 126-151` | A `make` run reproduces the v8 and v6 failures; three runner families drift (Makefile, ops/vm, the untracked .bat series). | `ops/vm/run_frozen_night.sh` is the single training runner; `make frozen-package` calls it; delete or commit the .bat family. |
| H12 | `src/backtest/backtest_results.csv` (31,270 rows, 53 with matched NPIs and exclusion types) is tracked in git against `.gitignore` and rule 7. | D.F1; commit a5cb75a | Derived data in the repository. | `git rm --cached`, regenerate under the data root, a CI check that refuses tracked `*.csv` / `*.parquet` outside `docs/reminders.json`. |

## 2. The data files (the VM scan of 2026-10-21 14:15 UTC, bundle 344 code)

The scan read 214 files across 44 sources on the VM's data root, counted rows, dated
every file by name and by the adapter's own date column, and placed each under the
2021-12 freeze. Six of its 24 flagged sources were scanner artifacts (a sibling file
the adapter never reads, a multi-sheet workbook with a banner, a banner-row zip, a
two-column reference table, a JSON feed, the keyword-resolved WARN postings); bundle
346 fixes those classes and a re-scan is one line. The rest are real, ranked:

**D1. The VM is running on the June/July sync.** Spending, NPPES and PECOS are dated
2026-06-17 on the VM; NPPES enumeration dates stop at 2026-06-06 (the June edition;
the laptop holds October); the LEIE is the September file (84,001 rows; the laptop
holds October's 84,094); the stage-one outputs (provider_dim, spending_fact,
exclusions.parquet) are from 2026-07-23. Nothing the operator fetched on 2026-10-20
is on the VM: no PBJ 2021 quarters (only 2023Q4), no POS Q4 2021 (only Q4 2023), no
Nursing Home December-2021 archive (only Nov2023 and Feb2022 hospice), no NADAC
2021 (2023 and 2026 only), and the hold folders still carry the three undated
duplicates deleted on the laptop. Every frozen run to date read this tree. Fix: a
preclean-only pull before v19 (`sync_data.sh down-preclean`, bundle 346; a FULL pull
would roll back the label work the lanes wrote on the VM under processed/ and
enforcement/), then stage one on the October NPPES / LEIE, then the current-day graph.

**D2. Under 2021-12, on the VM as scanned, ten sources read NOTHING:** nadac,
saturation, order_referring, hospice, hcris, pos, docgraph, owners, nh_ownership (and
owners_prior / nppes_history by design). After D1's pull, pos, pbj, hospice (the
December-2021 archive) and nadac 2021 come back. Order and Referring (editions from
2023-06), All Owners (from 2022-09), HCRIS (only FY2023 files on disk), saturation
(2026-08 only) and DocGraph have no pre-2022 edition: those are exactly the sources
the 2023-12 freeze exists to test (section 7 of the design note addendum).

**D3. DocGraph is a 2022 file inside the 2021-12 freeze.** `DocGraph_Hop_Teaming_2022.csv`
(210,313,975 rows) is the only referral edition, the graph loader applies no cutoff
to it, and `refers_to_edges` (2,000,000 rows) sits in every as-of graph: the referral
rings and referral features in the frozen runs describe 2022 patient sharing, one
year after the freeze. Batch 1 adds the edition date to the as-of graph build (drop
`refers_to` edges whose edition is after the cutoff) and the 2023-12 freeze reads it
correctly.

**D4. Silent-blank fields confirmed on disk** (the audit's class): the HHA cost-report
file has no administrative-cost column, so `admin_cost_share` is 0 for every home
health agency; the Medicaid NDC file carries no units, days supply or provider
state (drug spread runs on the per-claim proxy, as designed); the enrollment file
carries no reason column (the status description carries the cause, as handled).
The hospice file's combined "Measure Date Range" is handled by the adapter (scanner
artifact). POS lab and iQIES files have no beds by nature.

**D5. The newest deactivation report was never read.** The folder holds the June
report twice and the September report once; the export took the alphabetically
first zip (June). `nppes_deactivation.edition_of` now dates a report from its name
(the CMS MMDDYY stamp), the zip's inner name or the banner cell, and the export reads
the newest with a vintage-ledger row (bundle 346).

**D6. Placeholder dates in the inputs that the as-of filters see:** enrollment
segment starts at 0001-01-01; owner association dates at 1800-01-01 and 2099-12-31
(the SNF, FQHC and Hospital files). A 2099 association date is "unknown", and the
as-of owner-edge filter treats it as future (dropped from every frozen graph) rather
than unknown (kept). Batch 1 reads 1800 / 2099 / 0001 as unknown everywhere the
temporal filters run.

**D7. The current-day graph predates the label loads.** `graph/` is dated 2026-10-08;
the name-resolved exclusions, the re-run state lists and the lane rows landed on
2026-10-20. The forward rulers read this graph, so no run graded since the 20th saw
them (appendix B, F-8). The runner now needs a current-graph rebuild step (batch 1).

**D8. Smaller findings:** `processed/exclusions.parquet` holds 83,464 rows from a
84,001-row LEIE (537 rows fall out of `build_exclusions`; batch 1 names where);
`processed/ccn_to_npi_from_nppes.parquet` is empty (the NPPES type-06 fallback found
nothing); the Open Payments 2019 / 2020 / 2022 files carry payment dates to 2026-06
(the file is cumulative by program year, the row filter handles it); the CMS MUE
files carry a line feed inside the first header cell (handled); the three WARN
postings for California and Rhode Island are banner-row workbooks the adapter probes
sheet by sheet (handled); `nppes/npi_deactivation.csv` is the unreadable orphan from
the September inventory (delete it).

**Procurement gaps that stay gaps** (no pre-2022 edition exists): Order and
Referring, All Owners, DocGraph (the last public Shared Patient Patterns file is
2015), HCRIS FY2021 (downloadable: the FY2021 flattened files are on the CMS cost
report page and belong under preclean/hcris with the year in the name), Market
Saturation 2021 (the CMS archive has it; reminder pre_cutoff_editions_2021).

**The re-scan after the preclean pull (2026-10-21, bundle 347 code, 190 GB preclean):**
the October LEIE and NPPES are on the VM; under 2021-12 the freeze now reads POS Q4 2021
(both files), PBJ 2021Q3 and Q4, the nursing-home November 2021 ownership / penalties /
citations, NADAC 2021 and the Feb-2022 hospice release (its measure periods end before
the freeze); the six scanner artifacts are gone. Still nothing before 2022: Order and
Referring, All Owners, HCRIS, saturation, DocGraph (the 2023-12 freeze's work). Two
overshoots the first re-scan flagged were the scanner's, not the data's: the POS lab
file's certification-expiry dates run to 2027 by nature, and the overshoot check now
reads only the adapter's own date column; the NADAC 2021 edition carries effective
dates into January 2022, which the adapter does not filter (batch 1 adds the row
filter). The scan now caches each file's date range by size and modification time, so
a re-scan is minutes instead of forty.

Verified correct on the VM: Spending.csv = 238,015,729 rows (2018-01 to 2024-12),
conserved exactly into spending_fact.parquet; every PUF year 2016-2024 present with
its contract columns; the Part D opioid summary separate from the by-drug file; the
state lists for seven states loaded (5,750 label rows); the 72-edition All-Owners
archive intact; the NDC fact (21.5M rows) and the enrollment segments (65.9M rows)
built; the as-of 2021-12 full graph with 10.3M embedded nodes.

## 3. The adapter contracts (appendix A)

The inventory covers 45 readers. Beyond the HIGH rows above:

- **Crash-class gaps** (no named assertion; a renamed column is a KeyError deep in the
  stage): the Spending headers are SQL literals and the alias table that exists for them
  is unused (A.F-01); NPPES / PECOS / LEIE / owners name columns (A.F-02).
- **Death Master File is structurally dead**: the DOB corroboration it requires reads
  `provider_dim.dob`, and NPPES carries no DOB, so every match is `low` and
  `billing_after_death` never fires (A.F-07). The LEIE DOB on exclusion nodes is the
  only DOB in the system; the check should match against it or be retired.
- **Case-sensitive resolvers** in `nucc_taxonomy` and `asof_nppes` (A.§4d, §5i): a
  lower-cased header silently skips NUCC (every peer percentile falls to the fallback
  ladder) or drops the as-of NPPES temporal filter.
- **Logging that the audit cannot see**: `openpayments.py` warns through `logging`,
  which `_SourceAudit` never captures; the source reads as used (A.F-11).
- **`.xlsx` is admitted by the PUF file picker but `_read_any` has no Excel reader**
  (A.F-31).
- **`--skip-sources facility` also skips hcris and pos** (A.§crash-class).

## 4. The feature wiring (appendix B)

The 22-scheme wiring table (B.§A) is the first complete map of input -> producer ->
export block -> grain -> normalization. Verified correct: every runner flag exists in
its CLI (except H1); the three stop guards match the printed strings; the gate raises
on any unmapped column; the frozen path restores the five v3 scores. Beyond the HIGH
rows:

- **Seven of 22 schemes are entirely org- or CCN-grain broadcast** (ownership_integrity,
  worthless_services, hospice_ineligibility, saturation_fraud, contract_pharmacy,
  invalid_identity, cost_report_fraud): every member NPI carries the org's number, the
  label-smear shape the 2026-10 review fixed for the deactivation flag only (B.§A).
- **Percentiles of percentiles**: facility, NH and saturation adapters already emit
  percentiles; `rollup_ccn_to_org` takes the MAX; the export re-ranks across taxonomy
  peers (B.F-9).
- **`--skip-sources` is ignored by nine blocks** (sector_schemes, drug_markup,
  facility_code, mue, smoking_gun_timeline, graph_velocity, billing_lm, servicing,
  graph_embeddings) while the help text cites one of them (B.F-3).
- **Silent no-row paths** (a source that produced nothing leaves no SOURCES_REPORT row,
  so the runner guard cannot see it): hcris, pos, order_referring_referrer, kickback,
  nadac_namegrain, doj_case when org_nodes is None, graph_embeddings, the widened label,
  capacity_mismatch (B.F-11). Five org blocks log a USED line before the skip decision
  (B.F-10).
- **The forward ruler's graph is never rebuilt by a runner**: `--rebuild-graph` rebuilds
  only the as-of graph; the current-day graph that the rulers read is the only merge
  point of the `exclusions_*.parquet` files, so name-resolved and mcd_enroll rows reach
  the training label but not the ruler until a manual `make graph` (B.F-8).
- **`assessable` is written to the manifest and never enforced** by the scorer or the
  dossier (B.F-14). **`smoking_gun_timeline` never builds the death cut** although the
  leakage list names it (B.F-13). **The affiliated ruler (`__aff36`) has no grader**
  (B.F-22). **`measure_run.sh` passes the run's own json as its baseline**, so the
  cross-run regression alarm is inert (B.F-21).

## 5. The label chain (appendix C)

The chain is drawn hop by hop (C.§B) with the freeze instant at each layer (C.§table).
Verified correct: the index-URL gate on every case path; `usable = 0` never labels;
dismissals quarantine charges; self-disclosures out; docket / charged / commercial never
flip the hard label; the ruler refuses the charged tier; the tier label is month-start
strict; `_training_label` raises without exclusion nodes; the fuzzy tier is retired and
verified-only. Beyond H2 and H6:

- **The strict export admits state rows on a word list, not on the tier** (C.F4): an
  indictment or a credible-allegation suspension can ship as `state_fraud / exact_id`.
- **The four label gates are applied unevenly** (C.F5, F6, F7): `non_npi_role` runs only
  in the state-list loader, not in the LEIE name resolver (an LEIE "NURSE AIDE" lands on
  the one same-named NP); `name_agrees` runs only when `provider_dim` is passed, which
  the research CLI does not; `name_in_page` is never called by a loader, only by the
  manual `verify-sources` sub-command.
- **Provenance is lost at the graph loader**: `match_level` (unique-name, research) is
  dropped when exclusion nodes are built, so the strict export rates a name-resolved LEIE
  row as `exact_id` (C.F8).
- **Three outcome vocabularies** (strict export, case_labels, manus_lanes) disagree on
  what counts as a resolved outcome; the MFCU loader has no outcome gate at all (C.F12).
- **CIA entities resolve on Luhn alone** with no NPPES name or state check (C.F9).
- **The `charged` rule in STATE_TERM_RULES precedes the hard rule and is unanchored**
  ("DISCHARGED" matches `charged`) (C.F10).
- **Duplicate exclusion nodes**: revocations are unioned into `exclusions.parquet` AND
  written as their own file; the loader concatenates without dedup (C.F13).

## 6. Architecture and code health (appendix D)

63,908 lines in 16 packages; `src/model_a` holds 49 percent of them in 101 modules
and is six concerns in one folder (features, labels, training/evaluation, run
operations, research orchestration, investigator tools). `provider_features_export.py`
is 3,204 lines with five edition pickers. `clean_data.py` is both stage one and the
shared library every package imports. Beyond H9 to H12:

- **Rule 6 (one name normalizer) is broken ten times**: `norm_company`, `norm_org_name`,
  `normalize` in build_final_leads (imported by Travis's track), and seven more, with
  suffix lists that disagree (D.§b rule 6).
- **44 bare `duckdb.connect()` calls** remain after the one factory with a spill directory
  was written for the v10 memory failures (D.F8).
- **Result-changing silent fallbacks**: graph_embeddings returns `{}` on a PageRank
  failure (fraud proximity goes empty, no log); address_grounding drops its two columns
  on any geocoder error; the DuckDB factory drops its temp-dir config on a failed SET
  (D.F3).
- **No `logging` in `src` at all**; 400+ prints; `[WARN]` is a string convention (D.F12).
- **`src/model` (Travis's track) is a parallel universe**: hard-coded `~/Desktop/Data`,
  a different label column, vintage-pinned row-count asserts, zero tests, four of seven
  modules dead, its own LightGBM fit and a third normalizer (D.F6).
- **14 dead modules** (attempt_1, export_csv, filter_final_leads, select_advertise_leads,
  backtest_leie and score_universe with no test or runner, lookup_tool stubs,
  profile_data); three stale "scaffold" docstrings; production imports the test fixture
  package (D.F11, F14).
- **Dependencies**: `psutil` imported but undeclared; sqlalchemy / psycopg2 / dotenv /
  pydantic / httpx declared and never imported; pytest in runtime requirements (D.F15).
- **Guardrails intact**: the Manus key is read from the environment only and never
  printed; the lookup tool asserts no `fraud` key; Model B's FORBIDDEN list covers
  `person_priority`; intake routes PHI to the secure channel; no bare `except:` in `src`.

## 7. The fix plan

Nothing in this document is auto-fixed. The order below is the author's proposal; the
operator says go per batch. Batch 1 is small and blocks any run; batch 2 changes run
semantics and is v19's pre-registration; batch 3 is structure and can proceed in
parallel with runs.

**Batch 1 SHIPPED 2026-10-21 (bundle 349, tests/test_batch1_fixes_2026_10.py; the operator
delegated the plan: "i want the best possible model so its really your call").** Also
decided there: `src/model` (Travis's track) is RETIRED TO REFERENCE (no tests, its own
label column, hard-coded paths; the production scorer is `company_list` +
`network_ab._fit_predict`, the one the runners grade), and the Windows `.bat` runners
are retired: `ops/vm/run_frozen_night.sh` is the single runner definition and
`make frozen-package` now delegates to it. Items 6 (B.F-10, the five org blocks that
log a USED line before the skip decision) and the stage-one reject count (H10, batch
2) stay open. Added from the scan: the NADAC effective-date filter, the current-day
graph step in the runner (D7), the placeholder dates (D6), the LEIE duplicate
quarantine (D8), DocGraph's edition on the as-of graph (D3), and a `_file_period`
month token that no longer reads "_180" as month 18.

**Batch 1, before any run (one bundle, a day):**
1. H1 the tier choice lists; H3 the `return frames`; the runner guard fails on a MISSING
   SOURCES_REPORT row (B.F-11 blind spot).
2. H2 month-start on the case label, with the planted test.
3. H7 HCRIS as-of; B.F-7 the referrer-grain O&R `_asof_pick`; H5 the enrollment per-se
   check on the as-of fact.
4. H4 raise on a missing eligibility column; the SILENT BLANK class (A.F-10, F-11, F-14,
   F-15, F-16, F-04) becomes a RAISE or an explicit `NULL`, never a 0.
5. H6 one `state_term:` convention + the tier test; C.F10 anchored `charged`.
6. B.F-3 `--skip-sources` honoured by every block; B.F-10 / F-11 every block writes
   exactly one audit line; A.F-11 `logging.warning` routed into the audit.
7. H8 remove the three producer-less inputs from the weights (or the catalog says
   "reserved") so the documented weights are the effective weights.
8. B.F-8 a current-day graph rebuild step in the runner before the rulers (D7).
9. H12 untrack the backtest CSV; H11 the Makefile calls the shell runner.
10. D3 DocGraph edition date on the as-of graph; D6 placeholder dates read as unknown
    by every temporal filter; D8 the 537 LEIE rows named (quarantine, never silent).

**Batch 2, v19 semantics (new pre-registration):**
1. H9 `join_m1()` on the nine export joins and the broadcast.
2. H10 `store_rejects` + the raw conservation assert in stage one.
3. C.F4 strict export gated on the tier; C.F5 / F6 / F7 the gates applied on every path;
   C.F8 provenance in `excl_type`; C.F9 CIA entities through `name_agrees`; C.F12 one
   outcome vocabulary; C.F13 exclusion-node dedup; C.F14 Luhn everywhere.
4. B.F-9 pass-through for sources that already emit percentiles; B.F-14 `assessable`
   enforced; B.F-13 the death cut; B.F-21 the baseline; B.F-22 the affiliated grader.
5. A.F-07 billing_after_death against the LEIE DOB or retired; A.§4d / §5i case-tolerant
   resolvers; A.F-31 the Excel reader or the `.xlsx` glob removed.

**Batch 3, structure (parallel):**
1. D.F7 one `names.py`; D.F8 the 44 connects; D.F13 one `data_root()`; D.F12 logging.
2. D.F9 split the export; one `pick_edition()`.
3. D.F6 decide `src/model`; D.F14 delete the dead modules and attempt_1; D.F11 move the
   fixture; D.F15 the requirements.
4. D.F10 re-partition `model_a`; D.F16 the CLI smoke test over every entry point.

## 8. What the operator does now

1. Merge bundle 346 (section 2 filled from the scan; the scanner's false-positive
   classes fixed; the deactivation edition picker; the preclean-only sync).
2. Pull the raw inputs onto the VM (`sync_data.sh down-preclean`, reminder
   `vm_sync_preclean`), then stage one on the October NPPES / LEIE, then the
   current-day graph, then re-scan (one line) so section 2 reads against the real tree.
3. Say go on batch 1. It ships as one bundle with tests; v19 is pre-registered after
   batch 2 under the v17 checks plus the ones the ledger suggests.
4. Decide `src/model` with Travis (D.F6) and the .bat runner family (H11).
