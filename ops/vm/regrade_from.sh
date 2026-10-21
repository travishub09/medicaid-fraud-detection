#!/usr/bin/env bash
# regrade_from.sh — grade an EXISTING matrix under a new list definition, as a new tag.
#
#   bash ops/vm/regrade_from.sh v14 v13 2021-12
#
# The matrix, manifest, forward labels and OOF scores of frozen_<cut>_<src> are
# copied into frozen_<cut>_<new> (nothing is retrained: the scorer, the ruler
# and the label are identical), then steps 6 to 12 of run_frozen_night.sh run on
# the copy: priority list + pursuit book + large book, the four diagnostics, the
# digest + operator-book dossier, the registry (against the <new> pre-
# registration), the learning loop and the ledger. Use it when the CODE that
# defines a BOOK changed (v13 -> v14: the national-name list left the graded
# book) and a retrain would only re-derive the same scores. A re-grade is a NEW
# pre-registration: it is never written back over the source run.
set -euo pipefail
NEW="${1:?new tag, e.g. v14}"
SRC="${2:?source tag whose matrix is reused, e.g. v13}"
CUT="${3:?freeze month, e.g. 2021-12}"

DATA="${MEDICAID_DATA_ROOT:-/data/medicaid}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="$DATA/model_a/frozen_${CUT}_${NEW}"
SRC_RUN="$DATA/model_a/frozen_${CUT}_${SRC}"
PREV="$SRC_RUN"
GRAPH_ASOF="$DATA/graph_asof_${CUT}_full"
EXCL="$GRAPH_ASOF/nodes/exclusion_nodes.parquet"
# the merged label inputs (run_frozen_night.sh step 0 writes them; REBUILD_RULERS=1 below
# rebuilds them here too, then the three rulers, so a corrected case file re-grades a run)
CASES_BASE="$DATA/enforcement/doj_cases_v3.csv"
DEFENDANTS_BASE="$DATA/processed/case_defendant_npis.parquet"
CIA_CASES="$DATA/enforcement/cia_cases.csv"
CIA_NPIS="$DATA/processed/cia_defendant_npis.parquet"
AFFILIATED="$DATA/processed/affiliated_convictions.parquet"
CASES="$DATA/enforcement/doj_cases_v3_plus_cia.csv"
DEFENDANTS="$DATA/processed/case_defendant_npis_plus_cia.parquet"
REBUILD_RULERS="${REBUILD_RULERS:-}"
TRAIN_TIER="${TRAIN_TIER:-hard}"
PURSUIT_ORDER="${PURSUIT_ORDER:-dollar}"
export MEDICAID_DUCKDB_TMP="${MEDICAID_DUCKDB_TMP:-$DATA/interim/duckdb_tmp}"
export MEDICAID_DUCKDB_MEM="${MEDICAID_DUCKDB_MEM:-$(awk '/MemTotal/ {printf "%dGB", int($2/1024/1024*0.4)}' /proc/meminfo)}"

[ -f "$SRC_RUN/provider_features_for_model.parquet" ] || { echo "[$NEW] MISSING $SRC_RUN/provider_features_for_model.parquet"; exit 1; }
[ -f "$EXCL" ] || { echo "[$NEW] MISSING $EXCL"; exit 1; }
if [ -e "$RUN" ] && [ -z "${OVERWRITE:-}" ]; then echo "[$NEW] $RUN exists; set OVERWRITE=1 to reuse it"; exit 1; fi
mkdir -p "$RUN" "$MEDICAID_DUCKDB_TMP"
cd "$REPO"
# shellcheck disable=SC1091
[ -f "$REPO/.venv/bin/activate" ] && source "$REPO/.venv/bin/activate"
LOG="$RUN/night_run.log"
exec > >(tee -a "$LOG") 2>&1

step() { echo; echo "[$NEW] step $1: $2   ($(date '+%F %T'))"; }
fail() { echo; echo "[$NEW] FAILED at step $1  ($(date '+%F %T'))  -- paste the last 40 lines of $LOG to Claude"; exit 1; }

echo "[$NEW] started $(date '+%F %T')  (re-grade of $SRC's matrix under $(git -C "$REPO" rev-parse --short HEAD); order=$PURSUIT_ORDER tier=$TRAIN_TIER)"
step 0/7 "copy the matrix, manifest, labels and OOF scores from $SRC_RUN (no retrain)"
for f in provider_features_for_model.parquet feature_manifest.json provider_oof_scores.parquet \
         future_bans_after_${CUT}.csv future_bans_after_${CUT}.TIERS.md future_bans_after_${CUT}__fca${HORIZON}.csv \
         future_bans_after_${CUT}__fca${HORIZON}.TIERS.md PROVIDER_FEATURES_EXPORT_REPORT.md PROVIDER_FEATURES_DICTIONARY.md \
         SOURCES_REPORT.md EXPECTATIONS_REPORT.md CASE_VALIDATION.md SCHEME_HEALTH.md scheme_health.json \
         SCHEME_TRIAGE.md NETWORK_AB_REPORT.md; do
  [ -e "$SRC_RUN/$f" ] && cp -a "$SRC_RUN/$f" "$RUN/$f"
done
ls -la "$RUN" | head -30

if [ -n "$REBUILD_RULERS" ]; then
  step 0b/7 "merged label inputs + the forward rulers REBUILT (REBUILD_RULERS=1: the case file changed since $SRC)"
  python -u -m src.model_a.label_expansion merge-inputs --case-db "$CASES_BASE" --cia-cases "$CIA_CASES" \
      --defendant-npis "$DEFENDANTS_BASE" --cia-npis "$CIA_NPIS" --out-cases "$CASES" --out-npis "$DEFENDANTS" || fail 0
  python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
      --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --horizon-months "$HORIZON" \
      --out "$RUN/future_bans_after_${CUT}__fca${HORIZON}.csv" || fail 0
  if [ -f "$AFFILIATED" ]; then
    python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
        --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --horizon-months "$HORIZON" --affiliated "$AFFILIATED" \
        --out "$RUN/future_bans_after_${CUT}__aff${HORIZON}.csv" || fail 0
  fi
  python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
      --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --horizon-months "$HORIZON" --fca-case-only --qui-tam-only \
      --out "$RUN/future_bans_after_${CUT}__quitam${HORIZON}.csv" || fail 0
else
  [ -f "$CASES" ] || CASES="$CASES_BASE"
  [ -f "$DEFENDANTS" ] || DEFENDANTS="$DEFENDANTS_BASE"
fi

step 6/12 "priority list + pursuit book + large-biller book (the restored book definition)"
python -u -m src.model_a.pursuit_dossier --run-dir "$RUN" --priority-list --order "$PURSUIT_ORDER" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" || fail 6

step 7/12 "accuracy diagnostics on the PURSUIT book"
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --repeats 3 --qui-tam-ruler --list-name provider_priority_list_pursuit.csv || fail 7

step 8/12 "the large-biller book, and the broadcast A/B"
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --list-name provider_priority_list_large.csv || fail 8
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --list-name provider_priority_list_pmax.csv || fail 8    # the v17 sweep's winning order, graded as a third book (2026-10-20)
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --score-source refit --broadcast-rows weight --out "$RUN/ab_broadcast_weight/ACCURACY_DIAGNOSTICS.md" || fail 8
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --score-source refit --broadcast-rows mask --out "$RUN/ab_broadcast_mask/ACCURACY_DIAGNOSTICS.md" || fail 8

step 9/12 "results digest + pursuit dossier (operator book, national chains set aside; no Manus dispatch here)"
python -u -m src.model_a.results_digest --dir "$RUN" --out "$RUN/RESULTS_DIGEST.md" || fail 9
python -u -m src.model_a.pursuit_dossier --run-dir "$RUN" --operator-book --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" || fail 9

step 10/12 "register the run against the locked pre-registration"
python -u -m src.model_a.run_registry --data-root "$DATA" --run-dir "$RUN" || fail 10

step 11/13 "learning loop"
python -u -m src.model_a.learning_loop --run-dir "$RUN" --data-root "$DATA" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" --prev-run-dir "$PREV" || fail 11

step 12/13 "measurement (scheme AUC on the FCA ruler, signal ranking, label enrichment, ablations; MEASURE_ABLATION=0 skips the retrains)"
MEASURE_ABLATION="${MEASURE_ABLATION:-1}" bash "$REPO/ops/vm/measure_run.sh" "$NEW" "$CUT" || fail 12

step 13/13 "the run issue ledger"
python -u -m src.model_a.run_issues --run-dir "$RUN" --data-root "$DATA" --prev-run-dir "$PREV" || fail 13

echo
echo "[$NEW] DONE $(date '+%F %T')  -- tell Claude '$NEW finished'; then the packet:"
echo "  (cd $RUN && tar -czf /home/ubuntu/run_packet_${NEW}.tar.gz *.md *.json ab_broadcast_weight ab_broadcast_mask)   # tar; zip is not installed"
echo "  bash $REPO/ops/vm/sync_data.sh up frozen_${CUT}_${NEW}"
