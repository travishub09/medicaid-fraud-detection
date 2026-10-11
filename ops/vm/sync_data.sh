#!/usr/bin/env bash
# ops/vm/sync_data.sh  --  pull the operator's data root from OneDrive into the VM,
# and push a finished run's reports back.
#
#   bash ops/vm/sync_data.sh                 # OneDrive Desktop/data  ->  $MEDICAID_DATA_ROOT
#   bash ops/vm/sync_data.sh up frozen_2021-12_v12   # VM run folder reports -> OneDrive model_a/
#
# Needs `rclone config` done once on the VM with a remote named "onedrive" (the
# interactive login; docs/VM_RUNBOOK.md). Only the reports go back up (the *.md / *.json
# of a run folder plus the registry files): the parquet / csv outputs stay on the VM
# disk, and nothing in the data root is ever committed anywhere.
set -euo pipefail
DATA_ROOT="${MEDICAID_DATA_ROOT:-/data/medicaid}"
REMOTE="${ONEDRIVE_REMOTE:-onedrive}"
SRC_PATH="${ONEDRIVE_DATA_PATH:-Desktop/data}"

if [ "${1:-down}" = "up" ]; then
  RUN="${2:?run folder name, e.g. frozen_2021-12_v12}"
  echo "[sync] reports of $RUN -> $REMOTE:$SRC_PATH/model_a/$RUN/"
  rclone copy "$DATA_ROOT/model_a/$RUN" "$REMOTE:$SRC_PATH/model_a/$RUN" \
      --include "*.md" --include "*.json" --include "*.csv" --include "ab_broadcast_*/**" -P
  rclone copy "$DATA_ROOT/model_a" "$REMOTE:$SRC_PATH/model_a" \
      --include "MODEL_RUNS.md" --include "run_registry.jsonl" --include "issues_ledger.jsonl" --include "run_prereg.jsonl" -P
  exit 0
fi

echo "[sync] $REMOTE:$SRC_PATH -> $DATA_ROOT (first pull copies everything; later pulls only what changed)"
# --exclude: the laptop's run folders are not needed on the VM except the registry files
rclone sync "$REMOTE:$SRC_PATH" "$DATA_ROOT" \
    --exclude "model_a/frozen_*/**" --exclude "model_a/*/_resume_cache/**" \
    --transfers 8 --checkers 16 -P
# the registry / pre-registration / ledger files are the one thing both sides write
rclone copy "$REMOTE:$SRC_PATH/model_a" "$DATA_ROOT/model_a" \
    --include "MODEL_RUNS.md" --include "run_registry.jsonl" --include "issues_ledger.jsonl" --include "run_prereg.jsonl" -P
du -sh "$DATA_ROOT"
echo "[sync] done"
