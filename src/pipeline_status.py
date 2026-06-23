"""
pipeline_status.py — "where did I leave off?" in one command.

Run ``python -m src.pipeline_status`` (honors MEDICAID_DATA_ROOT, or pass
``--data-root``) to print a checklist of every pipeline stage — done vs missing,
with row counts and timestamps for the outputs that exist — and the exact next
command to run. Read-only: it just stats the output files each stage writes, so
it is always safe and instant.

A stage is "done" when its primary output marker exists (each stage writes its
outputs + a report only on success). Optional stages (the legacy CSV track,
verify_layer1) are labelled and never block the "next step" suggestion.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

# One row per stage, in run order:
#   key, label, marker (relative to data root), command, optional
STAGES = [
    ("integrate", "integrate (clean tables)", "processed/spending_fact.parquet",
     "python -m src.attempt_2.ingest.integrate", False),
    ("coverage", "coverage diagnostic", "processed/COVERAGE_DIAGNOSTIC.md",
     "python -m src.attempt_2.audit.diagnose_coverage", False),
    ("corruption", "corruption audit", "processed/CORRUPTION_AUDIT.md",
     "python -m src.attempt_2.audit.audit_corruption", False),
    ("features", "provider features", "features/provider_features.parquet",
     "python -m src.attempt_2.ingest.features", False),
    ("detect", "detect (3-layer leads)", "detection/fraud_leads.parquet",
     "python -m src.attempt_2.leads.detect", False),
    ("verify_layer1", "verify layer-1 cases", "detection/layer1_candidate_cases.parquet",
     "python -m src.attempt_2.leads.verify_layer1", True),
    ("refine_layer2", "refine layer-2 (v2)", "detection/fraud_leads_v2.parquet",
     "python -m src.attempt_2.leads.refine_layer2", False),
    ("refine_layer2_v3", "refine layer-2 (v3)", "detection/fraud_leads_v3.parquet",
     "python -m src.attempt_2.leads.refine_layer2_v3", False),
    ("company_rollup", "company rollup (legacy CSV)", "detection/company_rollup.parquet",
     "python -m src.attempt_2.leads.company_rollup", True),
    ("entity_graph", "entity graph", "graph/GRAPH_REPORT.md",
     "python -m src.entity_graph --input {root}/processed --out {root}/graph", False),
    ("company_features", "company features (NPI→org rollup)",
     "features/company_features.parquet",
     "python -m src.model_a.build_features", False),
    ("model_a", "Model A (dossiers)", "model_a/MODEL_A_REPORT.md",
     "python -m src.model_a --graph-dir {root}/graph "
     "--features {root}/features/company_features.parquet "
     "--spending {root}/processed/spending_fact.parquet --out {root}/model_a", False),
    ("doj_case_db", "DOJ case DB (calibration labels)",
     "feeds/enforcement/doj_cases.csv",
     "python -m src.enforcement.fetch --backfill-years 10   # needs network", True),
    ("calibration", "Model A calibration (priors + PU model)",
     "model_a/sector_priors.json",
     "python -m src.model_a.calibrate", True),
]


def _data_root(cli: str | None) -> Path:
    if cli:
        return Path(cli)
    return Path(os.environ.get("MEDICAID_DATA_ROOT",
                               str(Path.home() / "Desktop" / "data")))


def _detail(path: Path) -> str:
    """mtime + (for parquet) row count, read cheaply from file metadata."""
    when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    if path.suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
            rows = pq.ParquetFile(path).metadata.num_rows
            return f"{rows:>14,} rows   ({when})"
        except Exception:
            return f"{'':>14}        ({when})"
    return f"{'':>14}        ({when})"


def status(root: Path) -> list[tuple]:
    rows = []
    for key, label, marker, cmd, optional in STAGES:
        p = root / marker
        done = p.exists()
        rows.append((key, label, marker, cmd, optional, done, p))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", default=None,
                    help="override MEDICAID_DATA_ROOT")
    args = ap.parse_args()
    root = _data_root(args.data_root)

    print(f"\nPipeline status — data root: {root}")
    if not root.exists():
        print("  (data root does not exist yet — nothing has run)\n")
    print()
    rows = status(root)
    next_cmd = None
    for i, (key, label, marker, cmd, optional, done, p) in enumerate(rows, 1):
        box = "[x]" if done else "[ ]"
        tag = " (optional)" if optional else ""
        line = f"  {box} {i:>2}. {label}{tag}".ljust(38)
        if done:
            line += "  " + marker.replace("/", os.sep) + "   " + _detail(p)
        else:
            line += "  " + marker.replace("/", os.sep) + "   — missing"
        print(line)
        if next_cmd is None and not done and not optional:
            next_cmd = cmd.format(root=str(root))

    print()
    if next_cmd is None:
        print("All core stages complete. Dossiers are in "
              f"{root / 'model_a' / 'dossiers'}\n")
    else:
        print("Next step:")
        if os.name == "nt":
            print(f"  set MEDICAID_DATA_ROOT={root}")
        else:
            print(f"  export MEDICAID_DATA_ROOT={root}")
        print(f"  {next_cmd}\n")


if __name__ == "__main__":
    main()
