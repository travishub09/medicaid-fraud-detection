#!/usr/bin/env bash
# ops/vm/run_frozen_night.sh  --  the Linux twin of run_v11_night.bat for the VM.
#
#   bash ops/vm/run_frozen_night.sh v12 2021-12            # tag v12 on the 2021-12 freeze
#   bash ops/vm/run_frozen_night.sh v12 2021-12 --rebuild-graph   # force the as-of graph rebuild
#
# What differs from the laptop runner (2026-10-11):
#   * FULL graph embeddings: --max-embed-nodes 0 --light-walks-above 0 (no node cap and
#     full-density walks, 10 of 20 with window 5; the laptop embeds 1.5M of the
#     13.6M connected nodes and zero-fills the rest, which is why the motif columns came
#     out constant). The as-of graph is REBUILT here the first time, into
#     graph_asof_<cut>_full, so the capped laptop graph is never reused by mistake.
#   * The free-memory gate wants 24 GB (a 64 GB box with nothing else running clears it).
#   * Every step stops the chain and names itself; nothing dispatches to Manus.
# PRE-REGISTER FIRST (the registry refuses a run whose tag has no locked goal): the
# command is in the operator reminders (python -m src.reminders) and docs/VM_RUNBOOK.md.
set -uo pipefail
TAG="${1:?tag, e.g. v12}"
CUT="${2:?freeze month, e.g. 2021-12}"
REBUILD="${3:-}"
DATA="${MEDICAID_DATA_ROOT:-/data/medicaid}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="$DATA/model_a/frozen_${CUT}_${TAG}"
PREV="${PREV_RUN:-}"
SEEDS="${SEEDS:-10}"       # v12: 10 seeds (v11 top-50 Jaccard 0.60 on 5; the VM makes 10 cheap)
GRAPH_ASOF="$DATA/graph_asof_${CUT}_full"
# THE TRAINING LABEL (operator, 2026-10-12: "our label should be purely focused on the
# types of fraud cases that could be prosecuted under the False Claims Act"): the
# production scorer trains on the HARD tier only (fca_relevance: program-related
# crimes, health care fraud, kickbacks, abuse of billing privileges, false enrollment,
# DOJ cases); license actions / unstated-cause exclusions never train. Every scorer
# call below carries it; the registry records it as train_policy ("train = hard").
TRAIN_TIER="${TRAIN_TIER:-hard}"
EXCL="$GRAPH_ASOF/nodes/exclusion_nodes.parquet"
NPPES_ASOF="$(ls "$DATA"/preclean/nppes/npidata_pfile_*-${CUT//-/}??.csv 2>/dev/null | head -1)"
CASES="$DATA/enforcement/doj_cases_v3.csv"
DEFENDANTS="$DATA/processed/case_defendant_npis.parquet"
export MEDICAID_DUCKDB_TMP="${MEDICAID_DUCKDB_TMP:-$DATA/interim/duckdb_tmp}"
export MEDICAID_DUCKDB_MEM="${MEDICAID_DUCKDB_MEM:-$(awk '/MemTotal/ {printf "%dGB", int($2/1024/1024*0.4)}' /proc/meminfo)}"
mkdir -p "$MEDICAID_DUCKDB_TMP" "$RUN"
cd "$REPO"
# shellcheck disable=SC1091
[ -f "$REPO/.venv/bin/activate" ] && source "$REPO/.venv/bin/activate"
LOG="$RUN/night_run.log"
exec > >(tee -a "$LOG") 2>&1

step() { echo; echo "[$TAG] step $1: $2   ($(date '+%F %T'))"; }
fail() { echo; echo "[$TAG] FAILED at step $1  ($(date '+%F %T'))  -- paste the last 40 lines of $LOG to Claude"; exit 1; }

echo "[$TAG] started $(date '+%F %T')  (VM runner 2026-10-11: full embeddings, spilling DuckDB at $MEDICAID_DUCKDB_MEM, regularized scorer)"
FREEGB=$(awk '/MemAvailable/ {print int($2/1024/1024)}' /proc/meminfo)
echo "[$TAG]   free RAM: ${FREEGB} GB"
if [ "$FREEGB" -lt 24 ]; then echo "[$TAG] REFUSING TO START: only ${FREEGB} GB free (need 24)"; fail 0; fi
[ -f "$CASES" ] || { echo "[$TAG] MISSING $CASES (the case labels are the point)"; fail 0; }
[ -f "$DEFENDANTS" ] || { echo "[$TAG] MISSING $DEFENDANTS (run the defendant resolver first)"; fail 0; }

step 1/12 "point-in-time graph WITH FULL EMBEDDINGS, every node, full walks (rebuilt into $GRAPH_ASOF unless present)"
if [ -f "$GRAPH_ASOF/nodes/exclusion_nodes.parquet" ] && [ -z "$REBUILD" ]; then
  echo "[$TAG]   reusing $GRAPH_ASOF"
else
  [ -n "$NPPES_ASOF" ] || { echo "[$TAG] MISSING the as-of NPPES edition preclean/nppes/npidata_pfile_*-${CUT//-/}xx.csv"; fail 1; }
  python -u -m src.entity_graph --input "$DATA/processed" --out "$GRAPH_ASOF" --asof "$CUT" --asof-nppes "$NPPES_ASOF" \
      --max-embed-nodes 0 --light-walks-above 0 || fail 1
fi

step 2/12 "the per-NPI matrix (DOJ case labels ON, billing-NPI universe, label_weight, case-cluster groups, seeds=$SEEDS)"
python -u -m src.model_a.provider_features_export --graph-dir "$GRAPH_ASOF" --asof-cutoff "$CUT" --with-analytics \
    --case-db "$CASES" --defendant-npis "$DEFENDANTS" --no-servicing-rows --seeds "$SEEDS" --broadcast-rows weight \
    --group-policy case_cluster --leads "$DATA/detection/fraud_leads_v3.parquet" --preclean "$DATA/preclean" \
    --processed "$DATA/processed" --out "$RUN" || fail 2
grep -q "NO DOJ case labels" "$RUN/SOURCES_REPORT.md" && { echo "[$TAG] the matrix carries NO DOJ case labels -- stop"; fail 2; }
for s in servicing mue sector_schemes medicaid_enrollment billing_lm graph_embeddings; do
  if grep -E "^\| \`$s\` \| .*skipped" "$RUN/SOURCES_REPORT.md" >/dev/null; then
    echo "[$TAG] source $s was SKIPPED -- not a full-feature run; stop and paste its SOURCES_REPORT line"; fail 2
  fi
done

step 3/12 "forward labels (exclusion ruler, hard tier; FCA case ruler, 36 months)"
python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
    --out "$RUN/future_bans_after_${CUT}.csv" || fail 3
python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
    --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --horizon-months 36 \
    --out "$RUN/future_bans_after_${CUT}__fca36.csv" || fail 3

step 4/12 "network A/B (size-matched) on the forward label"
python -u -m src.model_a.network_ab --matrix "$RUN/provider_features_for_model.parquet" --manifest "$RUN/feature_manifest.json" \
    --future-label "$RUN/future_bans_after_${CUT}.csv" --cutoff "$CUT" --out "$RUN/NETWORK_AB_REPORT.md" || fail 4

step 5/12 "scheme triage"
python -u -m src.model_a.scheme_triage --run-dir "$RUN" || fail 5

step 6/12 "priority list + pursuit book + large-biller book"
python -u -m src.model_a.pursuit_dossier --run-dir "$RUN" --priority-list --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" || fail 6

step 7/12 "accuracy diagnostics on the PURSUIT book (the pre-registered checks read these)"
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months 36 --repeats 3 --list-name provider_priority_list_pursuit.csv || fail 7

step 8/12 "the large-biller book, and the broadcast A/B (weight vs mask, refit on the same matrix)"
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months 36 --list-name provider_priority_list_large.csv || fail 8
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months 36 --score-source refit --broadcast-rows weight --out "$RUN/ab_broadcast_weight/ACCURACY_DIAGNOSTICS.md" || fail 8
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months 36 --score-source refit --broadcast-rows mask --out "$RUN/ab_broadcast_mask/ACCURACY_DIAGNOSTICS.md" || fail 8

step 9/12 "results digest + pursuit dossier (operator book; no Manus dispatch here)"
python -u -m src.model_a.results_digest --dir "$RUN" --out "$RUN/RESULTS_DIGEST.md" || fail 9
python -u -m src.model_a.pursuit_dossier --run-dir "$RUN" --operator-book --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" || fail 9

step 10/12 "register the run against the locked pre-registration"
python -u -m src.model_a.run_registry --data-root "$DATA" --run-dir "$RUN" || fail 10

step 11/12 "learning loop"
python -u -m src.model_a.learning_loop --run-dir "$RUN" --data-root "$DATA" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" ${PREV:+--prev-run-dir "$PREV"} || fail 11

step 12/12 "the run issue ledger"
python -u -m src.model_a.run_issues --run-dir "$RUN" --data-root "$DATA" ${PREV:+--prev-run-dir "$PREV"} || fail 12

echo
echo "[$TAG] DONE $(date '+%F %T')  -- tell Claude '$TAG finished'; then the packet:"
echo "  (cd $RUN && zip -r ~/run_packet_${TAG}.zip *.md *.json ab_broadcast_weight ab_broadcast_mask)"
echo "  bash $REPO/ops/vm/sync_data.sh up frozen_${CUT}_${TAG}     # reports back to OneDrive"
