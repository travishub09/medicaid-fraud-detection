# CLAUDE.md

Project memory for Claude Code. Read this first; it is the contract for how to work
in this repository.

## What this project is

A **Healthcare Fraud Whistleblower Origination platform**: an intelligence-and-
acquisition engine for qui tam (False Claims Act) cases. Public healthcare data finds
*where* fraud signal concentrates (Model A), people/role data finds *who* plausibly
witnessed it and converts them into compliant marketing audiences (Model B), and an
underwriting model decides *which* resulting cases are worth financing (Model C) —
all on a shared healthcare entity-resolution graph, under a strict legal frame.

The full strategy is in `docs/platform/` (start at `docs/platform/README.md`); human onboarding lives in `docs/WHAT_WAS_BUILT.md` (the briefing), `docs/HOW_IT_WORKS.md`, `docs/GETTING_STARTED.md`, `docs/GLOSSARY.md`, `docs/READING_THE_OUTPUTS.md`, `docs/TROUBLESHOOTING.md`, and the data runbook `docs/platform/12-data-runbook.md` — keep them current when behavior or commands change. The supervised-model handoff is documented in `docs/PROVIDER_FEATURES_FOR_MODEL.md` (per-field reasoning + fraud rationale for Travis) and `docs/DATA_ACQUISITION_GUIDE.md` (step-by-step URLs + save paths for every unlock source). Two operator runbooks consolidate everything: `docs/RUNBOOK_TREY.md` (end-to-end run order + full data-acquisition guide) and `docs/RUNBOOK_TRAVIS.md` (the full metric catalog — definition, data source, and fraud rationale for every column — plus the training recipe + the merged per-scheme catalog in its §5.4 — what each scheme detects, its inputs + weights, feeding files, and the plain-English calc). The
source strategy document lives on branch `docs/source-pdfs` (confidential — never
copy its text wholesale into committed files).

**Operating thesis to never violate:** public data finds defendants and corroborates;
it does **not** make the legal claim. The human insider (the relator) is the asset.
Outputs are *investigative leads and marketing audiences for human/counsel review* —
never accusations, never adjudications about people.

## Repository layout

```
src/attempt_2/      CURRENT detection pipeline (13 stages) — the production core
  clean_data.py       stage 1 + THE shared normalizers (NPI Luhn, name, address)
  ingest/             integrate.py (assertion-driven integration), features.py
  audit/              coverage diagnostic, $21.8T corruption quarantine
  leads/              3-layer detection, layer-2 v3 concepts, company rollup/tracker
  export/             final CSVs
src/attempt_1/      DEPRECATED first pipeline — reference only, do not extend
src/backtest/       LEIE temporal validation (2.0× top-decile lift) — the proof
src/entity_graph/   canonical entity graph (nodes/edges/features/rings) — BUILT, tested
src/model/          supervised LightGBM lead scorer (Travis's build — PU training,
                    screening, exports; context in src/model/README.md)
src/model_a/        org fraud-risk → ERV (scaffold; will absorb leads/ core);
                    provider_features_export.py = per-NPI feature factory feeding
                    Travis's supervised model (scheme subscores + raw stats + label)
src/model_b/        whistleblower id/propensity — chain complete; person↔employer
                    resolver BUILT (running on real people gated on FCRA review)
src/funnel/         acquisition-funnel instrumentation: ListenLayer event taxonomy,
                    composite lead score, intake triage (launch gated on counsel)
src/model_c/        case underwriting — cold-start rules BUILT (priors/features/
                    underwriting/portfolio + public-disclosure screen)
src/lookup_tool/    billing-risk lookup v1 preview (public launch gated on Phase-0)
src/sourcing/       WARN surge monitor + CourtListener docket monitor (built)
src/ingest_cms/     Part B/D/DMEPOS/OpenPayments/Saturation/Facility/opioid/340B/
                    NPPES-deactivation/POS/order-referring/census/NADAC/HCRIS/
                    DocGraph adapters + NPPES API (built); ccn_npi_crosswalk
                    (PECOS CCN↔NPI → unlocks facility/HCRIS/POS schemes) +
                    claim_slices (ndc_claims/referred_claims builders) (built);
                    data-expansion-sprint stubs hospital_puf/geographic_variation/
                    nucc_taxonomy/sdud/chow (docs/platform/16 — contracts written, dormant)
src/feeds/          API-feed plumbing + DOJ/CourtListener/SAM/NPPES/ProPublica/
                    USAspending/openFDA clients (cached, injectable transport)
src/analytics/      peer engine + confidence + growth + plausibility (built)
src/enforcement/    DOJ case DB + API fetcher + SAM/state-licensing/OpenSanctions/medicare-revocations/
                    preclusion-list/death-master + label store (built)
src/nlp/            GLiNER zero-shot entity extraction (optional dep, injectable)
tests/              pytest; fixtures/synthetic.py generates data — no data files committed
docs/platform/      architecture, roadmap, and component specs (the source of truth)
```

## Commands

```bash
pip install -r requirements.txt

# Detection pipeline (needs real data in ~/Desktop/data/preclean/; see README)
python -m src.attempt_2.ingest.integrate            # stages run in README order

# Entity graph
python -m src.entity_graph --input ~/Desktop/data/processed --out ~/Desktop/data/graph
python -m src.entity_graph --fixture --out /tmp/graph_out    # synthetic, no real data

# Preflight — "do I have the data, named right?" (read-only; checks preclean/ inputs)
python -m src.preflight --data-root ~/Desktop/data

# Source registry — the single map of every source: WIRED adapter + file + schemes + gating.
# --registry prints it (data column filled if --data-root has data); --markdown regenerates
# docs/SOURCE_REGISTRY.md so the doc never goes stale.
python -m src.preflight --registry --data-root ~/Desktop/data
python -m src.preflight --markdown > docs/SOURCE_REGISTRY.md

# Pipeline status — "where did I leave off?" (read-only; honors MEDICAID_DATA_ROOT)
python -m src.pipeline_status

# Expectations — the standing calc-integrity report (every export writes one;
# also runs standalone on any delivered matrix)
python -m src.model_a.expectations --matrix provider_features_for_model.parquet --manifest feature_manifest.json

# Tests (work without any real data)
python -m pytest tests/ -v
```

## OPERATOR REMINDERS (standing, cross-session — the operator asked for this explicitly)

`docs/reminders.json` is the operator's reminder memory: download cadences
(monthly/quarterly/annual, with save paths + URLs) and the standing task queue.
`docs/OPERATOR_REMINDERS.md` is its rendered view; `python -m src.reminders`
prints what's due (`--done <key>` / `--done-task <key>` / `--add-task "..."`).
EVERY session must: (1) read the state early; (2) weave DUE items and queued
tasks into chat responses **even when off-topic** — the operator can only be
reminded inside a prompt response, so work reminders into whatever is being
discussed, and KEEP re-surfacing every open item in EVERY response until the
operator ABSOLUTELY confirms it is closed — never assume closure from
silence, a related action, or the passage of time; an ambiguous "done" gets
a clarifying check ("which item?") rather than a silent close; only an
explicit confirmation moves an item to done in the JSON. Repetition is the
point: the operator asked to be nagged; (2b) every
reminder surfaced in chat MUST carry the complete instructions inline — the
item's `steps` (search path, exact URL, what to click, exact save path with
the real data-root) — and anything runnable goes in a fenced copy-paste code
block (the item's `commands`), never prose-embedded commands and never a bare
"X is due"; for a session with 3+ downloads due, offer a rendered PDF;
(2c) the session CANNOT see a model run finish — the operator's prompt is the
only signal. When the operator says a run completed (or crashed), immediately
(a) run the equivalent of `python -m src.reminders --run-complete <run_folder>`
in the repo (queue the full post-run checklist: run packet, dossier
dispatch + poll, registry verdict), (b) restate ALL of those steps in the
response with their command blocks, and (c) RE-SURFACE every still-open
post-run task in EVERY subsequent response until the operator explicitly
confirms each is closed — only then mark it done. A run with open post-run
tasks is an open run, no matter how old;
(3) when the
operator confirms an action (or new work creates one), update the JSON and ship
it in the next bundle so the state ports forward; (4) exception: `*.csv` is
gitignored but `docs/reminders.json` is state, not data — it MUST stay
committed. Never let this file go stale: stale reminders are worse than none.
(5) PROOF GUARD (2026-10-09): download items carry `proof` globs (+ optional
`proof_recent_days`) and `--done <key>` REFUSES, with no state change, when
no matching non-empty file is on disk under data_root (`--force` overrides).
The operator pastes whole command blocks before opening the browser; every
new download reminder MUST carry a `proof`, and the chat must say "download
first, then paste" above every move/done block.

## Hard rules (enforced in code — keep them enforced)

1. **Identifiers are strings.** NPIs, PAC IDs, CCNs, ZIPs keep leading zeros. CSVs are
   read as all-VARCHAR Parquet first. Never let pandas infer numeric on an ID.
2. **Assertions hard-fail.** Every join asserts row-count and dollar conservation and
   RAISES on failure — never warn-and-continue. New stages must do the same.
3. **No fan-out.** Dollars are attributed via BILLING NPI only; joins are many-to-one
   by construction and asserted.
4. **Quarantine, never delete.** Bad rows go to a quarantine table with a reason.
5. **Explainability is mandatory.** Every score ships with named drivers. No bare,
   unexplainable composite ever reaches an output. (Defamation safety + counsel
   credibility depend on this.)
6. **Reuse the shared normalizers** in `src/attempt_2/clean_data.py`
   (`canonicalize_series`, `_normalize_name`, `_standardize_address`). Do not write
   a second name normalizer.
7. **HIPAA: no data in git.** `.gitignore` blocks `*.csv`/`*.parquet`/`*.xlsx`. Test
   data is *generated* by `tests/fixtures/synthetic.py`. Never force-add data files.
8. **One-sided robust statistics.** Fraud signals use peer-relative robust z
   (median/MAD, 1.4826 factor), one-sided (only excess is suspicious), clipped;
   guard zero-MAD peer groups. Public-facing numbers are percentiles, not z-scores.

## Legal guardrails (these shape code design — see docs/platform/01-legal-compliance.md)

- Model B outputs **audiences (role × org × channel), never named-individual call
  lists**. `person_priority` stays internal. FCRA risk if outputs adjudicate people.
- The "likely whistleblower at employer X" inference is **sensitive from creation**:
  minimize retention, never expose it in exports.
- The lookup tool returns percentiles + drivers + benign explanations — **no fraud
  boolean, no accusations**.
- Never build a hard dependency on data we cannot lawfully use (T-MSIS RIF DUAs and
  proprietary claims licenses bar litigation-targeting; see docs/platform/02).
- Intake collects no PHI at top of funnel; never encourage unauthorized access to
  employer systems or documents.

## Conventions

- Python 3.11+, DuckDB for heavy joins, pandas for orchestration/assertions, Parquet
  everywhere; NetworkX for graph features (the scoring pipeline needs no graph DB).
  Neo4j is the optional interactive layer — `entity_graph/neo4j_export.py` BUILT
  (offline `--neo4j-bulk` CSV import + online driver load; `pip install neo4j`
  only for the online path).
- Stages are CLI modules: `python -m src.<pkg>.<module>` with argparse; idempotent;
  read-only on inputs; write outputs + a Markdown report (`QA_REPORT.md` pattern).
- Node ids are namespaced strings: `provider:<npi>`, `org:<company_id>`,
  `owner:<key>`, `exclusion:<row>`.
- Scaffold modules raise `NotImplementedError` citing their spec doc; replace the
  raise, keep the docstring contract.
- Data flows: `~/Desktop/data/preclean/` (raw) → `interim/` → `processed/` →
  `features/` → `detection/` → `graph/`. Override with CLI flags; never hardcode
  new absolute paths.

## Current state & what's next

- **Built (parallel track):** `src/model` supervised LightGBM scorer with PU
  training, FP screening, and lead exports (see `src/model/README.md` — keep the
  two model tracks coordinated; `model_a` ERV and `model` scores are complements).
- **Built:** integration + corruption audit + 3-layer detection + company rollup +
  LEIE backtest (`attempt_2`, `backtest`); entity graph (`entity_graph`); Model A v1
  ERV composite + sector priors + target dossiers (`model_a`); WARN surge monitor
  (`sourcing`). Model B scoring chain logic-complete (`model_b`); Open Payments adapter +
  kickback co-occurrence (`ingest_cms`); validation harness (`model_a/validation`);
  CI + Makefile. Lookup-tool v1 preview, append-only label store, MEDICAID_DATA_ROOT env
  override, core-normalizer tests. API feed layer (DOJ/CourtListener/SAM/NPPES +
  freshness + ProPublica-990s + USAspending-awards→Model-C defendant size).
  Peer engine (`analytics/peers`), dossier-quality sprint (scoped damages, confidence
  bands, growth shock), public-disclosure screen (`model_c/public_disclosure`),
  government-interest overlay (`model_a/government_interest` — refresh the curated
  Work Plan table quarterly), Market Saturation + PBJ/Care Compare facility adapters
  (`ingest_cms/saturation.py`, `facility.py` → worthless_services /
  hospice_ineligibility / saturation_fraud schemes, dormant until files land),
  data-derived clinical plausibility (`analytics/plausibility.py` →
  `clinical_implausibility` blended into specialty_mismatch; `--provider-dim`).
  Model C cold-start underwriting (`model_c/priors|features|underwriting|portfolio`
  + `python -m src.model_c --fixture`): rules-based P(intervene) → recovery
  distribution → fund/pass/fund-with-terms + portfolio Monte Carlo, label-free,
  retires to a trained model when the case-outcome DB lands. Ownership-churn /
  CHOW (`entity_graph/ownership_churn` → ownership_turnover, A7), org event
  timeline + catalyst score (`sourcing/event_timeline`, A8), enforcement
  lookalikes (`model_a/lookalikes` → dossier corroboration, A9) — the last three
  dormant until owner snapshots / the DOJ backfill accumulate. Neo4j export
  (`entity_graph/neo4j_export`, `--neo4j-bulk`); June-2026 sweep adapters +
  schemes (`ingest_cms/opioid` → pill_mill, `nppes_deactivation` →
  invalid_identity, `hrsa_340b` → contract_pharmacy; see docs/platform/15).
  Reassignment affiliation edges + Census denominator (A5 complete). Full
  remaining build-out: NADAC drug spread (B4), HCRIS cost_report_fraud (B5),
  DocGraph referral edges + referral-ring detection (B10, un-gated),
  state-licensing + OpenSanctions → exclusion schema, SSA Death Master File
  (DOB-corroborated → invalid_identity), openFDA recall events, GLiNER wrapper
  (`src/nlp/`). New schemes: cost_report_fraud; drug_spread_anomaly +
  billing_after_death folded into existing schemes. Legal/operational gates on
  Model B activation, supervised graduation, the funnel/lookup launch, and the
  OpenSanctions license REMAIN (code built, guardrails intact). Supervised
  graduation harness BUILT (`model_a/supervised.py`: PU classifier +
  isotonic + quantile recovery; Model C reuses it), funnel layer BUILT
  (`src/funnel/`: events/lead_score/intake with no-PHI + no-fraud-boolean
  guardrails), person↔employer resolver BUILT (`entity_graph/person_resolver.py`:
  scored linkage + employed_by edges + tenure overlap; opaque person_id only).
  Provider feature export (`model_a/provider_features_export.py`): re-points the
  scheme-subscore engine from org-grain ERV ranking to a per-NPI training matrix
  for Travis's supervised model — full universe (no candidate gate), broadcasts
  org/CCN-grain features down to NPI, peer-normalizes adapter metrics, ships raw +
  `*__peerpct` + `subscore_*` + the `provider_on_leie` PU label, with a leakage
  manifest (hard vs. proximity-adjacent) and a data dictionary.
  The provider export now orchestrates ALL sources (per-NPI adapters + org/CCN-grain
  broadcast + optional `--with-analytics` growth/plausibility), with a CCN→NPI
  crosswalk builder, NDC/referral claim-slice builders, a stale-graph guard, and a
  `make provider-features` target that rebuilds the graph first. Every run ends with
  a consolidated `SOURCES_REPORT.md` (+ a `sources_audit` block in the manifest): a
  log-captured table of every source as used/skipped with the reason + the file it
  read, so a silently-dropped input (the year-suffixed Part-B/D skip class of bug) is
  caught at a glance; the per-NPI adapters now name the exact file in their log line.
  SSA Death Master File (`enforcement/death_master.py`, DOB-corroborated →
  billing_after_death) and OpenSanctions (`enforcement/opensanctions.py` CLI →
  `processed/exclusions_*.parquet`, merged into graph exclusion nodes by a
  generalized loader) are wired into the export; deceased/deactivated-NPI checks
  are DuckDB-filtered so they never pull the full spending fact into pandas.
  Procurement instructions for all unlock sources live in docs/platform/12.
  Ownership-churn is now operational: `entity_graph/ownership_snapshot.py`
  (`make owner-snapshot` monthly) archives dated owner-edge snapshots; the export
  diffs them into `ownership_turnover` once two accumulate (no data to procure).
  The four §9 model limitations are now mitigated in code: NUCC peer grouping
  (`ingest_cms/nucc_taxonomy.py` implemented → coherent classification cohort
  fallback on the percentile ladder), a widened multi-source PU label
  (`provider_on_exclusion` = LEIE + CMS revocations + SAM + OpenSanctions, with
  `exclusion_label_sources` provenance), fully DuckDB-native growth/plausibility/
  billing-LM (`*_duckdb` self-joins/GROUP BYs over the spending parquet — the
  provider×code matrix never enters pandas; full-universe `--with-analytics`, no RAM
  ceiling), and NPI-grain ownership refinements (`org_member_count` +
  `has_excluded_owner`).
  Pillar 3 of the ground-up redesign (docs/platform/17-provider-signal-architecture.md)
  is BUILT: `entity_graph/graph_embeddings.py` emits per-node DeepWalk-style
  embeddings + a personalized-PageRank fraud-proximity field + structural motifs
  (k-core/triangles/clustering), written as `graph/node_embeddings.parquet` and
  mapped to NPI grain in the export. The embeddings + motifs are computed on an
  EXCLUSION-FREE graph (exclusion nodes dropped before the walks) so `graph_emb_*`
  are CLEAN trainable features; only `graph_fraud_proximity` (exclusion-seeded
  personalized PageRank on the full graph) stays leakage-adjacent — the structural
  fix for org→NPI broadcast.
  Pillar 2 (label engine) started: `model_a/case_labels.py` turns the DOJ case DB
  into scheme-typed, time-boxed positives (resolve case→org→NPI, extract the conduct
  window), folded into the widened label as source `doj_case` with `fraud_scheme` /
  `conduct_start` / `conduct_end` label metadata (scheme-stratified + out-of-time
  training; `--case-db`). Pillar 1 (point-in-time store) BUILT: `model_a/feature_store.py`
  (`snapshot_features` valid-time stamp, `asof_join` reconstruct features strictly
  before a label date, `temporal_split` out-of-time masks; export `--snapshot`,
  `make feature-snapshot`). Output reframe BUILT: `model_a/clean_anchors.py`
  manufactures high-confidence negatives (`confirmed_clean`: institutional/FQHC or
  long-tenure + benign billing + no fraud proximity) and `model_a/case_control.py`
  matches each positive to comparable clean controls (taxonomy×state×size ladder)
  → `provider_features_matched.parquet` via export `--case-control`. Pillar 4
  started: `model_a/expected_billing.py` — the "digital twin" residual (robust
  per-taxonomy regression of billing on legitimate covariates → one-sided
  `billing_residual` = unexplained excess; doesn't punish the legitimately large)
  and `model_a/consistency.py` — cross-source incoherence flags (individual at
  institutional scale, no-tenure full-scale biller, solo billing implausibly broad
  codes, one-NPI org at institutional scale → `consistency_flags`). Weak-supervision
  label model (`model_a/weak_supervision.py`): ~12 labeling functions vote
  fraud/clean/abstain, a label model learns each one's accuracy from the anchors,
  fused into a soft `weak_label_score` for every provider (a TARGET in
  `label_metadata`, with per-LF audit in the manifest) — Snorkel-style expansion of
  the sparse hard labels. The remaining docs/platform/17 items are BUILT:
  temporal-graph velocity (`entity_graph/graph_velocity.py` — diff two feature
  snapshots → `graph_emb_drift` + degree/kcore/proximity deltas), external grounding
  (`model_a/address_grounding.py` — mailbox/PO-box + address-reuse flags), and the
  billing-LM moonshot (`model_a/billing_lm.py` — PPMI/SVD code embeddings +
  per-provider `billing_surprisal`; export `--with-analytics`). All seven §17
  pillars/items now have working cores. Depth upgrades BUILT:
  `model_a/billing_sequence_lm.py` (order-aware bigram billing LM over the
  code-adoption sequence → `sequence_surprisal`; transformer is the torch-optional
  swap behind the same interface), `feeds/geocode.py` (live Census geocoding,
  injectable transport → `addr_geocoded`/`addr_no_match` via the address-grounding
  hook; export `--geocode`), and `model_a/temporal_sources.py` (per-source
  valid-time: `point_in_time_tables` + `python -m src.entity_graph --asof` builds a
  leakage-correct point-in-time exclusion graph).
  Second limitation-mitigation batch BUILT: `model_a/calibration.py` (isotonic/Platt
  + reliability table + Brier/ECE — turns a ranking score into a calibrated
  P(offender) for Model C / thresholds), `model_a/pu_prior.py` (Elkan-Noto label-
  frequency `c` + class-prior + contamination-corrected top-decile lift — the honest
  PU metric), `model_a/billing_specialty.py` (billing-implied specialty via nearest-
  centroid in the billing-embedding space → `billing_taxonomy_mismatch`/`_fit`/
  `_margin` + `billing_implied_taxonomy`; defends the self-reported-taxonomy peer key),
  `enforcement/preclusion.py` (CMS Preclusion List → exclusions schema; widens the PU
  label with a `preclusion` source tag — sponsor-channel data, gated), and the export
  now emits `group_id` (manifest `group_cols`, for group-aware CV) + an `assessable`
  flag (manifest `assessability`, so thin-evidence providers aren't force-ranked).
  Third limitation-mitigation batch BUILT: `model_a/asof_billing.py` (point-in-time
  BILLING fact — `write_asof_spending`/`asof_provider_stats` + export `--asof-cutoff`
  filter the spending fact to pre-freeze service months so every billing feature is
  leakage-correct, closing the last temporal hole), `model_a/fdr.py` (false-discovery
  control on the lead list — empirical p-values + Benjamini-Hochberg + a model-based
  `fdr_threshold` from calibrated probabilities), `model_a/conformal.py` (distribution-
  free confidence — conformal p-values for offender-class membership + split-conformal/
  CQR recovery bands), per-subgroup calibration (`calibration.fit_grouped_calibrators`
  + `reliability_by_group`), and matched-set covariate-balance diagnostics
  (`case_control.covariate_balance` SMDs + `separability_auc`).
  Polish upgrades BUILT: USPS CMRA exact-match (`address_grounding.load_cmra_reference`
  → `addr_is_cmra`) + address-cluster-degree (`addr_distinct_orgs`/`addr_cluster_degree`
  = distinct orgs per suite; export loads `preclean/usps/cmra.csv` + joins org grain),
  and interpolated Kneser-Ney for the sequence LM (`billing_sequence_lm.build_kn_model`
  + `sequence_surprisal(smoothing="kn", order=2|3)` — continuation-probability
  smoothing for the rare-HCPCS tail; add-k stays the default).
  Native-file loaders for the operator: `hrsa_340b.load_opais` (reads the 3-worksheet
  OPAIS Covered Entity Daily Report .xlsx — Contract Pharmacies grain + merged State)
  and `nppes_deactivation.load_deactivation` (unzips the CMS Deactivated NPI Report
  .zip/.xlsx, auto-detects the banner header row); the export discovers .xlsx/.zip,
  not just .csv. `openpyxl` added to requirements. Non-UTF-8 tolerance: a shared
  `clean_data.read_csv_text` (UTF-8 → Latin-1 fallback) + `encoding='latin-1'` on the
  DuckDB `read_csv_auto` paths, so CP1252/Latin-1 CMS files (stray 0xA0 bytes) load
  instead of crashing; every raw-CSV entry point routes through it.
  Label-source ablation (`model_a/label_ablation.py`): the label counterpart of
  source_ablation — retrains OOF with one label source's positives removed
  (leie / opensanctions / sam / medicare_revocation / doj_case / …, from the
  `exclusion_label_sources` provenance), same screened features + folds, and
  measures the forward-lift delta per source with a cluster-bootstrap CI.
  Overlap guard reports the lift with training∩forward positives removed.
  Conclusions feed the NEXT pre-registration's label choice; never gate a run.
  Release gating: a pre-registration's `--expected` prose is for humans and the
  new `--check "<metric> <op> <value>"` expressions are the ENFORCED half —
  `recommend()` evaluates them against the registered row, and an unmet (or
  unmeasured) check is a HOLD. A prose-only bar is labelled "not enforced" on
  the report instead of being silently waved through.
  SCHEME TRIAGE — the standing underperformance feedback loop
  (`model_a/scheme_triage.py`, `make scheme-triage`, chained after every
  `provider-features` build): every scheme SCHEME_HEALTH flags (or whose label
  AUC is weak on 20+ positives) gets the six-question diagnosis — why
  underperforming / design right / fields missing / coded right / wired right /
  right files — each answered from the run's own artifacts (scheme_health.json,
  manifest sources_audit, EXPECTATIONS, CASE_VALIDATION, the preflight
  registry) and ending in a concrete action. Include in every night-runner.
  PURSUIT DOSSIER — the standing end-of-run artifact (`model_a/pursuit_dossier.py`,
  `make pursuit-dossier`, wired after every `provider-features` build and every
  night-runner): the top-10 cases to pursue, each written twice ("The short
  story" for any reader + "The technical picture" with named drivers), the
  inherited disclaimer/alternative-explanations frame, individuals as NPI+city,
  and one Manus public-records research prompt PER PROSPECT
  (pursuit_research_prompts.jsonl; `--dispatch` sends live off $MANUS_API_KEY
  with a re-attachable task registry, `--poll` folds findings back in; without
  a key the research is visibly pending, never silently skipped). A run
  without a pursuit dossier is not a finished run — include this step in every
  future night-runner .bat.
  Crash-resume checkpointing (`_ResumeCache` in the provider export): every
  adapter/org/analytics frame is written to `<out>/_resume_cache/` the moment
  it is computed, so a killed multi-hour build restarts from the last finished
  source instead of from zero. Keyed to git commit + freeze args (stale caches
  self-invalidate — never serve pre-bugfix frames); cleared on successful
  completion; `--no-resume-cache` disables.
  VINTAGE INSPECTOR (`src/vintage_inspect.py`, `python -m src.vintage_inspect
  --data-root ...`): reaches INSIDE every preclean file (DuckDB-streamed
  MIN/MAX over date-named columns; zip inner-name peek; banner-row header
  probing) to settle each file's data period. Writes preclean/VINTAGE_INSPECT.md
  (the TRUE re-download list = files nothing dates, the machine-run runbook
  Part-6 required-columns check, orphan folders no adapter reads, full
  inventory), auto-fills blank EDITIONS.csv data_period rows (operator entries
  never overwritten, "[auto: from data]" note), and proposes reviewable
  renames in rename_plan.bat (core contract names Spending/NPPES/PECOS/Caught
  are never renamed; nothing renames until the operator runs the .bat).
  SCHEME DISCOVERY — the standing new-scheme loop (`model_a/scheme_discovery.py`,
  `make scheme-discovery`, run after any procurement session): checks a curated
  candidate-scheme menu (hospice NF-care share / GIP share / visits-near-death /
  nurse minutes / spend-per-bene / dementia mix, NH ownership stakes + penalties,
  PBJ weekend staffing — each with its fraud rationale) against the ACTUAL
  preclean files and fields (READY TO WIRE / CHECK ROWS / BLOCKED), and lists
  every signal-looking column no wired adapter consumes (the raw material for
  schemes nobody has designed yet) → preclean/SCHEME_DISCOVERY.md.
  OPERATOR REMINDER ENGINE (`src/reminders.py` + `docs/reminders.json` — see
  the standing section above the hard rules; weave due items into every chat).
  FROZEN CHURN (`owners_vintage.asof_editions` + `merge_editions`): frozen
  runs now chain All-Owners editions dated on/before the cutoff (≥2 per
  dataset) for a leakage-correct pre-cutoff ownership_turnover, instead of
  skipping the vintage diff; datasets with too little pre-cutoff history are
  logged by name with the unlock (download an archived earlier edition).
  The operator's owners_prior/ archive (2023-2026, six dataset families)
  powers current-day chains immediately.
  FCA-RELEVANCE LABEL LAYER (`model_a/fca_relevance.py`): every exclusion /
  revocation record is tiered from its statute or reason (`excl_type`) —
  hard = looks like an FCA case (1128a1/a3/b1/b6/b7, BRCH CIA/SA, b16, abuse
  or misuse of billing privileges, false/misleading enrollment, DOJ cases),
  weak = misconduct not FCA on its face (license actions, patient abuse,
  derivative exclusions, preclusion), excluded = never a positive (loan
  default b14, paperwork b9-b13, non-compliance / not-operational
  revocations, OpenSanctions, SAM without a healthcare cause). The forward
  label takes `--fca-tier all|hard|hard+weak` (`prospective_label`), ships
  `fca_tier`/`fca_basis`/`label_policy` columns + a `.TIERS.md` audit; the
  registry records `label_policy` per row (lifts under different rulers are
  not comparable) and `--check "policy = hard"` / `"frozen = True"` are
  enforceable. `model_a/label_variants.py` reads the `__all` / `__hard` /
  `__hard_weak` suffixed label + A/B + ablation reports off one run folder
  into LABEL_VARIANTS.md (same matrix, three rulers). The manifest now carries
  a top-level `asof_cutoff` (registry falls back to `graph_substrate.asof` for
  v1-v6). v6 lesson encoded: the as-of graph rebuild MUST pass `--asof-nppes`
  or `address_layer_frozen` is false and post-cutoff addresses leak.
  LABEL AUDIT (`model_a/label_audit.py`, run before every pre-registration and
  after any label-source download): grades every label source on identifier
  (valid Luhn NPI vs name-only), date (undated / future / pre-1977 / placed vs
  cutoff), FCA relevance (hard/weak/excluded + the statutes the tier table does
  NOT know, which silently default to weak), and reach (NPI in the scored
  universe / ever billed); checks the matrix label join (unexplained positives,
  missed exclusions, billing-to-exclusion lag), the DOJ case DB (parse rates,
  duplicates, NPI-in-source, and RECALL = cases resolved to a scored NPI plus the
  unresolved settlement dollars), and the OIG CIA list (recall of the harvest
  against the official roster of FCA settlers) → LABEL_AUDIT.md + label_audit.json
  with a severity-ranked findings list, each with its fix. `enforcement/cia.py`
  is now implemented (normalize_cia + cia_case_rows; file at preclean/cia/cia_list.csv).
  TIER A CONTRADICTIONS (`model_a/contradictions.py`, current-day, no model
  run): the per-se facts ranked by dollars at issue — paid service months
  on/after the provider's own first exclusion (exclusion nodes), after NPPES
  deactivation (deactivation file), after recorded death (matrix flag),
  individual NPIs with impossible days (minutes map via pi_facts), and
  facility-only codes on individual NPIs (the Rhode Island shape) →
  CONTRADICTIONS.md + contradictions.csv with the innocent explanations to
  rule out first. A model rank is never the reason a PI is sent; a Tier A row is.
  CAPACITY CHECK (`model_a/capacity.py`, the Nick Shirley test): per billing
  NPI, billing-implied demand (patients/day, implied clinician-hours/day,
  per-diem patient-days) against documented capacity (largest of rendering
  NPIs on claims / PECOS reassigners / same-address individuals; an individual
  NPI is one person; NPPES practice locations as sites; POS certified beds via
  the CCN crosswalk; state licensed capacity via
  `ingest_cms/licensed_capacity.py` → `processed/licensed_capacity_by_npi.parquet`),
  flagged on the RATIOS (hours per clinician ≥ 12 long / ≥ 24 impossible,
  ≥ 40 patients per clinician for clinics, occupancy > 100%); labs, transport,
  DME and HCBS reported but not flagged on clinician ratios. Inputs to procure:
  the NPPES pl_pfile (inside the monthly zip) and state rosters
  (`preclean/state_capacity/<ST>_<what>.csv`; reminders carry the steps).
  STATE TERMINATIONS (`enforcement/state_terminations.py`): the state Medicaid
  terminated/excluded lists (Southeast first: GA, KY, MS, NC, SC, TN with NPI; AL
  name-only via a unique in-state NPPES name match) load into the exclusions schema as
  `state_term:<ST>:<reason>` and are tiered by the state's stated reason
  (`fca_relevance.STATE_TERM_RULES`: fraud-shaped = hard; license / derivative /
  bare 'terminated' = weak). They are the Medicaid-side labels that lead the LEIE by
  years; the operator's sorter files them under preclean/state_terminations/.
  DEFENDANT RESOLVER (`enforcement/defendant_resolver.py`): DOJ case defendants,
  people AND businesses, → NPIs. Press releases never print NPIs and the case→org
  join only lands on an enrolled org's exact name, so the DOJ label line was empty
  by construction. The resolver pulls every "<name>, of <city>, <state>" /
  "<city>, <state>-based" / "located in" mention (+ the primary defendant with the
  district's state), matches people on FIRST LAST and businesses on norm_org_name
  within the state (city narrows; generic names never match; unique hit only), and
  writes processed/case_defendant_npis.parquet + the residual research list
  (case_defendants_unresolved.csv + prompts jsonl, ambiguous first) +
  DEFENDANT_RESOLUTION.md. `case_labels.build_case_labels(defendant_npis=...)` adds
  the `resolved_defendant` basis (below named_npi, above org_billing); the export
  auto-loads it from processed/. `--mfcu-prompts` writes the state-MFCU sweep
  research prompts (9 SE states × 2020-2025) whose reviewed CSVs load through
  research_evidence.load_mfcu_cases. Prosecuted providers are labels, never leads.
  DEFENDANT RESEARCH (`enforcement/defendant_research.py`): the residue the resolver
  cannot settle → a tiered, de-duplicated Manus batch (tier 1 ambiguous people + small
  orgs; tier 2 unmatched on cases ≥ --min-amount; chains / fragments / role phrases /
  national corporations / place names never sent), written for REVIEW first
  (defendant_research_batch.csv + prompts jsonl), then `--dispatch` through the
  pursuit-dossier Manus plumbing (own registry), then `--poll --resolved` folds answers
  back: Luhn-valid NPI + high/medium confidence → status resolved / match_level
  research_<conf>; low → research_low; none → research_no_npi (a non-provider).
  CASE HARVEST (sources 1-6 of the DOJ-label expansion, 2026-09-27): (1) the DOJ
  press-release harvest keeps criminal health-care-fraud prosecutions too (they never
  say "False Claims Act"); every case row carries `case_type` (civil_fca /
  criminal_hcf / docket_fca / docket_qui_tam) and summaries run 1,500 chars so the
  resolver sees the "of <city>, <state>" mention; criminal titles name nobody, so the
  defendant comes from the body's first located mention. (2) `enforcement/
  docket_backfill.py`: CourtListener nature-of-suit 375 + 376 dockets since a date →
  case rows (defendant from the caption, district from the court id); docket-only
  cases are ALLEGATIONS → `case_labels` tags them source `docket_case` and the export
  records that source WITHOUT flipping the hard PU label. (3) `enforcement/
  oig_enforcement.py`: HHS-OIG enforcement-actions listing + action pages (tolerant
  HTML parse, cached raw, state from URL/text) → case rows. (4-6) `enforcement/
  case_harvest_prompts.py`: Manus prompt sets sharing ONE CSV schema (takedown
  court-document pages, Fraud Section year in review, HCFAC annual report, OIG
  semiannual reports, state MFCU sweeps) → --dispatch / --poll (answers saved for
  review; loaded only via research_evidence mfcu-cases). `feeds.client.
  default_fetch_text` is the HTML counterpart of the JSON client.
  STRICT FCA LABEL EXPORT (`model_a/fca_label_export.py`, `labels_fca_strict.csv`): the
  written label policy for claims-data joins (PurpleLab), enforced per row: tiers
  fca_civil (resolved civil FCA outcome), hcf_criminal (conviction / plea),
  excl_statute (LEIE fraud statutes), and under `--policy fca_shaped` state_case /
  state_fraud (fraud that could have been FCA but a state handled it); identifier
  rules (Luhn NPI, stated basis + accepted match level, in NPPES, entity type agrees,
  not deactivated before conduct), time-boxing (conduct window + window_inferred),
  provenance on every row, a quarantine file with reasons, a 30-row verification
  sample for a human check, FCA_LABEL_EXPORT.md with the policy text.
  LABEL VERIFICATION (`model_a/label_verification.py`): the 30-row human check on the
  strict label delegated to the research agent as a FIRST pass: batches of 10 rows,
  one verdict per row (confirmed / wrong_person / wrong_npi / not_a_provider /
  cannot_verify) with the evidence URL; --dispatch / --poll through the same Manus
  plumbing; verification_results.csv + LABEL_VERIFICATION.md with the tally and the
  tighten-or-ship verdict (confirmed < 90% or > 1 wrong_npi = tighten). The operator
  reads every flagged row; verdicts are evidence, never the final word.
  LABEL QUALITY ATTACK (2026-09-28, the precision layer on the resolved-defendant
  rows): `defendant_resolver.role_near` reads the role the release gives each person
  from the clause around the mention (clinical family / nonclinical / owner_exec /
  unknown; a co-defendant's clause is never read as this person's), `taxonomy_family`
  maps the candidate NPI's NUCC code to the same families, and `role_agreement`
  compares them: a recruiter / marketer / biller / office manager NEVER resolves on a
  name alone (status `nonclinical_role`: the unique name hit is someone else), a
  clinical role that contradicts the taxonomy is `role_mismatch`, an agreeing role is
  corroboration (`role_agree = agree`). `name_national_hits` counts NPIs with the same
  first+last name across every indexed state (commonness). The strict export ranks
  every row on an EVIDENCE LADDER (`evidence_strength`: exact_id / strong / moderate /
  weak, the ruler in FCA_LABEL_EXPORT.md), quarantines the role-rejected mentions with
  the reason, writes `verification_sample_strata.csv` (up to 12 rows per strength x
  tier cell, weak first: the sample that attacks the label where it is weakest; the
  random 30 stays too), and `--min-strength moderate|strong` drops the weak stratum
  once the verification says so. LABEL_VERIFICATION.md tallies confirmed by strength.
  CASE MERGE REFRESH (`case_db.merge_case_files`, default on): an incoming row for an
  announcement already stored fills its blanks (date, defendant, jurisdiction, amount,
  scheme, sector) and replaces a page-chrome summary ("Skip to main content"), so a
  better re-harvest of the same URL improves the row instead of being discarded; the
  CLI line prints the refresh counts. EMBEDDING BOUNDS (`graph_embeddings_sparse`,
  `--max-embed-nodes`, default 1.5M): nodes in components under 3 (a solo NPI + its
  own org, 13.6M of them nationally) keep their row with a zero vector, and at most
  the cap is embedded, fraud-proximate first then by degree (the 2026-09-28 MemoryError
  in the SVD on 13,660,702 connected nodes).
  VERIFICATION OUTCOME (2026-09-29, two batches, 249 rows, re-scored on rows still
  in the label): confirmed by evidence strength = strong 39/40 / moderate 69/75 (92%)
  / weak 65/88 (74%). MODERATE IS PROMOTED: the PurpleLab file is `--min-strength
  moderate` (exact_id + strong + moderate; processed/fca_label_strong/, 5,155 rows /
  4,672 NPIs); weak stays in the full-ladder file only (processed/fca_label/). The
  resolver's `--nppes-file` streams middle names for every person candidate
  (`candidate_person_npis`) from the raw NPPES file (UTF-8 first, Latin-1 fallback):
  63 middle-name conflicts quarantined, 31 ties settled by a middle name.
  `npi_candidate` stays on every resolver row (blank once resolved) so the quarantine
  shows the rejected NPI. STOP RULE (agreed 2026-09-29): precision work on the label
  is at diminishing returns (every kept stratum verifies at 90%+, the residual wrong
  rows are one-offs); the next label levers are the PurpleLab join itself and recall
  (the research residue), not more precision. Fixes from the flagged rows: a unique candidate whose NPPES middle name
  contradicts the release is rejected (`middle_conflict`), technician / head of
  marketing / nominal owner / non-provider are non-clinical roles, headline
  descriptors ("Worcester Pharmacy") and fragments ("Plea") never match an org
  (`is_descriptor_name`), medical specialties are org words, exclusion rows are
  verified as LEIE-vs-NPPES identity (a 'wrong_person' against the blank defendant is
  re-labelled a confirmed identity). Operator policy: a company named in a CRIMINAL
  release whose owner was convicted STAYS in the label as the billing vehicle, flagged
  `org_billing_party`. A resolver rerun carries prior research verdicts forward
  (`carry_research`) instead of erasing them.
  HCRIS RELATED PARTY (`ingest_cms/hcris_related_party.py`, 2026-10-06): reads the RAW
  SNF10FY<year>.zip cost-report files (header-less RPT/ALPHA/NMRC) and writes the
  Worksheet A-8-1 related-organization cells + S-2 identity cells for a CCN list or a
  state (CCN prefix), long-form plus a per-line summary with the column numbers kept in
  the headers (nothing interpreted). The published SNF cost-report file has no
  related-party column; this is where management fees to owner-affiliated companies
  live (the New York AG Lahasky theory). Dormant until preclean/hcris/raw/ holds the zips.
  AFFILIATE DISCLOSURE CHECK (`ingest_cms/hcris_affiliates.py`, 2026-10-07): for every
  SNF, raw A-8-1 presence + affiliate total vs PECOS organization owners/managers (via
  ccn_to_npi -> member_edges -> owned_by_edges -> owner_nodes type O); flags
  `affiliate_undisclosed` (org owners, no A-8-1) and ranks affiliate share of G-3
  opex within state x year; `--focus` details chosen CCNs. Wesleyan Village was the
  one home in its group with the gap; this gives the gap a base rate.
  JULY-2026 CMS FILES (2026-10-08): `ingest_cms/medicaid_ndc.py` streams
  "Medicaid Provider Spending by NDC" into processed/ndc_fact.parquet (row +
  dollar conservation asserted; as-of cutoff; column candidates in NDC_COLS),
  writes the NDC claim slice that un-gates drug_spread_anomaly when units exist,
  and emits per-NPI rx_* (pharmacy: prescriber HHI / top-prescriber share /
  excluded- and deactivated-prescriber fill shares) + px_* (prescriber: pharmacy
  HHI, dollars per patient-month, brand share vs NADAC) features.
  `ingest_cms/medicaid_enrollment.py` streams "Medicaid Provider Enrollment
  Segments" into Parquet; per-NPI mcd_* tenure / churn / primary-state features as
  of the cutoff; non-benign segment ends -> processed/exclusions_medicaid_enrollment
  .parquet as label source `mcd_enroll:<ST>:<reason>` (fca_relevance tiers it by the
  state-termination ruler; benign ends are never positives; REBUILD THE GRAPH after
  the file lands); paid-after-enrollment-end per-se check; provider_dim state fill.
  Both wired into `_run_npi_adapters` (processed + asof_cutoff + exclusion events
  now passed in), the preflight registry and the reminders.
  MODEL REVIEW 2026-10 (`docs/MODEL_REVIEW_2026-10.md` — read it before touching
  training/evaluation code): the audit found and this bundle FIXED, with
  regression tests in tests/test_audit_fixes_2026_10.py: label smear (org-grain
  billing_after_deactivation/death broadcast to every member then flipped to
  positive — now the NPI's own `deact_/death_paid_after`, org flag only for
  single-NPI orgs); frozen runs manufactured zero clean negatives (percentile
  compared to 0 — now <= 0.5); docket-only case labels raised NameError; label
  provenance tagged state_term / state_board / SAM / research rows as "leie"
  (`_label_source` fixed, SAM rows carry `sam:`); NUCC pooled no-taxonomy
  providers into a fake "unknown" cohort; the in-matrix tier label froze at month
  END while the graph + forward label freeze at month START; frozen-run DOJ labels
  were boxed by conduct year only (now also announced <= cutoff);
  `--skip-sources` ignored by org/CCN blocks. STILL OPEN and prioritized in the
  review: the adjacent-leak fence is not applied in source_ablation / network_ab
  (the gating metric is not the shipped model), substrate not frozen by default,
  calibration / FDR / conformal / PU-prior modules have no caller, raw 0.5 hot
  threshold, max-over-members rollup, src/model zero-anomaly negatives. Items
  marked (re-register) change metric semantics — new pre-registered run, never a
  silent swap.
  VERIFICATION CAMPAIGN 2026-10-08 (`docs/VERIFICATION_2026-10-08.md`): pyflakes
  over src (0 undefined names), coverage (68%; the stage-one chain and src/model
  were 0%), 14 planted-answer invariant tests (`tests/test_model_invariants_2026_10
  .py`: peers / subscores / fence / OOF scorer / calibration / conformal / FDR /
  PU prior / point-in-time / twin residual / case-control), and the whole stage
  chain run end to end on the raw fixture (`tests/test_stage_chain_end_to_end.py`,
  the Makefile `pipeline` order as subprocesses, planted truths asserted). Fixed:
  `src/attempt_2/audit_basis.py` (NEW) — features.py / company_lead_tracker.py /
  backtest score_universe.py reconciled against HARD-CODED 2024-vintage totals
  (230,133,234 rows / $1.1T) and could not run on any other spending file; they now
  read processed/corruption_audit.json, which audit_corruption.py writes (legacy
  constants only as the fallback; the report names the basis — operator reruns the
  audit once, reminder queued). integrate writes the EMPTY facility-flags table when
  preclean/owners/ is absent (features.py requires the file); audit_corruption NULL
  format; verify_layer1 tolerates a LEIE without waiver columns; refine_layer2 /
  company_rollup / tracker report crashes on a small or empty book; the export's
  --fixture path never bound `g`/`processed` and used min_peer 30 on 14 rows (11
  phantom expectation FAILs); company_list.oof_scores hard-coded the label column
  (pursuit dossier crashed on any matrix without the widened label — now reads the
  manifest); duplicate G0480 key. NOT verifiable: src/model (config asserts 308,038
  PU rows / 578 positives / 617,062 universe rows — Travis's call, same fix class),
  backtest_leie (no argparse; --help runs the backtest).
  ACCURACY SPRINT 2026-10-08 (`docs/ACCURACY_PLAN_2026-10.md` — the deep
  audit the operator asked for: label vs target, features/data vs typologies,
  evaluation vs the business outcome, with v6/v7 evidence and the ranked
  experiments; read it before planning v8). Built and tested: the FORWARD FCA
  RULER (`prospective_label.build_forward_case_label` + `merge_forward_labels`,
  CLI `--case-db --graph-dir --defendant-npis --horizon-months --fca-case-only`:
  DOJ/OIG cases with a resolved outcome announced after the cutoff, conduct
  begun on/before it; the exclusion ruler takes the same horizon so two freezes
  share one ruler length); the outcome rule on EVERY case-label tier (org_billing /
  affiliated rows need a settlement / judgment / conviction / plea too, and carry
  fca_strict_tier / intervened / qui_tam); the evidence ladder counts national
  name hits for BUSINESSES (a unique org match is moderate, not weak); the
  TRAINABILITY GATE (the export records `v3` / `provider_stats` / `capacity`
  sources, every NUMERIC adapter column is a candidate feature, trend suffixes
  inherit their base, new partition groups medicaid_ndc / medicaid_enrollment /
  capacity / servicing, and `build_provider_matrix` RAISES when any trainable
  column maps to no source — in-time runs trained without the five v3 billing
  scores and v6/v7 without capacity_mismatch because feature_partition filed
  them `unmapped`, and the July / servicing columns would have followed; frozen
  runs drop the v3 scores earlier as as-of-uncomputable, an OPEN gap; the
  manifest now carries `feature_partition` + `training_policy`); NATIVE missing values in company_list / network_ab /
  source_ablation (no zero fill in the training frame; the single-column leak
  screen keeps its fill); the PER-PATIENT MUE rule (lines > MUE x days x
  patients at billing x servicing x code x month; one-patient fallback logged);
  frozen-run PARITY (`asof_provider_stats`: service_volume = SUM(total_patients),
  gross = positive-only; Makefile `FROZEN_EXPORT_FLAGS ?= --with-analytics`);
  the peer engine's minimum on the metric's NON-NULL count with per-metric
  ladder fallback (`one_sided_percentiles(min_peer=)`, `<metric>__peer_fallback`);
  (a)(3) felony / (a)(12) program-termination revocations tiered weak; and
  ACCURACY DIAGNOSTICS (`model_a/accuracy_diagnostics.py`, run on every frozen
  run folder): org hits@16/@50 on the SHIPPED priority list (or a refit of the
  shipped scorer) with Wilson / exact intervals against random-from-pool
  (hypergeometric), dollar-ranked and size-matched baselines, tie-aware; lead
  time per horizon + the share of hits beyond the gap; the error taxonomy of
  the top 50 / 250; segments with EB-shrunk lift; eval-side label-noise
  sensitivity (ambiguous negatives); cross-fitted isotonic calibration on rank
  bins + org P(any) + FDR-bounded list size; `--repeats` stability (Jaccard of
  the top sets, precision SD); settlement-dollar recall per case; everything
  known at the freeze dropped first; headline = the FCA ruler. Writes
  ACCURACY_DIAGNOSTICS.md + accuracy_diagnostics.json (acc_* KPIs the registry
  reads; `--check` aliases hits50 / prec50_lo / base50 / lead_median /
  lead_ge_gap / inst_share50 / top50_jaccard / eval_policy / horizon /
  dollar_recall50) + accuracy_topk_rows.csv. RULE: v8 is a NEW pre-registration
  (feature set, NaN policy, MUE, peers, label rule and ruler all changed);
  grade it on hits@50 vs random under the FCA ruler, never on decile lift.
  READ-OUTS 2026-10-08/09 (`docs/ACCURACY_READOUT_v7_2026-10-08.md`): v7 and v6
  graded on the shipped list: FCA ruler hits@50 = 1 and 0 (random 0.2 / 0.1),
  the top 50 = institutional + cap-pinned rows with zero operator-class, the NPI
  head a 285 / 219-way tie with no case in the first 1,000 while the top 1%
  carries lift 3 to 4; both HOLD. Built in response: `company_list.fold_assignments`
  (StratifiedGroupKFold), `calibrate_oof` (cross-fit isotonic P), `rollup(probs=)`
  -> `score_p_any` / `score_p_max`; the dossier's companion
  `provider_priority_list_pursuit.csv` (operator book ONLY; the main list
  unchanged); diagnostics `--list-name` + `order_sweep` (section 2b: the same
  book under every sort key) + accuracy_tie_block.csv + the `capped_no_basis`
  cause. SORT KEY SETTLED BY THE SWEEP (v7, FCA, 1,855 operator orgs, 8 pool
  hits): dollar-weighted 1/2/3/4 at 16/50/100/250, p_max 0/2/2/3, p_any
  0/0/0/2, every basis-backed-first variant 0/0/0/x; the operator FILTER is
  the win (main list 0/1/2/3), so the pursuit file sorts on
  score_dollar_weighted then score_max, and score_p_any / score_p_max are
  columns only — never re-introduce a probability sort or a basis-first block
  without a sweep that beats 1/2/3/4. v8 grades the pursuit file.
  POST-AUDIT LEVERS (2026-10-10, built while v8 ran; none touch v8): (1)
  `case_db.mark_duplicate_settlements` tags the same settlement harvested twice
  (`duplicate_of`; rows kept, dollar aggregations skip them); (2) the resolver's
  FUZZY BUSINESS TIER (`fuzzy_resolve_orgs`: distinctive-token containment within
  the mention's state, status `resolved_fuzzy`) is held OUT of training and the
  strict export until `--verification` promotes a row (`fca_label_export
  --include-fuzzy` for the review file); (3) the PER-CASE BUDGET: `case_labels`
  emits `label_weight = min(1, max_case_npis / case_fanout)` on broadcast rows
  (org_billing / affiliated_individual; direct tiers 1.0; rows never dropped;
  `case_concentration` logs the top-5 share raw vs weighted), the export carries
  it as label METADATA (`training_policy.sample_weight`), and
  `company_list.training_weights` feeds it to LightGBM as sample_weight (a
  matrix without the column trains exactly as before: v9 semantics, new
  pre-registration); (4) the LARGE-BILLER BOOK `provider_priority_list_large.csv`
  (`pursuit_dossier.LARGE_LIST_NAME`, `--large-min-billed`, default $10M billed
  in the matrix window): the pursuit book filtered to the band where the v7
  segment table found precision highest, same order, graded on its own with
  `accuracy_diagnostics --list-name provider_priority_list_large.csv`
  (outputs suffixed `__large`; the registry still reads the pursuit grade).
  V9 TRAINING POLICY (2026-10-10, docs/ACCURACY_PLAN 4d): the manifest's
  `training_policy` carries `n_seeds` / `broadcast_rows` / `group_policy` /
  `n_case_clusters` (export flags `--seeds`, `--broadcast-rows weight|mask`,
  `--group-policy org|case_cluster`, default case_cluster); `company_list.
  training_policy(manifest)` + `set_training_policy()` (CLI override on
  pursuit_dossier / accuracy_diagnostics = an A/B on the same matrix, never
  the ship); `case_cluster_groups` merges orgs sharing a DOJ case into one fold
  group; `broadcast_only_mask` = positives whose only evidence is a broadcast
  tier (policy mask holds them out of training); seed bagging averages
  n_seeds fits per fold in both oof_scores and the diagnostics' refit; the
  registry rows carry the three keys (`--check "seeds >= 5"`, `"groups =
  case_cluster"`, `"broadcast = weight"`). run_v9_night.bat reuses v8's as-of
  graph and grades weight vs mask refits into ab_broadcast_*/ subfolders.
  DRUG SCHEMES FROM THE NDC FILE (2026-10-10, operator "yes do it"): (1) DRUG SPREAD
  WITHOUT UNITS: `medicaid_ndc.pharmacy_spread_proxy` (per NDC the national median
  paid per claim line over (pharmacy, NDC) pairs with >= 20 pairs, placeholders out;
  per pharmacy `rx_spread_proxy` = dollar-weighted excess over that median, one-sided,
  `rx_spread_coverage`, `rx_spread_top_share` = covered dollars at >= 2x the median)
  feeds `drug_outlier`; (2) PILL MILL ON MEDICAID: `medicaid_ndc.opioid_ndc_flags`
  derives is_opioid / is_long_acting per NDC from NADAC descriptions (agonist tokens
  in OPIOID_AGONISTS, antagonist-only and buprenorphine MAT products out, long-acting
  by ER / XR / CONTIN / PATCH / METHADONE tokens) -> `px_opioid_claims_share` /
  `px_opioid_paid_share` / `px_opioid_la_share` (prescriber) and `rx_opioid_paid_share`
  / `rx_opioid_la_share` (pharmacy), added to `pill_mill` beside the Medicare file's
  rates; the export passes the as-of NADAC file into the NDC block for both. (3) PHARMA
  KICKBACKS ON MEDICAID (built the same day once the operator downloaded the FDA NDC
  Directory): `ingest_cms/fda_ndc.py` (`load_fda_product` reads product.txt / the
  folder / the zip, Latin-1 tolerant; `labeler_map` = labeler5 (4- or 5-digit labeler
  zero-padded) -> modal LABELERNAME -> `mfr_key` via `manufacturer_key`, the shared
  first-distinctive-token normalizer applied to Open Payments payer names too;
  `product_schedule` = ndc9 -> DEA schedule / is_controlled) +
  `medicaid_ndc.medicaid_kickback` (Open Payments payments by (npi, mfr_key, first
  pay month, amount; `OP_COLS.payment_date` added; payments after the cutoff dropped)
  x the NDC fact's labeler5 -> `px_paid_mfr_share`, `px_paid_mfr_count`,
  `px_post_payment_lift` = max(0, (post - pre) / (post + pre)) over the window after
  vs before the first payment, pooled across pairs, `px_op_payment_total`; no payment
  = NULL never 0), added to `pharma_kickback`. The export's NDC block loads
  preclean/fda_ndc/ and reuses the Open Payments raw; the kickback block's
  `return frames` on --skip-sources kickback (which silently skipped every adapter
  after it, the July files included) is fixed. Preflight registry entry `fda_ndc`.
  Scheme weights changed: v9 semantics.
  LEARNING LOOP (`model_a/learning_loop.py`, 2026-10-10; night-runner step before
  the ledger): on ONE out-of-fold scoring (reads `provider_oof_scores.parquet`,
  which `build_priority_list` now persists, else computes it): (1) LABEL NOISE by
  confident learning (t1 / t0 = mean calibrated p of positives / negatives;
  suspect positives p < t0 with provenance, candidate positives p >= t1) ->
  label_noise_*.csv; (2) the ACTIVE-LEARNING QUEUE label_review_queue.csv +
  label_review_prompts.jsonl in the dossier's dispatch schema (`pursuit_dossier
  --dispatch-file`, one dispatcher at a time; verdicts are evidence, never the
  label); (3) FEATURE DRIFT vs `--prev-run-dir` (PSI on a DuckDB reservoir
  sample, FAIL >= 0.25 / WARN >= 0.10, null-rate delta) -> feature_drift.csv;
  (4) NEAR MISSES on the forward ruler (positives outside the top fraction, the
  five features where each sits furthest below the top set's robust profile, the
  recurring ones = feature requests) -> near_misses.csv; (5) CALIBRATION monitor
  (calibration_metrics ECE / Brier + `conformal_p` per provider via
  conformal_pvalues, written back to the OOF file: the first callers of those two
  modules). LEARNING_LOOP.md + learning_loop.json; run_issues folds drift / noise /
  misses / ECE into the ledger. Nothing changes a label or a model.
  RUN ISSUE LEDGER (`model_a/run_issues.py`, 2026-10-10; last step of every
  night-runner, after registration): reads the run's own artifacts (manifest
  expectations / sources_audit / feature_partition, EXPECTATIONS_REPORT rows,
  scheme_health, the SCHEME_TRIAGE vintage audit, the network verdict, the
  accuracy KPIs, label_audit.json, the locked pre-registration checks via
  `check_prereg`, and the previous run for what got worse) into ONE ranked list
  (FAIL first, recurring before new) with a named fix per row -> RUN_ISSUES.md +
  run_issues.json, folds it into `<data_root>/model_a/issues_ledger.jsonl`
  (first_seen / runs / open -> resolved when an earlier issue is absent now),
  and suggests the `--check` lines the next pre-registration should carry.
  Nothing is auto-fixed: model behaviour changes only through a new
  pre-registered run. On the v7 packet it ranks the 2023-dated facility / PBJ /
  POS editions inside the 2021-12 freeze as FAILs.
  ENROLLMENT FILE, THREE USES WIRED (2026-10-10 check): (1) STATE: `fill_state`
  was defined but never called; the export now fills a blank `practice_state` from
  `mcd_primary_state` after the adapter merge (manifest `state_fill_from_enrollment`),
  and the defendant resolver + contradictions fill provider_dim's `addr_state` from
  processed/medicaid_enrollment_features.parquet when it exists; (2) LABEL: ended-
  for-cause segments -> exclusions_medicaid_enrollment.parquet -> graph exclusion
  nodes (glob) -> `fca_relevance` tiers `mcd_enroll` by the state-termination ruler
  -> PU label + forward ruler; the strict export's `fca_shaped` policy now admits
  fraud-shaped `mcd_enroll` rows as `state_fraud` (source `mcd_enroll_<st>`, case
  type `medicaid_enrollment_end`); (3) PER SE: `paid_after_enrollment_end` ships
  `mcd_paid_after_end` on the matrix, and `contradictions._events` reports the
  enrollment ends as their own Tier A type `billing_after_enrollment_end` (window
  closed by the next segment start; managed-care / run-out / other-state innocent
  list) instead of folding them into billing_after_exclusion.
  V8 READ-OUT (2026-10-10, `docs/ACCURACY_READOUT_v8_2026-10-10.md`): HOLD, cause =
  PIPELINE ERROR not model performance. The night-runner's export line dropped
  `--case-db` / `--defendant-npis`, so the matrix trained on 920 exclusion positives
  and ZERO DOJ case labels while the FCA ruler graded it; the servicing grain added
  843,551 rendering-only rows (universe 1,460,613, 58% without a billing row) and
  diluted every percentile; refund rows pushed two sv_* shares outside [0,1]; five
  features were dead. hits@50 = 0 (random 0.3), top-1% FCA lift 0.76, Jaccard 0.00;
  the v8 dossier was NOT dispatched. Fixed in code: `servicing_grain` shares on
  positive dollars + clipped; export `--no-servicing-rows` (sv_* features kept, no
  rendering-only rows: the v9 universe is the billing-NPI universe); the export
  records `doj_case` as SKIPPED with the reason when no `--case-db` is given, and
  `run_issues` raises `label:doj_case:missing_case_labels` as a FAIL on any manifest
  whose sources carry no `doj_case`; run_v9_night.bat carries `--case-db`,
  `--defendant-npis`, `--no-servicing-rows` and stops itself when the SOURCES_REPORT
  says the matrix has no case labels. RULE: every training runner's export line
  carries `--case-db` + `--defendant-npis`; a run whose ledger shows the doj_case
  FAIL is not a model result and is never read as one.
  FUZZY TIER VERIFIED AND TIGHTENED (2026-10-10): the first fuzzy batch (108 rows, 11
  Manus tasks) confirmed 28 (26%): 45 wrong_npi, 33 wrong_person. Three failure classes,
  each now a rule in `fuzzy_resolve_orgs`: (1) HEADLINE DESCRIPTORS ("Telehealth
  Company", "Tampa Bay Autism Service Provider", "Fresno-Based Community Health System")
  never enter the tier (`is_headline_descriptor`: every token a place / sector /
  filler word or legal tail, "<City>-Based" / "-Area" read as a place); (2) ONE-WORD
  mentions ("ILYAYEV", "Diversicare") never enter; (3) containment is near-equality:
  the candidate may carry NO distinctive token the mention lacks when the mention has
  one distinctive token, at most ONE extra when it has two or more, and that extra only
  when the mention's city agrees with the candidate's (or no city is given); legal
  tails are stripped on both sides so "Mori, Bean and Brooks, P.A." still lands on
  MORI BEAN AND BROOKS INC. The verification prompt now says a legal-tail / spelling
  variant is the SAME entity (confirmed, not wrong_npi) and that the convicted owner's
  billing vehicle is a party (confirmed, not wrong_person): part of the 26% was the
  agent grading P.A. vs Inc as a different business. The tightened tier re-runs after
  v9 with `--verification` (the 28 confirmed promote), then a fresh sample goes out.
  `_nppes_person_key` survives an org-only provider table. `--verification` takes
  several results files (every batch's verdicts at once).
  V9 INVALID (2026-10-11): v9 ran on the OLD v9 runner. Windows saved the refreshed
  download as "run_v9_night (1).bat" and the copy line picked the stale file; the
  console shows "step 1/10" (the new runner says /12), the findstr guard printed
  nothing, "[doj_case] skipped: no --case-db", "843,551 servicing-only rows added".
  v9 = v8 repeated (hits@50 0 on every book and both broadcast arms); registered
  HOLD, no packet / dossier / poll. RULE: a refreshed runner ALWAYS gets a NEW
  filename (run_v10_night.bat), and the operator must read the findstr guard line
  before pre-registering. The v10 runner also stops itself after the export when
  the SOURCES_REPORT says the matrix carries no case labels. v9's one new skip:
  `billing_lm` Out of Memory in DuckDB on the 1.46M-row universe (the ledger
  carries it; the billing-NPI universe halves the load for v10).
  FUZZY BATCH 2 (2026-10-11, 39 rows under the tightened tier): 10 confirmed (26%),
  23 wrong_npi, 4 wrong_person. The descriptor / surname classes are gone; the
  survivor is ONE BRAND WORD on a random local business ("Kindred Healthcare" ->
  KINDRED HOSPITALS EAST, "Vibra Healthcare" -> VIBRA CLINICS, "Medco Health
  Solutions" -> MEDCO LLC, "United Therapeutics" -> D E THERAPEUTICS): the stop list
  stripped health / care / system / network / holdings from both sides before the
  equality test. RULE 4 (`_name_words`): every non-tail word of the mention must
  appear in the candidate, stop words included ("healthcare" reads as "health
  care"; tails = inc/llc/corp/company/pllc/... and connectors). LAST-CHANCE RULE:
  batch 3 under rule 4 reads 90%+ or the fuzzy tier is retired; civil-FCA
  defendants are parent corporations whose NPIs are differently named
  subsidiaries, which is an OWNERSHIP question (PECOS owner -> orgs broadcast) and
  a research question, not a name-matching one. Verified rows promote regardless
  (a verdict is per-row evidence); unverified fuzzy rows never ship.
  V10 READ-OUT (2026-10-11, `docs/ACCURACY_READOUT_v10_2026-10-11.md`): the FIRST
  VALID RUN of the series (6,366 DOJ-case NPIs, 1,644 positives, 617,062-row
  billing universe, 81 case clusters, 474 down-weighted broadcast rows). Pursuit
  book under the FCA ruler: hits 2 / 2 / 3 / 4 at 16 / 50 / 100 / 250 (random
  0.1 / 0.3 / 0.5 / 1.3; p 0.002 / 0.024 / 0.010 / 0.022), median lead 26 months,
  institutional share 0, top-1% NPI lift 2.2. HOLD on ONE real check: top-50
  Jaccard 0.09 (the dollar-weighted score below rank 1 is a 0.2000 PLATEAU, so
  the order inside it is fold noise while every re-shuffle still finds 2 hits).
  The other recorded FAIL, `prec50_lo > base50`, PASSED in fact (0.011 > 0.005):
  `eval_check` compared the metric to the literal word "base50" (v8 / v9 / v10
  all carried this false FAIL) and now resolves a right-hand side through the
  same aliases (`value_from`). Five sources died of memory on the operator's
  machine (servicing, mue, sector_schemes, medicaid_enrollment at a 386 MB
  allocation, billing_lm): 18 schemes scored instead of 22; v11 needs a quiet
  machine and a free-memory check in the runner. The dossier's default top-10
  came from the MAIN list (five hospitals + a national DME chain):
  `pursuit_dossier --operator-book` picks targets from the operator book
  (the entity_segment rule: no institutional names / 25+ providers / government /
  solo individuals); the v10 dispatch uses it. The broadcast A/B (single-seed
  refits: weight 0, mask 1 at 50) is too noisy to set the v11 policy. v11 =
  memory casualties back + a continuous tiebreak inside the plateau, graded by
  the order sweep before it ships.
  V11 BUILD (2026-10-11, operator: "no Manus credits on a HOLD run; make the next run
  the best yet"): (1) `src/attempt_2/duck.py` = ONE DuckDB connection factory
  (`connect()`: temp_directory so a hash aggregate SPILLS instead of asking the OS
  for RAM it does not have, memory_limit = MEDICAID_DUCKDB_MEM else 40% of physical
  RAM, temp dir MEDICAID_DUCKDB_TMP); servicing_grain, mue, sector_schemes,
  billing_lm, medicaid_ndc and medicaid_enrollment connect through it (the bare
  `duckdb.connect()` had no temp directory = no spilling = "Allocation failure").
  (2) `medicaid_enrollment.enrollment_features_from_parquet` computes the ten mcd_*
  columns INSIDE DuckDB (the export uses it; the pandas path stays for small frames
  and the parity test pins the two to the same numbers; `asof` is a DuckDB keyword,
  the alias is `asof_d`). (3) THE SCORER: `network_ab.SCORER_PARAMS` (300 rounds at
  lr 0.05, num_leaves 15, min_child_samples 100 scaled down to 20 on small frames,
  subsample 0.8 / freq 1, colsample 0.7, reg_lambda 1.0) replaces the bare 200-tree
  LightGBM; `_fit_predict` is shared by the production scorer, the A/B harnesses and
  the ablations, so this is v11 semantics (new pre-registration). (4) `build_provider_
  matrix` drops trainable columns with <= 1 distinct value and records them in the
  manifest `dropped_constant`; run_issues emits a WARN per dropped column. (5)
  `_asof_pick(fallback_newest=True)` for the NADAC reference in the NDC block (logged
  as a drug-name lookup after the cutoff). (6) `rx_spread_top_share` on positive
  dollars, clipped. run_v11_night.bat (new filename): MEDICAID_DUCKDB_MEM=6GB +
  _TMP under interim/, a 10 GB free-RAM gate, and a stop after the export if any of
  the five sources is skipped. The v10 dossier was NOT dispatched (closed as skipped).
  VM KIT (2026-10-11, operator: "move to a VM depending on cost; run the graph
  embeddings in full and every feature"): the laptop is 16 GB (840 MB free when
  measured; the resolver alone holds 4.8 GB). `docs/VM_RUNBOOK.md` (costs: 16 vCPU /
  128 GB about $1/hour, about $6 a run, a 500 GB disk about $40/month while
  stopped; provisioning; rclone pulls the data root straight from OneDrive; tmux;
  the packet back), `ops/vm/setup_ubuntu.sh`, `ops/vm/sync_data.sh` (down = data
  root from OneDrive; `up <run>` = reports + registry files back),
  `ops/vm/run_frozen_night.sh <tag> <cut>` = the Linux twin of the night runner
  with `--max-embed-nodes 0` (no cap) rebuilding `graph_asof_<cut>_full`, a 24 GB
  free-RAM gate, and a stop when any of servicing / mue / sector_schemes /
  medicaid_enrollment / billing_lm / graph_embeddings is skipped. The VM pulls the
  code with `git pull` from the pushed branch (no bundles); the laptop stays the
  operator's seat (merges, pushes, Manus, reading). v12 = the FIRST VM run (the
  AWS vCPU quota was pending on 2026-10-11, so v11 ran on the laptop fallback).
  FUZZY TIER RETIRED (2026-10-11): batch 3 under rule 4 read 9/23 (39%); the three
  batches read 26 / 26 / 39 and the survivors were always the same two shapes, a parent
  corporation on a differently named local business (Prime Healthcare Services ->
  PRIME CARE HEALTH SERVICES; word-set containment ignores order and "healthcare" =
  "health care" made them equal; North Fulton -> SOUTH FULTON because compass words
  were place words) and headline descriptors no word rule catches ("O.C.-Based Health
  Care Organization", "RGV Area Home Health", "Berks County Group"). Those are
  OWNERSHIP (PECOS owner -> orgs broadcast) and RESEARCH questions, not name matching.
  `defendant_resolver.retire_unverified_fuzzy`: the tier still runs so
  `apply_verification` promotes the 47 verdict-confirmed rows on every rerun; every
  row left `resolved_fuzzy` is demoted to `fuzzy_retired` (candidate kept in
  `npi_candidate`); `--fuzzy` keeps them for a review file only. Nothing unverified
  reaches training or the strict export. Total label gain from the tier: 47 rows for
  about 170 Manus research rows; the next recall lever is the research residue.
  The operator's first rerun promoted only 30 of the 47: `apply_verification` promoted a
  confirmed verdict only onto a row the CURRENT tier produced, and the tightened rules no
  longer match 17 of the confirmed mentions. Fixed: a confirmed (case, defendant, Luhn NPI)
  verdict promotes the mention on its own (`carried`, match_level `fuzzy_verified`,
  strength moderate in fca_label_export.OK_MATCH_LEVELS). A verdict outlives the rule.
  V11 READ-OUT (2026-10-12, `docs/ACCURACY_READOUT_v11_2026-10-12.md`): the first
  COMPLETE run (40 sources, 22 schemes, the five v10 memory casualties back, plateau
  gone: 1 tie at rank 50). HOLD on three checks, all about the top 50: hits@50 = 1
  (bar 2; random 0.3), prec50_lo 0.0035 < base 0.0057, top-50 Jaccard 0.60 (bar 0.7;
  v10 0.09; 31 of 50 orgs in every draw). Below 50 the best list yet: 4 at 100 and 6
  at 250 (random 0.6 / 1.4, p 0.001 both), NPI top-1% lift 8.4 (v10 2.2), lead 29 mo,
  institutional share 0. 44 of the top 50 are `capped_no_basis` rows with 0 hits;
  the order sweep (basis-first 1/2/3/3 vs shipped 0/1/4/6) does not beat the shipped
  order across budgets, so the sort stays. Broadcast A/B refit: weight 1/2/3/5, mask
  0/1/1/4 (policy stays weight). Fixed from the packet: rx_opioid shares on positive
  lines + clipped (NULL stays NULL: DuckDB greatest() skips NULLs); the near-miss z
  guarded (MAD floor 1e-6 x scale, z clipped at 50; rx_spread_coverage had read
  -2.7e14); the operator-book dossier sets aside person-named ("LAST, FIRST", no
  business token: `person_shaped_name`) and unnamed orgs; the large book's floor is
  50M (at 10M it equalled the pursuit book, since the list's case-size bar implies 10M
  billed); acc_org_hits100 / hits250 KPIs + registry aliases `hits100` / `hits250` /
  `base100` / `p100_vs_random` so a pre-registration can hold the deeper budgets.
  OPEN, needs the data: subscore_invalid_identity is all-NaN (0% coverage) in three
  runs, and medicaid_enrollment found 0 segments ended for cause in 3.6M NPIs (its
  label line is empty); `src/parquet_probe.py` (`--pattern` liveness table,
  `--values` top values, DuckDB-streamed) is the operator's one-line answer to both.
  v12 = first VM run: full embeddings, SEEDS=10 (run_frozen_night.sh env), the fixes,
  checks add `hits100 >= 4` and `seeds >= 10`. The v11 dossier was not dispatched.
  THE TWO PROBES ANSWERED (2026-10-12): (1) billing_after_deactivation had 9,378 non-null
  rows (9,195 exactly 0, 183 positive) and its __peerpct 0: the peer ladder has no
  national level, so thin cells fall to "not scored" and all-zero cells are degenerate,
  NaN everywhere, and the subscore that consumed the percentile died with it. Fix:
  `RAW_SUBSCORE_INPUTS` (billing_after_deactivation, billing_after_death) bypass peer
  normalization and drive invalid_identity raw (clipped 0..1); `_one_sided_peer_pct`
  logs any column with raw values but no percentile (a dead __peerpct). (2) the
  enrollment segments parquet carries NULL status and reason on all 66M rows: the raw
  file's column names are not in ENROLL_COLS, so the label line was empty in every run;
  the loader now WARNS loudly when neither maps. HEADER READ 2026-10-12: NPI, STATE_CD,
  ENRL_START_DT, ENRL_END_DT, ENRL_STATUS_CD, ENRL_STATUS_DESC ("ACTIVE - ACTIVE",
  "TERM - STATE EXCLUSION/ DEBARMENT, ETC.": the DESC carries the cause, no reason
  column), ENRL_PLAN_CD/DESC (MEDICAID / CHIP), PRVDR_TYPE/DESC; rows are MONTHLY per
  NPI x state x plan. ENROLL_COLS maps them; `termination_events` is vectorized
  (merge_asof) and collapses a status held for N months into ONE event dated the first
  month-end, reinstated by the next benign start; pending / in-process statuses are
  benign. The operator rebuilds the segments (reminder enrollment_rebuild) and pastes
  the status table so BENIGN_END / STATE_TERM_RULES can be tuned to the real vocabulary.
  The first rebuild (2026-10-12) wrote the 66M-row segments parquet correctly, then the
  CLI's `pd.read_parquet` + pandas feature path died on the 16 GB laptop ("realloc of
  size 10781263744 failed"): the CLI now runs `enrollment_features_from_parquet` AND the
  new `termination_events_from_parquet` (episodes + reinstatement as one DuckDB query,
  only the episodes come back) through the shared `duck.connect()` factory; the pandas
  functions stay for small frames and a three-cutoff parity test pins the two paths.
  THE STATUS TABLE READ (2026-10-12, 439,416 ended episodes, 389k NPIs): every state
  stamps ONE bulk code on nearly every termination (FL "TERM - OTHER" 114k, CO
  "NON-COMPLIANCE" 46k, TN "MEDICAID AUTHORITY" 23k, MO "ACTION TAKEN BY MEDICAID/CHIP"
  19k, IA "STATE EXCLUSION/ DEBARMENT" 13k of IA's 14k), and under label policy 'all'
  (the runners pass no --label-tier, so `provider_on_exclusion` = ANY exclusion node)
  all 389k would have trained as positives beside about 2,500 real ones. Fix, three
  layers: (1) `fca_relevance.MCD_ENROLL_EXCLUDED` tiers the no-cause codes 'excluded'
  (OTHER / UNKNOWN / DENIED / duplicate number / not operational / failure to report /
  NON-COMPLIANCE / MEDICAID AUTHORITY / ACTION TAKEN), Medicare-Medicaid exclusion and
  state exclusion/debarment weak, license actions weak, fraud text hard; (2)
  `medicaid_enrollment.split_label_candidates`: only a sanction-NAMED status
  (`LABEL_WORTHY`) that is not the state's bulk code (`STATE_DEFAULT_SHARE` 0.40 of
  the state's ended episodes, states with >= 100 episodes, weak tier only: a hard
  fraud status is never a "default") reaches exclusions_medicaid_enrollment.parquet;
  everything else is kept in medicaid_enrollment_ends_administrative.parquet with
  `drop_reason` + `state_share` (quarantine, never delete) and its own report table;
  (3) `_widened_label_from_graph` logs positives by source and WARNS when one source
  outweighs all others combined. Expected label candidates: a few thousand license /
  exclusion ends (FL 3k, NC/MN/OH/CT hundreds), weak tier; the per-se
  billing_after_enrollment_end check keeps reading the features' last end date.
  SECOND REBUILD (2026-10-12, bundle 285): 439,416 ended episodes = 6,435 label
  candidates (weak 6,365: FL license 3,095, NC / MN / MA / IA Medicare-Medicaid
  exclusion, CT / MN / AL license revoked, state / federal debarment; hard 70: AL
  abuse of billing privileges 40, MO felony conviction 18) + 432,981 administrative
  (no_sanction_named 419,830, state_default_code 13,100 = IA's bulk "STATE
  EXCLUSION", excluded_tier 51). Gray zone for a v13 label ablation, NOT in v12's
  label: the agency codes where they are a minority code (FL "MEDICAID AUTHORITY"
  2,133 at 2 percent, IA / TX / AL "ACTION TAKEN" at 5 to 6 percent). The same report
  showed median tenure 95,809 months: placeholder end dates (9999-12-31) set the
  current-day as-of 8,000 years out. Both feature paths and both event paths now take
  the as-of as the latest end date NOT in the future, and an end after it is an OPEN
  segment (frozen runs pass a real cutoff and were never affected).
  THIRD REBUILD (2026-10-12, bundle 286) read 299,247 ended episodes / 3,726 candidates:
  nulling the placeholder ends had turned every termination STILL IN FORCE (one row,
  TERM status, end 9999-12-31) into an "open segment" and dropped its event. The two
  readings of a non-benign status row are now explicit in both paths (`_future_end`):
  a real end beyond the cutoff = still enrolled as of the cutoff (no event, an open
  segment: the state-list shape where the status says how the segment ENDED); an
  open-ended terminated status = a termination in force, dated by its START, not an
  open enrollment (the CMS monthly shape where the status is the state DURING the
  row); a row with a real end = an event at that end. mcd_open_segments counts only
  benign or future-end rows, mcd_last_end_date takes the start of an in-force
  termination, and the placeholder test covers both readings at two cutoffs.
  FOURTH REBUILD (2026-10-12, bundle 287): 1,001,644 ended or in-force episodes (the
  560k terminations still in force were invisible before), median tenure 140 months,
  22,232 label candidates (weak 21,855, hard 377). The table showed a convention the
  share rule misses: CT "LICENSE REVOKED" on 9,732 providers (19 percent of CT's
  ends, about 100x any other state's rate per enrolled NPI), AZ "STATE EXCLUSION/
  DEBARMENT" 3,080; NC's bulk "MEDICARE/MEDICAID EXCLUSION" 94,336 was already
  demoted at 70 percent. Bundle 288 adds the RATE RULE (`state_outlier_rate`): a
  weak sanction-named status is demoted when the state applies it at
  `STATE_OUTLIER_RATE_X` (20) times the leave-one-out median rate per enrolled NPI of
  the other states with >= 10,000 NPIs that use the same text (>= 3 references, >= 100
  rows); `state_npi_counts_from_parquet` supplies the denominators, the admin table
  carries `state_rate`. FL's 3,095 license actions sit within a few x of OH / IN / AL
  and stay. Hard statuses are never demoted.
  FIFTH REBUILD (2026-10-12, bundle 288): 5,427 candidates; the rate rule had also
  demoted FL "LOSS OF LICENSE" (0.98 percent of FL's enrolled NPIs) and OH (0.4
  percent) because the reference states barely use the code (GA 0.01, TX 0.0003
  percent) and the median sat near zero. Bundle 289 adds `STATE_OUTLIER_MIN_RATE`
  (0.02): a convention must ALSO cover at least 2 percent of all the state's enrolled
  NPIs (no real sanction category does). CT 8.9 percent and AZ 2.25 percent stay
  out; FL and OH come back; expected about 9,400 candidates.
  THE TRAINING LABEL IS FCA-SHAPED ONLY (operator, 2026-10-12: "our label should be
  purely focused on the types of fraud cases that could be prosecuted under the
  False Claims Act"). The ruler already tiered every record (hard = FCA-shaped on
  its face; weak = license actions, unstated-cause exclusions, patient abuse,
  derivative; excluded = never), and the FORWARD ruler used `--fca-tier hard`, but
  the runners trained under policy 'all'. Now: `ops/vm/run_frozen_night.sh` passes
  `--label-tier hard --exclusion-nodes <as-of graph>` (TRAIN_TIER env) to
  pursuit_dossier, every accuracy_diagnostics call and learning_loop;
  `company_list._training_label` RAISES when a tier policy has no exclusion nodes
  (it used to fall back to 'all' silently); `default_exclusion_nodes` finds the
  VM's graph_asof_<cut>_full; accuracy_diagnostics resolves the default nodes;
  `training_label_summary` is written by build_priority_list as
  training_label_policy.json and the registry reads it as `train_policy` /
  `train_positives` (aliases `train`, `train_positives`): v12 pre-registers
  `train = hard`, distinct from `policy` (the forward ruler). The enrollment
  file's bare "FELONY CONVICTION" is weak (a conviction of unstated kind); "ABUSE
  OF BILLING PRIVILEGES" stays hard. The weak candidates stay in the exclusions
  file (graph nodes, fraud proximity, the per-se checks) but never train.
  PI FEEDBACK LOOP (2026-10-12): the field investigator's first report on PI packet
  v11 (surveillance of three cards + former employees WITH tenure windows for about
  20 practices; interviews next) is attorney work product: it lives under
  <data_root>/enforcement/pi_reports/, never in git, and no person it names goes
  into a committed file or a reminder. `pi_facts --deep` now also writes the WHEN
  sheet (`window_sheet` -> PI_FACTS_<set>_WINDOWS.md + _windows.json: active years =
  years with >= 25 percent of the peak year's dollars, ramp month, peak year, first /
  last billed, per practice, no person named) so interviews go to formers whose
  tenure overlaps the active window. `--orgs-file` / `--npis` accept bare NPIs
  (resolved to org ids through the run matrix: the packet prints NPIs); the
  packet's NPI list is a target list and stays under enforcement/pi_reports/,
  never in git. `billing_facts` also names the rendering NPIs by dollars and
  share (a clinician seen on site who is not on the list worked under someone
  else's number) and, per top code, the worst / median month in lines per
  CALENDAR day and patients (for a per-diem code that is residents per day and
  the census: the occupancy ratio against licensed places, the Special Homes
  question; for a visit code, patients that month against the clinicians). The investigator's titles list (same day) is
  read by the ROLE RULE: billers / revenue-cycle / QA / compliance know whose NPI
  went on the claim, rendering clinicians know who saw the patient, HR / operations
  / service coordinators know the headcount (the innocent explanation on every
  hours-per-day card, so that role ends a card as fast as it advances one); front
  desk and assistants are second-call. A card whose formers are all coordinators
  and teachers with no therapist among them reads as an agency, not a clinic.
  The read-outs (PI_FEEDBACK_CROSSREF / PI_ROLES_READOUT) identify people by role
  and tenure only and stay with the PI report, never in git.
  ORG FIND (`model_a/org_find.py`, 2026-10-12, counsel's "did we see this one?"):
  `--name <regex>` prints every NPPES NPI the name matches (provider_dim), the
  same NPIs in the run matrix (org node, dollars, label, top subscores) and their
  rank on every provider_priority_list*.csv, plus the pi_facts line for the org
  ids; `--codes 90867,90868,90869` prints the matched NPIs' dollars and share on
  the codes and the NATIONAL top-N billing NPIs on them with distinct rendering
  clinicians and the entity type, classed by shape (an individual's NPI with one
  or no renderer on the codes = one person's number; an org NPI over many
  renderers = the agency shape); `--npi <list>` = whose number went on their work
  (dollars under the NPI's own number vs as the rendering clinician on other
  billing NPIs' claims, with the billers it rendered under: the question NPPES
  cannot answer about a staff NPI a PI found at a card's address). All DuckDB;
  the fact never enters pandas. A tip from outside the model is answered from
  the operator's files, never from memory.
  EMBEDDINGS BACK ON IN FULL (operator, 2026-10-12): the node cap was already liftable
  (`--max-embed-nodes 0`, the VM runner's setting); the walk lightening above one
  million connected nodes (4 walks of 12, window 3 instead of 10 of 20, window 5) was
  hard-coded for the 16 GB laptop and is now `compute_node_embeddings_sparse(
  light_walks_above=)` / `python -m src.entity_graph --light-walks-above N` (0 = never
  lighten); `ops/vm/run_frozen_night.sh` passes 0. The component cap (150k) and the
  mail-drop pruning (100 orgs per address) stay: those are signal rules, not memory.
  V12 READ-OUT (2026-10-13, `docs/ACCURACY_READOUT_v12_2026-10-13.md`): the first
  VM run (full embeddings, 10 seeds, train = hard, 40 sources, 22 schemes, 0 calc
  FAILs). Pursuit book, FCA ruler: 1 / 1 / 4 / 7 at 16 / 50 / 100 / 250 (random 0.1
  / 0.3 / 0.6 / 1.4; the best 250 of the series), lead 29 mo, inst share 0, NPI
  top-1% lift 6.7. HOLD on the same three top-50 checks as v11 (hits50 1, prec50_lo
  < base, Jaccard 0.62); 44 of the top 50 are capped_no_basis with 0 hits for the
  third run. THE LARGE BOOK PASSED: 3 / 5 / 5 at 50 / 100 / 250 (p 0.004 at 50).
  Order sweep split: basis-first 1/3/3/3 vs shipped 1/1/4/7 (sort stays; a MIXED
  order is the v13 sweep row). Full embeddings: NO MEASURABLE SIGNAL (+0.007 AUC,
  CI spans 0); ten seeds did not move Jaccard. The operator-book dossier named
  Mayo Clinic Arizona / SSM Health / Sutter VNA / Assurex / Ameritox / Alere: the
  entity_segment name cue needs a curated institutional list before v13's dossier.
  Not dispatched. v13 = two graded books (pursuit at 100/250, large at 50), the
  mixed-order sweep row, the institutional list, Jaccard bar 0.6 or at 100.
  The VM packet is a tar (zip is not on the image; setup now installs it).
  V13 CODE (2026-10-13): `pursuit_dossier._NATIONAL_NAMES` / `is_national_name`
  (curated hospital-system / national-lab / national-chain names) joins the
  entity_segment institutional rule; `pursuit_order(book, 'dollar'|'basis50')` is
  the ONE sort definition shared by the shipped pursuit book (`--order`, runner
  env PURSUIT_ORDER) and the diagnostics' order sweep (row "basis-backed first in
  the top 50"); the registry reads accuracy_diagnostics__large.json as
  `acc_large_*` with aliases `large_hits50` / `large_prec50_lo` / `large_base50`
  so a pre-registration grades two books. THE SWEEP (v12 re-graded 2026-10-13):
  basis50 on dollar-weighted = 1 / 3 / 3 / 7 vs shipped 1 / 1 / 4 / 7 (wins 50,
  loses 100, ties 16 and 250): not a win across budgets, so the pursuit sort
  STAYS dollar and the 50 budget is graded on the large book. v13 runs with
  PURSUIT_ORDER=dollar.
  V13 READ-OUT (2026-10-13, `docs/ACCURACY_READOUT_v13_2026-10-13.md`): HOLD, cause =
  the v13 CODE. The national-name list in `entity_segment` moved 43 orgs out of the
  graded pursuit book and FIVE OF v12's EIGHT FCA hits with them: the forward hits
  were national home-health / DME / lab chains that settled qui tam cases. 'Do not
  send a PI' is not 'not a case'. Same ruler (509), same scorer (NPI lift 6.3):
  pursuit 0/0/1/2 on a 3-hit pool, large 0/1/2/2 on 2. Inside the owner-run class
  the model is near chance; its demonstrated precision lives in chains and large
  billers (label recall on owner-run operators is the lever). The dossier still
  named Mayo / SSM / Sutter / Assurex / Ameritox / Alere (the operator-book pick in
  `build()` has its own mask). FIXED: the list is OUT of entity_segment (written as
  a `national_chain` flag column) and IN the dossier's operator-book target pick;
  `ops/vm/regrade_from.sh <new> <src> <cut>` re-grades an existing matrix under a
  new list definition as a new tag (steps 6 to 12 only, no retrain). v14 = v13's
  matrix re-graded, pre-registered under the v13 checks. Not dispatched.
  LABEL AUDIT + EXPANSION PLAN (2026-10-13, operator: 'a full audit on our label and
  a plan to expand it without compromising integrity; the goal is FCA qui tam
  cases'; `docs/LABEL_EXPANSION_PLAN_2026-10.md`): from the packets: 3,049 forward
  positives on paper, 509 in the universe (83 percent never join: convicted
  INDIVIDUALS who never billed under their own NPI), 24,774 undated exclusion rows,
  872 felony-unstated + 839 license-unstated + 705 not-operational rows hidden in
  the weak tier, 130 post-cutoff cases resolvable to the universe, a $4M hospital
  VOLUNTARY self-disclosure as the largest training case (241 NPIs), the qui_tam
  flag present on every case row and absent from the ruler. Plan: Tier 0 (no new
  data) = employer resolution for convicted individuals (basis
  affiliated_conviction), a qui tam ruler, self-disclosures out, a second freeze
  (2020-12) + 48-month horizon, date the undated; Tier 1 (official records with
  identifiers) = the OIG CIA roster as the recall ruler + exact-name positives,
  unsealed qui tam dockets with outcome entries, state AG / MFCU resolutions in the
  ten largest Medicaid states, big-state sanction lists with reasons, license-
  number joins for board actions, reading the unstated felonies. Integrity gate on
  every row: official resolved outcome or fraud-shaped statute, a hard identifier,
  a date, a tier + provenance; verification sample before any source trains.
  THE AUDITOR'S NUMBERS (LABEL_AUDIT_v13.md, 2026-10-13): LEIE 8 percent valid NPI
  (66,081 name-only), SAM 6 percent (11,234 bad Luhn), state lists 19 percent;
  13,141 valid NPIs across the feeds, 1,125 in the universe; DOJ training
  positives 959 of 1,082 by org-billing broadcast (hospital settlements), 123
  resolved defendants, 2 affiliated; 16,911 cases, 0 with an NPI in the source,
  315 resolve to a scored NPI, 5,976 say qui tam, 2,681 duplicate pairs ($547B);
  CIA 339 parties, 32 percent in the case DB, 27 percent resolve by name.
  BUILT (bundle 307, `model_a/label_expansion.py`): `affiliated` = hard-tier
  excluded individuals -> the biller whose claims carried them as RENDERING NPI in
  the 24 months before the exclusion (one biller per individual, share >= 5
  percent or $25k; the rest listed with drop_reason; `prospective_label
  --affiliated` folds the kept billers in as positives dated by the exclusion,
  excl_type affiliated:<statute>); `cia` = CIA parties -> enrolled providers by
  EXACT name key to an org node or to a PECOS ORGANIZATION OWNER then every org it
  owns (chains over 150 orgs listed, never broadcast; label_weight = budget / NPIs;
  cia_cases.csv in the case-DB schema + cia_defendant_npis.parquet in the resolver
  schema + CIA_RESOLUTION.md + cia_residue.csv); `case_db.is_self_disclosure` +
  the `self_disclosure` column, set aside by case_labels' outcome rule; the QUI TAM
  ruler (`build_forward_case_label(qui_tam_only=True)`, `prospective_label
  --qui-tam-only`, `accuracy_diagnostics --qui-tam-ruler` -> rulers['qui_tam']).
  No fuzzy matching anywhere in the chain. The three rulers run on v14's matrix as
  a SWEEP (task label_expansion_run); v15 pre-registers the winner.
  MANUS LANES (operator 2026-10-13, bundle 308, plan section 6): `label_expansion
  names` resolves name-only hard-tier exclusion rows since 2015 OFFLINE first (the
  defendant resolver's NPPES index: unique record in the state = resolved,
  several = ambiguous residue with the candidate NPIs, none = no_match; resolved
  rows -> exclusions_name_resolved.parquet in the exclusions schema, merged by
  the next graph build); `case_harvest_prompts --sets cia` (CIA parties missing
  from the case DB + the ownership resolver's residue, 5 per prompt: CSV A the
  settlement release + qui tam / relator role, CSV B the enrolled entities with
  NPIs) and `--sets exclusion_names` (ambiguous rows only, 10 per prompt, the
  candidates listed); loaders `research_evidence cia-entities` (resolver rows,
  resolved only on a Luhn-valid NPI) and `exclusion-npis` (valid NPI, confidence
  >= medium, and the NPI must be one of the candidates we listed). Offline exact
  first, Manus for the residue, a human gate on every answer.
  EXCLUSION-SOURCE AUDIT (2026-10-13, `docs/EXCLUSION_SOURCES_AUDIT_2026-10.md`):
  LEIE's UPIN / DOB / GENERAL / SPECIALTY / ZIP / waiver fields were dropped by
  `integrate.build_exclusions`; now carried into processed/exclusions.parquet and
  the exclusion nodes. `ccn_npi_crosswalk --other-ids-out` writes the NPPES Other
  Provider Identifiers of type 02 (Medicare UPIN) and 05 (state Medicaid id +
  issuing state) as a long table; `label_expansion names --other-ids` resolves by
  UPIN first (`excl_upin`, a hard identifier, no name logic). STATE_TERM_RULES:
  indictments, charges and credible-allegation payment suspensions are weak (an
  allegation is not an outcome; one pinned test moved). OpenSanctions' value is as
  an identifier crosswalk (it de-duplicates LEIE + SAM + 45 state lists into one
  entity), never a label; its program / reason properties would make its rows
  tier-able. SAM's 'NPI' field holds UEI / CAGE / DUNS (11,234 bad Luhn):
  quarantine. COMMERCIAL-PLAN FRAUD (operator's question): already in the label
  where it is federal (18 USC 1347 covers private programs; 1128(a)(3) is hard
  tier); the gap is state-court insurance-fraud convictions, which belong as a
  TRAINING-only source tag `hcf_commercial` admitted on a conviction or judgment,
  switched on by a label ablation on the FCA ruler, never a forward positive and
  never in the strict PurpleLab label. Follow-ups in task label_sources_followups.
  THE CHARGED TIER (operator 2026-10-13: 'the biggest possible label, every provider
  in it with a strong signal they likely committed fraud in any way'; an indictment
  is a strong signal): `fca_relevance.TIERS` = hard / charged / weak / excluded;
  a fraud indictment or charge with no outcome = `charged` (a federal HCF charge
  converts about nine times in ten), trainable under `--label-tier hard+charged`
  (`tier_mask`; accepted by pursuit_dossier / accuracy_diagnostics / learning_loop /
  consensus / label_expansion), REFUSED by prospective_label (the forward ruler
  grades on outcomes only). 1128(b)(8) / (b)(15) are hard (the billing vehicle and
  its operator). A credible-allegation payment suspension stays weak (a referral,
  not a charge). v15 = the A/B hard vs hard+charged on one matrix before the
  runner default moves. Queued: the 452 charged-only case rows as `charged`
  positives, and an outcome-refresh harvest that converts them to hard.
  CHARGED CASES IN THE LABEL (2026-10-13, built while v14 ran): `case_labels`
  no longer drops a charged-only case row. `charged_outcome(case_type, text)` =
  a criminal_hcf indictment / charge / arrest with no plea, verdict or sentence, or a
  civil_fca row where the government filed or intervened in the complaint with no
  settlement or judgment (a relator's own complaint stays a docket row); such rows
  carry `label_source = charged_case`, `fca_strict_tier = charged`, direct and
  org_billing bases only (a charge never broadcasts to affiliated staff), and the
  export records the source WITHOUT flipping `provider_on_exclusion` (same
  mechanism as docket_case). `company_list.tier_label` ORs charged_case rows in
  only when the policy keeps the charged tier (hard+charged, hard+weak);
  `set_label_policy` now accepts every `fca_relevance.TIER_POLICIES` entry (it
  used to raise on hard+charged while the CLIs offered it); the summary carries
  `n_charged_positives` and the registry reads it as `train_charged`. Precedence
  per NPI: doj_case > charged_case > docket_case, so an outcome release converts
  the charge by itself. ORG RESOLUTION FIX: `resolve_settled_orgs(require_recovery=
  False)` in case_labels (two passes, resolved rows first so an outcome owns the
  org's name key ahead of its own earlier charge): the amount>0-or-intervened
  filter had kept every criminal conviction release without a stated dollar
  amount, and every charge, off the org_billing tier; the outcome rule is the
  gate now (v15 semantics). OUTCOME REFRESH LANE: `charged_only_cases(case_db)`
  lists the charged rows; `case_harvest_prompts --sets outcome_refresh --case-db
  [--since]` writes Manus prompts (10 per prompt: plea / verdict / sentencing /
  settlement / dismissal / acquittal with the evidence URL); `research_evidence
  outcome-refresh --in <csv> --case-db` turns each resolved answer into its OWN
  case row (keyed by the evidence URL, dated by the outcome, summary in the
  outcome rule's words, case_type from the answer) merged into the case DB, and
  marks a dismissed / acquitted charge `usable=0` with a `qc_note` (quarantine,
  never delete). v15 = the A/B hard vs hard+charged on one matrix (two
  accuracy_diagnostics refits) before the runner default moves.
  V14 READ-OUT (2026-10-13, `docs/ACCURACY_READOUT_v14_2026-10-13.md`): v13's
  matrix re-graded with the book restored. Seven of eight checks pass (large
  hits@50 2, p 0.049; pursuit 1 / 1 / 3 / 7 at 16 / 50 / 100 / 250; lead 29 mo;
  inst share 0; Jaccard 0.61; train = hard); HOLD on hits100 >= 4 (3: v12's
  fourth hit sits between 100 and 250 under v13's seed draw, the noise floor the
  Jaccard describes). The v13 HOLD was the code, confirmed. The dossier named no
  institution for the first time (home-health / toxicology / pain / hospice /
  spine operators); not dispatched. Sweep second reading: basis50 on dollar =
  1/3/3/7 vs shipped 1/1/3/7 (wins 50, ties the rest; v12 won 50, lost 100):
  sort stays dollar, the row runs again in v15. Broadcast A/B weight 1/2/2/3 vs
  mask 0/1/1/3: weight stays. v15 = full VM run on the expanded label (bundles
  307 to 312) under the v14 checks + the hard vs hard+charged A/B + the three-
  ruler sweep; 100 bar stays at 4.
  LABEL EXPANSION, FIRST REAL RUN (2026-10-13, VM on v14's inputs): affiliated
  convictions = 238 individuals with a rendering history under another biller,
  168 kept biller rows (80 at share > 0.5), +55 forward positives (3,049 to
  3,106); the qui tam ruler = 1,372 of the 1,801 resolved FCA cases in the
  horizon; 185 charged-only case rows carry charged_case. CIA: 80 of 339 parties
  resolved by exact name, but 26,536 NPIs, 11,217 of them Walgreen Co (one org
  node), CVS 1,401, Lincare 710: the exact-org path had NO chain guard (only the
  owner path did). FIXED (bundle 314): `cia_resolve(max_npis=150)` lists any
  party reaching more than 150 NPIs as `chain_org` (never labelled, the same
  rule case_labels applies to a settled org); `party_components` splits a
  compound party line ("X, P.A. and John Doe, M.D.; d.b.a. Z") on and / & / ; /
  d.b.a. with credential tails stripped, and each component tries both exact
  paths (`cia_component`); `is_manufacturer_name` classes drug / device makers
  in the residue as `manufacturer_not_enrolled` (cia_prompts never sends them, nor
  chains); `evidence_strength` rates cia_* / research_cia moderate (they used to
  fall to weak and be dropped by case_labels); case_labels honours a resolver
  row's own `label_weight` as a cap on the direct tier (the per-party budget).
  SECOND CIA RUN (bundle 315 on the VM): 185 of 339 parties resolved (component
  110, exact org 70, owner 5), 2,497 NPIs (largest party 150), residue 154 (126
  no exact match, 17 manufacturers, 11 chains): usable. The no-match residue is
  the Manus cia lane (task label_manus_lanes) plus a future person-component
  path ("Clinic, P.A. and Dr. X": the person resolves like a defendant, by
  name in state). Lundbeck / Astellas / ResMed / Innovasis joined the
  manufacturer list. The `names` step
  had NEVER run: the module's `__main__` guard sat above `exclusion_name_resolve`
  so the CLI raised NameError (guard moved to the end; a subprocess test pins it).
  Its second run read 0 rows: the VM's current-day graph predates bundle 309, so
  its exclusion nodes carry no state / upin (338,572 of 388,634 rows name-only).
  `names --leie preclean/Caught.csv` (bundle 317) builds the table from the raw
  LEIE through integrate.build_exclusions instead, and `name_resolve_funnel`
  prints the rows surviving each filter so an empty result names its stage.
  Third run (--leie): 12,014 name-only hard-tier rows since 2015 -> 843 resolved,
  610 ambiguous (the Manus residue), 10,560 with no NPPES record (aides, nurses,
  clerks who never held an NPI: never a provider label).
  V15 RUNNER (bundle 318): `label_expansion merge-inputs` writes
  enforcement/doj_cases_v3_plus_cia.csv + processed/case_defendant_npis_plus_cia
  .parquet (merge_case_files; resolved CIA rows concatenated with their
  label_weight; the originals untouched) and run_frozen_night.sh step 0 builds
  them; step 3 adds the affiliated (__aff36) and qui tam (__quitam36) rulers; step
  7 passes --qui-tam-ruler; step 8 adds ab_tier_charged/ (refit, --label-tier
  hard+charged, weight) beside ab_broadcast_weight/ (= the hard arm). cia_case_rows'
  summary now states the settlement in the outcome rule's words (a bare 'Corporate
  Integrity Agreement' read as no outcome and every CIA row was being dropped).
  v15 passes --rebuild-graph (the 843 new exclusion nodes). Task v15_run carries
  the pre-registration line (the v14 checks, train = hard).
  V15 READ-OUT (2026-10-14, `docs/ACCURACY_READOUT_v15_2026-10-14.md`): HOLD on
  hits100 >= 4 (3) for the third run; the other seven pass, and the pursuit book
  passes the 50 budget for the first time since v10 (1 / 2 / 3 / 6, p 0.031;
  large 1 / 2 / 4 / 5; Jaccard 0.64). PIPELINE FINDING: the CIA parties never
  reached the label. The merged inputs were used (4,066 resolved defendant NPIs),
  but the VM's cia_cases.csv was written by bundle 315, BEFORE the summary fix in
  318, so case_labels' outcome rule dropped every CIA row ("2,633 case rows
  without a resolved outcome" vs 325 in v14); the ruler stayed at 509 and the
  training label grew by 53 (26 name-resolved exclusions in the universe, 27
  DOJ-case rows from the require_recovery=False org resolution). RULE: an input
  file produced by an earlier bundle is re-run after the bundle that changes its
  format; `merge_label_inputs` now counts the CIA rows that pass the outcome
  rule and WARNS at zero. Charged A/B: identical to hard at every budget (1/1/1/3;
  68 charged positives): policy stays hard. Qui tam ruler (351 in universe):
  0/1/2/5, p 0.001 at 250. Order sweep: basis50 = shipped (1/2/3/6): sort stays
  dollar. Broadcast: weight 1/1/1/3, mask 0/0/3/3: weight stays. v16 = re-run
  `label_expansion cia`, then `REBUILD_RULERS=1 ops/vm/regrade_from.sh v16 v15
  2021-12` (merge-inputs + the three rulers rebuilt, v15's matrix re-graded; no
  retrain): the post-freeze CIA parties land on the FORWARD ruler. Aurora Health
  Care joined _NATIONAL_NAMES (the v15 dossier named it). Not dispatched.
  V16 READ-OUT (2026-10-14, `docs/ACCURACY_READOUT_v16_2026-10-14.md`): v15's
  matrix re-graded with the CIA parties on the rulers. The FCA ruler grew from
  509 to 583 in-universe positives (2,044 on paper; 166 resolvable cases; 284
  pre-freeze CIA parties moved to known-at-freeze = v17 training positives) and
  the shipped list held: pursuit 1 / 2 / 3 / 7 (random 0.1 / 0.4 / 0.7 / 1.8),
  large 1 / 2 / 5 / 6, Jaccard 0.64. HOLD on hits100 >= 4 (3) for the THIRD
  consecutive grade with 7 at 250 each time; the sweep offers no order that
  reads 4 at 100. RULE: if v17 (retrain on the CIA training rows) still reads
  3 at 100, v18's pre-registration grades the 100 budget on the large book (5
  here) and keeps the pursuit bars at 50 and 250. Not dispatched.
  THE OPERATOR'S QUESTIONS (2026-10-14, `docs/MODEL_QUESTIONS_2026-10-14.md`, read
  before planning v18): 16 of 22 schemes grade at coin-flip AUC on the label and
  only worthless_services / single_service_mill validate on DOJ cases; the 11
  DEGRADED schemes are missing INPUTS, the largest being the five v3 billing
  scores no frozen run can compute (an as-of version is the wiring gap); the
  facility / PBJ / POS 2023 editions leak into the 2021-12 freeze (8 runs, never
  fixed: exclude them until the pre-cutoff editions exist); 804 of 881 forward
  positives rank outside the top 1 percent and fall short on
  clinical_implausibility / new_code_burst / tenure / servicing spread (the
  owner-run shape); source_ablation and signal_ranking are BUILT but not in the
  VM runner; every Manus label lane is written and none dispatched. Order before
  any bar moves: measurement in the runner, the as-of v3 scores + the vintage
  exclusion, a label-conditioned enrichment report, the Manus label lanes (CIA
  and names first) + hcf_commercial, then v18 on the surviving scheme set.
  LAPTOP MERGE (2026-10-13): the pasted `git push` line after merge_bundle.bat lost
  its first letters every time ("'t' is not recognized"): a git child of the batch
  file reads the queued keystrokes of the next pasted line. `ops/laptop/merge_push
  .bat N` is the ONE-LINE merge from bundle 320 on (copy from Downloads, fetch,
  ff-only merge, push, close the task; every child reads NUL). Chat instructions:
  merge and push on ONE line (`call merge_bundle.bat ... && git push ...`), never
  a separate push line after a batch file.
  TMS TIP (2026-10-13): Learning to Achieve Wellness (Brandon FL) has zero Medicaid
  TMS dollars (Medicare / commercial, or the psychiatrist's individual NPI); the
  national TMS table found a NEVADA cluster of 20+ short-lived single-renderer
  100%-TMS entities (an ownership / coverage-date research question) and seven
  individual NPIs carrying TMS alone (Rosa Bellota NV $1.0M the largest).
  V26 DOSSIER + THE FIRST PURSUIT PACKET (2026-10-26/27, docs: the packet and memos are
  work product under <data_root>/enforcement/, never git): the v26 operator book was
  dispatched (13 tasks: 10 targets + 3 national arms sent before bundle 398), polled to
  zero pending, and read with the research: PURSUE TrueTox (NJ comptroller 2021 audit,
  $24M overcharges on the same testing; our NY $56M), HealthTrackRx (2021 OIG
  self-disclosure for sober-home kickbacks under its old name AIT), plus the two
  current-day cards (Andes home care: two of the five agencies share 1989 Coney Island
  Ave and a would-be owner, two settled with DOJ in 2009; Morrison skin grafts: CA
  podiatry board accusation filed 2026-04-27, Medi-Cal pays grafts by invoice, DOJ sued
  over PalinGen invoices 2026-06). HOLD Ideal Option (a federal court enforced a DOJ CID
  2026-09-02: an active FCA investigation). SET ASIDE Stavros (MassHealth PCA fiscal
  intermediary: $1.3B of attendant wages), Unilab + Diagnostic Laboratory of Oklahoma
  (Quest arms under old names), Laboratory Services Cooperative, Kennedy Donovan (all
  on _NATIONAL_NAMES now). BUILT the same two days: `model_a/attending_concentration.py`
  (individual NPIs in the servicing field of other organizations' claims, ranked
  nationally; the shape the billing-NPI matrix cannot see), `enforcement/first_to_file.py`
  (named-party docket search through the CourtListener API; a sealed qui tam is
  invisible to every public search), `ops/vm/run_current_day.sh` (the operational list
  with no freeze; refuses to run beside a frozen run), the NY rendering-rule memo (FFS:
  PA under the employing physician at the physician fee with the PA named; NP 95% since
  2023-10; unlicensed = not payable; managed care: no plan permits a PA visit under the
  supervising physician, UHC requires the NP/PA's own NPI, the model contract requires
  the rendering clinician on every encounter; the private-practice PA cap rose 4 -> 6 on
  2025-02-22). RULE: a custom research prompt goes through `pursuit_dossier
  --dispatch-file` in its OWN folder (its own registry; never the run's), and the
  attachments are pulled with urllib from the results JSON before the signed links
  expire. The dispatch ran once on 397 code and once on 398: the registry de-duplicates
  by org_id, so a re-run after a pick change sends only the new targets.
  VM (2026-10-12): r6i.4xlarge launched in us-east-2 (quota 32), Ubuntu 26.04, rclone
  1.75 installed by the rclone script (the apt 1.60 is too old for the OneDrive token),
  `rclone config` answers: onedrive / onedrive / Enter / Enter / 1 / n / n, token from
  `rclone authorize "onedrive"` on the laptop, drive = the entry named "OneDrive
  (personal)", first `sync_data.sh` pull running under tmux `sync`.
  Full suite: `pytest tests/` (1,069 tests + 2 skipped). Dead columns are left OUT OF THE
  TRAINABLE LISTS but KEPT in the parquet (case_size reads billing_residual by name).
  FREEZE POINT (2026-10-14, operator: "freeze a version of the current code that
  we can go back to"): tag `freeze-v16-2026-10-14` + branch `freeze/v16-2026-10-14`
  at 868e951 (the v16/v17 code: the questions doc is the last commit). Return with
  `git checkout freeze-v16-2026-10-14` (or run the VM from it: `git checkout
  freeze/v16-2026-10-14` in ~/medicaid-fraud-detection). merge_push.bat fetches and
  pushes `refs/tags/freeze-*` from every bundle; every later freeze gets a NEW dated
  tag, never a moved one.
  BUILD-OUT 2026-10-14 (the questions doc's §7 order, built in one day, v18
  semantics = new pre-registration): (1) AS-OF V3 SCORES (`model_a/asof_v3.py`):
  concentration / payment_intensity / service_intensity / specialty_mismatch /
  temporal recomputed from the as-of spending fact with the EXACT v3 formulas
  (`features.rate_features` at billing-NPI grain, the temporal maturity window
  measured from the last pre-cutoff month, `rare_share_te` at taxonomy x entity,
  `refine_layer2_v3.score_concepts`); `_apply_asof_freeze(with_v3=True)` restores
  the five scores + log_max_single_month + n_concept_signals after dropping the
  full-history columns (export `--no-asof-v3` = the v6..v17 behaviour); the runner
  stops when the "[asof_v3] restored" line is missing. Planted test: a twin that
  differs only after the cutoff gets identical scores. (2) POST-CUTOFF EDITIONS
  (bundle 324): `_asof_files` / `_asof_newest` make the multi-file snapshot
  blocks (PBJ quarters, POS quarters, NH Care Compare ownership / penalties /
  citations) read ONLY editions dated on/before the freeze (the dated-eligible
  set, else the undated file with a flag, else nothing with the post-cutoff
  names logged); they used to pool every file on disk, which is why 2023
  editions sat inside the 2021-12 freeze for eight runs. The runner's
  FROZEN_SKIP is empty by default (set `facility,pos` to force a skip); the
  2021 editions to download are reminder `pre_cutoff_editions_2021` (POS Q4
  2021, PBJ 2021Q3/Q4, NH Dec-2021 archive, Order & Referring 2021-12, NADAC
  2021, All-Owners 2021 editions for ownership_turnover). (3)
  MEASUREMENT IN THE RUNNER (`ops/vm/measure_run.sh`, step 12 of 13 in
  run_frozen_night.sh and regrade_from.sh, before the ledger): scheme_health
  `--future-label` adds `fca_auc` / `n_fca_pos` / `fca_verdict` (signal >= 0.58,
  coin-flip, inverted < 0.42, thin < 20 forward positives; known-at-freeze rows
  dropped) and `--json-out` refreshes scheme_health.json; signal_ranking.csv;
  LABEL_ENRICHMENT; label_ablation + source_ablation --oof --per-group on the FCA
  ruler (`MEASURE_ABLATION=0` skips the two retrains). run_issues raises
  `prune_candidate` WARN for a scheme coin-flip on BOTH labels (20+ positives on
  each) and `fca_inverted`; a prune is a new pre-registration, never the ledger.
  (4) LABEL ENRICHMENT (`model_a/label_enrichment.py`, `--run-dir`): for every
  value of taxonomy / state / entity type / billing-size band / tenure / distinct-
  code band / active months / org size / each scheme's top decile / HCPCS codes
  billed (>= 1 percent of gross, DuckDB over the as-of fact) / dominant code /
  dominant-code share, the share among forward positives vs the universe, lift,
  and the hypergeometric tail; rows under 5 positives listed but unsupported ->
  LABEL_ENRICHMENT.md + .csv + .json. The design brief for schemes nobody wrote.
  (5) hcf_commercial (`case_labels.COMMERCIAL_SOURCE`, case_type
  `hcf_commercial`): commercial-plan fraud convictions as a TRAINING-only source.
  Harvest set `commercial` (`case_harvest_prompts --sets commercial`, one prompt
  per state-year, resolved outcomes only) -> `research_evidence commercial-cases
  --in --state --case-db` (admits conviction / plea / judgment / settlement text,
  quarantines charges, keeps a 10-digit NPI as npi_in_source) -> case rows ->
  case_labels tier `hcf_commercial` (direct / org_billing bases; precedence
  doj_case > charged_case > hcf_commercial > docket_case) -> the export records the
  source without flipping the hard label -> `company_list.tier_label` ORs it in
  under `hard+commercial` / `hard+charged+commercial` (`fca_relevance.
  TIER_POLICIES`, `_policy_tiers`); `prospective_label` never takes it
  (label_source doj_case only), `fca_label_export.outcome_tier` returns '' for it,
  the registry reads `train_commercial`; the runner adds `ab_tier_commercial/`
  when the merged case file carries such rows. Tests: test_asof_v3,
  test_hcf_commercial, test_label_enrichment_and_fca_health (1,111 pass).
  (6) THE RESEARCH LANES UNATTENDED (`model_a/manus_lanes.py` + `ops/vm/
  manus_lanes.sh <tag>`; operator: "you have my Manus key, build everything"):
  one command on the VM (the key lives in ~/.medicaid_env) refreshes the CIA +
  names residues, BUILDS every lane's prompts (cia / names / outcome_refresh /
  label_verification / commercial / defendant_research / label_review from the
  run folder) under processed/manus_lanes/<lane>/, DISPATCHES under a budget
  (MANUS_MAX_NEW per lane, MANUS_TOTAL across lanes; the dossier's registry +
  lock, re-attach for free), POLLS for MANUS_WAIT_MINUTES, LOADS every answer
  through the lane's own loader (`tables_from_answer` reads CSV attachments,
  fenced csv blocks and pipe tables; the gates: Luhn NPI, confidence >= medium,
  the NPI among OUR candidates, a resolved outcome, charged rows quarantined;
  `_harvest_cases_from_table` types CIA CSV-A rows civil_fca / criminal_hcf),
  re-runs merge-inputs and writes MANUS_LANES.md + manus_lanes.json (prompts /
  dispatched / completed / loaded / quarantined per lane). Loading is idempotent
  (dedup on the files' keys) so a fresh `label_expansion cia` never loses the
  research rows. The human review is the report + <lane>_quarantine.csv; label
  verification / review stay evidence. (7) THE CHECKS THAT ANSWER THE QUESTIONS
  ARE WIRED (operator: "if you need to run audits, wire those checks"):
  `model_a/data_refresh.py` (DATA_REFRESH.md: every source's file, inferred
  edition, verdict vs the freeze = POST-CUTOFF / MIXED / UNDATED / DERIVED /
  SKIPPED, the action and the reminder key; the downloads due from
  docs/reminders.json; the vintage inspector's re-download list),
  `model_a/questions_readout.py` (QUESTIONS_READOUT.md: the six standing
  questions answered from scheme_health / sources_audit / LABEL_ENRICHMENT /
  learning_loop / run_issues / label_audit / training_label_policy / the
  rulers / manus_lanes.json, a missing artifact named, never guessed),
  `label_audit` now in measure_run.sh (step 3b) with data_refresh (3c) and the
  readout (5b), and the vintage inspector as runner step 0a (weekly, VINTAGE_
  MAX_AGE_DAYS, VINTAGE_INSPECT=0 skips). Tests: test_manus_lanes (a fake
  service answers every lane), test_data_refresh_and_questions.
  FIRST LIVE LANE CYCLE (2026-10-14, graded 2026-10-20): 212 tasks dispatched
  under the 40-per-lane / 250 budget, 180 answered with real CSV attachments
  (one CIA answer: 9 settlements + 125 enrolled entities, 57 with NPIs), 32
  errored at the service; the loader crashed on the FIRST lane (nine CSV-A
  defendants shared five release URLs, build_case_db de-duplicated on case_id,
  the positional case_type assignment raised) and run_cycle stopped, so every
  lane stayed unloaded and MANUS_LANES.md read all zeros. FIXED (bundle 326):
  one case row per defendant (`url#<name key>`, the DOJ feed's convention) in
  `_harvest_cases_from_table` and `load_commercial_cases`, fields aligned by
  case_id, and a per-lane try/except (a LOAD FAILED note in the report, the
  other lanes continue). Re-loading is free: the answers and their signed links
  (valid to 2026-11-01) are on the VM; `manus_lanes --poll --load` (task
  lanes_reload). `org_find` creates its --out folder and survives an empty
  NPPES match (the AIS Healthcare lookup, 2026-10-20: the company is enrolled
  as BOND PHARMACY INC d/b/a Advanced Infusion Solutions, Ridgeland MS +
  Dallas, and 1ST AMERICA INFUSION SERVICES LLC, Valdosta GA; $29M Medicaid
  2018-2024, cap-pinned subscores, no list rank, no per-se fact; the relator's
  Medicare theory is a claims-level test for the PurpleLab POC).
  V17 READ-OUT (2026-10-20, `docs/ACCURACY_READOUT_v17_2026-10-20.md`; the run
  finished 2026-10-14 18:14 on the freeze-point code): the retrain with the 284
  pre-freeze CIA parties (training label 1,545, 1,396 DOJ-case). Pursuit book,
  FCA ruler: 1 / 2 / 5 / 7 at 16 / 50 / 100 / 250 (random 0.1 / 0.4 / 0.7 /
  1.8; p 0.047 / 0.000 / 0.000): hits@100 PASSES for the first time after
  three runs at 3. Large book 1 / 4 / 4 / 6 (p 0.000 at 50, the best 50 of the
  series). Lead 25.5 mo, inst share 0, NPI top-1% lift 9.0 (best yet). HOLD on
  ONE check: top-50 Jaccard 0.575 (bar 0.6; 27 orgs in every draw). Charged and
  broadcast A/Bs identical to the hard arm. THE ORDER SWEEP: `score_p_max`
  with basis-backed rows first in the top 50 read 2 / 3 / 5 / 9 against the
  shipped 1 / 2 / 5 / 7, the first probability order to beat the dollar order
  at every budget since v7 retired P(any). Built: `pursuit_order` takes `pmax`
  / `pmax_basis50`, `build_priority_list` writes `provider_priority_list_pmax.csv`
  (PMAX_LIST_NAME, its own `pmax_rank`; the diagnostics loader reads pmax_rank /
  pursuit_rank / large_rank as the book's order), both runners grade it as a
  THIRD book (`__pmax`), the registry reads `acc_pmax_*` (aliases pmax_hits16
  / 50 / 100 / 250, pmax_prec50_lo, pmax_base50). The shipped sort stays dollar
  until the pmax book holds on a second matrix. DOSSIER FIX: the operator-book
  pick in `build()` ranked the whole rollup and named six California nursing
  homes billing $250k to $1.2M (Royal Palms, Sharon Care, Heritage Manor,
  Norwalk, Downey, Ensign San Dimas); `restrict_to_pursuit_book` keeps only
  the pursuit book's rows (the ~$10M/yr case-size bar), in the book's order.
  Not dispatched (HOLD). v18 keeps `hits100 >= 4` (it passed) and
  `top50_jaccard >= 0.6` (the open check).
  LANES LOADED (2026-10-20 21:11 UTC, `--poll --load` only, nothing sent): cia 910
  rows / 254 quarantined, names 22 / 236 (graph rebuild needed: they become exclusion
  nodes), outcome_refresh 208 (8 charged rows cleared by dismissal / acquittal, 398
  charged rows still pending), commercial 76 / 3 (5 answers without a table),
  defendant_research 26 folded / 14 no_npi, label_verification 110 verdicts
  (confirmed 96 of 104 decided = 92 percent, wrong_npi 5, wrong_person 3: the written
  rule says TIGHTEN on the wrong_npi count; the 8 rows are the operator's read, task
  lanes_review). RULES: `--poll` and `--load` are free and read-only at the service;
  `--dispatch` spends credits and runs only on the operator's explicit go (task
  lanes_dispatch_2 is gated, never a nag). VM ssh one-liners from the laptop need
  `source .venv/bin/activate` (the runners activate it themselves; a bare ssh shell
  has no `python`). A VM ssh timeout with the instance Running on the same IP is the
  security group's /32 SSH rule pinned to an old home IP (task vm_ssh_rule). v18 =
  bundle 327 merged + pulled, pre-register, `--rebuild-graph` (the name-resolved
  exclusions), ten seeds, three books graded (task v18_run carries the ssh lines).
  THE VERIFICATION READ (2026-10-20, docs/LABEL_VERIFICATION_READ_2026-10-20.md): of
  the eight flagged rows five were wrong, each one of four shapes, now four gates in
  `enforcement/label_gates.py`: (1) `is_index_url` (a case row keyed to a press-release
  INDEX, usao-mdfl/pr, is never evidence: harvest loaders quarantine it, case_labels
  drops it on every file); (2) `non_npi_role` (a state-list row whose own words say
  aide / CNA / PCA / driver / clerk never name-matches NPPES: `match_basis =
  non_npi_role`); (3) `name_agrees` (a researched NPI must carry the defendant's name
  in NPPES: last name present + FIRST name exact for a person, every distinctive word
  + at most one extra + city agreement for an org; the solo "<Name>, M.D., P.C." =
  the physician shape agrees; `defendant_research.fold_results(provider_dim=)` reads
  provider_dim through DuckDB and demotes a conflict to `research_name_conflict` with
  the NPI in `npi_candidate`); (4) `name_in_page` + `research_evidence verify-sources
  --case-db` (the research-harvested state / commercial rows are read against their
  cited page; a defendant not on it is `usable=0`, qc_note `defendant_not_on_page`).
  The names and research lanes now write their quarantine files. Three rows were
  KEEPs (the doctor's own P.C., a physician the OIG listing omits but the release
  names, a 40-page MFCU report). Order on the VM (task lanes_gates_apply): re-load
  the lanes, re-run the state loader, verify-sources, THEN v18 --rebuild-graph.
  PLATFORM AUDIT (2026-10-21, operator: stop v18, deep-scan the code and the data files before
  moving forward; `docs/PLATFORM_AUDIT_2026-10-21.md` + the four appendices under
  docs/audit_2026-10-21/ = adapter contracts, feature wiring, label chain, architecture; READ
  IT before touching the export, the label chain or a runner): twelve HIGH findings, two of
  which would have ended v18 (the runner's hard+commercial tier rejected by the diagnostics
  CLI choice list; the case label boxed at month END while every other freeze is month START),
  three silent-blank fields that score a scheme on a default of 0 while SOURCES_REPORT says
  'used', the nadac_namegrain early return that drops both July-2026 sources, HCRIS pooling
  every edition under a freeze, three producer-less scheme inputs, nine dedup-instead-of-
  assert joins, stage-one ignore_errors with no reject count, the Makefile without --case-db,
  a tracked backtest CSV. Fix plan in section 7 (batch 1 before any run, batch 2 = v19
  semantics, batch 3 structure), gated on the operator's go per batch. THE DATA SCANNER
  (`src/data_audit.py`, `ops/vm/data_audit.sh <cutoff>`): every preclean file graded against
  the adapter's OWN `*_COLS` map (REQUIRED MISSING = the adapter raises / SILENT BLANK = a
  default feeds a scheme / OK), dated by name and by the adapter's date column, placed under
  the export's `_asof_files` picker for the freeze, processed/ + graph/ inventoried against the
  derived files the export expects, the last run's sources_audit joined -> DATA_AUDIT.md +
  data_audit.json. Run it after every download session and before every pre-registration;
  the hand-written REQUIRED_COLS in vintage_inspect is the older, partial version of the same
  check. The operator's console is the SHORT BOARD below; never paste the full page in chat.
  THE SECOND FREEZE + THE THREE GAP-CLOSERS (2026-10-21, bundle 345; docs/FREEZE_DESIGN_NOTE
  addendum): the second freeze is 2023-12 (horizon 33, `HORIZON` env; the ruler files carry
  the horizon: `__fca33`), NOT 2023-06: `_file_period` now reads quarter / month tokens
  (Q4_2023 = 2023-12, 2021Q3 = 2021-09, Nov2023 = 2023-11) and every edition the note listed
  for 2023-06 is dated after June 2023. `model_a/freeze_inputs.py` resolves the as-of NPPES for
  any cutoff (nppes + nppes_history; newest on/before within 12 months, else nearest later,
  staleness recorded in freeze_inputs.json; runner step 0; `ASOF_NPPES` overrides);
  `model_a/freeze_pair.py` = FREEZE_PAIR.md (two runs side by side: grade per book, checks
  that HOLD ON BOTH, sources used at one freeze and skipped at the other, vintage verdicts);
  `entity_graph/owners_archive.py` copies every All-Owners file into owners_prior/ dated
  (idempotent) and the runner archives the month's owner-edge snapshot (rule 2, no reminder
  needed on the VM); THE VINTAGE LEDGER (`_VINTAGE`, manifest `vintage_audit`, a table in
  SOURCES_REPORT): every edition pick records pre_cutoff / undated_substituted /
  post_cutoff_reference / nothing_eligible / current_day_newest, run_issues grades a
  substitution WARN and a post-cutoff pick FAIL, `scheme_health.leak_suspect` (forward AUC
  >= in-time AUC + 0.15 and >= 0.70) is a run_issues FAIL (rule 3). CURRENT-DAY PICKING
  (hold_folders_to_live answered): `_asof_files(keep=)` reads the newest edition (POS, HCRIS,
  NH tables) or the newest four quarters (PBJ) instead of pooling every file; `_asof_newest`
  takes the newest period, not the alphabetically first name. HCRIS is as-of (audit H7). The
  four `--label-tier` choice lists read TIER_POLICIES (audit H1: a second-freeze run could not
  finish without it). Tests: tests/test_second_freeze_2026_10.py (1,150 pass).
  THE FIRST DATA SCAN READ (2026-10-21 14:15 UTC, PLATFORM_AUDIT section 2, bundle 346): THE VM
  RUNS ON THE JUNE/JULY SYNC (Spending / NPPES / PECOS 2026-06-17, the September LEIE, none of
  the 2021 editions fetched on 10-20); `sync_data.sh down-preclean` pulls preclean/ only (a
  FULL `down` would overwrite the label work the lanes wrote on the VM under processed/ and
  enforcement/: never run it after a lanes load); DocGraph 2022 sits inside the 2021-12
  freeze (no cutoff on refers_to edges: batch 1); the newest deactivation report was never
  read (`nppes_deactivation.edition_of` / `pick_deactivation_file`: the CMS MMDDYY stamp, the
  zip inner name or the banner cell; the export's `_deactivation_file` takes the newest with
  a ledger row); placeholder dates 0001 / 1800 / 2099 reach the temporal filters; the current-
  day graph predates the 10-20 label loads. SCANNER CLASSES FIXED: `_PROBE` hooks call the
  adapter's own loader for multi-sheet / banner / keyword sources (warn, hrsa_340b,
  nppes_deactivation), `_is_adapter_file` lists a sibling the adapter never reads without
  grading it, `_CRITICAL_ANY` (hospice end_date OR date_range), `_CRITICAL_EXEMPT` (POS lab /
  iQIES beds), json feeds and two-column reference tables. Tests:
  tests/test_data_audit_scan_fixes_2026_10.py.
  BATCH 1 SHIPPED (2026-10-21, bundle 349; the operator delegated the plan: 'i want the best
  possible model so its really your call'; tests/test_batch1_fixes_2026_10.py): the case label
  freezes at month START (case_labels); `research_evidence.load_state_exclusions` writes
  `state_term:<ST>:<action>` (the ruler tiers it; `--source mfcu` keeps `mfcu_<st>:`);
  STATE_TERM_RULES: a resolved outcome wins, charge words anchored (\\bcharged\\b); the
  silent-blank class RAISES (order_referring without DME, PBJ without LPN/CNA, NH owner name /
  fine, OPAIS contract pharmacy, Open Payments product columns, LEIE EXCLTYPE) or stays NULL
  (Part B avg_allowed, HCRIS cost columns with min_count=1 sums); the per-NPI adapter loop never
  returns early (StopIteration pattern); the enrollment per-se check reads the AS-OF fact
  (`_asof_spend_path`) or skips with a line; `_have()` honours --skip-sources for every block
  and main writes one skip line per requested name; audit lines on the no-row paths (kickback,
  hcris, pos, doj_case without org_nodes, the widened label, capacity); the referrer-grain O&R
  file goes through `_asof_pick`; `nadac.asof_rows` filters prices by effective date on both
  price sites; `temporal_sources.real_dates` reads 0001 / 1800 / 2099 / 9999 as UNKNOWN (owner
  edges kept as undated, exclusions dropped as undated); the enrollment SQL nulls a start
  before 1900; `integrate.merge_exclusion_sources` quarantines the duplicate rows
  (processed/exclusions_duplicates.parquet: the 537 LEIE rows named); `entity_graph.
  docgraph_edition` + the as-of build refuses a DocGraph edition after the cutoff (D3: the 2022
  file sat inside every 2021-12 graph); `_file_period`'s month token is 01..12 with an optional
  day; the three producer-less scheme inputs are RESERVED (out of the weights; the catalog in
  RUNBOOK_TRAVIS 5.4 lists them as reserved); the runner's step 2b rebuilds the CURRENT-DAY graph
  when an exclusion source is newer than it (REBUILD_CURRENT=1 forces); `make frozen-package`
  DELEGATES to ops/vm/run_frozen_night.sh (TAG= ASOF_CUTOFF= HORIZON= SEEDS=) and
  `make provider-features` carries --case-db / --defendant-npis when the files exist;
  src/backtest/backtest_results.csv + backtest_report.json untracked (a test refuses any tracked
  csv / parquet / xlsx). DECISIONS: `src/model` (Travis's track) is RETIRED TO REFERENCE (never
  run by a runner; the production scorer is company_list + network_ab._fit_predict); the
  Windows .bat runners are retired, ops/vm/run_frozen_night.sh is THE runner. v19 = the first
  run on this code (new pre-registration, task v19_run); batch 2 (v20 semantics) next.
  BATCH 2 SHIPPED (2026-10-21, bundle 350, built while v19 ran on the batch-1 code; v20 = the
  first run on it, NEW pre-registration; tests/test_batch2_fixes_2026_10.py, 25 tests):
  `provider_features_export.join_m1` (validate m:1; duplicate right-side rows set aside in
  `_quarantine/join_duplicates.parquet` + manifest `join_quarantine`, the first kept, logged)
  replaces every `drop_duplicates("npi")` merge and `_broadcast_org_to_npi` RAISES when
  npi_to_org maps one NPI to two orgs; `clean_data.csv_to_parquet` reads with `store_rejects`
  (the malformed raw lines kept in `<stem>_rejects.parquet`, counted by LINE: parquet rows +
  rejected lines = every line seen); `PRE_RANKED_PREFIXES` (pbj_ / deficiency_ / nh_ /
  hospice_) + `PRE_RANKED_INPUTS` (market_saturation_index, hcris_cost_anomaly) drive their
  subscores raw (clipped 0..1) instead of a percentile of a percentile; `_read_any` reads
  .xlsx. LABEL CHAIN: the strict export's state rows are gated on `fca_tier(et)[0] == "hard"`
  (the ruler, not a word list) and read the provenance suffix (`"1128a1|excl_name_state_unique"`:
  `fca_relevance.PROVENANCE_SUFFIX` strips ONLY a match-level suffix `|excl_* / |research_* /
  |fuzzy_*`; the pipes inside a SAM / state / enrollment reason body stay; the names lane
  writes it; the strict export rates such rows moderate / name_match, never exact_id);
  `exclusion_name_resolve` refuses a non-NPI role (aide / CNA / driver / clerk from LEIE
  GENERAL + SPECIALTY: status `non_npi_role`); `research_evidence.load_cia_entities(provider_dim=)`
  runs `name_agrees` on every CIA entity NPI (`research_name_conflict`, NPI kept in
  `npi_candidate`, reason in `gate_note`; without provider_dim the row says "name gate not
  run"); `defendant_research --poll` REFUSES without provider_dim (`--provider-dim`, default
  beside --resolved; `--no-name-gate` = review-only fold); manus_lanes runs
  `quarantine_absent_defendants` on the case DB right after a cia / commercial load (page
  verification on the load path, best effort, the failure named in the tally); ONE outcome
  vocabulary (`manus_lanes._OUTCOME_OK` / `_CHARGE_ONLY` = the strict export's regexes;
  `load_mfcu_cases` sets usable=0 + qc_note on a row without a resolved outcome);
  `entity_graph.dedup_exclusion_sources` (npi + name_key + excl_type + excl_date, logged;
  exclusions_duplicates.parquet never loaded); LUHN EVERYWHERE: `sam_api.load_sam_extract`,
  `opensanctions.normalize_opensanctions`, `medicaid_enrollment.luhn_gate_candidates`
  (-> administrative `invalid_npi`), `case_labels` named / resolved NPIs, and the export's
  widened label (a ten-digit non-NPI never flips a positive; the count logged). SCORER:
  `company_list.unassessable_mask` holds `assessable == 0` rows out of every training fold
  (scored out-of-fold, like the ambiguous rows; `rollup` adds `n_unassessable_members`; a
  matrix without the column trains as before); `source_ablation._fenced` applies the SHIPPED
  scorer's ban list (LABEL_ADJACENT_NET + leakage_adjacent + leakage_hard) to every ablation
  set (the MODEL_REVIEW open item: the gating metric is now the shipped model). MEASUREMENT:
  `accuracy_diagnostics._discover_rulers` reads every `future_bans_after_<cut>__<suffix><H>.csv`
  (key = suffix without the horizon: `aff` is graded, `fca_file` / `qui_tam_file` yield to
  the --case-db rulers); measure_run.sh takes the scheme-health baseline from
  `$PREV_RUN/scheme_health.json` (the regression alarm was inert: it read the run's own file).
  DEATH CUT: `match_deceased_providers(dob_by_npi=)` takes the exclusion nodes' DOB (NPPES
  has none, so every match was "low" and billing_after_death never fired); the export's
  smoking-gun block passes it. Case-tolerant header resolvers in `nucc_taxonomy._resolve` and
  `asof_nppes._pick`. OPEN (LOW, batch 3): B.F-10, F-15..F-20.
  SATURATION LEAK FOUND AND FIXED (2026-10-21, bundle 354/355): the live Market Saturation
  file labels its reference periods "2021-10-01 to 2022-09-30" (rolling quarters from 2020);
  `compute_saturation_metrics` compared the first seven characters, so a 2021-12 freeze
  admitted every window STARTING on/before the cutoff and took the latest = the one ending
  September 2022: a nine-month leak in market_saturation_index on every frozen run v10..v19
  (v19 ran on that code; its read-out names it). `saturation.period_end` parses the END of
  the label (ISO / slash / month-name / bare year); `load_saturation_folder` stacks every csv
  under preclean/saturation (the live file + the CMS archive's 2014-2015 / 2016-2017 /
  2018-2019 files, downloaded 2026-10-21; a file without a reference_period column is dated
  from its name) and the export reads the folder, not the alphabetically first file (the
  archive file would have replaced the live one); the vintage ledger records the period
  used. RULE: on the VM, pull the code BEFORE the preclean sync that brings the archive.
  BIG-STATE SANCTION LISTS + THE SORTER (2026-10-21, bundle 356): the operator downloaded NY
  OMIG, CA Medi-Cal S&I, TX HHSC-OIG (.xls), IL HFS, PA Medicheck, OH, MI (label plan L1.4);
  the `sort_state_lists.py` the reminders named had never been committed, so
  `src/enforcement/state_list_sorter.py` is it: Downloads -> state from the file name (two-
  letter prefix, state name, or agency token SOMIG / Medicheck / suspended-ineligible /
  TexasExclusions / ExclusionSuspensionList / Sanctioned_Provider_List), the loader's own
  header check, browser duplicates collapsed by digest, `<ST>_terminations_<yyyymm>.<ext>`
  (the month from the name else today), dry run by default, `--apply` copies (the download
  stays). `xlrd` added to requirements for .xls. The runner's NEW step 2a runs
  `state_terminations` on the VM when a list is newer than its parquet (nothing had ever run
  the loader on the VM), then 2b rebuilds the current-day graph. THE LABEL STAYS FRAUD-ONLY
  (operator, same day): every state row is tiered from its stated reason by STATE_TERM_RULES;
  an unknown reason is WEAK and never trains under `--label-tier hard`; the report's "reasons
  the tier table does not know" list is read before any rule is added.
  V19 READ-OUT (2026-10-22, `docs/ACCURACY_READOUT_v19_2026-10-22.md`): the first run on batch
  1 + the as-of v3 scores + the 2021 editions + the October inputs (train 1,712 hard, ruler 610).
  HOLD on three shallow checks: large hits@50 1 (v17 4), pursuit hits@100 2 (v17 5), large
  prec50_lo < base; the deep budgets held or improved (pursuit 1 / 2 / 2 / 8, the best 250 of the
  series; large 1 / 1 / 4 / 6; Jaccard 0.62 passes; NPI top-1% lift 7.5). Cause = the head
  re-ordered under three planned changes (the as-of v3 scores restored: three subscores from 25%
  to 73% coverage; the 2021 facility / PBJ / POS editions replacing the 2023 ones; the label +167),
  plus the saturation leak still in this run. THE ORDER SWEEP HELD ON THE SECOND MATRIX: pmax with
  basis-backed rows first in the top 50 read 1 / 3 / 4 / 8 vs the shipped 1 / 2 / 2 / 8 (v17: 2 /
  3 / 5 / 9 vs 1 / 2 / 5 / 7), so v20 SHIPS `PURSUIT_ORDER=pmax_basis50` (the dossier picks from it;
  the dollar order stays a graded book). The label ablation trains on policy `all` and said the
  weak enrollment / state / deactivation rows hurt: confirmation of the hard policy, not a change
  (batch 3: `label_ablation --label-tier`). hospice_ineligibility's leak_suspect reads as a
  selection effect (hospices only, every input pre-cutoff). Inova and Invitae joined
  `_NATIONAL_NAMES` (the dossier named them). Not dispatched. v20 = batch 2 + the post-v19 inputs
  (2021 HCRIS, the saturation archive, 17 state lists, the verify re-check, the CIA entity load),
  pre-registered under the v19 checks with the pursuit book under pmax_basis50.
  V21 BUILD FROM THE v19 LESSONS (2026-10-22, built while v20 ran; v21 semantics =
  new pre-registration; tests/test_class_priors_and_lab_scheme_2026_10.py): (1) CLASS
  PRIORS (`model_a/class_priors.py`): the v19 LABEL_ENRICHMENT read showed the forward
  FCA cases concentrated in clinical labs (taxonomy 291U, lift 26), organizations
  (entity type 2) and large orgs, while the matrix files primary_taxonomy / entity_type
  / practice_state as IDENTIFIERS and every billing metric is a within-taxonomy
  percentile, so the scorer could not see the class base rate at all. `prior_keys`
  (taxonomy / family / entity / state) + `encode_fold` (m-estimate target encoding,
  M_SMOOTH 50, computed INSIDE each training fold: test rows from the fold's training
  statistics, training rows leave-one-out, so no row's own label reaches its feature)
  + `rate_table` (the auditable "clinical laboratories 1.2% vs 0.3%" record, written as
  class_prior_rates.csv beside the diagnostics). Threaded as training policy:
  `company_list.set_training_policy(class_priors=)` / `training_policy()['class_priors']`
  / `fold_design` + `prior_keys_for` shared by oof_scores and the diagnostics'
  oof_with_folds; CLI `--class-priors` on pursuit_dossier / accuracy_diagnostics (an
  A/B on the same matrix) and on the export (writes manifest
  training_policy.class_priors, default none = v9..v20 semantics); the registry row
  carries `class_priors` (alias `priors`); both runners grade `ab_class_priors/`
  (env CLASS_PRIORS_AB, default taxonomy,entity; empty skips) beside the hard arm.
  `state` is enforcement density as much as fraud density: A/B it alone, never by
  default. (2) LAB TESTING SCHEME (`ingest_cms/sector_schemes.py` lab families ->
  lab_paid_share / lab_lines_per_patient / lab_definitive_drug_share /
  lab_genetic_share / lab_resp_panel_share; `scheme_subscores` `lab_testing`): the
  enriched codes were one typology no scheme named (definitive toxicology G0480-83,
  genetic 81xxx + PLA, respiratory panels 87xxx / U000x, chemistry panels, bilirubin,
  HbA1c, venipuncture). Registered in scheme_code_families (`lab_code_set`),
  sector_priors (own_billing, 0.40), dossier narratives, case_validation (sector lab
  + telehealth, conduct medical_necessity), RUNBOOK_TRAVIS 5.4; ADAPTER_FEATURE_COLS
  derives from the weight table so the five metrics are peer-normalized by the export.
  Mix shares are of the LAB dollars (NULL without lab dollars; an observed 0 is real).
  ORDER: v20 reads out -> ab_class_priors grade read (hits vs the hard arm) -> v21
  pre-registers the winner + the lab scheme + the prune list (14 coin-flip schemes).
  THE PRUNE A/B (2026-10-22, the third v21 ingredient; tests/test_prune_ab_2026_10.py):
  `scheme_health.prune_candidates` is the ONE prune rule (coin-flip or inverted on the
  FCA ruler AND label AUC < 0.58 on 20+ positives, HEALTHY or DEGRADED; thin = unjudged);
  run_issues reads it; `scheme_health --prune-list <scheme_health.json>` prints the
  comma list for the runners. Training policy `drop_schemes` (`company_list.
  parse_schemes` / `apply_drop_schemes`: subscore_<s> + evidence_n_<s> held out of the
  scorer in oof_scores and the diagnostics' `_screened_columns`; the scheme's raw
  INPUTS stay, the subscores stay on the matrix and in the dossier's drivers);
  `--drop-schemes` on pursuit_dossier / accuracy_diagnostics (the A/B) and the export
  (manifest training_policy.drop_schemes, the list a pre-registration commits to);
  registry `drop_schemes` (alias `prune`). Both runners grade `ab_prune/`: PRUNE_AB=auto
  (default) takes the candidates from the PREVIOUS run's scheme_health.json
  (run_frozen_night: $PREV_RUN; regrade_from: the source run), a comma list overrides,
  empty skips. v21 pre-registers `prune = <list>` only if ab_prune holds the deep
  budgets against the hard arm.
  V20 KILLED FOR v21 (2026-10-22, operator: "why not kill v20 and just do v21 now?"):
  about five hours into v20 (the embeddings), with the lab scheme needing a full export
  anyway and the priors / prune arms graded inside any run, v21 starts about six hours
  sooner at the cost of the batch-2-only baseline. The runner now takes the SHIPPED
  policy from env: CLASS_PRIORS (e.g. taxonomy,entity -> the export's --class-priors)
  and DROP_SCHEMES (-> --drop-schemes); when CLASS_PRIORS is set, step 8 grades
  ab_no_priors/ (--class-priors '') instead of ab_class_priors/, so the counterfactual
  always exists. v21 ships priors taxonomy,entity, NOT the prune list (ab_prune/ only),
  under the v20 checks + `priors = taxonomy,entity` + `prune = none` (task v21_run).
  SCHEME DEEP DIVE (2026-10-22, operator: "why are the non-signal schemes not working:
  calculated wrong, missing data, or not a real indicator; what undocumented schemes is
  the model picking up; what has the industry got that we have not wired";
  `docs/SCHEME_DEEP_DIVE_2026-10-22.md`, read before any prune or scheme design): per
  scheme a diagnosis class (A calc / design defect, B inputs missing, C wrong
  population, D not an indicator as built, E unjudged, F signal) from the v19 packet's
  four instruments read together. FINDINGS: half the coin-flip schemes are class C (the
  Medicare physician files score INDIVIDUALS, the FCA ruler is 82% organizations:
  pharma_kickback's top decile is DEPLETED of cases, lift 0.26; single_service_mill is
  inverted because the ruler's population is diversified); seven inputs are near-
  constant flags whose percentile is a tie block (class A); the validated schemes are
  SECTOR schemes with a sector file (worthless_services 0.94 on proven cases, hospice
  0.84); the model's unnamed signal = labs (now lab_testing), org size / breadth (the
  class priors), the billing-embedding dimensions (undecoded), the servicing spread,
  and sector codes with no scheme (T1015 FQHC, H2015 / S9480 community behavioral,
  G0299 home health, A4253 diabetic DME, U0003 COVID). FOURTEEN unwired schemes ranked
  (personal_care_hcbs first: the code family exists for damages scoping and no scheme
  reads it; T1019 is NOT on the DOJ ruler because MFCUs prosecute it, so its ruler is
  the state lists). BUILT: `scheme_health` reports `fca_auc_ind` / `fca_auc_org` (+ the
  positives) per scheme so v21's packet says class C vs D before any prune
  (tests/test_scheme_health_population_split_2026_10.py). Order: measure (v21), fix
  the class-A flags, wire batch one (personal care, home health, lab unbundling,
  psychotherapy duration) as a pre-registration graded on its own ruler.
  BATCH ONE OF THE UNWIRED SCHEMES (2026-10-22, v22 semantics = new pre-registration;
  tests/test_batch_one_schemes_2026_10.py): four of the deep dive's fourteen, all from
  the spending fact in `ingest_cms/sector_schemes.py` (one SQL pass with the NEMT / BH
  / lab metrics): `evv_personal_care` (the BROAD_HCPCS_CODES T1019 family: pc_paid_share
  / pc_lines_per_patient_month / pc_paid_per_patient_month; patient-months =
  SUM(patients) over months, so lines over it is the units-per-patient-month proxy),
  `home_health_intensity` (HH_CODES: G0299 / G0300, G0151-G0162, S9122-S9124, T1021 /
  T1022, T1030 / T1031: hh_* the same three), `lab_unbundling` (lab_component_share =
  component dollars over component + panel dollars on the chemistry-panel family;
  lab_draw_per_test = venipuncture lines per non-draw lab line), `session_inflation`
  (psy_60min_share 90837 / 90838 of 90832-90838; em_prolonged_share 99354-99359 /
  99415-99417 / G2212 per E/M line; psy_interactive_share 90785). NULL without the
  family (the row is absent), an observed 0 on a share is real. Registered in the
  weights, code families, sector priors, dossier narratives, case_validation (sectors
  personal_care / home_health / lab / behavioral; conduct phantom_billing / upcoding /
  medical_necessity), RUNBOOK_TRAVIS 5.4, and Model B's role matrix (line-of-sight
  roles for the new names + lab_testing). RULER NOTE: personal care is prosecuted by the
  state MFCUs, so its grade comes from the state-termination / MFCU rulers; a coin flip
  on the DOJ ruler there is a ruler fact, not a scheme fact.
  BATCH 3 (2026-10-22, the three fixes the v19 read-out named; tests/test_batch3_fixes_
  2026_10.py): (1) `nppes_deactivation.pick_deactivation_file(asof_cutoff=)` reads the
  newest edition on/before the cutoff month under a freeze (v10..v20 read the newest
  file under every freeze: reactivations / corrections recorded after the freeze rode
  along); `_deactivation_file(asof_cutoff=)` at all three call sites writes the ledger
  verdict pre_cutoff / post_cutoff_reference / current_day_newest; the CMS MMDDYY stamp
  is read before `.` as well as `_`. (2) run_issues: a SCHEME_TRIAGE POST-CUTOFF row is
  the file ON DISK; when the manifest's vintage ledger covers the source (token match)
  it is INFO `post_cutoff_on_disk` (the ledger row says what was read), else the FAIL
  stands (the ten-run facility / PBJ / POS false FAIL). (3) `label_ablation --label-tier
  --exclusion-nodes --cutoff`: the baseline is the SHIPPED tier label
  (company_list._training_label), union-positive rows the policy drops are masked from
  training (never negatives), a source whose rows never train under the policy has no
  minus_ row; measure_run.sh passes TRAIN_TIER (default hard). v19's ablation under
  policy 'all' had "found" what the hard policy already did.
  LAPTOP IS NOT A COMPUTE SEAT (operator 2026-10-22, after the data scan and the capacity
  check took the 16 GB laptop to 15.6 GB / 100 percent disk): the laptop does merges,
  browser downloads, reminders and reading ONLY. The data scan (`ops/vm/data_audit.sh`),
  the licensed-roster load (`ingest_cms.licensed_capacity`) and the capacity check
  (`model_a.capacity`) run on the VM after the preclean sync (tasks post_download_checks
  and capacity_on_vm carry the ssh lines); run_licensed_capacity.bat / run_capacity_v3.bat
  are retired. Never hand the operator a laptop line that reads the spending fact.
  THE WESLEYAN RESIDENT'S LEDGER (2026-10-22; the read-out and the 40 transcribed
  therapy lines went to the operator as chat files for `<data_root>/enforcement/
  pi_reports/wesleyan/`, NEVER git; the resident, her account number and the facility
  user who printed the report are named nowhere in the repo): nine photos of the
  facility's own "Transaction Report by Effective Date" (Jan 2025 to Sep 2026, UHC
  Managed Medicaid): care level HIGH billed every day against her "not high care since
  end of 2023"; PT in Jan 2025 (two Sundays) and OT in May and Sep 2025 (an eval then
  fifteen straight weekdays) she says never happened, every $50 line offset to zero
  (therapy is inside the per diem, so the damages theory is the MDS / case-mix level,
  never the lines); a $30/day private-room incentive to verify; cafeteria charges to
  a Medicaid resident. The model cannot see any of it (no HCPCS on a per diem), so it
  is a document-and-witness lead. BUILT: `ingest_cms/pbj_therapy.py` (the PBJ Daily
  NON-Nurse Staffing file's OT / PT / SLP hours per facility-day, DuckDB-filtered on
  the CCN so the laptop may run it; `--sessions` puts the ledger dates against them;
  the ONE verdict is a session on a day the home reported ZERO hours of that
  discipline = a contradiction between the facility's two filings; innocent
  explanations listed first) and `ingest_cms/ccn_lookup.py` (every row for one CCN
  across a folder of CMS CSVs: citations, penalties, ownership). Reminder
  `pbj_nonnurse_2025` (the three 2025 quarters, proof glob) + task
  `wesleyan_ledger_check` carry the order: PBJ check, counsel's record request
  (therapy notes + MDS Section O / GG, level-of-care authorizations, UHC claims), the
  Ohio cost-report request, the Care Compare rows. Weekend therapy on a long-stay
  Medicaid resident is a question, not a finding.
  V21 READ-OUT (2026-10-23, `docs/ACCURACY_READOUT_v21_2026-10-23.md`): the first run
  with class priors shipped. Pursuit book, FCA ruler: 3 / 3 / 4 / 8 at 16 / 50 / 100 /
  250 (random 0.1 / 0.4 / 0.7 / 1.8; the best 16 and 50 of the series), large 3 / 3 / 5
  / 6, lead 24 mo, inst share 0, zero capped_no_basis in the top 50. HOLD on ONE check
  (15 of 16): top-50 Jaccard 0.247 (bar 0.6). The priors did it, twice: the taxonomy
  prior gave a 199-NPI hematology class a 24.6% training rate (49 positives from ONE
  broadcast settlement, lift 70) and the NPI-grain top 1% filled with the high-prior
  classes (NPI lift 0.0 against 8.7 on the no-priors arm; the first 6,000 NPIs hold
  none of 611 forward positives), and the per-fold encoding of thin classes
  re-shuffles the top 50 between draws (9 orgs in every draw). FIXED (v22 semantics):
  `class_priors.encode_fold(w=, min_n=)` counts positives by the per-case
  `label_weight` and sends a class under `MIN_CLASS_N` (300) training rows to the
  global rate; `rate_table` records `weighted_positives` / `below_min_n`;
  `company_list.fold_design` passes `training_weights`. Also: the lab mix shares are
  clipped (refund months read -0.0002); hospital-licensed members (NUCC 27xxxx /
  28xxxx: `pursuit_dossier.hospital_org_ids`) make an org institutional for the
  dossier pick and a `hospital_licensed` flag column, never a filter on the graded
  book (the v21 pick named Kingman Healthcare, Owensboro Health, SVMC, Park Nicollet);
  "HEALTH PLAN" is a cue word. `regrade_from.sh` copies the aff / quitam rulers,
  grades `ab_no_priors/` when the source manifest ships priors, and packs the csv
  files and every arm. RULE: every pre-registration from v22 carries
  `npi_lift1pct >= 3` (the NPI grain collapsed while every org check but Jaccard
  passed). The arms (single refits, main list): priors +1/+1/+1 over none; prune
  mixed (stays none); charged +1/+1 over hard (re-run on v22); mask over weight this
  run (noise). The order sweep: shipped pmax_basis50 wins 16 and 50, plain pmax wins
  100 (6 vs 4); sort stays. cost_report_fraud (0.90) and hospice (0.83) leak_suspect
  read as the institutional selection effect (every pick pre_cutoff). v22 = re-grade
  of v21's matrix (the re-score runs the new encoder; no export), pre-registered
  under the v21 sixteen + npi_lift1pct. Not dispatched.
  V22 READ-OUT (2026-10-24, `docs/ACCURACY_READOUT_v22_2026-10-24.md`): v21's matrix
  re-scored under the corrected priors (label-weighted positives + the 300-row class
  floor). HOLD on five of seventeen: pursuit 0 / 1 / 1 / 1 (v21 3 / 3 / 4 / 8), large
  0 / 1 / 1 / 4, Jaccard 0.009 (0 orgs in every draw); NPI top-1% lift 19.3 (v21 0.0,
  the best ever) and the main list 1 / 2 / 4 / 4 (v21 0 / 1 / 2 / 4). THE LESSON: the
  fix moved the signal from organizations to individuals. v21's prior-dominated scorer
  ranked the settlement classes (national labs, health systems under one NPI: LabCorp,
  Cleveland Clinic, Genomic Health, Providence, Medline, BioReference = the hits) to the
  head; v22's feature-dominated scorer ranks home-care / HCBS / group-home agencies
  (raw member scores 0.57 to 0.69, no FCA case) there, and the labs fell to ranks 45 to
  1,136. The calibrated score_p_max is a 296-way plateau (0.010853) at the top of the
  book, so the pmax order inside it is fold noise. The lab / hospital priors barely
  moved (1.16% to 1.09%, 2.15% to 1.97%); 590 of 775 classes fell under the floor.
  BUILT (v23 semantics): the prior ENCODER is training policy (`prior_min_n` /
  `prior_weighted` in the manifest, export `--prior-min-n` / `--prior-weighted`,
  CLI overrides on the dossier and the diagnostics, `company_list.prior_encoder_args`
  the one reader, registry aliases `prior_floor` / `prior_weighted`); the three
  encoders (v21 = 0 / off, floor = 300 / off, weighted = 300 / on) are refit arms
  `ab_prior_<name>/` in both runners (PRIOR_MIN_N / PRIOR_WEIGHTED env = the ship, that
  one skipped); EVERY refit arm now grades the OPERATOR book under the shipped order
  (`accuracy_diagnostics.operator_book_from_refit` on a rollup carrying calibrated
  probabilities + the shared `pursuit_dossier.entity_segments` rule -> `acc_pursuit_
  hits16/50/100/250`, aliases `refit_pursuit_hits*`): v22's arms graded the main list
  only while the main list and the pursuit book moved in opposite directions. RULE: an
  arm that cannot grade the pursuit book cannot pick a policy. v23 = re-grade of v21
  with the floor-only encoder shipped (PRIOR_WEIGHTED=0), v21 and weighted as arms,
  under the seventeen checks. The runner DONE banner's packet hint listed the arms from
  the repo cwd (none): `$(cd $RUN && ls -d ab_*)` now. Not dispatched.
  ARMS INHERIT THE SHIP (bundle 389, 2026-10-24, built while v23 ran): every
  `--score-source refit` arm (broadcast weight / mask, no_priors, prune, charged,
  commercial) and every graded book line in both runners carries `$PRIOR_FLAGS`, so an
  arm is the shipped policy plus ONE change (v23's broadcast / prune / charged arms
  refit under the manifest-default WEIGHTED encoder while the ship was floor-only; only
  its ab_prior_* arms are a clean comparison). `training_label_summary(manifest=)` writes
  the EFFECTIVE `training_policy` (CLI override, else manifest) into
  training_label_policy.json and `run_registry` prefers it over the manifest's keys: a
  re-grade copies the SOURCE manifest, so v23's row reads the encoder 0 / off from v21's
  manifest while its lists were built under 300 / 0 (the launch line is the record for
  v23). tests/test_bundle389_arm_flags_2026_10.py.
  V23 READ-OUT + THE PRIOR LEAK (2026-10-24, `docs/ACCURACY_READOUT_v23_2026-10-24.md`):
  v21's matrix under the floor-only encoder read EXACTLY like v21 (pursuit 1 / 3 / 5 / 8,
  large 2 / 3 / 6 / 6, Jaccard 0.240, NPI lift 0.0; HOLD on two) while the weighted arm
  read like v22 (NPI lift 19.3, books collapsed): the floor changed nothing, which exposed
  the defect. `class_priors.encode_fold` encoded TRAINING rows leave-one-out, so inside a
  class every positive sat exactly one count below every negative and LightGBM read the
  prior column as the label (synthetic twin: training AUC 1.000, OOF 0.589 vs 0.664 without
  priors, median score down 20x); at test time every row carries the fold's full rate, lands
  on the negatives' side of the split, and the model predicts "negative" for nearly everyone
  (the v21 / v23 books' median raw score 0.0000016 against a base rate of 0.0028). EVERY
  class-prior result v21..v23 and every ab_class_priors / ab_no_priors / ab_prior_* arm is
  contaminated; the v21 / v22 read-outs described the leak's expression. FIX (v24
  semantics): training rows encoded NESTED from a deterministic inner five-fold
  (`encode_fold(inner_folds=)`, `PRIOR_INNER_FOLDS` 5; 0 = the LOO path, a gradable arm,
  never a default: on the twin nested priors beat no priors, 0.698 vs 0.664); policy knob
  `prior_inner_folds` (manifest, export / dossier / diagnostics `--prior-inner-folds`,
  `set_training_policy`, `prior_encoder_args` + `rate_table_args`, registry alias
  `prior_inner`), runners `PRIOR_INNER=5` in `$PRIOR_FLAGS`, `PRIOR_ARMS` now
  name:min_n:weighted:inner with default `loo_v22:300:1:0 nested_unweighted:300:0:5`.
  RULES: a target encoding fed to trees is never leave-one-out; a model whose median raw
  score sits far below the label base rate is broken before any hit is read (add the check
  to the diagnostics); v24 = v21's matrix re-graded nested, pre-registered under the v23
  seventeen + `prior_inner >= 5` + `prior_floor = 300` + `prior_weighted = True`, graded
  against the no-priors arm (operator book 0 / 1 / 3 / 4); if nested does not beat it, v25
  ships without class priors. tests/test_prior_encoder_leak_2026_10.py. Not dispatched.
  V24 READ-OUT (2026-10-25, `docs/ACCURACY_READOUT_v24_2026-10-25.md`): the first honest
  class-prior run (nested encoder). HOLD on ONE of twenty: top-50 Jaccard 0.555 (28 orgs in
  every draw; v17 0.575, v19 0.618). Pursuit 1 / 3 / 4 / 7, large 1 / 3 / 4 / 6, NPI lift
  6.7, lead 24, inst 0, score scale 0.168 ok (v21 / v23 0.0006). The arms settle the priors
  question: the leaking v22 encoder refit reads score scale 0.014 (degenerate, NPI lift
  19.3: the new check catches it); the shipped nested policy refit and the no-priors refit
  read the SAME operator book (0 / 1 / 3 / 4) and no-priors has the better NPI grain (8.7
  vs 6.7). Honest class priors add nothing measurable: the trees already find the lab /
  system classes from the billing features. The shipped list vs its own refit (1 / 3 / 4 / 7
  vs 0 / 1 / 3 / 4, same policy and seeds) is the fold draw the Jaccard measures. DECISION
  (the rule written before v24): v25 = v21's matrix re-graded WITHOUT class priors
  (`CLASS_PRIORS=''` on the regrade runner, which now takes the override on every ship
  and arm line; `PRIOR_ARMS=''`; nested priors graded as ab_class_priors/), under the
  twenty with `priors = none`; a pass is the first PROMOTE candidate. Fixed in bundle
  391: the diagnostics' rate-table block imported the pre-refactor argument builder (the
  guarded NameError meant v24 wrote no class_prior_rates csv); the dossier's override log
  line names the encoder keys. The broadcast-mask arm reads degenerate (0.085): mask
  stays an arm. FQHCs (JWCH Institute, North East Medical Services) reached the dossier
  pick: a `health center` cue is due on the institutional list. Not dispatched.
  V25 READ-OUT (2026-10-25, `docs/ACCURACY_READOUT_v25_2026-10-25.md`): v21's matrix
  WITHOUT class priors. HOLD on ONE of twenty: pursuit hits@100 = 3 (bar 4); pursuit
  2 / 2 / 3 / 6, large 2 / 2 / 2 / 6, Jaccard 0.622 PASSES (31 orgs in every draw), NPI
  lift 8.7, score scale 0.185, lead 22. THE SERIES HAS CONVERGED: v17 / v19 / v24 / v25
  are one model (2-3 at 50, 2-5 at 100, 6-8 at 250, Jaccard 0.55-0.62, NPI 7-9), each
  missing a different check by one hit; the head checks now measure the fold draw, and
  no further re-grade of v21's matrix is informative. MEASUREMENT DEFECT FIXED (bundle
  392): the diagnostics' refit used plain GroupKFold and skipped the unassessable mask
  while the shipped scorer uses StratifiedGroupKFold and holds them out, so every arm
  sat 1-2 hits below the shipped list at 16 / 50 in v24 and v25 by construction;
  `oof_with_folds` now calls `company_list.fold_assignments(seed=)` (repeat 0 reproduces
  the shipped scores exactly, pinned by test; a repeat re-draws the same design).
  Arm-vs-arm readings stand. Also fixed: `${PRIOR_ARMS-default}` (an EMPTY value is no
  arms; `:-` read it as unset and v25 ran two no-op encoder refits); KINGMAN HEALTHCARE
  and SONORA QUEST on `_NATIONAL_NAMES` (repeat dossier picks). THE DOSSIER IS THE
  OPERATOR'S DECISION: the rule says no credits on a HOLD; the other reading is that v25
  is the first honest, stable, 19-of-20 list and the operational baseline; the tasks stay
  open until the operator says dispatch or skip. v26 = REPLICATION ON THE SECOND FREEZE
  (2023-12, HORIZON=33, no priors, pmax_basis50, train hard; `run_frozen_night.sh v26
  2023-12`), pre-registered with the second freeze's own hit bars (hits100 >= 2, hits250
  >= 4: fewer cases resolve in 33 months) + the structural twenty; the data scan runs
  first on the idle VM; the lanes dispatch (gated) feeds both freezes.
  FREEZE POINT v25 (2026-10-26, operator: "freeze this version and come back to it if v26 /
  v27 do not get there"): tag `freeze-v25-2026-10-25` + branch `freeze/v25-2026-10-25` at
  3898faa (the commit v25 ran on; bundle 393 carries both; merge_push.bat pushes freeze-*
  tags). THE BASELINE POINTER (`run_registry --pin-baseline <tag> --reason --freeze-tag` ->
  `<data_root>/model_a/BASELINE.json`; `--run-dir @baseline` on every output CLI;
  `--champion` prints it): the OPERATOR-PINNED fallback run, distinct from the champion (a
  validation fact: the newest PROMOTE; a HOLD run can be a baseline, never a champion). The
  run artifacts stay in /data/medicaid/model_a/frozen_2021-12_v25 on the VM disk (persists
  while stopped) and `sync_data.sh up frozen_2021-12_v25` copies the reports + registry to
  OneDrive; the packet tar is on the laptop. RULE: v26 / v27 replace the baseline only by
  beating it on an independent ruler (the second freeze) or passing all twenty on this one;
  the pin moves by the operator's word, never by a runner.
  CD1, THE FIRST CURRENT-DAY RUN (2026-10-28, `ops/vm/run_current_day.sh cd1`, DONE 02:04 UTC,
  0 FAIL, 43 of 46 sources, 27 schemes, 617,062 providers, 3,270 hard positives): the
  operator book's head is labs + health systems again (the national list missed Aegis,
  Curative, Ballad, OhioHealth, UofL, Fred Hutchinson, Oakwood, Mercy, Nassau Health Care,
  MTM, AMR: added), the pick re-named Ideal Option (HOLD) and HealthTrackRx (watch) a week
  after v26's research settled them, and six new operator-run names (Genesis Reference
  Labs, Accu Reference, Gravity Diagnostics, National Labs, Childrens Health Care
  Associates, Pediatria) are the research candidates. The attending-concentration report
  confirmed the lens (Andes rank 7, Morrison rank 18; their billing entities rank 1,756 and
  1,950 on the main list, which is why the scorer alone never surfaced them); most other
  rows are medical directors on CMHC / OTP / ambulance claims and lab directors on
  COVID-era tests (innocent by construction); one desk candidate: a San Diego cardiologist,
  $83M as renderer across 31 billers, 36478 vein ablation. The contradictions report was
  WRONG at the top: 819 of 1,194 billing_after_exclusion rows were Florida FINE / CAP
  listings (Boston Medical Center $768M first) and the enrollment-end rows read one state's
  termination against claims billed everywhere. FIXED (bundle 408): `contradictions.
  event_keep` drops fines / caps / probation, another state's action against a provider
  billing from elsewhere (the provider's state is the proxy; the fact carries no claim
  state) and OpenSanctions mirrors, into contradictions_setaside.csv with the reason.
  STANDING VERDICTS (`model_a/pursuit_verdicts.py`, `<data_root>/model_a/pursuit_verdicts.csv`,
  org_id / org_name / verdict pursue|hold|watch|set_aside / date / reason / run): the
  operator's decisions on organizations, honoured by every dossier pick
  (`dossier_verdicts_applied.csv`), never by the graded books; `--init` seeds the v26 read,
  `--add` records a new one (the operator's word). ANDES CORRECTION (Josh, 2026-10-27):
  the docket check on the CHHA's name missed the December 2025 NY AG / E.D.N.Y. settlement
  with its SISTER agency Americare, Inc. ($55M: wage parity 2014-2020 + $10M Medicaid, from
  a whistleblower complaint; AG reporting for three years; investigations into other
  agencies named "ongoing"): different conduct and years, but the government has a file on
  the group. RULE: every first-to-file names list carries sibling entities, d/b/a names
  and the owners' other companies. The Shemias agreed to buy Extended in September 2019
  and closed in January 2022; "no link" reads "no direct link"; the $10M a month is the
  late-2024 run rate under his number, not an average.
  CD1 RESEARCH READ (2026-10-29, the six dispatched 2026-10-28): Accu Reference Medical Lab
  (NJ, $184M; former owner's 2018 kickback conviction, 2022 OIG billing settlement, new parent
  NeoDNA 2025) and Genesis Reference Laboratories (FL/CO, $42M; 2023 DOJ FCA settlement for
  2019-2021 marketer kickbacks; the case is the post-settlement billing) = pursue candidates;
  Gravity Diagnostics and National Labs = watch; Childrens Health Care Associates (CHOP
  practice plan) and Pediatria (Aveanna) = set aside. The verdicts file worked (the re-render
  skipped the four decided names) and the refilled pick named three more subsidiaries (REM NJ =
  Sevita, Home Care Delivered, Sleep Management = Viemed), now on the national list with
  Pediatria and CHCA; Total Wellness Centers (MA, $117M) is the one new candidate. The plain
  `--poll` through build() died silently three times in an ssh session; `--poll-only` and a
  tmux-logged `--operator-book --poll` are the way (40 minutes of scoring).
  V26 CRASH + RESUME (2026-10-26, bundle 394): v26 (the second freeze) died at step 8 after
  five finished hours: the prune arm's "skipped" echo read `$PRUNE_AB` under `set -u` (no
  previous 2023-12 run = no prune list, a branch no run had taken). Guarded
  (`${PRUNE_AB:-auto}`). `RESUME_FROM=<step id>` on run_frozen_night.sh re-enters the SAME run
  folder at that step (ids 0 0m 1 2 2a 2b 3 4 5 6 7 8 8p 8c 8x 9 10 11 12 13; `run_step`
  prints 'skipped' for every earlier step; the pre-registration is untouched). v26 resumes at
  8p (the charged arm, the dossier, registration, the measurement ablations, the ledger). The
  2023-12 scan (audit_20261026_0012) found NO pre-cutoff All-Owners edition for the second
  freeze (reminder owners_2023_editions: the archived 2023-12 releases into owners_prior/)
  and `admin_costs` absent in the HHA cost reports (the HHA rows of cost_report_fraud
  compute on a default). RULE: a runner branch no run has taken is untested; `bash -n` does
  not catch `set -u`; every `$VAR` in an echo is `${VAR:-}`.
  V26 READ-OUT, THE SECOND FREEZE (2026-10-26, `docs/ACCURACY_READOUT_v26_2026-10-26.md`,
  `docs/FREEZE_PAIR_v25_v26_2026-10-26.md`): the 2023-12 export under v25's policy, graded on
  the 33-month ruler (273 positives in the universe). HOLD on ONE of seventeen: Jaccard
  0.588 (26 of 50). Pursuit 1 / 2 / 5 / 6 (p 0.014 / 0.000 / 0.000), large 1 / 3 / 5 / 6,
  NPI lift 15.0 (best of the series), score scale 0.215, lead median 4 months (the ruler is
  young; lead_ge_gap exactly 0.5). FREEZE PAIR: 8 of 8 comparable checks hold on both
  freezes. Independence is PARTIAL: the matrices are two years apart but 1,266 of v26's
  2,598 ruler positives (the 2024 cases) are inside v25's 36-month window; three of the six
  hits are on both rulers, Tactile Systems (31 mo) and Dana-Farber (24 mo) are not. The
  shipped-policy refit reads the shipped book to the hit (the bundle-392 fold fix
  confirmed); priors add nothing on this freeze either. FIXED (bundle 395, v27 semantics
  for two schemes): `facility.pick_edition_candidates` + the export's fallback loop (the
  hospice picker took the May-2026 release for 2023-12, 0 CCNs, while Nov-2023 sat beside
  it: subscore_hospice_ineligibility all-NaN in v26); `em_prolonged_share` /
  `psy_interactive_share` capped at 1 (add-on lines over base lines read 469x / 199x) and
  `lab_component_share` on positive-only dollars (1.47 on refund months);
  `pursuit_dossier.public_since_freeze` sets aside every org with a member on the run's
  forward ruler (v26's pick named Precision Toxicology, a 2024 settlement, first), lists them
  in dossier_public_since_freeze.csv, never ranks on it (`--keep-public-cases` restores);
  SANFORD / STANFORD HEALTH / EUROFINS on the national list. DISPATCH: the rebuilt v26
  operator book is the candidate (two years more current than v25, the method replicated);
  the operator's word sends it. v27 = the 2023-12 export again once the owners editions
  and the hospice fix are in. RULE: a frozen run's dossier is an operational pick made
  today; the ruler may remove rows from it and never order them.
  THE NY PA RULE (2026-10-23, the operator: 'it doesn't seem like fraud, does it?' after Sage's
  Wallabout visit found a real, busy practice): NY Medicaid FFS has the supervising physician
  BILL a registered PA's services and PAYS THEM AT THE PHYSICIAN FEE (Physician Policy
  Guidelines: 'payment according to the standard fees that physicians normally receive; no
  duplication or increase in charges solely because an RPA provided assistance'); the duty is
  disclosure (the claim must indicate the services rendered by or with the RPA, the RPA's name
  and Medicaid ID; the PA in the rendering field with the physician as supervising; PAs enrolled
  as non-billing; at most six RPAs per physician in private practice). So 'many clinicians
  billed under one physician's NPI' is a claim-content defect with NO rate damages on NY FFS;
  the money theories that survive are unlicensed / unenrolled renderers, supervision ratios,
  and the managed-care plans' own rendering rules (unverified). RULE for the capacity cards: an
  individual-NPI hours flag on a GROUP practice is a records question (PA rule, state by state)
  before it is a PI card; card 1 of packet v11 is a desk question for counsel, not a visit.
  SHORT BOARD (2026-10-21, operator: the full page after every --done was unreadable
  in a command window): `python -m src.reminders` and every state change print
  `render_board` (one line per due item and queued task, no steps or commands);
  `--full` prints the page; docs/OPERATOR_REMINDERS.md always carries the full page.
  REMINDER ENGINE RULE (2026-10-21): `--done-task` REMOVES a task from `tasks`; a
  `done` flag closes nothing (four closes written with a flag stayed on the operator's
  page for a day). Close a task in the JSON by moving it to the `closed` list.
  PORTING RULE (2026-10-21): a bundle's reminders.json REPLACES the laptop's copy on
  merge, so every `--done` / `--done-task` the operator ran since the last bundle must be
  replicated in the JSON before the next bundle is cut (leie and the 2021 editions were
  re-opened by bundle 334 that way). Read the operator's pasted engine output for closes.
- **Next increments:** run adapters/exposure against real procured files; DOJ
  fetcher + 10-year backfill; docket monitor; Model B person-resolver (the one
  missing piece to activate the B chain; gated on people-data license). The
  **data-expansion sprint** (`docs/platform/16`) is the queued build: stubs for
  hospital PUFs, Geographic Variation, NUCC crosswalk, SDUD, CHOW, and OIG CIA
  are in place with contracts — build NUCC first (it fixes peer grouping for
  every scheme); OpenSanctions commercial license is the one Brad decision.
- **Gated on data/licensing:** person↔employer resolution (people-data vendors, FCRA
  review), Model C labels (DOJ/PACER case DB), `refers_to`/`pays` edges.
- See `docs/platform/ROADMAP.md` for the full phase plan and
  `docs/platform/09-data-procurement.md` for exactly what data to acquire and why.

## Git

- Work on feature branches; `main` is protected history. The pre-platform original
  build is pinned at branch `original-build` / tag `pre-platform-snapshot`.
- Commit author must be `Claude <noreply@anthropic.com>`.
- Raw data, PDFs of the confidential strategy, and derived parquet never go to git
  (the strategy PDF lives only on the dedicated `docs/source-pdfs` branch).
