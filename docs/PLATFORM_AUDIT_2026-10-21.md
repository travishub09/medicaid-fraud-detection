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

## 2. The data files (PENDING the VM scan)

What the code review can say about the files without seeing them:

- **Every vintage guard is filename-based**, and the preflight registry's canonical names
  are UNDATED (`partb.csv`, `nadac.csv`, `order_referring.csv`). A file saved under the
  registry's own name passes every freeze guard with "vintage unverified" (A.F-08). The
  scanner reports every undated file a frozen run would read.
- **Two sources are discovered by two different rules**: Order and Referring by
  `_asof_pick` at org grain and by `_first_existing` (no cutoff, alphabetically first)
  at referrer grain (A.F-13, B.F-7); HCRIS by a bare glob (H7).
- **Four adapters apply no cutoff to rows that carry dates**: PBJ WorkDate and POS
  (filename only), NH ownership association date (not in the map), the NDC / referral
  claim slices (A.F-17, F-18, F-06).
- **The registry and the readers disagree** on LEIE names (registry accepts three,
  integrate reads `Caught.csv`), on the facility folder (registry says [ok] for ANY csv;
  the export's patterns are prefix- and case-sensitive on the Linux VM, so
  `Deficiencies.csv` matches nothing there), and eleven read folders are absent from
  the registry (NH ownership / penalties, `reference/mue`, `reassignment`, as-of NPPES,
  `state_capacity`, `sam`, `cia`, `warn`, `owners_prior`, `census`) (A.F-09, F-24, F-25).
- **NPI validity is uneven**: Luhn in state_terminations / revocations / preclusion;
  length-only in SAM and the enrollment file; OpenSanctions takes `idNumber` unverified;
  the label filter is `len >= 10` (A.F-27, C.F14).
- **The silent-blank class** (a missing column becomes a default and a scheme input is
  computed on it, with the source reported as used): Part B `avg_allowed` -> 0;
  Open Payments product columns -> correlation 0 for everyone; 340B contract-pharmacy
  name -> concentration 0; PBJ LPN / CNA hours -> 0; HCRIS cost columns -> 0; NH owner
  name / fine -> 0; LEIE `EXCLTYPE` -> every row untiered (A.F-10, F-11, F-14, F-15,
  F-16, F-04). The scanner now carries this list as `_CRITICAL` and grades a missing
  one as SILENT BLANK.

**How the scan works** (`src/data_audit.py`, bundle 343 + 344): for every source in the
preflight registry plus the eleven unlisted folders, it finds every candidate file the way
the export does, reads the header (BOMs, banner rows, Latin-1, zip inner files), resolves
it against the adapter's `*_COLS` map, grades the result REQUIRED MISSING (the adapter
raises) / SILENT BLANK (the dangerous class above) / OK, dates the file by name and by the
adapter's own date column (MIN / MAX streamed through DuckDB), runs the export's
`_asof_files` picker under `--cutoff` to say which editions a frozen run reads and which
it skips, counts rows, inventories `processed/` and `graph*/` against the derived files
the export expects, and joins the last run's `sources_audit`. Output:
`DATA_AUDIT.md` + `data_audit.json`. `ops/vm/data_audit.sh` runs it frozen and
current-day, with the preflight registry, the vintage inspector and scheme discovery,
and packs one tar.

**What the operator reads when the tar lands**: section 1 of `frozen_2021-12/DATA_AUDIT.md`
(the sources with an issue, first), section 2 (what a frozen run reads), the derived-file
table (every `exists = NO` is a stage that has not run on the VM). The author turns that
into section 2 of this document (replacing PENDING) and into the fix batch.

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
8. B.F-8 a current-day graph rebuild step in the runner before the rulers.
9. H12 untrack the backtest CSV; H11 the Makefile calls the shell runner.

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

1. Merge bundle 344 (this document + the scanner's required / silent-blank grading).
2. Run the VM scan (`ops/vm/data_audit.sh 2021-12`, lines in reminder
   `platform_audit_data_scan`), pull the tar, attach it. The author fills section 2.
3. Say go on batch 1. It ships as one bundle with tests; v19 is pre-registered after
   batch 2 under the v17 checks plus the ones the ledger suggests.
4. Decide `src/model` with Travis (D.F6) and the .bat runner family (H11).
