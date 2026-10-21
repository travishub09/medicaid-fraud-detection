#!/usr/bin/env bash
# The data-file deep scan (operator, 2026-10-21): every preclean file graded against
# the adapters' own column maps and the freeze, the processed/ inventory, the preflight
# registry, the vintage inspector and the scheme-discovery scan, packed into ONE tar for
# the laptop. Read-only on every input. ~20-40 minutes with --counts (the spending fact
# is scanned once by DuckDB).
#
#   bash ops/vm/data_audit.sh [cutoff=2021-12] [run_dir=<latest frozen_<cutoff>_v*>]
set -uo pipefail
CUT="${1:-2021-12}"
ROOT="${MEDICAID_DATA_ROOT:-/data/medicaid}"
cd "$(dirname "$0")/../.." || exit 1
source .venv/bin/activate 2>/dev/null || true
RUN="${2:-$(ls -d "$ROOT"/model_a/frozen_${CUT}_v* 2>/dev/null | sort -V | tail -1)}"
STAMP=$(date +%Y%m%d_%H%M)
OUT="$ROOT/audit_$STAMP"; mkdir -p "$OUT"
echo "[data_audit.sh] root=$ROOT cutoff=$CUT run=$RUN out=$OUT"

echo "[1/5] preflight registry"
python -m src.preflight --data-root "$ROOT" > "$OUT/PREFLIGHT.txt" 2>&1
python -m src.preflight --registry --data-root "$ROOT" > "$OUT/PREFLIGHT_REGISTRY.txt" 2>&1

echo "[2/5] data_audit (frozen $CUT, with row counts)"
python -m src.data_audit --data-root "$ROOT" --cutoff "$CUT" --counts \
  ${RUN:+--run-dir "$RUN"} --out "$OUT/frozen_$CUT" > "$OUT/data_audit_frozen.log" 2>&1
echo "[3/5] data_audit (current-day)"
python -m src.data_audit --data-root "$ROOT" --out "$OUT/current" > "$OUT/data_audit_current.log" 2>&1

echo "[4/5] vintage inspector + scheme discovery"
python -m src.vintage_inspect --data-root "$ROOT" > "$OUT/vintage_inspect.log" 2>&1
cp "$ROOT/preclean/VINTAGE_INSPECT.md" "$OUT/" 2>/dev/null
python -m src.model_a.scheme_discovery --data-root "$ROOT" > "$OUT/scheme_discovery.log" 2>&1
cp "$ROOT/preclean/SCHEME_DISCOVERY.md" "$OUT/" 2>/dev/null
cp "$ROOT/preclean/EDITIONS.csv" "$OUT/" 2>/dev/null

echo "[5/5] last run's reports"
if [ -n "$RUN" ] && [ -d "$RUN" ]; then
  mkdir -p "$OUT/last_run"
  cp "$RUN"/SOURCES_REPORT.md "$RUN"/feature_manifest.json "$RUN"/scheme_health.json \
     "$RUN"/SCHEME_HEALTH.md "$RUN"/RUN_ISSUES.md "$RUN"/EXPECTATIONS_REPORT.md \
     "$RUN"/LABEL_AUDIT.md "$RUN"/DATA_REFRESH.md "$OUT/last_run/" 2>/dev/null
fi
git log --oneline -1 > "$OUT/code_version.txt"
tar -czf "$HOME/data_audit_$STAMP.tar.gz" -C "$ROOT" "audit_$STAMP"
echo "[data_audit.sh] DONE -> $HOME/data_audit_$STAMP.tar.gz"
tail -8 "$OUT/data_audit_frozen.log"
