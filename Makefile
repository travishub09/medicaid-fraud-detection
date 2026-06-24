# Orchestration entry points. `make help` lists everything.
# Real-data paths follow the repo convention (~/Desktop/data); override with
# DATA_ROOT=/path make <target>.

DATA_ROOT ?= $(HOME)/Desktop/data
PY        ?= python3

.PHONY: help install test demo graph model-a provider-features ccn-crosswalk opensanctions pipeline warn ci-local

help:
	@echo "Targets:"
	@echo "  install    pip install -r requirements.txt"
	@echo "  test       run the full pytest suite (synthetic; no real data needed)"
	@echo "  demo       end-to-end on the synthetic fixture -> /tmp/demo (start here)"
	@echo "  pipeline   run the 13-stage detection pipeline on real data (DATA_ROOT=$(DATA_ROOT))"
	@echo "  graph      build the entity graph from real processed data"
	@echo "  model-a    score orgs + render dossiers from real graph/features/spending"
	@echo "  provider-features  rebuild graph, then export the per-NPI training matrix for Travis"
	@echo "  ccn-crosswalk      build processed/ccn_to_npi.parquet (PECOS_FILE=path)"
	@echo "  opensanctions      normalize OpenSanctions bulk -> processed/exclusions_opensanctions.parquet (OPENSANCTIONS_FILE=path)"
	@echo "  warn       WARN surge monitor (set WARN_CSV=path)"
	@echo "  ci-local   what CI runs: tests + fixture end-to-end + doc-link check"

install:
	pip install -r requirements.txt

test:
	$(PY) -m pytest tests/ -v

demo:
	$(PY) -m src.model_a --fixture --out /tmp/demo --top-k 3
	@echo "\n--- open these ---"
	@echo "/tmp/demo/MODEL_A_REPORT.md"
	@ls /tmp/demo/dossiers/ | head -3 | sed 's|^|/tmp/demo/dossiers/|'

pipeline:
	$(PY) -m src.attempt_2.ingest.integrate
	$(PY) -m src.attempt_2.audit.diagnose_coverage
	$(PY) -m src.attempt_2.audit.audit_corruption
	$(PY) -m src.attempt_2.ingest.features
	$(PY) -m src.attempt_2.leads.detect
	$(PY) -m src.attempt_2.leads.verify_layer1
	$(PY) -m src.attempt_2.leads.refine_layer2
	$(PY) -m src.attempt_2.leads.refine_layer2_v3
	$(PY) -m src.attempt_2.leads.company_rollup
	$(PY) -m src.attempt_2.leads.company_lead_tracker --min-net-paid 10000000
	$(PY) -m src.attempt_2.leads.finalize_tracker
	$(PY) -m src.attempt_2.export.export_final_leads --min-net-paid 10000000

graph:
	$(PY) -m src.entity_graph --input $(DATA_ROOT)/processed --out $(DATA_ROOT)/graph

model-a:
	$(PY) -m src.model_a --graph-dir $(DATA_ROOT)/graph \
		--features $(DATA_ROOT)/detection/company_features.parquet \
		--spending $(DATA_ROOT)/processed/spending_fact.parquet \
		--provider-dim $(DATA_ROOT)/processed/provider_dim.parquet \
		--out $(DATA_ROOT)/model_a

# Per-NPI feature export for Travis's supervised model. Depends on `graph` so the
# entity graph is ALWAYS rebuilt first — the fix for the stale-ownership sequencing
# trap (the ownership_integrity signal is computed per-org and inherited by NPI).
provider-features: graph
	$(PY) -m src.model_a.provider_features_export \
		--graph-dir $(DATA_ROOT)/graph \
		--leads $(DATA_ROOT)/detection/fraud_leads_v3.parquet \
		--preclean $(DATA_ROOT)/preclean \
		--processed $(DATA_ROOT)/processed \
		--out $(DATA_ROOT)/model_a/provider_features $(PF_FLAGS)

# One-time PECOS CCN↔NPI crosswalk (unlocks facility/HCRIS/POS schemes).
ccn-crosswalk:
	$(PY) -m src.ingest_cms.ccn_npi_crosswalk --in $(PECOS_FILE) \
		--out $(DATA_ROOT)/processed/ccn_to_npi.parquet

# OpenSanctions bulk (LEIE + ~45 state exclusion lists + SAM) -> exclusion nodes.
# The graph merges any processed/exclusions_*.parquet on the next build.
opensanctions:
	$(PY) -m src.enforcement.opensanctions --in $(OPENSANCTIONS_FILE) \
		--out $(DATA_ROOT)/processed/exclusions_opensanctions.parquet

model-c:
	$(PY) -m src.model_c --erv $(DATA_ROOT)/model_a/erv_ranked.parquet \
		--out $(DATA_ROOT)/model_c

neo4j-bulk:
	$(PY) -m src.entity_graph --input $(DATA_ROOT)/processed \
		--out $(DATA_ROOT)/graph --neo4j-bulk $(DATA_ROOT)/neo4j
	@echo "Run $(DATA_ROOT)/neo4j/import.sh against a stopped Neo4j database"

warn:
	$(PY) -m src.sourcing.warn_monitor --warn $(WARN_CSV) \
		--graph-dir $(DATA_ROOT)/graph \
		--erv $(DATA_ROOT)/model_a/erv_ranked.parquet \
		--out $(DATA_ROOT)/sourcing

ci-local: test
	$(PY) -m src.entity_graph --fixture --out /tmp/graph_ci
	$(PY) -m src.model_a --fixture --out /tmp/model_a_ci --top-k 1
	@test -f /tmp/model_a_ci/MODEL_A_REPORT.md && echo "fixture e2e OK"
	$(PY) -m src.model_c --fixture --out /tmp/model_c_ci
	@test -f /tmp/model_c_ci/MODEL_C_REPORT.md && echo "model-c fixture e2e OK"

feeds-backfill:
	$(PY) -m src.enforcement.fetch --backfill-years 10
	$(PY) -m src.sourcing.docket_monitor --graph-dir $(DATA_ROOT)/graph \
		--erv $(DATA_ROOT)/model_a/erv_ranked.parquet --since 2016-01-01

feeds-refresh:
	$(PY) -m src.enforcement.fetch
	$(PY) -m src.sourcing.docket_monitor --graph-dir $(DATA_ROOT)/graph \
		--erv $(DATA_ROOT)/model_a/erv_ranked.parquet
	$(PY) -m src.enforcement.sam_api
	$(PY) -m src.feeds.freshness
