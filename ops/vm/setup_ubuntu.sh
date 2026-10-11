#!/usr/bin/env bash
# ops/vm/setup_ubuntu.sh  --  one-time setup of a night-run VM (Ubuntu 22.04 / 24.04).
#
# Run as the login user (not root) right after the VM boots:
#   curl -fsSL https://raw.githubusercontent.com/travishub09/medicaid-fraud-detection/claude/happy-mccarthy-3skrn3/ops/vm/setup_ubuntu.sh | bash
# or copy this file up and `bash setup_ubuntu.sh`.
#
# It installs Python + git + rclone, clones the repo at the dev branch, installs the
# requirements into a venv, and prepares the data root. The DATA itself comes from
# OneDrive through rclone (ops/vm/sync_data.sh) once `rclone config` has been run
# interactively ONCE on the VM (it opens a browser login; follow docs/VM_RUNBOOK.md).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/travishub09/medicaid-fraud-detection.git}"
BRANCH="${BRANCH:-claude/happy-mccarthy-3skrn3}"
REPO_DIR="${REPO_DIR:-$HOME/medicaid-fraud-detection}"
DATA_ROOT="${MEDICAID_DATA_ROOT:-/data/medicaid}"

echo "[setup] packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git rclone build-essential libgomp1 htop

echo "[setup] repo -> $REPO_DIR (branch $BRANCH)"
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" fetch origin "$BRANCH" && git -C "$REPO_DIR" checkout "$BRANCH" && git -C "$REPO_DIR" pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

echo "[setup] python venv + requirements"
python3 -m venv "$REPO_DIR/.venv"
# shellcheck disable=SC1091
source "$REPO_DIR/.venv/bin/activate"
pip install --upgrade pip wheel -q
pip install -r "$REPO_DIR/requirements.txt" -q
pip install psutil -q

echo "[setup] data root -> $DATA_ROOT"
sudo mkdir -p "$DATA_ROOT" && sudo chown "$USER":"$USER" "$DATA_ROOT"
mkdir -p "$DATA_ROOT/interim/duckdb_tmp"

# the environment every shell gets: the data root, the DuckDB ceiling, the Manus key
# placeholder (fill it in by hand; never commit it)
ENVF="$HOME/.medicaid_env"
if [ ! -f "$ENVF" ]; then
  cat > "$ENVF" <<EOF
export MEDICAID_DATA_ROOT=$DATA_ROOT
export MEDICAID_DUCKDB_TMP=$DATA_ROOT/interim/duckdb_tmp
# DuckDB ceiling: about 40 percent of RAM (the rest is for pandas / LightGBM)
export MEDICAID_DUCKDB_MEM=\$(awk '/MemTotal/ {printf "%dGB", int(\$2/1024/1024*0.4)}' /proc/meminfo)
# export MANUS_API_KEY=...        # only if the dossier dispatch will run from here
EOF
  grep -q ".medicaid_env" "$HOME/.bashrc" || echo "source $ENVF" >> "$HOME/.bashrc"
fi

echo "[setup] smoke test (no data needed)"
cd "$REPO_DIR" && python -m pytest tests/test_feature_partition.py -q

cat <<EOF

[setup] DONE. Next:
  1. rclone config          (one interactive login to OneDrive; remote name: onedrive)
  2. bash ops/vm/sync_data.sh   (pulls Desktop/data from OneDrive into $DATA_ROOT)
  3. bash ops/vm/run_frozen_night.sh v11 2021-12   (the first full-feature run)
Read docs/VM_RUNBOOK.md for the whole flow and the costs.
EOF
