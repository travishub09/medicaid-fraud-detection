# Roadmap

The build sequence reconciles the master document's "90-Day Build Plan" and "Build
Sequence" with the repo's actual state. Phases 0–6 mirror the strategy's priority
operating model; each lists entry state, deliverables, reused modules, and exit
criteria. The ordering principle from the strategy holds: **everything starts
heuristic and label-free and works on day one; outcomes earn the ML upgrades.**

## Phase 0 — Legal & compliance design (gating)
- **Entry:** none. This blocks fatal risk before any lead generation.
- **Deliverables:** intake disclaimers, privilege workflow, funder-control policy,
  document-handling rules, state-solicitation review, prohibited-claims list.
- **Exit:** an approved compliance playbook ([01-legal-compliance.md](01-legal-compliance.md)).
- **Status:** specified here; legal sign-off is an external action.

## Phase 1 — Data foundation + entity graph
- **Entry:** raw CMS/OIG sources.
- **Deliverables:** assertion-checked source tables **(done — `integrate.py`)** and
  the canonical entity graph **(done — Increment 1, `src/entity_graph/`)**.
- **Reused:** `integrate.py`, `company_rollup.py` (generalized by the resolver).
- **Exit:** graph build runs and is tested. **Met** (`pytest tests/test_entity_graph.py`).
- **Next in this phase:** ingest Part D / DMEPOS / Open Payments; add probabilistic
  person↔employer resolution once people-data is licensed.

## Phase 2 — Typology models (Model A productionization) — *immediate fast-follow*
- **Entry:** entity graph + feature base.
- **Deliverables:** scheme subscores → noisy-OR composite → sector prior × graph
  boost → ERV ranking; fold the new graph features into the feature store; the
  temporal-holdout validation harness (generalize `src/backtest/`).
- **Reused:** `features.py`, `detect.py`, `refine_layer2_v3.py`,
  `company_lead_tracker.py`, `src/backtest/`; scaffold in `src/model_a/`.
- **Exit:** ranked org dossiers with scheme hypothesis, ERV, and named drivers;
  precision@k reported on a post-cut enforcement holdout.

## Phase 3 — Witness graph (Model B)
- **Entry:** Model A org ranking + flagged-org scheme hypotheses; licensed people-data.
- **Deliverables:** person↔employer resolution + temporal `employed_by` edges;
  B1 knowledge (with the tenure-overlap hard gate), B2 propensity, reachability;
  audiences keyed by role×org×channel.
- **Reused:** `scheme_role_matrix.py`; `entity_graph/person_resolver.py` (built out).
- **Exit:** de-identified marketing audiences with message angles — not call lists.

## Phase 4 — Campaign activation
- **Entry:** audiences + the public lookup tool as the SEO front door.
- **Deliverables:** persona landing pages, lead magnets, search/LinkedIn/retargeting
  campaigns, attorney-referral outreach.
- **Reused:** `src/lookup_tool/` (built out behind Model A percentiles).
- **Exit:** initial lead flow and a baseline CAC.

## Phase 5 — Nurture & qualification
- **Entry:** lead flow.
- **Deliverables:** CRM + secure intake, content sequences, counsel-mediated
  qualification (materiality, originality, lawful access, damages).
- **Exit:** counsel-qualified and evidence-qualified leads; model feedback.

## Phase 6 — Case diligence & financing (Model C)
- **Entry:** qualified leads + accumulating outcome labels.
- **Deliverables:** case features, calibrated intervention model + quantile recovery
  model, portfolio Monte Carlo, investment memos; the litigation-finance product.
- **Reused:** scaffold in `src/model_c/`.
- **Exit:** fund/pass/fund-with-terms decisions; selection-bias controls in place.

## Sequencing notes
- Phases 1–2 are where this repo already has the most equity; finishing Model A's
  ERV ranking is the highest-leverage next coding increment.
- Phase 3 is gated on a data-licensing + compliance decision (people-data + FCRA),
  not just engineering.
- Phases 4–6 are progressively more product/ops/legal than modeling.
