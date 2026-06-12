# Orchestration entry points. `make help` lists everything.
# Real-data paths follow the repo convention (~/Desktop/data); override with
# DATA_ROOT=/path make <target>.

DATA_ROOT ?= $(HOME)/Desktop/data
PY        ?= python3

.PHONY: help install test demo graph model-a pipeline warn ci-local

help:
	@echo "Targets:"
	@echo "  install    pip install -r requirements.txt"
	@echo "  test       run the full pytest suite (synthetic; no real data needed)"
	@echo "  demo       end-to-end on the synthetic fixture -> /tmp/demo (start here)"
	@echo "  pipeline   run the 13-stage detection pipeline on real data (DATA_ROOT=$(DATA_ROOT))"
	@echo "  graph      build the entity graph from real processed data"
	@echo "  model-a    score orgs + render dossiers from real graph/features/spending"
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
		--out $(DATA_ROOT)/model_a

warn:
	$(PY) -m src.sourcing.warn_monitor --warn $(WARN_CSV) \
		--graph-dir $(DATA_ROOT)/graph \
		--erv $(DATA_ROOT)/model_a/erv_ranked.parquet \
		--out $(DATA_ROOT)/sourcing

ci-local: test
	$(PY) -m src.entity_graph --fixture --out /tmp/graph_ci
	$(PY) -m src.model_a --fixture --out /tmp/model_a_ci --top-k 1
	@test -f /tmp/model_a_ci/MODEL_A_REPORT.md && echo "fixture e2e OK"
