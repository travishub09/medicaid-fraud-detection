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
# the ruler horizon in months (36 for the 2021-12 freeze, 33 for 2023-12); the ruler
# files carry it in their names and every diagnostics call passes it (set -u: it was
# never defined here and the v22 launch died at the copy list, 2026-10-23)
HORIZON="${HORIZON:-36}"

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
# v23: the shipped prior encoder (see the arms at step 8); the manifest's value is the
# fallback the scorer reads when the CLI passes nothing, so these are passed explicitly
PRIOR_MIN_N="${PRIOR_MIN_N:-300}"
PRIOR_WEIGHTED="${PRIOR_WEIGHTED:-1}"
# v24: training rows' prior from an inner K-fold (0 = the leave-one-out encoder of v21..v23,
# the label leak the v23 read-out found; an arm, never the ship)
PRIOR_INNER="${PRIOR_INNER:-5}"
PRIOR_FLAGS="--prior-min-n $PRIOR_MIN_N --prior-weighted $PRIOR_WEIGHTED --prior-inner-folds $PRIOR_INNER"
# v25: CLASS_PRIORS set (even empty) overrides the SOURCE manifest's class priors on every ship
# and arm line ('' = none; parse_spec reads the word none as no priors); unset = the manifest's
if [ -n "${CLASS_PRIORS+x}" ]; then
  PRIOR_FLAGS="$PRIOR_FLAGS --class-priors ${CLASS_PRIORS:-none}"
fi
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

echo "[$NEW] started $(date '+%F %T')  (re-grade of $SRC's matrix under $(git -C "$REPO" rev-parse --short HEAD); order=$PURSUIT_ORDER tier=$TRAIN_TIER prior_min_n=$PRIOR_MIN_N prior_weighted=$PRIOR_WEIGHTED prior_inner=$PRIOR_INNER)"
step 0/7 "copy the matrix, manifest, labels and OOF scores from $SRC_RUN (no retrain)"
for f in provider_features_for_model.parquet feature_manifest.json provider_oof_scores.parquet \
         future_bans_after_${CUT}.csv future_bans_after_${CUT}.TIERS.md future_bans_after_${CUT}__fca${HORIZON}.csv \
         future_bans_after_${CUT}__fca${HORIZON}.TIERS.md future_bans_after_${CUT}__aff${HORIZON}.csv \
         future_bans_after_${CUT}__aff${HORIZON}.TIERS.md future_bans_after_${CUT}__quitam${HORIZON}.csv \
         future_bans_after_${CUT}__quitam${HORIZON}.TIERS.md freeze_inputs.json training_label_policy.json \
         PROVIDER_FEATURES_EXPORT_REPORT.md PROVIDER_FEATURES_DICTIONARY.md \
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
python -u -m src.model_a.pursuit_dossier --run-dir "$RUN" --priority-list --order "$PURSUIT_ORDER" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" $PRIOR_FLAGS || fail 6

step 7/12 "accuracy diagnostics on the PURSUIT book"
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" $PRIOR_FLAGS --repeats 3 --qui-tam-ruler --list-name provider_priority_list_pursuit.csv || fail 7

step 8/12 "the large-biller book, and the broadcast A/B"
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" $PRIOR_FLAGS --list-name provider_priority_list_large.csv || fail 8
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" $PRIOR_FLAGS --list-name provider_priority_list_pmax.csv || fail 8    # the v17 sweep's winning order, graded as a third book (2026-10-20)
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --score-source refit $PRIOR_FLAGS --broadcast-rows weight --out "$RUN/ab_broadcast_weight/ACCURACY_DIAGNOSTICS.md" || fail 8
python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
    --horizon-months "$HORIZON" --score-source refit $PRIOR_FLAGS --broadcast-rows mask --out "$RUN/ab_broadcast_mask/ACCURACY_DIAGNOSTICS.md" || fail 8
# v21: the class-prior A/B. v22: when the SOURCE manifest ships class priors (its
# training_policy.class_priors), the counterfactual is the no-priors arm (ab_no_priors/),
# as in run_frozen_night.sh; otherwise CLASS_PRIORS_AB (default taxonomy,entity; empty skips)
# grades priors as an arm. NOTE: the re-score itself (step 6) runs the CURRENT encoder on the
# manifest's policy, so a code change to class_priors.py (v22: weights + size floor) lands
# here without an export.
SHIPPED_PRIORS="$(python - "$RUN/feature_manifest.json" <<'PY'
import json, sys
pol = json.load(open(sys.argv[1])).get("training_policy") or {}
print(",".join(pol.get("class_priors") or []))
PY
)"
if [ -n "${CLASS_PRIORS+x}" ]; then SHIPPED_PRIORS="$CLASS_PRIORS"; fi     # v25: the override is the ship
if [ -n "$SHIPPED_PRIORS" ]; then
  echo "[$NEW] shipped priors = $SHIPPED_PRIORS: grading the no-priors counterfactual (ab_no_priors/)"
  python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
      --horizon-months "$HORIZON" --score-source refit $PRIOR_FLAGS --broadcast-rows weight --class-priors "" --out "$RUN/ab_no_priors/ACCURACY_DIAGNOSTICS.md" || fail 8
elif [ -n "${CLASS_PRIORS_AB:-taxonomy,entity}" ]; then
  python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
      --horizon-months "$HORIZON" --score-source refit $PRIOR_FLAGS --broadcast-rows weight --class-priors "${CLASS_PRIORS_AB:-taxonomy,entity}" --out "$RUN/ab_class_priors/ACCURACY_DIAGNOSTICS.md" || fail 8
fi
# v23: the prior-encoder A/B (the v22 read-out: floor + weights fixed the NPI grain and broke
# the pursuit book). The SHIPPED encoder is PRIOR_MIN_N / PRIOR_WEIGHTED / PRIOR_INNER (default
# 300 / 1 / 5 = v24: floor, label-weighted, NESTED); every other encoder listed is graded as a
# refit arm, main list AND the operator book under the shipped order (acc_pursuit_*). v24 arms:
# loo_v22 = the v22 encoder (leave-one-out: the leak, graded so the fix is measured on the same
# matrix), nested_unweighted = the fix without the label weights. Format name:min_n:weighted:inner.
PRIOR_ARMS="${PRIOR_ARMS-loo_v22:300:1:0 nested_unweighted:300:0:5}"   # unset = the default arms; EMPTY = no encoder arms (v25 ran two no-op refits because :- read empty as unset)
for arm in $PRIOR_ARMS; do
  IFS=: read -r name mn wt inner <<< "$arm"
  if [ "$mn" = "$PRIOR_MIN_N" ] && [ "$wt" = "$PRIOR_WEIGHTED" ] && [ "$inner" = "$PRIOR_INNER" ]; then continue; fi
  echo "[$NEW] prior A/B arm $name (min_n $mn, weighted $wt, inner_folds $inner)"
  python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
      --horizon-months "$HORIZON" --score-source refit --broadcast-rows weight --prior-min-n "$mn" --prior-weighted "$wt" --prior-inner-folds "$inner" --out "$RUN/ab_prior_${name}/ACCURACY_DIAGNOSTICS.md" || fail 8
done
# v21: the prune A/B (PRUNE_AB=auto reads the SOURCE run's scheme_health.json; a list overrides; empty skips)
PRUNE_LIST=""
case "${PRUNE_AB:-auto}" in
  "") ;;
  auto) [ -f "$SRC_RUN/scheme_health.json" ] && PRUNE_LIST="$(python -m src.model_a.scheme_health --prune-list "$SRC_RUN/scheme_health.json")" ;;
  *) PRUNE_LIST="$PRUNE_AB" ;;
esac
if [ -n "$PRUNE_LIST" ]; then
  echo "[$NEW] prune A/B: holding out $PRUNE_LIST"
  python -u -m src.model_a.accuracy_diagnostics --run-dir "$RUN" --case-db "$CASES" --graph-dir "$DATA/graph" --defendant-npis "$DEFENDANTS" --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" \
      --horizon-months "$HORIZON" --score-source refit $PRIOR_FLAGS --broadcast-rows weight --drop-schemes "$PRUNE_LIST" --out "$RUN/ab_prune/ACCURACY_DIAGNOSTICS.md" || fail 8
else
  echo "[$NEW] prune A/B skipped (no prune list)"
fi

step 9/12 "results digest + pursuit dossier (operator book, national chains set aside; no Manus dispatch here)"
python -u -m src.model_a.results_digest --dir "$RUN" --out "$RUN/RESULTS_DIGEST.md" || fail 9
python -u -m src.model_a.pursuit_dossier --run-dir "$RUN" --operator-book --label-tier "$TRAIN_TIER" --exclusion-nodes "$EXCL" $PRIOR_FLAGS || fail 9

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
echo "  (cd $RUN && tar -czf /home/ubuntu/run_packet_${NEW}.tar.gz *.md *.json *.csv ab_broadcast_weight ab_broadcast_mask $(cd $RUN && ls -d ab_tier_charged ab_tier_commercial ab_class_priors ab_no_priors ab_prune ab_prior_* 2>/dev/null))   # tar; zip is not installed"
echo "  bash $REPO/ops/vm/sync_data.sh up frozen_${CUT}_${NEW}"
