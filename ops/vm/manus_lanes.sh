#!/usr/bin/env bash
# ops/vm/manus_lanes.sh — every research lane, unattended, from one command (2026-10-14).
#
#   source ~/.medicaid_env          # MANUS_API_KEY + MEDICAID_DATA_ROOT
#   tmux new -s lanes -d 'cd ~/medicaid-fraud-detection && source .venv/bin/activate && source ~/.medicaid_env && bash ops/vm/manus_lanes.sh v17'
#
# What it does, in order (every step logs to $DATA/processed/manus_lanes/lanes_run.log):
#   0. refresh the lane inputs: label_expansion cia (party -> enrolled NPIs) and names
#      (name-only exclusions -> NPIs offline) so the residues the prompts ask about are current
#   1. build every lane's prompts (cia, names, outcome_refresh, commercial, defendant_research,
#      label_verification, label_review from the run folder)
#   2. dispatch under a budget: MANUS_MAX_NEW per lane (default 40), MANUS_TOTAL across lanes
#      (default 250); re-runs re-attach for free (the registry) and never re-send
#   3. poll until every task answers or MANUS_WAIT_MINUTES (default 240) pass, every
#      MANUS_POLL_EVERY seconds (default 600)
#   4. load every answer through the lane's loader (the machine gates; quarantine files for the rest)
#   5. merge-inputs (the next run's label files) and the MANUS_LANES report
# Lanes: MANUS_LANES env (comma list) narrows the set. A lane with nothing to ask writes 0 prompts.
set -uo pipefail
TAG="${1:-}"
CUT="${2:-2021-12}"
DATA="${MEDICAID_DATA_ROOT:-/data/medicaid}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="${RUN_DIR:-$DATA/model_a/frozen_${CUT}_${TAG}}"
LANES="${MANUS_LANES:-cia,names,outcome_refresh,label_verification,commercial,defendant_research,label_review}"
MAX_NEW="${MANUS_MAX_NEW:-40}"
TOTAL="${MANUS_TOTAL:-250}"
WAIT="${MANUS_WAIT_MINUTES:-240}"
EVERY="${MANUS_POLL_EVERY:-600}"
GRAPH="$DATA/graph"
OUT="$DATA/processed/manus_lanes"
mkdir -p "$OUT"
cd "$REPO"
# shellcheck disable=SC1091
[ -f "$REPO/.venv/bin/activate" ] && source "$REPO/.venv/bin/activate"
LOG="$OUT/lanes_run.log"
exec > >(tee -a "$LOG") 2>&1
[ -n "${MANUS_API_KEY:-}" ] || { echo "[lanes] MANUS_API_KEY is not set (source ~/.medicaid_env)"; exit 1; }
step() { echo; echo "[lanes] step $1: $2   ($(date '+%F %T'))"; }
warn() { echo "[lanes] step $1 had a problem ($(date '+%F %T')); continuing (the report says what loaded)"; }

echo "[lanes] started $(date '+%F %T')  lanes=$LANES max_new=$MAX_NEW total=$TOTAL wait=${WAIT}m run=$RUN"

step 0/5 "refresh the lane inputs (label_expansion cia + names)"
if [ -f "$DATA/preclean/cia/cia_list.csv" ]; then
  python -u -m src.model_a.label_expansion cia --cia "$DATA/preclean/cia/cia_list.csv" --graph-dir "$GRAPH" \
      --out-cases "$DATA/enforcement/cia_cases.csv" --out-npis "$DATA/processed/cia_defendant_npis.parquet" || warn 0
fi
if [ -f "$DATA/preclean/Caught.csv" ] && [ -f "$DATA/processed/provider_dim.parquet" ]; then
  python -u -m src.model_a.label_expansion names --leie "$DATA/preclean/Caught.csv" --provider-dim "$DATA/processed/provider_dim.parquet" \
      $( [ -f "$DATA/processed/nppes_other_ids.parquet" ] && echo --other-ids "$DATA/processed/nppes_other_ids.parquet" ) \
      --out "$DATA/processed/exclusion_name_resolution.parquet" --out-exclusions "$DATA/processed/exclusions_name_resolved.parquet" || warn 0
fi

step 1/5 "build + dispatch (budget $MAX_NEW per lane, $TOTAL total)"
python -u -m src.model_a.manus_lanes --data-root "$DATA" ${TAG:+--run-dir "$RUN"} --lanes "$LANES" \
    --build --dispatch --max-new "$MAX_NEW" --total-budget "$TOTAL" || warn 1

step 2/5 "poll for up to ${WAIT} minutes, then load through the gates"
python -u -m src.model_a.manus_lanes --data-root "$DATA" ${TAG:+--run-dir "$RUN"} --lanes "$LANES" \
    --poll --wait-minutes "$WAIT" --poll-every "$EVERY" --load || warn 2

step 3/5 "re-apply the CIA answers after a fresh cia resolve (the loader is idempotent)"
python -u -m src.model_a.manus_lanes --data-root "$DATA" --lanes cia --load || warn 3

step 4/5 "the merged label inputs for the next run"
python -u -m src.model_a.label_expansion merge-inputs --case-db "$DATA/enforcement/doj_cases_v3.csv" \
    --cia-cases "$DATA/enforcement/cia_cases.csv" --defendant-npis "$DATA/processed/case_defendant_npis.parquet" \
    --cia-npis "$DATA/processed/cia_defendant_npis.parquet" --out-cases "$DATA/enforcement/doj_cases_v3_plus_cia.csv" \
    --out-npis "$DATA/processed/case_defendant_npis_plus_cia.parquet" || warn 4

step 5/5 "the report"
python -u -m src.model_a.manus_lanes --data-root "$DATA" --lanes "$LANES" --report || warn 5
echo
echo "[lanes] DONE $(date '+%F %T')  -- read $OUT/MANUS_LANES.md; tasks still running answer on the next"
echo "  python -m src.model_a.manus_lanes --data-root \"$DATA\" --lanes \"$LANES\" --poll --load   (free: re-attaches by task id)"
echo "  then REBUILD THE GRAPH before the next frozen run when names loaded rows (--rebuild-graph)."
