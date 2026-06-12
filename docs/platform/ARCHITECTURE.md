# Architecture

The platform is eight layers. Each layer has a clear purpose, a primary output,
and — for credibility and to keep the build honest — an explicit mapping to what
exists in this repository today versus what is still a gap.

## The 8 layers

| Layer | Purpose | Primary output |
|---|---|---|
| Data acquisition | Collect public, OSINT, workforce, litigation, enforcement, marketing-behavior data | Normalized source tables + refresh schedule |
| Entity resolution | Link NPI / TIN / CCN / org / facility / owner / address / people / employment | Canonical healthcare entity graph |
| Model A | Score organizations by fraud-signal strength and exposure | Ranked organization dossiers (scheme + ERV) |
| Model B | Identify likely witnesses and persona audiences | Persona-ranked marketing audiences |
| Model C | Underwrite case viability, intervention likelihood, fundability | Counsel- and finance-qualified case score |
| Marketing engine | Build trust, acquire leads, convert | Content, ads, landing pages, nurture |
| Analytics | Instrument behavior and route leads | Event stream, lead score, audiences |
| Counsel workflow | Preserve privilege, verify evidence, avoid solicitation/privacy problems | Intake checklist + case handoff packet |

The chain: **A ranks orgs → B ranks people and defines audiences → the pipeline
reaches/nurtures/converts → intake produces a claim → C underwrites which to
finance.** Outcomes flow back: resolved cases retrain A and C, conversions retrain
B. Everything starts as an explainable heuristic that works on day one and earns
its ML upgrade only once outcomes exist.

## Repo mapping (today vs. gap)

### Data acquisition — Partial
- **Today:** `src/attempt_2/clean_data.py` + `ingest/integrate.py` ingest CMS
  Medicaid Spending, NPPES, PECOS, OIG LEIE, and CMS All-Owners into assertion-
  checked Parquet (`provider_dim`, `npi_xwalk`, `spending_fact`, `owner_edges`,
  `exclusions`). `audit/audit_corruption.py` quarantines the fake $20T+ rows.
- **Gap:** Part D, DMEPOS, Open Payments, Market Saturation, structured DOJ/OIG
  case DB, WARN, people-data vendors, PACER. See [02-data-sources.md](02-data-sources.md).

### Entity resolution — Partial (Increment 1 built)
- **Today:** `src/entity_graph/` builds canonical Provider / Organization / Owner /
  Exclusion nodes and temporal `member_of` / `owned_by` / `excluded_in` /
  `co_located_with` edges; the deterministic resolver (PAC → shared-owner →
  exact-name) generalizes `leads/company_rollup.py`; `graph_features.py` computes
  excluded-party distance, related-party density, shell score, community and
  centrality; `ring_detection.py` finds shells, common-owner clusters, and
  excluded-party proximity. Tested in `tests/test_entity_graph.py`.
- **Gap:** probabilistic person↔employer resolution (`person_resolver.py` stub) and
  optional Neo4j export (`neo4j_export.py` stub). See [03-entity-resolution.md](03-entity-resolution.md).

### Model A — Partial
- **Today:** `src/attempt_2/ingest/features.py` (peer-relative robust-z features),
  `leads/detect.py` (3-layer prioritization), `leads/refine_layer2_v3.py`
  (de-correlated concept anomaly), `leads/company_lead_tracker.py` (company-grain
  scoring), and `src/backtest/` (LEIE temporal validation, 2.0× top-decile lift).
- **Gap:** scheme subscores, noisy-OR composite, sector prior, ERV ranking, PU
  supervised graduation, and graph-feature integration — scaffolded in
  `src/model_a/`. See [04-model-a.md](04-model-a.md).

### Model B — Not started
- **Today:** scaffold `src/model_b/` with the scheme-to-role line-of-sight matrix
  populated (`scheme_role_matrix.py`).
- **Gap:** everything else (knowledge gate, propensity, reachability, audiences) +
  the people-data ingestion it depends on. See [05-model-b.md](05-model-b.md).

### Model C — Not started
- **Today:** scaffold `src/model_c/`.
- **Gap:** features, calibrated intervention classifier, quantile recovery model,
  portfolio Monte Carlo + the PACER/DOJ label assembly. See [06-model-c.md](06-model-c.md).

### Marketing engine / Analytics / Counsel workflow — Not started
- **Today:** specified in [07-sourcing-and-marketing.md](07-sourcing-and-marketing.md)
  and [01-legal-compliance.md](01-legal-compliance.md); public lookup tool scaffolded
  in `src/lookup_tool/`.
- **Gap:** content hub, landing pages, CRM/nurture, event instrumentation, and the
  privileged intake workflow.

## Data + storage conventions (inherited from the repo)

- Identifiers are strings with leading zeros preserved; all-VARCHAR Parquet on ingest.
- Every join asserts row-count and dollar conservation — builds hard-fail on fan-out.
- Quarantine, never delete. Explainability is mandatory: store drivers, not bare scores.
- Raw/derived data is gitignored (HIPAA); only code, docs, and reports are committed.
- The graph is relational/columnar (Parquet + NetworkX), not a required graph DB.
