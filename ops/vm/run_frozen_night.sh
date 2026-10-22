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
# v21 (2026-10-22): the SHIPPED training policy's class priors (CLASS_PRIORS, e.g.
# taxonomy,entity; empty = none, the v9..v20 scorer) and prune list (DROP_SCHEMES,
# empty = none). Written into the manifest by the export; the registry reads them as
# `priors` / `prune`. When the shipped policy carries priors, step 8 grades a NO-priors
# arm (ab_no_priors/) instead of ab_class_priors/, so a counterfactual always exists.
CLASS_PRIORS="${CLASS_PRIORS:-}"
DROP_SCHEMES="${DROP_SCHEMES:-}"
# 2026-10-14 (docs/MODEL_QUESTIONS §2): the facility (Care Compare + PBJ, one source) and
# POS files on the data root are 2023 editions, information from AFTER a 2021-12 freeze;
# eight runs carried them as a ledger FAIL. Bundle 324: the PBJ / POS / NH blocks now read
# ONLY editions dated on/before the freeze (none on disk = the block reads nothing and
# says so), so the forced skip is off by default; FROZEN_SKIP=facility,pos still forces it.
# The 2021 editions are the reminder pre_cutoff_editions_2021.
FROZEN_SKIP="${FROZEN_SKIP-}"
MEASURE_ABLATION="${MEASURE_ABLATION:-1}"   # measure_run.sh: 0 skips the slow label/source ablations
EXCL="$GRAPH_ASOF/nodes/exclusion_nodes.parquet"
# 2026-10-21 (the second freeze): the as-of NPPES edition for ANY cutoff comes from
# src/model_a/freeze_inputs.py (preclean/nppes + nppes_history: the newest edition on/before
# the cutoff when it is at most 12 months old, else the nearest later one, recorded in
# $RUN/freeze_inputs.json and graded by run_issues); ASOF_NPPES=<path> overrides.
HORIZON="${HORIZON:-36}"   # the ruler length in months (2023-12 freeze: 33 ends 2026-09, fully elapsed)
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

# 2026-10-14: the vintage inspector dates every preclean file from its contents (the
# TRUE re-download list, EDITIONS.csv auto-fill). It streams the whole data root, so it
# runs when its report is older than VINTAGE_MAX_AGE_DAYS (7) or absent; VINTAGE_INSPECT=0 skips.
VI="$DATA/preclean/VINTAGE_INSPECT.md"
if [ "${VINTAGE_INSPECT:-1}" = "1" ]; then
  if [ ! -f "$VI" ] || [ -n "$(find "$VI" -mtime +"${VINTAGE_MAX_AGE_DAYS:-7}" 2>/dev/null)" ]; then
    step 0a/13 "vintage inspector (dates every preclean file from its contents; weekly)"
    python -u -m src.vintage_inspect --data-root "$DATA" || echo "[$TAG]   vintage inspector failed; DATA_REFRESH will say so"
  else
    echo "[$TAG]   vintage inspector report is current ($VI)"
  fi
fi

step 0/13 "freeze inputs (as-of NPPES edition for $CUT, horizon $HORIZON) + the owner-edition archive"
NPPES_ASOF="${ASOF_NPPES:-}"
if [ -z "$NPPES_ASOF" ]; then
  NPPES_ASOF="$(python -u -m src.model_a.freeze_inputs --data-root "$DATA" --cutoff "$CUT" --run-dir "$RUN" --horizon-months "$HORIZON" | tee /dev/stderr | tail -1)"
fi
[ -n "$NPPES_ASOF" ] && [ -f "$NPPES_ASOF" ] || { echo "[$TAG] no as-of NPPES edition for $CUT (preclean/nppes or nppes_history; names like npidata_pfile_20050523-20231210.csv)"; fail 0; }
echo "[$TAG]   as-of NPPES: $NPPES_ASOF"
# docs/FREEZE_DESIGN_NOTE_2026-10-21.md rule 2: build our own archive. Every All-Owners
# file in preclean/owners is copied into owners_prior/ under a dated name (idempotent), and
# this month's owner-edge snapshot is archived from the current graph when it is missing.
python -u -m src.entity_graph.owners_archive --data-root "$DATA" || echo "[$TAG]   owners_archive failed (non-fatal)"
SNAP="$DATA/owner_snapshots/owners_$(date +%Y-%m).parquet"
if [ ! -f "$SNAP" ] && [ -f "$DATA/graph/edges/owned_by_edges.parquet" ]; then
  python -u -m src.entity_graph.ownership_snapshot --owned-by "$DATA/graph/edges/owned_by_edges.parquet" --snapshots-dir "$DATA/owner_snapshots" \
    || echo "[$TAG]   owner snapshot failed (non-fatal)"
else
  echo "[$TAG]   owner snapshot for $(date +%Y-%m): $([ -f "$SNAP" ] && echo present || echo 'no current graph edges; make graph first')"
fi

step 0/13 "the merged label inputs (case DB + CIA cases; resolver NPIs + CIA party NPIs)"
python -u -m src.model_a.label_expansion merge-inputs --case-db "$CASES_BASE" --cia-cases "$CIA_CASES" \
    --defendant-npis "$DEFENDANTS_BASE" --cia-npis "$CIA_NPIS" --out-cases "$CASES" --out-npis "$DEFENDANTS" || fail 0

step 1/13 "point-in-time graph WITH FULL EMBEDDINGS, every node, full walks (rebuilt into $GRAPH_ASOF unless present)"
if [ -f "$GRAPH_ASOF/nodes/exclusion_nodes.parquet" ] && [ -z "$REBUILD" ]; then
  echo "[$TAG]   reusing $GRAPH_ASOF  (pass --rebuild-graph when processed/exclusions_*.parquet changed: v15 did)"
else
  python -u -m src.entity_graph --input "$DATA/processed" --out "$GRAPH_ASOF" --asof "$CUT" --asof-nppes "$NPPES_ASOF" \
      --max-embed-nodes 0 --light-walks-above 0 || fail 1
fi

step 2/13 "the per-NPI matrix (DOJ case labels ON, billing-NPI universe, label_weight, case-cluster groups, seeds=$SEEDS, as-of v3 scores, priors=${CLASS_PRIORS:-none}, prune=${DROP_SCHEMES:-none}, skip=${FROZEN_SKIP:-none})"
python -u -m src.model_a.provider_features_export --graph-dir "$GRAPH_ASOF" --asof-cutoff "$CUT" --with-analytics \
    --case-db "$CASES" --defendant-npis "$DEFENDANTS" --no-servicing-rows --seeds "$SEEDS" --broadcast-rows weight \
    --group-policy case_cluster --class-priors "$CLASS_PRIORS" --drop-schemes "$DROP_SCHEMES" \
    --leads "$DATA/detection/fraud_leads_v3.parquet" --preclean "$DATA/preclean" \
    --processed "$DATA/processed" --out "$RUN" ${FROZEN_SKIP:+--skip-sources "$FROZEN_SKIP"} || fail 2
grep -q "NO DOJ case labels" "$RUN/SOURCES_REPORT.md" && { echo "[$TAG] the matrix carries NO DOJ case labels -- stop"; fail 2; }
grep -q "\[asof_v3\] restored" "$RUN/night_run.log" || { echo "[$TAG] the as-of v3 scores were NOT restored (no '[asof_v3] restored' line) -- stop"; fail 2; }
for s in servicing mue sector_schemes medicaid_enrollment billing_lm graph_embeddings; do
  if grep -E "^\| \`$s\` \| .*skipped" "$RUN/SOURCES_REPORT.md" >/dev/null; then
    echo "[$TAG] source $s was SKIPPED -- not a full-feature run; stop and paste its SOURCES_REPORT line"; fail 2
  fi
done

# 2026-10-21 (PLATFORM_AUDIT B.F-8 / D7): the forward rulers read the CURRENT-DAY graph
# ($DATA/graph), the only merge point of processed/exclusions_*.parquet; it was never
# rebuilt by a runner, so label rows loaded after its build date never reached a ruler.
# 2026-10-21: the state lists arrive with the preclean sync (the operator's sorter files them as
# <ST>_terminations_<yyyymm>.*); nothing on the VM ran the loader, so a new list never became
# exclusion nodes. The loader runs when any list is newer than its parquet (or the parquet is
# missing); step 2b then sees the newer exclusions file and rebuilds the current-day graph.
step 2a/13 "state termination lists -> processed/exclusions_state_terminations.parquet (when a list is newer)"
ST_DIR="$DATA/preclean/state_terminations"
ST_OUT="$DATA/processed/exclusions_state_terminations.parquet"
if [ -d "$ST_DIR" ] && ls "$ST_DIR"/*_terminations_* >/dev/null 2>&1; then
  ST_NEWER=$( [ -f "$ST_OUT" ] && find "$ST_DIR" -maxdepth 1 -name '*_terminations_*' -newer "$ST_OUT" | head -3 || echo "no parquet yet" )
  if [ -n "$ST_NEWER" ]; then
    echo "[$TAG]   loading the state lists (newer: $ST_NEWER)"
    python -u -m src.enforcement.state_terminations --in-dir "$ST_DIR" --provider-dim "$DATA/processed/provider_dim.parquet" --out "$ST_OUT" || fail 2a
    echo "[$TAG]   read $DATA/processed/STATE_TERMINATIONS_REPORT.md: the reasons the tier table does not know tier WEAK"
  else
    echo "[$TAG]   state lists unchanged since $ST_OUT: reused"
  fi
else
  echo "[$TAG]   no state lists under $ST_DIR"
fi

step 2b/13 "the current-day graph (rebuilt when an exclusion source is newer than it; REBUILD_CURRENT=1 forces)"
CUR="$DATA/graph/nodes/exclusion_nodes.parquet"
NEWER=$(find "$DATA/processed" -maxdepth 1 -name 'exclusions*.parquet' -newer "$CUR" 2>/dev/null | head -5)
if [ ! -f "$CUR" ] || [ -n "$NEWER" ] || [ "${REBUILD_CURRENT:-0}" = "1" ]; then
  echo "[$TAG]   rebuilding $DATA/graph (newer exclusion sources: ${NEWER:-none / forced})"
  python -u -m src.entity_graph --input "$DATA/processed" --out "$DATA/graph" --max-embed-nodes 0 --light-walks-above 0 || fail 2b
else
  echo "[$TAG]   current-day graph is newer than every exclusion source: reused"
fi

step 3/13 "forward labels (exclusion ruler, hard tier; FCA case ruler, 36 months)"
python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
    --out "$RUN/future_bans_after_${CUT}.csv" || fail 3
python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
    --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --horizon-months "$HORIZON" \
    --out "$RUN/future_bans_after_${CUT}__fca${HORIZON}.csv" || fail 3
if [ -f "$AFFILIATED" ]; then
  python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
      --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --horizon-months "$HORIZON" --affiliated "$AFFILIATED" \
      --out "$RUN/future_bans_after_${CUT}__aff${HORIZON}.csv" || fail 3
fi
python -u -m src.model_a.prospective_label --exclusion-nodes "$DATA/graph/nodes/exclusion_nodes.parquet" --cutoff "$CUT" --fca-tier hard \
    --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --horizon-months "$HORIZON" --fca-case-only --qui-tam-only \
    --out "$RUN/future_bans_after_${CUT}__quitam${HORIZON}.csv" || fail 3

step 4/13 "network A/B (size-matched) on the forward label"
python -u -m src.model_a.network_ab --matrix "$RUN/provider_features_for_model.parquet" --manifest "$RUN/feature_manifest.json" \
    --future-label "$RUN/future_bans_after_${CUT}.csv" --cutoff "$CUT" --out "$RUN/NETWORK_AB_REPORT.md" || fail 4

step 5/13 "scheme triage"
python -u -m src.model_a.scheme_triage --run-dir "$RUN" || fail 5

step 6/13 "priority list + pursuit book + large-biller book"
python -u -m src.model_a.pursuit_dossier --run-dir "$RUN" --priority-list --order "$PURSUIT_ORDER" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" || fail 6

step 7/13 "accuracy diagnostics on the PURSUIT book (the pre-registered checks read these)"
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --repeats 3 --qui-tam-ruler --list-name provider_priority_list_pursuit.csv || fail 7

step 8/13 "the large-biller book, and the broadcast A/B (weight vs mask, refit on the same matrix)"
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --list-name provider_priority_list_large.csv || fail 8
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --list-name provider_priority_list_pmax.csv || fail 8    # the v17 sweep's winning order, graded as a third book (2026-10-20)
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --score-source refit --broadcast-rows weight --out "$RUN/ab_broadcast_weight/ACCURACY_DIAGNOSTICS.md" || fail 8
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --score-source refit --broadcast-rows mask --out "$RUN/ab_broadcast_mask/ACCURACY_DIAGNOSTICS.md" || fail 8
# v21 (2026-10-22): the CLASS-PRIOR A/B — the class base rate (taxonomy + entity type, the
# v19 LABEL_ENRICHMENT lesson) target-encoded out-of-fold inside every training fold; the
# hard arm without priors is ab_broadcast_weight above (same matrix, seeds, tier). Env
# CLASS_PRIORS_AB (default taxonomy,entity; empty skips). Graded, never shipped from here.
if [ -n "$CLASS_PRIORS" ]; then
  # the shipped policy carries priors: the counterfactual is the NO-priors refit
  python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
      --horizon-months "$HORIZON" --score-source refit --broadcast-rows weight --class-priors "" --out "$RUN/ab_no_priors/ACCURACY_DIAGNOSTICS.md" || fail 8
elif [ -n "${CLASS_PRIORS_AB:-taxonomy,entity}" ]; then
  python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
      --horizon-months "$HORIZON" --score-source refit --broadcast-rows weight --class-priors "${CLASS_PRIORS_AB:-taxonomy,entity}" --out "$RUN/ab_class_priors/ACCURACY_DIAGNOSTICS.md" || fail 8
fi
# v21: the PRUNE A/B — the scheme-health prune candidates (coin-flip on both labels) held out
# of the refit. PRUNE_AB=auto (default) reads the PREVIOUS run's scheme_health.json (this run's
# is written at step 12); an explicit comma list overrides; empty / no previous run skips.
PRUNE_LIST=""
case "${PRUNE_AB:-auto}" in
  "") ;;
  auto) [ -n "$PREV" ] && [ -f "$PREV/scheme_health.json" ] && PRUNE_LIST="$(python -m src.model_a.scheme_health --prune-list "$PREV/scheme_health.json")" ;;
  *) PRUNE_LIST="$PRUNE_AB" ;;
esac
if [ -n "$PRUNE_LIST" ]; then
  echo "[$TAG] prune A/B: holding out $PRUNE_LIST"
  python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
      --horizon-months "$HORIZON" --score-source refit --broadcast-rows weight --drop-schemes "$PRUNE_LIST" --out "$RUN/ab_prune/ACCURACY_DIAGNOSTICS.md" || fail 8
else
  echo "[$TAG] prune A/B skipped (no prune list: PRUNE_AB=$PRUNE_AB, PREV=$PREV)"
fi
# v15: the charged tier A/B (the hard arm is ab_broadcast_weight above: same matrix, same seeds, tier hard)
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier hard+charged --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --score-source refit --broadcast-rows weight --out "$RUN/ab_tier_charged/ACCURACY_DIAGNOSTICS.md" || fail 8
# 2026-10-14: the commercial-plan fraud A/B (source hcf_commercial, training only), run only
# once the case DB carries such rows (research_evidence commercial-cases)
if grep -q "hcf_commercial" "$CASES" 2>/dev/null; then
  python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier hard+commercial --exclusion-nodes "$EXCL" \
      --horizon-months "$HORIZON" --score-source refit --broadcast-rows weight --out "$RUN/ab_tier_commercial/ACCURACY_DIAGNOSTICS.md" || fail 8
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
echo "  (cd $RUN && tar -czf /home/ubuntu/run_packet_${TAG}.tar.gz *.md *.json *.csv ab_broadcast_weight ab_broadcast_mask ab_tier_charged $(ls -d ab_tier_commercial ab_class_priors ab_no_priors ab_prune 2>/dev/null))   # tar is always installed; zip is not"
echo "  bash $REPO/ops/vm/sync_data.sh up frozen_${CUT}_${TAG}     # reports back to OneDrive"
echo "  python -m src.model_a.freeze_pair --runs <the 2021-12 run> $RUN     # the two-ruler read-out (FREEZE_PAIR.md)"
