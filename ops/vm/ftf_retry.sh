#!/usr/bin/env bash
# ftf_retry.sh <FIRST_TO_FILE.md> <out_root> [chunk=25] [gap_s=20] [sleep_s=1800]
#
# The court-record service (CourtListener) throttles a long names list (HTTP 429 after a few
# dozen names, 217 of 250 refused on 2026-10-30). This re-runs ONLY the refused names, in small
# chunks with a long pause between names and a longer pause between chunks, inside the tmux
# session the caller starts. Each chunk writes its own folder under <out_root>; the merged
# read is `grep` over <out_root>/*/FIRST_TO_FILE.md. Public dockets only; a sealed qui tam is
# invisible to every public search.
set -euo pipefail
MD="$1"; OUT="$2"; CHUNK="${3:-25}"; GAP="${4:-20}"; SLEEP="${5:-1800}"
cd "$(dirname "$0")/../.."
# shellcheck disable=SC1091
source .venv/bin/activate
set -a; [ -f "$HOME/.medicaid_env" ] && source "$HOME/.medicaid_env"; set +a
export MEDICAID_DATA_ROOT="${MEDICAID_DATA_ROOT:-/data/medicaid}"
mkdir -p "$OUT"
awk '/^## /{n=substr($0,4)} /^_Not searched/{print n}' "$MD" > "$OUT/refused.txt"
echo "[ftf_retry] $(wc -l < "$OUT/refused.txt") refused name(s) from $MD"
split -l "$CHUNK" -d "$OUT/refused.txt" "$OUT/chunk_"
i=0
for f in "$OUT"/chunk_*; do
  [ -s "$f" ] || continue
  i=$((i + 1))
  echo "[ftf_retry] chunk $i: $f ($(wc -l < "$f") names) $(date -u +%FT%TZ)"
  python -m src.enforcement.first_to_file --since 2010-01-01 --name-gap "$GAP" --names-file "$f" --out "${f}_out" || true
  sleep "$SLEEP"
done
echo "[ftf_retry] DONE $(date -u +%FT%TZ)"
grep -h '^Names searched' "$OUT"/chunk_*_out/FIRST_TO_FILE.md 2>/dev/null || true
