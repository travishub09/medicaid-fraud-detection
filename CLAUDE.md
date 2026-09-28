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
  Full suite: `pytest tests/` (935 tests).
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
