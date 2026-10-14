#!/usr/bin/env bash
# ops/vm/measure_run.sh — the MEASUREMENT block of a frozen run (2026-10-14).
#
#   bash ops/vm/measure_run.sh v18 2021-12            # on an existing run folder
#   MEASURE_ABLATION=0 bash ops/vm/measure_run.sh v18 2021-12   # skip the slow retrains
#
# docs/MODEL_QUESTIONS_2026-10-14.md §7 item 1: the measurement that says which scheme
# families to drop was BUILT but never in the runner. This block runs on the run's own
# matrix and forward rulers, retrains nothing the registry grades, and feeds the ledger:
#   1. SCHEME_HEALTH refreshed WITH the per-scheme AUC on the FCA ruler (fca_verdict)
#   2. signal_ranking.csv       per-feature separation on covered rows
#   3. LABEL_ENRICHMENT.md      what the forward cases light up (codes / taxonomies /
#                               states / size / tenure / scheme top deciles) — the
#                               design brief for schemes nobody has written
#   4. LABEL_ABLATION.md        retrain with each label SOURCE removed, forward delta + CI
#   5. SOURCE_ABLATION.md       core vs full vs leave-one-source-family-out, OOF on the
#                               forward ruler (the slow one; MEASURE_ABLATION=0 skips 4+5)
# run_frozen_night.sh and regrade_from.sh call it before the ledger. Nothing here moves
# a bar: a prune is a new pre-registration.
set -uo pipefail
TAG="${1:?tag, e.g. v18}"
CUT="${2:?freeze month, e.g. 2021-12}"
DATA="${MEDICAID_DATA_ROOT:-/data/medicaid}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="${RUN_DIR:-$DATA/model_a/frozen_${CUT}_${TAG}}"
MEASURE_ABLATION="${MEASURE_ABLATION:-1}"
MATRIX="$RUN/provider_features_for_model.parquet"
MANIFEST="$RUN/feature_manifest.json"
FCA="$RUN/future_bans_after_${CUT}__fca36.csv"
[ -f "$FCA" ] || FCA="$RUN/future_bans_after_${CUT}.csv"
SPEND="$DATA/interim/spending_asof_${CUT}.parquet"
[ -f "$SPEND" ] || SPEND="$DATA/processed/spending_fact.parquet"
cd "$REPO"
# shellcheck disable=SC1091
[ -f "$REPO/.venv/bin/activate" ] && source "$REPO/.venv/bin/activate"
[ -f "$MATRIX" ] || { echo "[$TAG] measure: MISSING $MATRIX"; exit 1; }
[ -f "$FCA" ] || { echo "[$TAG] measure: MISSING the forward ruler $FCA"; exit 1; }

m() { echo; echo "[$TAG] measure $1: $2   ($(date '+%F %T'))"; }
mfail() { echo "[$TAG] measure step $1 FAILED ($(date '+%F %T')) -- the run's grade stands; paste the lines above to Claude"; }

m 1/5 "scheme health on BOTH labels (exclusion label + the FCA ruler)"
python -u -m src.model_a.scheme_health --matrix "$MATRIX" --manifest "$MANIFEST" --future-label "$FCA" \
    --baseline "$RUN/scheme_health.json" --out "$RUN/SCHEME_HEALTH.md" --json-out "$RUN/scheme_health.json" || mfail 1

m 2/5 "signal ranking (per-feature separation on covered rows)"
python -u -m src.model_a.signal_ranking --matrix "$MATRIX" --manifest "$MANIFEST" --out "$RUN/signal_ranking.csv" || mfail 2

m 3/5 "label enrichment (what the forward cases light up)"
python -u -m src.model_a.label_enrichment --run-dir "$RUN" --future-label "$FCA" --spending "$SPEND" || mfail 3

GRAPH_ASOF="$DATA/graph_asof_${CUT}_full"
EXCL="$GRAPH_ASOF/nodes/exclusion_nodes.parquet"
[ -f "$EXCL" ] || EXCL="$DATA/graph/nodes/exclusion_nodes.parquet"
CASES="$DATA/enforcement/doj_cases_v3_plus_cia.csv"
[ -f "$CASES" ] || CASES="$DATA/enforcement/doj_cases_v3.csv"
m 3b/5 "label audit (every label source graded: identifier, date, FCA relevance, reach; the case DB and the CIA roster)"
python -u -m src.model_a.label_audit --exclusion-nodes "$EXCL" --matrix "$MATRIX" --case-db "$CASES" \
    $( [ -f "$DATA/preclean/cia/cia_list.csv" ] && echo --cia "$DATA/preclean/cia/cia_list.csv" ) \
    --org-nodes "$DATA/graph/nodes/org_nodes.parquet" --cutoff "$CUT" --out "$RUN/LABEL_AUDIT.md" || mfail 3b
m 3c/5 "data refresh (what to download, what cannot be dated) + the standing questions readout"
python -u -m src.model_a.data_refresh --run-dir "$RUN" --data-root "$DATA" --reminders "$REPO/docs/reminders.json" || mfail 3c

if [ "$MEASURE_ABLATION" = "1" ]; then
  m 4/5 "label ablation (one label source removed at a time, forward delta + CI)"
  python -u -m src.model_a.label_ablation --matrix "$MATRIX" --manifest "$MANIFEST" --future-label "$FCA" \
      --out "$RUN/LABEL_ABLATION.md" --splits 5 --boot 200 || mfail 4
  m 5/5 "source ablation OOF on the forward ruler (core vs full vs leave-one-family-out; the slow one)"
  python -u -m src.model_a.source_ablation --matrix "$MATRIX" --manifest "$MANIFEST" --future-label "$FCA" \
      --oof --per-group --splits 5 --cutoff "$CUT" --out "$RUN/SOURCE_ABLATION.md" || mfail 5
else
  echo "[$TAG] measure 4/5 + 5/5 skipped (MEASURE_ABLATION=0)"
fi
m 5b/5 "QUESTIONS_READOUT (schemes / wiring / enrichment / data / label / research lanes, from the artifacts)"
python -u -m src.model_a.questions_readout --run-dir "$RUN" --data-root "$DATA" || mfail 5b
echo "[$TAG] measure DONE $(date '+%F %T')"
