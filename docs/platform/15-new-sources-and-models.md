# 15 — New Data Sources & Models (June 2026 sweep)

The additive sources and models from the June-2026 research sweep
(doc 14 §E), written so a non-specialist can act: **where to download**, **what
it is**, **what it's good for**, **how we use it** (which adapter/scheme), and —
for the models — **how the model works**. Legal frame is unchanged: every output
is an investigative hypothesis for human/counsel review, never an accusation;
nothing here crosses a DUA, license, or FCRA gate.

Status key: **BUILT** = adapter + scheme in code, tested, dormant until the file
lands. **CONTRACT** = documented build-spec here, not yet coded.

---

## Part 1 — The graph database: Neo4j (BUILT)

**What changed.** The earlier plan used Kùzu; Kùzu was acquired by Apple and its
repo archived in Oct 2025. We switched to **Neo4j**, the mature standard for
property-graph storage with first-class visualization (Neo4j Browser / Bloom).

**What it is.** A property-graph database: nodes (Provider, Owner, Exclusion,
Org) and typed relationships (`member_of`, `owned_by`, `excluded_in`,
`co_located`) with properties on each. The relational node/edge parquet the
pipeline already produces maps onto it one-to-one.

**What it's good for.** Interactive investigation — an analyst pulls up a flagged
org and walks its ownership network, finds common-owner rings and shared-address
shells by clicking, and runs ad-hoc Cypher. The *scoring* still runs on the
relational tables + NetworkX (no Neo4j dependency in the pipeline); Neo4j is the
human lens on top.

**How we use it.** `src/entity_graph/neo4j_export.py`, two paths:
- **Offline bulk load** (no driver, fastest for a fresh DB):
  `python -m src.entity_graph --input <dir> --out <graph> --neo4j-bulk <dir>`
  writes `neo4j-admin database import` CSVs (`:ID`/`:LABEL`/`:START_ID`/
  `:END_ID`/`:TYPE`), a runnable `import.sh`, and `analyst_queries.cypher` (the
  ring patterns `ring_detection.py` computes, but clickable).
- **Online load** via the official driver (idempotent, safe to re-run):
  ```python
  from neo4j import GraphDatabase
  from src.entity_graph.neo4j_export import export_to_neo4j
  drv = GraphDatabase.driver(uri, auth=(user, password))
  with drv.session() as s:
      export_to_neo4j("~/Desktop/data/graph", session=s)
  ```
  Node ids are the namespaced keys (`org:`, `provider:`, `owner:`,
  `exclusion:`), so MERGE never duplicates on re-import.

**Download / install.** Neo4j Community Edition (free, self-hosted) or Aura Free
(hosted). `pip install neo4j` for the driver. No PHI lives in the graph (IDs,
names, structure only) — but host it on the same secured box as the rest.

---

## Part 2 — New data sources

### 2.1 CMS Part D Opioid Prescriber rates — BUILT (`ingest_cms/opioid.py`)
- **Download:** data.cms.gov → "Medicare Part D Opioid Prescriber Summary File"
  (annual CSV) → `preclean/opioid/opioid_prescriber_YYYY.csv`. Free.
- **What it is:** per-prescriber (NPI) opioid claim counts, opioid prescribing
  rate, and the long-acting split.
- **Good for:** the pill-mill / diversion signature — an opioid share far above
  specialty peers, especially long-acting.
- **How we use it:** `compute_opioid_metrics` → `opioid_claim_share`,
  `opioid_long_acting_share` (raw) → `to_peer_percentiles` → the **`pill_mill`**
  scheme (recovery multiplier 0.45, J-code family).

### 2.2 NPPES deactivation file — BUILT (`ingest_cms/nppes_deactivation.py`)
- **Download:** download.cms.gov/nppes → monthly NPI deactivation file →
  `preclean/nppes/npi_deactivation.csv`. Free.
- **What it is:** NPI + deactivation date.
- **Good for:** billing on/after an NPI's deactivation — a fly-by-night / stolen
  or retired-identity signal. Cleaner than the SSA Death Master File because it
  is keyed by NPI (exact match), not name/SSN.
- **How we use it:** `deactivated_npis` + `billing_after_deactivation`
  (joins the spending fact + `npi_to_org`) → `billing_after_deactivation` (0–1
  share) → the **`invalid_identity`** scheme (recovery multiplier 0.85 — billing
  under a dead NPI is near-fully unsupported).

### 2.3 HRSA 340B OPAIS — BUILT (`ingest_cms/hrsa_340b.py`)
- **Download:** 340bopais.hrsa.gov → Daily Report (covered entities + contract
  pharmacies, Excel) → `preclean/hrsa/opais_daily.csv`. Free, no login.
- **What it is:** every 340B covered entity and its registered contract
  pharmacies.
- **Good for:** the contract-pharmacy arbitrage/diversion shape — an entity with
  an outsized contract-pharmacy footprint.
- **How we use it:** `covered_entities` → per-entity contract-pharmacy count;
  `attach_340b` joins to org nodes by `norm_org_name` →
  `is_340b_covered_entity` + `contract_pharmacy_concentration` (0–1) → the
  **`contract_pharmacy`** scheme. Name-key match, so it's corroborative context.

### 2.4 SSA Death Master File — BUILT (`enforcement/death_master.py`)
- **Download:** public DMF (free, FOIA) or the NTIS Limited-Access DMF (<3 yr,
  certification required) at dmf.ntis.gov.
- **What it is:** deceased names + dates of birth/death.
- **Good for:** services billed under a deceased provider's identity.
- **How we use it — with the noise guardrail baked in:** `parse_dmf` +
  `match_deceased_providers` match on order-invariant name tokens; a match is
  ``high`` confidence ONLY when both sides carry a date of birth and it agrees,
  else ``low`` (name-only). `billing_after_death` feeds the `invalid_identity`
  scheme from HIGH-confidence matches only (`high_confidence_only=True`);
  name-only matches surface as a review flag, never a score driver — the
  defamation/explainability guardrail. The exact-keyed NPPES deactivation file
  (2.2) remains the primary "invalid identity" signal; this adds the deceased
  case where a DOB corroborates.

### 2.5 CMS Provider of Services (POS) file — BUILT (`ingest_cms/pos.py`)
- **Download:** data.cms.gov → "Provider of Services" (facility + clinical-lab
  files), CCN grain → `preclean/pos/pos_facility.csv`. Free.
- **What it is:** facility characteristics — beds, facility type, CLIA capacity.
- **Good for:** capacity-vs-billing reconciliation (billed volume that exceeds a
  facility's physical/licensed capacity = the "impossible org").
- **How we use it:** `compute_pos_capacity` → per-CCN beds/type/state;
  `capacity_billing_mismatch` divides a supplied per-CCN billed volume by beds
  and one-sided percentile-ranks within facility peers (size band × state) →
  `capacity_mismatch`, feeding `worthless_services`.

### 2.6 CMS Order & Referring file — BUILT (`ingest_cms/order_referring.py`)
- **Download:** data.cms.gov → "Order and Referring" (NPIs eligible to order/
  refer) → `preclean/order_referring/order_referring.csv`. Free, refreshed often.
- **What it is:** which NPIs may legally order/refer DME, Part B, HHA, PMD.
- **Good for:** catching DME/HHA claims whose ordering NPI is ineligible — a
  sharp kickback/phantom-order tell.
- **How we use it:** `eligible_referrers` → per-NPI eligibility flags;
  `ineligible_referral_share` flags the share of an org's referred dollars whose
  referrer isn't eligible for that order type → `ineligible_referral_share`,
  sharpening `dme_ring`.

### 2.7 CMS Revalidation / Reassignment file — BUILT (`entity_graph/build_edges.py`)
- **Download:** data.cms.gov → "Revalidation Clinic Group Practice Reassignment"
  → `preclean/reassignment/reassignment.csv`. Free.
- **What it is:** physician ↔ group-practice reassignment relationships.
- **Good for:** an affiliation-graph layer complementing PECOS ownership; dense
  reassignment churn is a roll-up/concealment signal (folds into A7 once monthly
  snapshots accumulate).
- **How we use it:** `build_reassignment_edges` emits provider→group
  `reassigns_to` edges (group resolved by NPI via `npi_to_org`, else
  `org:pac:<pac>`; unresolvable groups dropped, never guessed);
  `reassignment_features` gives per-group size. Wired into the graph build as an
  optional input (`tables["reassignment"]`) — the fixture build is unchanged
  when it's absent.

### 2.x Census county population + ZIP→county — BUILT (`ingest_cms/census_population.py`)
- **Download:** Census county population estimates + the HUD/Census ZIP→county
  crosswalk (both free) → `preclean/census/`.
- **What it is:** county populations and the ZIP→county bridge.
- **Good for:** the "volume vs local denominator" HALF of A5 clinical
  plausibility (the piece previously deferred) — an org billing more
  services/beneficiaries per capita than its county supports is the
  phantom-patient shape; also context for market saturation.
- **How we use it:** `county_population` / `zip_to_county` parse the files
  (string FIPS/ZIP); `analytics.plausibility.local_denominator_plausibility`
  computes per-capita rate vs county population → `local_volume_implausibility`,
  blended into `specialty_mismatch` (0.5 v3 / 0.3 clinical / 0.2 local). The
  org→county join awaits the ZIP crosswalk + address ZIPs on orgs (the parsers
  are built).

### 2.8 T-MSIS DQ Atlas — BUILT (`src/analytics/tmsis_quality.py`)
- **Download:** medicaid.gov DQ Atlas → per-state data-quality scores (public;
  NOT the gated claims). Free.
- **What it is:** CMS's own assessment of each state's Medicaid reporting
  quality.
- **Good for:** the data-confidence band (A2) — down-weight signals from states
  with poor reporting so we don't over-flag a data artifact.
- **How we use it:** `attach_state_quality` maps the org's state through the
  curated `STATE_DQ` tier table (refresh from the Atlas, like the OIG Work Plan
  table); `confidence_band` then downgrades — a high DQ-Atlas concern → LOW, a
  medium concern → MEDIUM, each with a named reason. Wired into the Model A run.

### 2.9 USAspending.gov API — BUILT (`src/feeds/usaspending.py`)
- **Download:** api.usaspending.gov/api/v2 (no key). Free; the client calls it
  live (POST), so it runs on Trey's box, not in CI.
- **What it is:** federal grants/contracts to organizations.
- **Good for:** a defendant size/solvency feature for Model C (a bigger
  going-concern defendant raises intervention odds) and the procurement graph.
- **How we use it:** `fetch_recipient_awards` / `org_federal_funding` sum each
  org's federal-award footprint by `norm_org_name`; `build_case_features` maps
  `federal_funding_total` (log-scaled, saturating at $50M) into Model C's
  `defendant_size`, a bounded `mult_defendant_size` (1.0–1.25×) on P(intervene)
  — neutral when there's no funding, so it never moves a defendant we know
  nothing about.

### 2.10 openFDA — BUILT (`feeds/openfda.py`, recall/enforcement scope)
- **Download:** open.fda.gov drug/device enforcement endpoints (free, real API).
- **What it is — honest scope:** the API serves ENFORCEMENT (recall) reports and
  adverse events; it does NOT cleanly serve warning letters / Form-483
  inspections (those are in the FDA dashboard's FOIA reading room). So the client
  pulls what the API actually provides — drug/device recalls.
- **Good for:** lab/pharma/device integrity corroboration.
- **How we use it:** `fetch_enforcement` pulls recall reports; `enforcement_events`
  matches recalling firms to our org nodes by `norm_org_name` → `fda_recall`
  events for the A8 timeline + dossier integrity context. Corroboration, not an
  accusation. (Warning-letter/483 ingestion stays a documented gap — needs the
  dashboard FOIA path, not the API.)

### 2.11 ProPublica Nonprofit Explorer API — BUILT (`src/feeds/propublica_nonprofits.py`)
- **Download:** projects.propublica.org/nonprofits/api (free, no key).
- **What it is:** IRS 990 data — nonprofit hospital exec comp + Schedule R
  related orgs.
- **Good for:** owner/related-party enrichment (doc 14 B8) without parsing raw
  EDGAR; a finance-persona hint for Model B.
- **How we use it:** `search_nonprofits` / `fetch_organization` pull 990s;
  `nonprofit_owner_edges` matches our org nodes to nonprofits by the shared
  `norm_org_name` (conservative exact key) → enrichment rows the entity graph
  turns into nonprofit-affiliation context. Public 990 data only.

---

## Part 3 — Cheap-license data (Brad decision; not enterprise-priced)

### 3.1 OpenSanctions — adapter BUILT (`enforcement/opensanctions.py`); license is Brad's call
- **What it is:** one normalized feed aggregating HHS-OIG LEIE + ~45 state
  Medicaid exclusion lists + SAM + global PEPs/sanctions (opensanctions.org).
- **Good for:** this *is* doc 14 B3 (state exclusions) pre-built, plus owner PEP
  screening.
- **How we use it:** `normalize_opensanctions` maps the OpenSanctions FtM entity
  export into the shared `exclusions` schema → more exclusion nodes in the graph;
  feeds `excluded_party_distance` / `ownership_integrity`. **The CODE is built;
  the DATA needs a (modest) commercial license for our use** — building the
  adapter doesn't require it, running it on the bulk feed does. Brad decision.

### 3.2 Definitive Healthcare alternatives (Provyx / AcuityMD)
- **What it is:** facility org-chart + affiliation data, pay-per-record (Provyx)
  or mid-market (AcuityMD) — far below DH/IQVIA enterprise pricing.
- **Good for:** Model B persona mapping (who holds target titles at a flagged
  org) when we get there.
- **How we'd use it:** an enrichment source feeding the (gated) person-resolver.

---

## Part 4 — How the new models work

### 4.1 Neo4j (graph database) — see Part 1
Storage + query, not a predictive model. Cypher pattern-matching finds rings the
relational pipeline already scores; Bloom/Browser is the visualization.

### 4.2 Splink — probabilistic entity resolution (BUILT, optional backend)
`src/entity_graph/probabilistic_resolver.py` — `resolve_probabilistic(records)`
clusters records into resolved entities. Splink is an optional dependency
(`requirements-trey.txt`); the deterministic resolver stays the default. Below is
how the model works.
- **What it is:** a free (MIT) probabilistic record-linkage library implementing
  the Fellegi-Sunter model on DuckDB — the database we already use.
- **How it works:** for each candidate record pair it estimates, per field
  (name, address, DOB, …), the probability the values agree GIVEN the pair is a
  true match vs. GIVEN it isn't (the `m` and `u` probabilities), learned
  **unsupervised** via expectation-maximization — no training labels. It
  combines the per-field weights into a match score, with term-frequency
  adjustments (a shared rare name counts more than a shared common one) and
  blocking rules to avoid comparing all N² pairs. Output: scored clusters of
  records that are the same real-world entity.
- **Why it matters here:** the manifesto calls entity resolution "the hard
  part." Our resolver today is exact-key + alias matching (deterministic, brittle
  to spelling). Splink is the upgrade path for (a) org dedup across CMS files and
  registries and (b) the gated person↔employer resolution Model B needs. It runs
  in-process on DuckDB; no new infrastructure.
- **How it's built here:** `resolve_probabilistic` reduces names with the shared
  `norm_org_name`, compares on the normalized name (exact / Jaro-Winkler), state,
  and optional address, and clusters pairs above a threshold. The Fellegi-Sunter
  weights ship as DOCUMENTED COLD-START values (exact name ≈0.99, near-name
  ≈0.85, distinct ≈0.02 at the default prior) — the same cold-start discipline as
  Model A's priors. On real data, `calibrate=True` refines them with Splink's
  unsupervised EM (EM doesn't converge meaningfully on tiny fixtures, so the
  cold-start weights are the tested default).

### 4.3 GLiNER — zero-shot entity & relation extraction (BUILT, `src/nlp/extract.py`)
- **What it is:** a small (≈200–400M param) BERT-family model that does
  named-entity recognition for ANY label you pass at inference time — no
  per-type training — and runs on CPU. GLiNER2 adds text classification and
  relation extraction in one model.
- **How it works:** you give it text plus a list of labels ("organization",
  "person", "job title", "fraud allegation"); it scores spans against those
  label embeddings in a single forward pass and returns the matching entities.
  Zero-shot = no labeled training set required.
- **Why it matters here:** extracting org/person/role entities from unstructured
  text — CourtListener docket case names, DOJ press releases, 990 PDFs, review
  text — and classifying grievance language. Serves both the planned
  grievance classifier and the doc 14 D2 creative-data extraction.
- **How it's built here:** `src/nlp/extract.py` — `extract_entities(texts,
  labels=…, model=…)` returns a tidy (text_id, label, text, score) frame.
  `load_gliner` lazy-loads the model (optional dep, downloads weights on first
  use — runtime only). The `model` arg is INJECTABLE (anything exposing
  `predict_entities`), so tests run against a fake and never download weights;
  `gliner` is in `requirements-trey.txt`. Feeds the case-DB parser, the docket
  monitor, and (counsel-cleared) review collectors.

### 4.4 The new Model-A schemes (BUILT)
`pill_mill`, `contract_pharmacy`, and `invalid_identity` are not ML models —
they are new entries in the explainable scheme registry
(`model_a/scheme_subscores.py`), each a weighted blend of the 0–1 features the
adapters above produce, squashed by the same sigmoid as every other scheme, and
combined into the org's noisy-OR risk with named drivers. They light up
automatically the day their source files are loaded — no retraining, no labels.

### 4.5 Embedding & clinical-coding models (context)
For the name-matching embeddings the planned `bge-small-en-v1.5` is fine; newer
small open options (Jina v5-small, Qwen3-Embedding) exist if more headroom is
wanted. Clinical ICD/CPT coders (OpenMed NER, MedGemma) are for the eventual
intake-document parsing — lower priority, since A5 derives clinical plausibility
directly from the data without them.

---

## Sequencing

1. **Neo4j** is built — stand up Community/Aura and run `--neo4j-bulk` once real
   graph data exists.
2. **Load the BUILT sweep adapters' files** as they're procured (opioid, NPPES
   deactivation, 340B) — the schemes fire automatically.
3. **T-MSIS DQ Atlas (2.8)** next — smallest, sharpens every confidence band.
4. **Splink (4.2)** when org-dedup or person-resolution accuracy becomes the
   bottleneck — the highest-leverage model upgrade.
5. OpenSanctions (3.1) when Brad clears a modest commercial license — retires the
   per-state exclusion scraping.
6. The remaining CONTRACT sources fill in smallest-first.
