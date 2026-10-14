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
PURSUIT_ORDER="${PURSUIT_ORDER:-dollar}"   # v13: dollar (shipped) or basis50 (ship only when the v12 sweep says it wins)
# 2026-10-14 (docs/MODEL_QUESTIONS §2): the facility (Care Compare + PBJ, one source) and
# POS files on the data root are 2023 editions, information from AFTER a 2021-12 freeze;
# eight runs carried them as a ledger FAIL. Frozen runs exclude them until the pre-cutoff
# editions are downloaded (FROZEN_SKIP="" re-admits them; the vintage audit still grades).
FROZEN_SKIP="${FROZEN_SKIP-facility,pos}"
MEASURE_ABLATION="${MEASURE_ABLATION:-1}"   # measure_run.sh: 0 skips the slow label/source ablations
EXCL="$GRAPH_ASOF/nodes/exclusion_nodes.parquet"
NPPES_ASOF="$(ls "$DATA"/preclean/nppes/npidata_pfile_*-${CUT//-/}??.csv 2>/dev/null | head -1)"
# v15 (2026-10-13): the case DB plus the CIA case rows, the resolver NPIs plus the CIA
# party NPIs, merged by step 0 into ONE file each (the originals are never modified)
CASES_BASE="$DATA/enforcement/doj_cases_v3.csv"
DEFENDANTS_BASE="$DATA/processed/case_defendant_npis.parquet"
CIA_CASES="$DATA/enforcement/cia_cases.csv"
CIA_NPIS="$DATA/processed/cia_defendant_npis.parquet"
AFFILIATED="$DATA/processed/affiliated_convictions.parquet"
CASES="$DATA/enforcement/doj_cases_v3_plus_cia.csv"
DEFENDANTS="$DATA/processed/case_defendant_npis_plus_cia.parquet"
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
[ -f "$CASES_BASE" ] || { echo "[$TAG] MISSING $CASES_BASE (the case labels are the point)"; fail 0; }
[ -f "$DEFENDANTS_BASE" ] || { echo "[$TAG] MISSING $DEFENDANTS_BASE (run the defendant resolver first)"; fail 0; }
[ -f "$CIA_CASES" ] || echo "[$TAG]   note: no $CIA_CASES (label_expansion cia not run): the CIA parties stay out of this run"
[ -f "$AFFILIATED" ] || echo "[$TAG]   note: no $AFFILIATED (label_expansion affiliated not run): the affiliated ruler is skipped"
[ -f "$DATA/processed/exclusions_name_resolved.parquet" ] || echo "[$TAG]   note: no exclusions_name_resolved.parquet (label_expansion names not run)"

step 0/13 "the merged label inputs (case DB + CIA cases; resolver NPIs + CIA party NPIs)"
python -u -m src.model_a.label_expansion merge-inputs --case-db "$CASES_BASE" --cia-cases "$CIA_CASES" \
    --defendant-npis "$DEFENDANTS_BASE" --cia-npis "$CIA_NPIS" --out-cases "$CASES" --out-npis "$DEFENDANTS" || fail 0

step 1/13 "point-in-time graph WITH FULL EMBEDDINGS, every node, full walks (rebuilt into $GRAPH_ASOF unless present)"
if [ -f "$GRAPH_ASOF/nodes/exclusion_nodes.parquet" ] && [ -z "$REBUILD" ]; then
  echo "[$TAG]   reusing $GRAPH_ASOF  (pass --rebuild-graph when processed/exclusions_*.parquet changed: v15 did)"
else
  [ -n "$NPPES_ASOF" ] || { echo "[$TAG] MISSING the as-of NPPES edition preclean/nppes/npidata_pfile_*-${CUT//-/}xx.csv"; fail 1; }
  python -u -m src.entity_graph --input "$DATA/processed" --out "$GRAPH_ASOF" --asof "$CUT" --asof-nppes "$NPPES_ASOF" \
      --max-embed-nodes 0 --light-walks-above 0 || fail 1
fi

step 2/13 "the per-NPI matrix (DOJ case labels ON, billing-NPI universe, label_weight, case-cluster groups, seeds=$SEEDS, as-of v3 scores, skip=${FROZEN_SKIP:-none})"
python -u -m src.model_a.provider_features_export --graph-dir "$GRAPH_ASOF" --asof-cutoff "$CUT" --with-analytics \
    --case-db "$CASES" --defendant-npis "$DEFENDANTS" --no-servicing-rows --seeds "$SEEDS" --broadcast-rows weight \
    --group-policy case_cluster --leads "$DATA/detection/fraud_leads_v3.parquet" --preclean "$DATA/preclean" \
    --processed "$DATA/processed" --out "$RUN" ${FROZEN_SKIP:+--skip-sources "$FROZEN_SKIP"} || fail 2
grep -q "NO DOJ case labels" "$RUN/SOURCES_REPORT.md" && { echo "[$TAG] the matrix carries NO DOJ case labels -- stop"; fail 2; }
grep -q "\[asof_v3\] restored" "$RUN/night_run.log" || { echo "[$TAG] the as-of v3 scores were NOT restored (no '[asof_v3] restored' line) -- stop"; fail 2; }
for s in servicing mue sector_schemes medicaid_enrollment billing_lm graph_embeddings; do
  if grep -E "^\| \`$s\` \| .*skipped" "$RUN/SOURCES_REPORT.md" >/dev/null; then
    echo "[$TAG] source $s was SKIPPED -- not a full-feature run; stop and paste its SOURCES_REPORT line"; fail 2
  fi
done

step 3/13 "forward labels (exclusion ruler, hard tier; FCA case ruler, 36 months)"
python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
    --out "$RUN/future_bans_after_${CUT}.csv" || fail 3
python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
    --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --horizon-months 36 \
    --out "$RUN/future_bans_after_${CUT}__fca36.csv" || fail 3
if [ -f "$AFFILIATED" ]; then
  python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
      --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --horizon-months 36 --affiliated "$AFFILIATED" \
      --out "$RUN/future_bans_after_${CUT}__aff36.csv" || fail 3
fi
python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
    --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --horizon-months 36 --fca-case-only --qui-tam-only \
    --out "$RUN/future_bans_after_${CUT}__quitam36.csv" || fail 3

step 4/13 "network A/B (size-matched) on the forward label"
python -u -m src.model_a.network_ab --matrix "$RUN/provider_features_for_model.parquet" --manifest "$RUN/feature_manifest.json" \
    --future-label "$RUN/future_bans_after_${CUT}.csv" --cutoff "$CUT" --out "$RUN/NETWORK_AB_REPORT.md" || fail 4

step 5/13 "scheme triage"
python -u -m src.model_a.scheme_triage --run-dir "$RUN" || fail 5

step 6/13 "priority list + pursuit book + large-biller book"
python -u -m src.model_a.pursuit_dossier --run-dir "$RUN" --priority-list --order "$PURSUIT_ORDER" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" || fail 6

step 7/13 "accuracy diagnostics on the PURSUIT book (the pre-registered checks read these)"
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months 36 --repeats 3 --qui-tam-ruler --list-name provider_priority_list_pursuit.csv || fail 7

step 8/13 "the large-biller book, and the broadcast A/B (weight vs mask, refit on the same matrix)"
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months 36 --list-name provider_priority_list_large.csv || fail 8
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months 36 --score-source refit --broadcast-rows weight --out "$RUN/ab_broadcast_weight/ACCURACY_DIAGNOSTICS.md" || fail 8
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months 36 --score-source refit --broadcast-rows mask --out "$RUN/ab_broadcast_mask/ACCURACY_DIAGNOSTICS.md" || fail 8
# v15: the charged tier A/B (the hard arm is ab_broadcast_weight above: same matrix, same seeds, tier hard)
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier hard+charged --exclusion-nodes "$EXCL" \
    --horizon-months 36 --score-source refit --broadcast-rows weight --out "$RUN/ab_tier_charged/ACCURACY_DIAGNOSTICS.md" || fail 8
# 2026-10-14: the commercial-plan fraud A/B (source hcf_commercial, training only), run only
# once the case DB carries such rows (research_evidence commercial-cases)
if grep -q "hcf_commercial" "$CASES" 2>/dev/null; then
  python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier hard+commercial --exclusion-nodes "$EXCL" \
      --horizon-months 36 --score-source refit --broadcast-rows weight --out "$RUN/ab_tier_commercial/ACCURACY_DIAGNOSTICS.md" || fail 8
else
  echo "[$TAG]   no hcf_commercial rows in $CASES: the commercial A/B is skipped (harvest set 'commercial', loader research_evidence commercial-cases)"
fi

step 9/13 "results digest + pursuit dossier (operator book; no Manus dispatch here)"
python -u -m src.model_a.results_digest --dir "$RUN" --out "$RUN/RESULTS_DIGEST.md" || fail 9
python -u -m src.model_a.pursuit_dossier --run-dir "$RUN" --operator-book --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" || fail 9

step 10/13 "register the run against the locked pre-registration"
python -u -m src.model_a.run_registry --data-root "$DATA" --run-dir "$RUN" || fail 10

step 11/13 "learning loop"
python -u -m src.model_a.learning_loop --run-dir "$RUN" --data-root "$DATA" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" ${PREV:+--prev-run-dir "$PREV"} || fail 11

step 12/13 "measurement: scheme AUC on the FCA ruler, signal ranking, label enrichment, label + source ablations (ops/vm/measure_run.sh)"
MEASURE_ABLATION="$MEASURE_ABLATION" bash "$REPO/ops/vm/measure_run.sh" "$TAG" "$CUT" || fail 12

step 13/13 "the run issue ledger"
python -u -m src.model_a.run_issues --run-dir "$RUN" --data-root "$DATA" ${PREV:+--prev-run-dir "$PREV"} || fail 13

echo
echo "[$TAG] DONE $(date '+%F %T')  -- tell Claude '$TAG finished'; then the packet:"
echo "  (cd $RUN && tar -czf /home/ubuntu/run_packet_${TAG}.tar.gz *.md *.json *.csv ab_broadcast_weight ab_broadcast_mask ab_tier_charged $(ls -d ab_tier_commercial 2>/dev/null))   # tar is always installed; zip is not"
echo "  bash $REPO/ops/vm/sync_data.sh up frozen_${CUT}_${TAG}     # reports back to OneDrive"
