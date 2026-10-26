#!/usr/bin/env bash
# ops/vm/run_current_day.sh  --  the OPERATIONAL list: a current-day export (no freeze),
# scored on data through the file's last month, with the standing current-day reports
# (attending concentration, Tier A contradictions) and the operator-book dossier.
#
#   bash ops/vm/run_current_day.sh cd1              # tag cd1 -> $DATA/model_a/current_<yyyymm>_cd1
#   REBUILD_CURRENT=1 bash ops/vm/run_current_day.sh cd1
#
# Why (2026-10-26): every frozen run is blind to anything after its freeze by design; the
# home care lead (one physician's number on $97M of five agencies' aide claims, all of it
# 2024) and the skin-substitute lead (a new entity opened October 2023) sat on no list
# because v26 freezes at 2023-12. The operational pursuit book needs today's data.
#
# What it does NOT do: no forward ruler (nothing has resolved after today), no accuracy
# diagnostics, no registration, no arms, no Manus dispatch. The scorer and its policy are
# the frozen runner's (train hard, ten seeds, no class priors unless CLASS_PRIORS is set,
# pmax_basis50 by default) so the list is the same model applied to the current fact.
# The dossier is built from the operator book; `--dispatch` is the operator's word.
set -uo pipefail
TAG="${1:?tag, e.g. cd1}"
DATA="${MEDICAID_DATA_ROOT:-/data/medicaid}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
YM="$(date +%Y%m)"
RUN="$DATA/model_a/current_${YM}_${TAG}"
SEEDS="${SEEDS:-10}"
TRAIN_TIER="${TRAIN_TIER:-hard}"
PURSUIT_ORDER="${PURSUIT_ORDER:-pmax_basis50}"
CLASS_PRIORS="${CLASS_PRIORS:-}"
PRIOR_MIN_N="${PRIOR_MIN_N:-300}"
PRIOR_WEIGHTED="${PRIOR_WEIGHTED:-1}"
PRIOR_INNER="${PRIOR_INNER:-5}"
PRIOR_FLAGS="--prior-min-n $PRIOR_MIN_N --prior-weighted $PRIOR_WEIGHTED --prior-inner-folds $PRIOR_INNER"
DROP_SCHEMES="${DROP_SCHEMES:-}"
GRAPH="$DATA/graph"
EXCL="$GRAPH/nodes/exclusion_nodes.parquet"
CASES_BASE="$DATA/enforcement/doj_cases_v3.csv"
DEFENDANTS_BASE="$DATA/processed/case_defendant_npis.parquet"
CIA_CASES="$DATA/enforcement/cia_cases.csv"
CIA_NPIS="$DATA/processed/cia_defendant_npis.parquet"
CASES="$DATA/enforcement/doj_cases_v3_plus_cia.csv"
DEFENDANTS="$DATA/processed/case_defendant_npis_plus_cia.parquet"
export MEDICAID_DUCKDB_TMP="${MEDICAID_DUCKDB_TMP:-$DATA/interim/duckdb_tmp}"
export MEDICAID_DUCKDB_MEM="${MEDICAID_DUCKDB_MEM:-$(awk '/MemTotal/ {printf "%dGB", int($2/1024/1024*0.4)}' /proc/meminfo)}"
mkdir -p "$MEDICAID_DUCKDB_TMP" "$RUN"
cd "$REPO"
# shellcheck disable=SC1091
[ -f "$REPO/.venv/bin/activate" ] && source "$REPO/.venv/bin/activate"
LOG="$RUN/current_run.log"
exec > >(tee -a "$LOG") 2>&1

step() { echo; echo "[$TAG] step $1: $2   ($(date '+%F %T'))"; }
fail() { echo; echo "[$TAG] FAILED at step $1  ($(date '+%F %T'))  -- paste the last 40 lines of $LOG to Claude"; exit 1; }
STEPS=(0 1 2 3 4 5 6)
RESUME_FROM="${RESUME_FROM:-}"
run_step() {
  [ -z "$RESUME_FROM" ] && return 0
  local i="" j="" idx
  for idx in "${!STEPS[@]}"; do
    [ "${STEPS[$idx]}" = "$1" ] && i=$idx
    [ "${STEPS[$idx]}" = "$RESUME_FROM" ] && j=$idx
  done
  [ -n "$j" ] || { echo "[$TAG] unknown RESUME_FROM '$RESUME_FROM' (ids: ${STEPS[*]})"; exit 1; }
  [ -n "$i" ] || { echo "[$TAG] run_step: unknown step id '$1'"; exit 1; }
  if [ "$i" -lt "$j" ]; then echo "[$TAG] step $1 skipped (RESUME_FROM=$RESUME_FROM)"; return 1; fi
  return 0
}
[ -n "$RESUME_FROM" ] && echo "[$TAG] RESUMING at step $RESUME_FROM in $RUN"

echo "[$TAG] started $(date '+%F %T')  (current-day export: no freeze, no ruler; DuckDB at $MEDICAID_DUCKDB_MEM)"
FREEGB=$(awk '/MemAvailable/ {print int($2/1024/1024)}' /proc/meminfo)
echo "[$TAG]   free RAM: ${FREEGB} GB"
if [ "$FREEGB" -lt 24 ]; then echo "[$TAG] REFUSING TO START: only ${FREEGB} GB free (need 24)"; fail 0; fi
[ -f "$CASES_BASE" ] || { echo "[$TAG] MISSING $CASES_BASE (the case labels are the point)"; fail 0; }
[ -f "$DEFENDANTS_BASE" ] || { echo "[$TAG] MISSING $DEFENDANTS_BASE (run the defendant resolver first)"; fail 0; }
if pgrep -f "run_frozen_night.sh" >/dev/null 2>&1; then echo "[$TAG] REFUSING TO START: a frozen run is going (never beside it)"; fail 0; fi

if run_step 0; then
step 0/6 "the merged label inputs (case DB + CIA cases; resolver NPIs + CIA party NPIs)"
python -u -m src.model_a.label_expansion merge-inputs --case-db "$CASES_BASE" --cia-cases "$CIA_CASES" \
    --defendant-npis "$DEFENDANTS_BASE" --cia-npis "$CIA_NPIS" --out-cases "$CASES" --out-npis "$DEFENDANTS" || fail 0
fi

if run_step 1; then
step 1/6 "the current-day graph WITH FULL EMBEDDINGS (rebuilt when an exclusion source is newer than it, or REBUILD_CURRENT=1)"
NEWER=$(find "$DATA/processed" -maxdepth 1 -name 'exclusions*.parquet' -newer "$EXCL" 2>/dev/null | head -5)
if [ ! -f "$EXCL" ] || [ -n "$NEWER" ] || [ "${REBUILD_CURRENT:-0}" = "1" ]; then
  echo "[$TAG]   rebuilding $GRAPH (newer exclusion sources: ${NEWER:-none / forced})"
  python -u -m src.entity_graph --input "$DATA/processed" --out "$GRAPH" --max-embed-nodes 0 --light-walks-above 0 || fail 1
else
  echo "[$TAG]   current-day graph is newer than every exclusion source: reused"
fi
[ -f "$GRAPH/node_embeddings.parquet" ] || echo "[$TAG]   note: $GRAPH has no node_embeddings.parquet (graph_emb_* will be absent; REBUILD_CURRENT=1 builds them)"
fi

if run_step 2; then
step 2/6 "the per-NPI matrix on the CURRENT fact (no --asof-cutoff; DOJ case labels ON, billing-NPI universe, seeds=$SEEDS, priors=${CLASS_PRIORS:-none}, prune=${DROP_SCHEMES:-none})"
python -u -m src.model_a.provider_features_export --graph-dir "$GRAPH" --with-analytics \
    --case-db "$CASES" --defendant-npis "$DEFENDANTS" --no-servicing-rows --seeds "$SEEDS" --broadcast-rows weight \
    --group-policy case_cluster --class-priors "$CLASS_PRIORS" --drop-schemes "$DROP_SCHEMES" $PRIOR_FLAGS \
    --leads "$DATA/detection/fraud_leads_v3.parquet" --preclean "$DATA/preclean" \
    --processed "$DATA/processed" --out "$RUN" ${CURRENT_SKIP:+--skip-sources "$CURRENT_SKIP"} || fail 2
grep -q "NO DOJ case labels" "$RUN/SOURCES_REPORT.md" && { echo "[$TAG] the matrix carries NO DOJ case labels -- stop"; fail 2; }
for s in servicing mue sector_schemes medicaid_enrollment billing_lm; do
  if grep -E "^\| \`$s\` \| .*skipped" "$RUN/SOURCES_REPORT.md" >/dev/null; then
    echo "[$TAG] source $s was SKIPPED -- not a full-feature run; stop and paste its SOURCES_REPORT line"; fail 2
  fi
done
fi

if run_step 3; then
step 3/6 "priority list + pursuit book + large-biller book (order $PURSUIT_ORDER, train $TRAIN_TIER)"
python -u -m src.model_a.pursuit_dossier --run-dir "$RUN" --priority-list --order "$PURSUIT_ORDER" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" || fail 3
fi

if run_step 4; then
step 4/6 "the current-day reports the scorer cannot see: attending concentration (clinicians of record on other organizations' claims) + Tier A contradictions"
python -u -m src.model_a.attending_concentration --spending "$DATA/processed/spending_fact.parquet" \
    --provider-dim "$DATA/processed/provider_dim.parquet" --min-paid "${ATTENDING_MIN_PAID:-2000000}" --top 100 \
    --out "$RUN/attending_concentration" || fail 4
python -u -m src.model_a.contradictions --spending "$DATA/processed/spending_fact.parquet" --exclusion-nodes "$EXCL" \
    --matrix "$RUN/provider_features_for_model.parquet" --provider-dim "$DATA/processed/provider_dim.parquet" \
    --out "$RUN/contradictions" || echo "[$TAG]   contradictions failed (not fatal); see the log"
fi

if run_step 5; then
step 5/6 "scheme triage + results digest + the operator-book dossier (no Manus dispatch here)"
python -u -m src.model_a.scheme_triage --run-dir "$RUN" || echo "[$TAG]   scheme triage failed (not fatal)"
python -u -m src.model_a.results_digest --dir "$RUN" --out "$RUN/RESULTS_DIGEST.md" || echo "[$TAG]   results digest failed (not fatal)"
python -u -m src.model_a.pursuit_dossier --run-dir "$RUN" --operator-book --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" || fail 5
fi

if run_step 6; then
step 6/6 "the run issue ledger"
python -u -m src.model_a.run_issues --run-dir "$RUN" --data-root "$DATA" || echo "[$TAG]   run_issues failed (not fatal)"
fi

echo
echo "[$TAG] DONE $(date '+%F %T')  -- tell Claude '$TAG finished'; then the packet:"
echo "  (cd $RUN && tar -czf /home/ubuntu/run_packet_${TAG}.tar.gz *.md *.json *.csv attending_concentration contradictions 2>/dev/null)"
echo "  bash $REPO/ops/vm/sync_data.sh up current_${YM}_${TAG}     # reports back to OneDrive"
echo "  the operator book is $RUN/PURSUIT_DOSSIER.md; --dispatch on the dossier line is the operator's word"
