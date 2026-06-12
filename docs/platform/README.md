# Healthcare Fraud Whistleblower Origination — Platform Docs

This directory is the single source of truth for building the platform described in
the strategy master document (`docs/source-pdfs/Healthcare_Fraud_Whistleblower_Strategy_Master_CLIENT_READY.pdf`,
on branch `docs/source-pdfs`). It maps that strategy onto this repository, sequences
the build, and specifies each component.

## Thesis

The defensible business is not a whistleblower advertising site — it is an
intelligence-and-acquisition engine. It starts from healthcare-payment **anomaly
detection** (Model A) to rank organizations by fraud-risk and recoverable exposure,
translates each anomaly into the **people** who plausibly witnessed it (Model B),
runs trust-first, education-led outreach to convert them into qui tam relators, and
**underwrites** the resulting False Claims Act cases for litigation finance (Model
C). The moat is the compounding outcome-label set on a shared healthcare
entity-resolution graph, all under a strict legal/compliance frame.

## How to read these docs

| Doc | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | The 8-layer architecture and what each layer maps to in this repo today |
| [ROADMAP.md](ROADMAP.md) | Phased build sequence; what is done, in progress, and next |
| [01-legal-compliance.md](01-legal-compliance.md) | FCA economics, the four gates, Zafirov, and the guardrails everything else must honor |
| [02-data-sources.md](02-data-sources.md) | Prioritized data-source catalog; ingested today vs. needed next |
| [03-entity-resolution.md](03-entity-resolution.md) | The canonical entity graph (the foundation; **Increment 1, now built**) |
| [04-model-a.md](04-model-a.md) | Organization fraud-risk and exposure |
| [05-model-b.md](05-model-b.md) | Whistleblower identification and propensity |
| [06-model-c.md](06-model-c.md) | Case-selection and intervention-likelihood (underwriting) |
| [07-sourcing-and-marketing.md](07-sourcing-and-marketing.md) | Relator-sourcing engine, personas, funnel, intake |
| [08-litigation-finance.md](08-litigation-finance.md) | Unit economics and fund construction |
| [09-data-procurement.md](09-data-procurement.md) | Exactly what data to acquire, where, and what each file powers |
| [10-workflows.md](10-workflows.md) | Operational workflows end-to-end + the model rationale |
| [11-icp-selection.md](11-icp-selection.md) | The first two typologies (ICPs) and why |
| [GAPS.md](GAPS.md) | The honest punch list of everything still missing |

## Component status (current repo state)

| Component | Status | Where |
|---|---|---|
| Data acquisition | **Partial** | CMS Spending / NPPES / PECOS / LEIE / All-Owners ingested (`src/attempt_2/ingest/integrate.py`); Part D, DMEPOS, Open Payments, structured DOJ/OIG, people-data, PACER not yet |
| Entity resolution | **Partial** | Canonical graph + deterministic resolver + graph features **built** (`src/entity_graph/`); probabilistic person↔employer resolution is a stub |
| Model A (org fraud-risk) | **Partial → v1 built** | 3-layer detection + company rollup + LEIE backtest (`src/attempt_2/leads/`, `src/backtest/`); **v1 ERV composite built**: scheme subscores → noisy-OR → sector prior × graph boost → ERV + target dossiers (`src/model_a/`, tested); supervised graduation still scaffold |
| Model B (whistleblower) | **Not started** | Scaffold + scheme-to-role matrix (`src/model_b/`) |
| Model C (underwriting) | **Not started** | Scaffold (`src/model_c/`) |
| Public lookup tool | **Not started** | Scaffold (`src/lookup_tool/`) |
| Marketing / sourcing | **Not started** | Spec only (this directory) |
| Analytics / instrumentation | **Not started** | Spec only |
| Counsel workflow / intake | **Not started** | Spec only |

## Increment 1 (this pass): the entity-resolution graph

The hardest, most load-bearing piece is built and tested:
`python -m src.entity_graph --fixture --out /tmp/graph_out` and
`pytest tests/test_entity_graph.py`. See [03-entity-resolution.md](03-entity-resolution.md).
