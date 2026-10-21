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

if [ "${1:-down}" = "down-preclean" ]; then
  # 2026-10-21: the VM's processed/ and enforcement/ hold label work written ON THE VM
  # (the research lanes, the name-resolved exclusions, the merged case file) that the
  # laptop does not have; a full `down` would roll them back to OneDrive's copies.
  # This pulls the RAW inputs only (the October LEIE / NPPES, the 2021 editions, the
  # hold folders) and deletes VM extras under preclean/ that OneDrive no longer has.
  echo "[sync] $REMOTE:$SRC_PATH/preclean -> $DATA_ROOT/preclean (raw inputs only)"
  rclone sync "$REMOTE:$SRC_PATH/preclean" "$DATA_ROOT/preclean" --transfers 8 --checkers 16 -P
  du -sh "$DATA_ROOT/preclean"
  echo "[sync] done (preclean only; processed/ and enforcement/ untouched)"
  exit 0
fi

echo "[sync] $REMOTE:$SRC_PATH -> $DATA_ROOT (first pull copies everything; later pulls only what changed)"
echo "[sync] WARNING: a full pull REPLACES processed/ and enforcement/ with OneDrive's copies; after a lanes"
echo "[sync]          load on the VM use 'down-preclean' instead (2026-10-21)"
# --exclude: the laptop's run folders (6.5 GB) and the attempt_2 stage outputs under
# features/ (15 GB; the frozen runs read detection/, never features/) stay behind.
# Measured 2026-10-11: preclean 178 GB, interim 23 GB, processed 15 GB, the rest small.
rclone sync "$REMOTE:$SRC_PATH" "$DATA_ROOT" \
    --exclude "model_a/frozen_*/**" --exclude "model_a/*/_resume_cache/**" --exclude "features/**" \
    --transfers 8 --checkers 16 -P
# the registry / pre-registration / ledger files are the one thing both sides write
rclone copy "$REMOTE:$SRC_PATH/model_a" "$DATA_ROOT/model_a" \
    --include "MODEL_RUNS.md" --include "run_registry.jsonl" --include "issues_ledger.jsonl" --include "run_prereg.jsonl" -P
du -sh "$DATA_ROOT"
echo "[sync] done"
