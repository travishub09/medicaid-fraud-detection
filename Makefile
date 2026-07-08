# Orchestration entry points. `make help` lists everything.
# Real-data paths follow the repo convention (~/Desktop/data); override with
# DATA_ROOT=/path make <target>.

DATA_ROOT ?= $(HOME)/Desktop/data
PY        ?= python3

.PHONY: help install test demo graph model-a provider-features feature-snapshot frozen-package ccn-crosswalk opensanctions owner-snapshot pipeline warn ci-local

# Feature-freeze cutoff for the frozen-package (Travis's forward network test).
ASOF_CUTOFF ?= 2023-12

help:
	@echo "Targets:"
	@echo "  install    pip install -r requirements.txt"
	@echo "  test       run the full pytest suite (synthetic; no real data needed)"
	@echo "  demo       end-to-end on the synthetic fixture -> /tmp/demo (start here)"
	@echo "  pipeline   run the 13-stage detection pipeline on real data (DATA_ROOT=$(DATA_ROOT))"
	@echo "  graph      build the entity graph from real processed data"
	@echo "  model-a    score orgs + render dossiers from real graph/features/spending"
	@echo "  provider-features  rebuild graph, then export the per-NPI training matrix for Travis"
	@echo "  feature-snapshot   archive a valid-time snapshot of the matrix (run on a cadence; point-in-time store)"
	@echo "  frozen-package     leakage-correct as-of matrix + forward-ban label for Travis's network test (ASOF_CUTOFF=$(ASOF_CUTOFF))"
	@echo "  ccn-crosswalk      build processed/ccn_to_npi.parquet (PECOS_FILE=path)"
	@echo "  opensanctions      normalize OpenSanctions bulk -> processed/exclusions_opensanctions.parquet (OPENSANCTIONS_FILE=path)"
	@echo "  owner-snapshot     archive this month's owner edges (run monthly; unlocks ownership_turnover at 2+)"
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

# Archive a valid-time snapshot of the per-NPI matrix so point-in-time history
# accumulates for as-of (out-of-time) training. Run on a cadence (e.g. monthly).
feature-snapshot:
	$(PY) -m src.model_a.feature_store \
		--matrix $(DATA_ROOT)/model_a/provider_features/provider_features_for_model.parquet \
		--store-dir $(DATA_ROOT)/feature_snapshots --asof $(ASOF)

# The frozen package for Travis's A/B network test. Three ordered steps, all
# leakage-correct as-of ASOF_CUTOFF:
#   1. point-in-time graph — embeddings/proximity/rings built ONLY from exclusions
#      and owner edges known before the cutoff (no future bans leaking into features);
#   2. the per-NPI matrix with the billing fact filtered to pre-cutoff service months
#      (--asof-cutoff) so every billing feature is as-of-correct too;
#   3. the forward label — NPIs whose FIRST exclusion lands on/after the cutoff.
# Travis then scores the step-2 matrix against the step-3 label, WITH network
# columns included, to see whether the graph family predicts future bans it never saw.
frozen-package:
	$(PY) -m src.entity_graph --input $(DATA_ROOT)/processed \
		--out $(DATA_ROOT)/graph_asof_$(ASOF_CUTOFF) --asof $(ASOF_CUTOFF)
	$(PY) -m src.model_a.provider_features_export \
		--graph-dir $(DATA_ROOT)/graph_asof_$(ASOF_CUTOFF) \
		--asof-cutoff $(ASOF_CUTOFF) \
		--leads $(DATA_ROOT)/detection/fraud_leads_v3.parquet \
		--preclean $(DATA_ROOT)/preclean --processed $(DATA_ROOT)/processed \
		--out $(DATA_ROOT)/model_a/frozen_$(ASOF_CUTOFF)
	$(PY) -m src.model_a.prospective_label \
		--exclusion-nodes $(DATA_ROOT)/graph/nodes/exclusion_nodes.parquet \
		--cutoff $(ASOF_CUTOFF) \
		--out $(DATA_ROOT)/model_a/frozen_$(ASOF_CUTOFF)/future_bans_after_$(ASOF_CUTOFF).csv
	$(PY) -m src.model_a.network_ab \
		--matrix $(DATA_ROOT)/model_a/frozen_$(ASOF_CUTOFF)/provider_features_for_model.parquet \
		--manifest $(DATA_ROOT)/model_a/frozen_$(ASOF_CUTOFF)/feature_manifest.json \
		--future-label $(DATA_ROOT)/model_a/frozen_$(ASOF_CUTOFF)/future_bans_after_$(ASOF_CUTOFF).csv \
		--out $(DATA_ROOT)/model_a/frozen_$(ASOF_CUTOFF)/NETWORK_AB_REPORT.md
	@echo "Frozen package ready in $(DATA_ROOT)/model_a/frozen_$(ASOF_CUTOFF)/"
	@echo "  provider_features_for_model.parquet  = as-of features (network cols INCLUDED)"
	@echo "  future_bans_after_$(ASOF_CUTOFF).csv  = the forward label to score against"
	@echo "  NETWORK_AB_REPORT.md                  = size-matched network A/B verdict"

# One-time PECOS CCN↔NPI crosswalk (unlocks facility/HCRIS/POS schemes).
ccn-crosswalk:
	$(PY) -m src.ingest_cms.ccn_npi_crosswalk --in $(PECOS_FILE) \
		--out $(DATA_ROOT)/processed/ccn_to_npi.parquet

# OpenSanctions bulk (LEIE + ~45 state exclusion lists + SAM) -> exclusion nodes.
# The graph merges any processed/exclusions_*.parquet on the next build.
opensanctions:
	$(PY) -m src.enforcement.opensanctions --in $(OPENSANCTIONS_FILE) \
		--out $(DATA_ROOT)/processed/exclusions_opensanctions.parquet

# Archive this month's owner edges. Run monthly (after `make graph`); once two
# snapshots accumulate, ownership_turnover lights up in the provider export.
owner-snapshot:
	$(PY) -m src.entity_graph.ownership_snapshot \
		--owned-by $(DATA_ROOT)/graph/edges/owned_by_edges.parquet \
		--snapshots-dir $(DATA_ROOT)/owner_snapshots

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
