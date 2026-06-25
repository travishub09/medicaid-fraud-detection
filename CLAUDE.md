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

The full strategy is in `docs/platform/` (start at `docs/platform/README.md`); human onboarding lives in `docs/WHAT_WAS_BUILT.md` (the briefing), `docs/HOW_IT_WORKS.md`, `docs/GETTING_STARTED.md`, `docs/GLOSSARY.md`, `docs/READING_THE_OUTPUTS.md`, `docs/TROUBLESHOOTING.md`, and the data runbook `docs/platform/12-data-runbook.md` — keep them current when behavior or commands change. The supervised-model handoff is documented in `docs/PROVIDER_FEATURES_FOR_MODEL.md` (per-field reasoning + fraud rationale for Travis) and `docs/DATA_ACQUISITION_GUIDE.md` (step-by-step URLs + save paths for every unlock source). The
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
                    death-master + label store (built)
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

# Pipeline status — "where did I leave off?" (read-only; honors MEDICAID_DATA_ROOT)
python -m src.pipeline_status

# Tests (work without any real data)
python -m pytest tests/ -v
```

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
  `make provider-features` target that rebuilds the graph first.
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
  `exclusion_label_sources` provenance), DuckDB-streamed growth/plausibility
  (`*_from_parquet` — full-universe `--with-analytics`, no pandas OOM), and
  NPI-grain ownership refinements (`org_member_count` + `has_excluded_owner`).
  Pillar 3 of the ground-up redesign (docs/platform/17-provider-signal-architecture.md)
  is BUILT: `entity_graph/graph_embeddings.py` emits per-node DeepWalk-style
  embeddings + a personalized-PageRank fraud-proximity field + structural motifs
  (k-core/triangles/clustering), written as `graph/node_embeddings.parquet` and
  mapped to NPI grain in the export (`graph_emb_*` + `graph_fraud_proximity` =
  leakage-adjacent; motifs = clean) — the structural fix for org→NPI broadcast.
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
  Full suite: `pytest tests/` (319 tests).
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
