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
  VM (2026-10-12): r6i.4xlarge launched in us-east-2 (quota 32), Ubuntu 26.04, rclone
  1.75 installed by the rclone script (the apt 1.60 is too old for the OneDrive token),
  `rclone config` answers: onedrive / onedrive / Enter / Enter / 1 / n / n, token from
  `rclone authorize "onedrive"` on the laptop, drive = the entry named "OneDrive
  (personal)", first `sync_data.sh` pull running under tmux `sync`.
  Full suite: `pytest tests/` (1,061 tests + 2 skipped). Dead columns are left OUT OF THE
  TRAINABLE LISTS but KEPT in the parquet (case_size reads billing_residual by name).
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
